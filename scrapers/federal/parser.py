"""Parser for HealthCare.gov `/plans/search` responses."""

from __future__ import annotations

from typing import Any, Iterator


def iter_plans(response: Any, url: str = "") -> Iterator[dict[str, Any]]:
    """Each plan object in one response page: `{"plans": [...], "total": n, ...}`."""
    if isinstance(response, dict):
        yield from response.get("plans") or []
