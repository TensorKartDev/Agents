"""Authentication helpers for AGX web surfaces."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class SessionUser:
    user_id: str
    tenant_id: str
    tenant_name: str
    username: str
    email: str
    role: str
    display_name: str


class AuthManager:
    """Password hashing and signed cookie session management."""

    def __init__(self, secret: Optional[str] = None) -> None:
        self.secret = (secret or os.getenv("AGX_AUTH_SECRET") or "agx-dev-secret-change-me").encode("utf-8")
        self.session_ttl_seconds = int(os.getenv("AGX_SESSION_TTL_SECONDS", "43200"))

    def hash_password(self, password: str, salt: Optional[str] = None) -> tuple[str, str]:
        raw_salt = salt or secrets.token_hex(16)
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            raw_salt.encode("utf-8"),
            120_000,
        )
        return base64.b64encode(digest).decode("ascii"), raw_salt

    def verify_password(self, password: str, password_hash: str, salt: str) -> bool:
        candidate_hash, _ = self.hash_password(password, salt=salt)
        return hmac.compare_digest(candidate_hash, password_hash)

    def issue_session(self, user: SessionUser) -> str:
        payload = {
            "user_id": user.user_id,
            "tenant_id": user.tenant_id,
            "tenant_name": user.tenant_name,
            "username": user.username,
            "email": user.email,
            "role": user.role,
            "display_name": user.display_name,
            "exp": int(time.time()) + self.session_ttl_seconds,
        }
        body = _urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        sig = hmac.new(self.secret, body.encode("ascii"), hashlib.sha256).digest()
        return f"{body}.{_urlsafe_b64encode(sig)}"

    def read_session(self, token: str) -> Optional[SessionUser]:
        if not token or "." not in token:
            return None
        body, signature = token.split(".", 1)
        expected = _urlsafe_b64encode(hmac.new(self.secret, body.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            return None
        try:
            payload = json.loads(_urlsafe_b64decode(body).decode("utf-8"))
        except Exception:
            return None
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        return SessionUser(
            user_id=str(payload.get("user_id", "")),
            tenant_id=str(payload.get("tenant_id", "")),
            tenant_name=str(payload.get("tenant_name", "")),
            username=str(payload.get("username", "")),
            email=str(payload.get("email", "")),
            role=str(payload.get("role", "developer")),
            display_name=str(payload.get("display_name") or payload.get("username") or ""),
        )


def _urlsafe_b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _urlsafe_b64decode(value: str) -> bytes:
    padded = value + "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))
