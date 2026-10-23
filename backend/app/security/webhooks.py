import hashlib
import hmac

from backend.app.core.config import settings


def verify_signature(body: bytes, signature: str | None) -> bool:
    if not settings.webhook_secret or not signature:
        return False
    expected = hmac.new(
        settings.webhook_secret.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()
    supplied = signature.removeprefix("sha256=")
    return hmac.compare_digest(expected, supplied)
