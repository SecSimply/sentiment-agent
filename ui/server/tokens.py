import base64
import hashlib
import hmac


def make_token(secret: str, page_id: str, version: int) -> str:
    """Public-URL capability token. Derived, not stored: bumping the page's token_version revokes it."""
    mac = hmac.new(secret.encode(), f"lab:{page_id}:{version}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac)[:32].decode()


def verify_token(secret: str, page_id: str, version: int, token: str) -> bool:
    return hmac.compare_digest(make_token(secret, page_id, version), token)
