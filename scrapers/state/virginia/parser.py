"""Parser for the responses of Virginia's three-step flow."""

from __future__ import annotations

from typing import Any, Iterator

PLANS_ENDPOINT = "/getIndividualPlans"


def iter_plans(response: Any, url: str = "") -> Iterator[dict[str, Any]]:
    """Each plan object in one response: `{"success": true, "data": [...]}`.

    Only the last of the three requests per scenario (getIndividualPlans) lists plans.
    When `url` is given, responses from the other two endpoints yield nothing.
    """
    if url and not url.endswith(PLANS_ENDPOINT):
        return
    if isinstance(response, dict):
        yield from response.get("data") or []
