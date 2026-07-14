"""Resolve a user-supplied channel reference (URL / handle / ID) into the
parameters for a single `channels.list` call (1 quota unit).

We deliberately avoid `search.list` (100 units): every supported form maps to
`id`, `forHandle` or `forUsername`. A custom `/c/<name>` URL is attempted as a
handle (best effort); if that fails the caller surfaces a clear error asking for
the @handle or the /channel/<id> URL.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

# A channel ID is "UC" followed by 22 chars of the base64url alphabet.
_CHANNEL_ID_RE = re.compile(r"^UC[0-9A-Za-z_-]{22}$")


def parse_channel_reference(reference: str) -> dict[str, str]:
    """Return kwargs for `YouTubeClient.get_channel` (channel_id | handle | username)."""
    ref = (reference or "").strip()
    if not ref:
        raise ValueError("Empty channel reference.")

    if _CHANNEL_ID_RE.match(ref):
        return {"channel_id": ref}
    if ref.startswith("@"):
        return {"handle": ref}

    parsed = urlparse(ref)
    if parsed.scheme in {"http", "https"}:
        parts = [p for p in parsed.path.split("/") if p]
        if not parts:
            raise ValueError(f"Could not find a channel in the URL: {reference!r}")
        head = parts[0]
        if head == "channel" and len(parts) > 1 and _CHANNEL_ID_RE.match(parts[1]):
            return {"channel_id": parts[1]}
        if head == "user" and len(parts) > 1:
            return {"username": parts[1]}
        if head.startswith("@"):
            return {"handle": head}
        if head == "c" and len(parts) > 1:  # legacy custom URL — best-effort handle
            return {"handle": _as_handle(parts[1])}
        return {"handle": _as_handle(head)}

    # Bare token: treat as a handle.
    return {"handle": _as_handle(ref)}


def _as_handle(value: str) -> str:
    return value if value.startswith("@") else f"@{value}"
