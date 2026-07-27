from __future__ import annotations

import base64
import json
import os
import ipaddress
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class ProductionSettings:
    environment: str = "development"
    database_url: str = "sqlite:///:memory:"
    token_signing_key: bytes = b""
    vault_master_key: bytes | None = None
    vault_keys: dict[int, bytes] = field(default_factory=dict)
    vault_active_key_version: int = 1
    audit_anchor_key: bytes = b""
    audit_anchor_path: Path | None = None
    token_ttl_seconds: int = 3600
    issuer: str = "difoundry"
    bootstrap_enabled: bool = True
    bootstrap_token: str | None = None
    cors_origins: tuple[str, ...] = ()
    trusted_proxy_cidrs: tuple[str, ...] = ()
    max_request_bytes: int = 2_000_000
    rate_limit_per_minute: int = 180
    login_lockout_threshold: int = 8
    login_lockout_seconds: int = 900
    password_time_cost: int = 3
    password_memory_cost: int = 65536
    password_parallelism: int = 2
    static_dir: Path | None = None

    def __post_init__(self) -> None:
        if self.vault_master_key is not None and not self.vault_keys:
            self.vault_keys = {self.vault_active_key_version: self.vault_master_key}
        if not self.vault_keys:
            self.vault_keys = {1: (b"development-DIFOUNDRY-VAULT-KEY" * 2)[:32]}
        if self.vault_active_key_version not in self.vault_keys:
            raise ValueError("Active vault key version is missing from keyring")
        for value in self.trusted_proxy_cidrs:
            try:
                ipaddress.ip_network(value, strict=False)
            except ValueError as exc:
                raise ValueError(f"Invalid trusted proxy CIDR: {value}") from exc

    def assert_startup_safe(self) -> None:
        if self.environment != "production":
            return
        errors: list[str] = []
        if self.database_url.startswith("sqlite"):
            errors.append("production requires PostgreSQL, not SQLite")
        if not self.token_signing_key or self.token_signing_key.startswith(b"development-"):
            errors.append("DIFOUNDRY_TOKEN_KEY is not externally configured")
        if not self.vault_keys or any(key.startswith(b"development-") for key in self.vault_keys.values()):
            errors.append("DIFOUNDRY_VAULT_KEYS is not externally configured")
        if not self.audit_anchor_key or self.audit_anchor_key.startswith(b"development-"):
            errors.append("DIFOUNDRY_AUDIT_ANCHOR_KEY is not externally configured")
        if self.audit_anchor_path is None:
            errors.append("DIFOUNDRY_AUDIT_ANCHOR_PATH is required")
        if self.bootstrap_enabled and not self.bootstrap_token:
            errors.append("enabled bootstrap requires DIFOUNDRY_BOOTSTRAP_TOKEN")
        if "*" in self.cors_origins:
            errors.append("wildcard production CORS is forbidden")
        if errors:
            raise ValueError("Unsafe production configuration: " + "; ".join(errors))

    @classmethod
    def from_env(cls) -> "ProductionSettings":
        environment = os.getenv("DIFOUNDRY_ENV", "development").lower()
        token_key = _key_from_env("DIFOUNDRY_TOKEN_KEY", 32)
        vault_keys, active_version = _vault_keyring_from_env()
        anchor_key = _key_from_env("DIFOUNDRY_AUDIT_ANCHOR_KEY", 32)
        static_value = os.getenv("DIFOUNDRY_STATIC_DIR")
        anchor_path = os.getenv("DIFOUNDRY_AUDIT_ANCHOR_PATH")
        bootstrap_default = "false" if environment == "production" else "true"
        return cls(
            environment=environment,
            database_url=os.getenv("DIFOUNDRY_DATABASE_URL", "sqlite:///:memory:"),
            token_signing_key=token_key,
            vault_master_key=vault_keys[active_version],
            vault_keys=vault_keys,
            vault_active_key_version=active_version,
            audit_anchor_key=anchor_key,
            audit_anchor_path=Path(anchor_path) if anchor_path else None,
            token_ttl_seconds=int(os.getenv("DIFOUNDRY_TOKEN_TTL", "3600")),
            issuer=os.getenv("DIFOUNDRY_TOKEN_ISSUER", "difoundry"),
            bootstrap_enabled=os.getenv("DIFOUNDRY_BOOTSTRAP_ENABLED", bootstrap_default).lower() == "true",
            bootstrap_token=os.getenv("DIFOUNDRY_BOOTSTRAP_TOKEN"),
            cors_origins=tuple(value.strip() for value in os.getenv("DIFOUNDRY_CORS_ORIGINS", "").split(",") if value.strip()),
            trusted_proxy_cidrs=tuple(value.strip() for value in os.getenv("DIFOUNDRY_TRUSTED_PROXY_CIDRS", "").split(",") if value.strip()),
            max_request_bytes=int(os.getenv("DIFOUNDRY_MAX_REQUEST_BYTES", "2000000")),
            rate_limit_per_minute=int(os.getenv("DIFOUNDRY_RATE_LIMIT_PER_MINUTE", "180")),
            login_lockout_threshold=int(os.getenv("DIFOUNDRY_LOGIN_LOCKOUT_THRESHOLD", "8")),
            login_lockout_seconds=int(os.getenv("DIFOUNDRY_LOGIN_LOCKOUT_SECONDS", "900")),
            password_time_cost=int(os.getenv("DIFOUNDRY_PASSWORD_TIME_COST", "3")),
            password_memory_cost=int(os.getenv("DIFOUNDRY_PASSWORD_MEMORY_COST", "65536")),
            password_parallelism=int(os.getenv("DIFOUNDRY_PASSWORD_PARALLELISM", "2")),
            static_dir=Path(static_value) if static_value else None,
        )


def _vault_keyring_from_env() -> tuple[dict[int, bytes], int]:
    encoded_ring = os.getenv("DIFOUNDRY_VAULT_KEYS")
    active = int(os.getenv("DIFOUNDRY_VAULT_ACTIVE_KEY_VERSION", "1"))
    if encoded_ring:
        try:
            raw = json.loads(encoded_ring)
            ring = {int(version): _decode_key(value, "DIFOUNDRY_VAULT_KEYS") for version, value in raw.items()}
        except Exception as exc:
            raise ValueError("DIFOUNDRY_VAULT_KEYS must be JSON mapping integer versions to URL-safe base64 keys") from exc
        if active not in ring:
            raise ValueError("DIFOUNDRY_VAULT_ACTIVE_KEY_VERSION is not present in DIFOUNDRY_VAULT_KEYS")
        return ring, active
    return {1: _key_from_env("DIFOUNDRY_VAULT_KEY", 32)}, 1


def _decode_key(value: str, name: str, length: int = 32) -> bytes:
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except Exception as exc:
        raise ValueError(f"{name} must contain URL-safe base64 keys") from exc
    if len(decoded) != length:
        raise ValueError(f"{name} keys must decode to exactly {length} bytes")
    return decoded


def _key_from_env(name: str, length: int) -> bytes:
    value = os.getenv(name)
    if not value:
        return (f"development-{name}-not-for-production".encode("utf-8") * 4)[:length]
    return _decode_key(value, name, length)


def production_key_configured(name: str) -> bool:
    return bool(os.getenv(name))
