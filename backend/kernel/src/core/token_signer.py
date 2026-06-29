"""token_signer — HS256 JWT mint + verify for the data-service auth seam.

Minimal, dependency-free HS256 (stdlib ``hmac``/``hashlib``/``base64``/``json``)
so B-sim adds no dependency and emits STANDARD JWTs — interoperable with PyJWT
or PostgREST if B-real ever points them at the same shared secret. For
RS256/JWKS (B-real, asymmetric), swap the verifier for a public-key
implementation behind the same :class:`~src.core.token_verifier.TokenVerifier`
port; nothing else changes.

Security notes: the verifier pins ``alg=HS256`` (rejects ``none`` /
alg-confusion), uses ``hmac.compare_digest`` (constant time), and enforces
``exp``. The HMAC secret lives ONLY on the trusted side (host signer + data
service verifier) — never in the sandbox, which holds only a signed token.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

from src.core.token_verifier import OwnerClaims

_HEADER = {"alg": "HS256", "typ": "JWT"}


class InvalidTokenError(Exception):
    """A present but invalid credential — bad signature / expired / malformed.

    The data service maps this to HTTP 401: a forged/expired token is a hard
    failure, never a silent fallthrough."""


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(segment: str) -> bytes:
    pad = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + pad)


def _sign(secret: str, signing_input: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return _b64url_encode(digest)


class TokenSigner:
    """Mints short-lived HS256 JWTs (host / refresh-hook side)."""

    def __init__(self, secret: str, *, default_ttl_s: int = 900) -> None:
        if not secret:
            raise ValueError("token signing secret must be non-empty")
        self._secret = secret
        self._default_ttl_s = default_ttl_s

    def sign(
        self,
        *,
        user_id: str,
        session_id: str | None = None,
        ttl_s: int | None = None,
        now: int | None = None,
    ) -> str:
        issued = int(time.time()) if now is None else now
        ttl = self._default_ttl_s if ttl_s is None else ttl_s
        claims: dict[str, Any] = {
            "sub": user_id,
            "role": "authenticated",
            "iat": issued,
            "exp": issued + ttl,
        }
        if session_id is not None:
            claims["session_id"] = session_id
        header_b64 = _b64url_encode(json.dumps(_HEADER, separators=(",", ":")).encode("utf-8"))
        claims_b64 = _b64url_encode(json.dumps(claims, separators=(",", ":")).encode("utf-8"))
        signing_input = f"{header_b64}.{claims_b64}".encode("ascii")
        return f"{header_b64}.{claims_b64}.{_sign(self._secret, signing_input)}"


class HmacTokenVerifier:
    """``TokenVerifier`` — verifies HS256 JWTs against a shared ``secret``."""

    def __init__(self, secret: str) -> None:
        if not secret:
            raise ValueError("token verify secret must be non-empty")
        self._secret = secret

    def verify(self, token: str | None) -> OwnerClaims | None:
        if not token:
            return None  # no credential → caller may fall back (header path)
        parts = token.split(".")
        if len(parts) != 3:
            raise InvalidTokenError("malformed token")
        header_b64, claims_b64, sig_b64 = parts
        try:
            header = json.loads(_b64url_decode(header_b64))
        except (ValueError, json.JSONDecodeError) as exc:
            raise InvalidTokenError("malformed header") from exc
        if header.get("alg") != "HS256":  # pin algorithm — no "none"/confusion
            raise InvalidTokenError("unsupported alg")
        expected = _sign(self._secret, f"{header_b64}.{claims_b64}".encode("ascii"))
        if not hmac.compare_digest(expected, sig_b64):
            raise InvalidTokenError("bad signature")
        try:
            claims = json.loads(_b64url_decode(claims_b64))
        except (ValueError, json.JSONDecodeError) as exc:
            raise InvalidTokenError("malformed claims") from exc
        exp = claims.get("exp")
        if exp is not None and int(time.time()) >= int(exp):
            raise InvalidTokenError("expired")
        sub = claims.get("sub")
        if not sub:
            raise InvalidTokenError("missing sub")
        session_id = claims.get("session_id")
        return OwnerClaims(
            user_id=str(sub),
            session_id=str(session_id) if session_id else None,
        )
