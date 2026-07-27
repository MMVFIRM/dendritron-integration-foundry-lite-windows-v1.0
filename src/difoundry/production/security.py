from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import delete, insert, select, update

from .database import PlatformDatabase, decode_json, encode_json, now_iso, secret_blobs
from .models import Principal, Role, new_id


class AuthenticationError(ValueError):
    pass


class PasswordService:
    def __init__(self, time_cost: int = 3, memory_cost: int = 65536, parallelism: int = 2) -> None:
        self._hasher = PasswordHasher(time_cost=time_cost, memory_cost=memory_cost, parallelism=parallelism)
        # Equalizes unknown-user and known-user verification cost.
        self._dummy_hash = self._hasher.hash("difoundry-dummy-password-never-valid")

    def hash(self, password: str) -> str:
        return self._hasher.hash(password)

    def verify(self, encoded: str | None, password: str) -> bool:
        candidate = encoded or self._dummy_hash
        try:
            valid = self._hasher.verify(candidate, password)
        except (VerifyMismatchError, InvalidHashError):
            valid = False
        return bool(encoded) and valid


class TokenService:
    def __init__(self, key: bytes, issuer: str, ttl_seconds: int = 3600):
        if len(key) < 32:
            raise ValueError("Token signing key must be at least 32 bytes")
        self.key = key
        self.issuer = issuer
        self.ttl_seconds = ttl_seconds

    def issue(self, principal: Principal) -> str:
        now = int(time.time())
        header = {"alg": "HS256", "typ": "DIF"}
        payload = {
            "iss": self.issuer,
            "sub": principal.user_id,
            "tenant": principal.tenant_id,
            "tenant_slug": principal.tenant_slug,
            "email": principal.email,
            "role": principal.role.value,
            "ver": principal.token_version,
            "jti": uuid4().hex,
            "iat": now,
            "exp": now + self.ttl_seconds,
        }
        encoded = f"{_b64_json(header)}.{_b64_json(payload)}"
        signature = _b64(hmac.new(self.key, encoded.encode("ascii"), hashlib.sha256).digest())
        return f"{encoded}.{signature}"

    def verify(self, token: str) -> Principal:
        try:
            header_part, payload_part, signature = token.split(".")
        except ValueError as exc:
            raise AuthenticationError("Malformed token") from exc
        encoded = f"{header_part}.{payload_part}"
        expected = _b64(hmac.new(self.key, encoded.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            raise AuthenticationError("Invalid token signature")
        try:
            payload = json.loads(_unb64(payload_part))
        except Exception as exc:
            raise AuthenticationError("Invalid token payload") from exc
        if payload.get("iss") != self.issuer:
            raise AuthenticationError("Invalid token issuer")
        if int(payload.get("exp", 0)) <= int(time.time()):
            raise AuthenticationError("Token expired")
        try:
            return Principal(
                user_id=payload["sub"],
                tenant_id=payload["tenant"],
                tenant_slug=payload["tenant_slug"],
                email=payload["email"],
                role=Role(payload["role"]),
                token_version=int(payload.get("ver", 0)),
            )
        except Exception as exc:
            raise AuthenticationError("Invalid token claims") from exc


@dataclass(slots=True)
class SecretEnvelope:
    secret_ref: str
    key_version: int


class EncryptedSecretStore:
    """Versioned AES-256-GCM keyring. Plaintext is never returned through HTTP."""

    def __init__(
        self,
        database: PlatformDatabase,
        master_key: bytes | None = None,
        key_version: int = 1,
        *,
        keys: dict[int, bytes] | None = None,
        active_key_version: int | None = None,
    ):
        if keys is None:
            if master_key is None:
                raise ValueError("A vault key or keyring is required")
            keys = {key_version: master_key}
        if not keys or any(len(value) != 32 for value in keys.values()):
            raise ValueError("Every vault key must be exactly 32 bytes")
        self.database = database
        self.keys = dict(keys)
        self.active_key_version = active_key_version or max(self.keys)
        if self.active_key_version not in self.keys:
            raise ValueError("Active vault key version is missing from keyring")

    def _cipher(self, version: int) -> AESGCM:
        try:
            return AESGCM(self.keys[version])
        except KeyError as exc:
            raise KeyError(f"Vault key version {version} is unavailable") from exc

    def put(
        self,
        tenant_id: str,
        resource_type: str,
        resource_id: str,
        value: dict[str, Any],
        secret_ref: str | None = None,
    ) -> SecretEnvelope:
        reference = secret_ref or new_id("sec")
        version = self.active_key_version
        nonce = os.urandom(12)
        aad = f"{tenant_id}:{resource_type}:{resource_id}:{reference}:{version}".encode()
        ciphertext = self._cipher(version).encrypt(nonce, encode_json(value).encode(), aad)
        stamp = now_iso()
        with self.database.begin() as connection:
            existing = connection.execute(
                select(secret_blobs).where(
                    secret_blobs.c.secret_ref == reference,
                    secret_blobs.c.tenant_id == tenant_id,
                )
            ).mappings().first()
            if existing and (existing["resource_type"] != resource_type or existing["resource_id"] != resource_id):
                raise ValueError("Secret reference is already bound to another resource in this tenant")
            values = dict(
                resource_type=resource_type,
                resource_id=resource_id,
                ciphertext=_b64(ciphertext),
                nonce=_b64(nonce),
                key_version=version,
                updated_at=stamp,
            )
            if existing:
                result = connection.execute(
                    update(secret_blobs)
                    .where(secret_blobs.c.secret_ref == reference, secret_blobs.c.tenant_id == tenant_id)
                    .values(**values)
                )
                if result.rowcount != 1:
                    raise RuntimeError("Secret update did not modify exactly one tenant-scoped row")
            else:
                connection.execute(
                    insert(secret_blobs).values(
                        tenant_id=tenant_id,
                        secret_ref=reference,
                        created_at=stamp,
                        **values,
                    )
                )
        return SecretEnvelope(reference, version)

    def resolve(self, tenant_id: str, secret_ref: str) -> dict[str, Any]:
        row = self.database.fetch_one(
            secret_blobs,
            secret_blobs.c.secret_ref == secret_ref,
            secret_blobs.c.tenant_id == tenant_id,
        )
        if row is None:
            raise KeyError("Secret not found")
        version = int(row["key_version"])
        aad = f"{tenant_id}:{row['resource_type']}:{row['resource_id']}:{secret_ref}:{version}".encode()
        plaintext = self._cipher(version).decrypt(
            _unb64_bytes(row["nonce"]),
            _unb64_bytes(row["ciphertext"]),
            aad,
        )
        return decode_json(plaintext.decode())

    def rotate_tenant(self, tenant_id: str, target_version: int) -> int:
        if target_version not in self.keys:
            raise KeyError(f"Vault key version {target_version} is unavailable")
        rows = self.database.fetch_all(secret_blobs, secret_blobs.c.tenant_id == tenant_id)
        rotated = 0
        old_active = self.active_key_version
        try:
            self.active_key_version = target_version
            for row in rows:
                if int(row["key_version"]) == target_version:
                    continue
                value = self.resolve(tenant_id, row["secret_ref"])
                self.put(
                    tenant_id,
                    row["resource_type"],
                    row["resource_id"],
                    value,
                    row["secret_ref"],
                )
                rotated += 1
        finally:
            self.active_key_version = target_version if target_version in self.keys else old_active
        return rotated

    def delete(self, tenant_id: str, secret_ref: str) -> None:
        with self.database.begin() as connection:
            result = connection.execute(
                delete(secret_blobs).where(
                    secret_blobs.c.secret_ref == secret_ref,
                    secret_blobs.c.tenant_id == tenant_id,
                )
            )
            if result.rowcount not in {0, 1}:
                raise RuntimeError("Tenant-scoped secret delete affected multiple rows")


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _unb64_bytes(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _unb64(value: str) -> str:
    return _unb64_bytes(value).decode("utf-8")


def _b64_json(value: dict[str, Any]) -> str:
    return _b64(json.dumps(value, separators=(",", ":"), sort_keys=True).encode())
