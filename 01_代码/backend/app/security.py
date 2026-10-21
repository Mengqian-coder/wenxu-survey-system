"""Stateless access and submission capabilities, with distinct token purposes."""
from datetime import datetime, timedelta, timezone
import secrets
import unicodedata

from bson import ObjectId
import jwt
from pwdlib import PasswordHash

from .errors import APIError

password_hasher = PasswordHash.recommended()
# Avoid a cheap username-existence timing distinction at the login endpoint.
dummy_hash = password_hasher.hash(secrets.token_urlsafe(32))


def normalize_username(username: str) -> tuple[str, str]:
    display = unicodedata.normalize("NFKC", username).strip()
    key = display.lower()
    if not 3 <= len(display) <= 32 or not 3 <= len(key) <= 128:
        raise APIError(422, "INVALID_USERNAME", "用户名长度必须为 3～32 字符")
    if any(c.isspace() or unicodedata.category(c).startswith("C") for c in display):
        raise APIError(422, "INVALID_USERNAME", "用户名不能包含空白或控制字符")
    return display, key


def issue_token(settings, subject: ObjectId, purpose: str, **claims) -> str:
    now = datetime.now(timezone.utc)
    minutes = settings.access_token_minutes if purpose == "access" else settings.submission_token_minutes
    return jwt.encode({
        "sub": str(subject), "purpose": purpose, "iat": now, "exp": now + timedelta(minutes=minutes),
        "iss": "mongo-survey", "aud": "mongo-survey-api", **claims,
    }, settings.secret_key, algorithm="HS256")


def decode_token(settings, token: str, purpose: str) -> dict:
    try:
        data = jwt.decode(
            token, settings.secret_key, algorithms=["HS256"], issuer="mongo-survey", audience="mongo-survey-api",
            options={"require": ["sub", "purpose", "iat", "exp"]},
        )
        if data["purpose"] != purpose or not ObjectId.is_valid(data["sub"]):
            raise ValueError("wrong token purpose")
        return data
    except (jwt.InvalidTokenError, ValueError, TypeError):
        raise APIError(401, "INVALID_SUBMISSION_TOKEN" if purpose == "submission" else "INVALID_TOKEN",
                       "填写凭据无效或已过期" if purpose == "submission" else "认证凭据无效或已过期") from None
