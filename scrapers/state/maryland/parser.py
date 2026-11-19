"""Parser for Maryland `preelig_complete` responses."""

from __future__ import annotations

from typing import Any, Iterator


def iter_plans(response: Any, url: str = "") -> Iterator[dict[str, Any]]:
    """Each plan object in one response: `{"preelig": {"plans": [...]}}`."""
    preelig = response.get("preelig") if isinstance(response, dict) else None
    if isinstance(preelig, dict):
        yield from preelig.get("plans") or []
