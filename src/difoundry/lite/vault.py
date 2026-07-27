from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .database import LiteDatabase, now_iso
from .key_protection import load_or_create_vault_key


class LocalVault:
    """Local AES-256-GCM vault. The key remains outside the SQLite database."""

    def __init__(self, database: LiteDatabase, key_path: Path):
        self.database = database
        self.key_path = key_path
        self.key = self._load_or_create_key()
        self.cipher = AESGCM(self.key)

    def _load_or_create_key(self) -> bytes:
        return load_or_create_vault_key(self.key_path)

    def put(self, value: dict[str, Any], secret_ref: str | None = None) -> str:
        reference = secret_ref or self.database.new_id("sec")
        nonce = os.urandom(12)
        aad = f"foundry-lite:{reference}".encode()
        ciphertext = self.cipher.encrypt(nonce, json.dumps(value, sort_keys=True).encode(), aad)
        stamp = now_iso()
        with self.database.transaction() as db:
            db.execute(
                "INSERT INTO lite_secrets(secret_ref,nonce,ciphertext,created_at,updated_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(secret_ref) DO UPDATE SET nonce=excluded.nonce,ciphertext=excluded.ciphertext,updated_at=excluded.updated_at",
                (reference, base64.b64encode(nonce).decode(), base64.b64encode(ciphertext).decode(), stamp, stamp),
            )
        return reference

    def resolve(self, secret_ref: str | None) -> dict[str, Any]:
        if not secret_ref:
            return {}
        row = self.database.one("SELECT * FROM lite_secrets WHERE secret_ref=?", (secret_ref,))
        if row is None:
            raise KeyError("Secret not found")
        plaintext = self.cipher.decrypt(
            base64.b64decode(row["nonce"]),
            base64.b64decode(row["ciphertext"]),
            f"foundry-lite:{secret_ref}".encode(),
        )
        return json.loads(plaintext)

    def delete(self, secret_ref: str | None) -> None:
        if not secret_ref:
            return
        with self.database.transaction() as db:
            db.execute("DELETE FROM lite_secrets WHERE secret_ref=?", (secret_ref,))
