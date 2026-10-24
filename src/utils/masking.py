"""Logging helpers for sensitive identifiers."""

__all__ = ["mask_account_id"]


def mask_account_id(account_id: str | None) -> str:
    """Return a log-safe representation of an account id.

    Only the last four characters are kept, so logs stay useful for matching
    entries to an account without exposing the full identifier.

    >>> mask_account_id("2000123456")
    '***3456'
    >>> mask_account_id(None)
    '<not set>'
    """
    if not account_id:
        return "<not set>"
    return f"***{account_id[-4:]}"
