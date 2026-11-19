"""HealthCare.gov plan search (states on the federal enrollment platform).

`resolve` turns a ZIP into its counties with one GET to `/counties/by/zip/{zip}` (the
search requires a county FIPS code, and its results depend on the county, not the ZIP).
`fetch_plans` quotes one scenario: one POST to `/plans/search` per page, advancing `offset` until the response's `total` is reached. The API returns at
most 10 plans per page whatever `limit` asks for, so a ZIP with 15 plans costs two
requests. The documented public API lives at
https://marketplace.api.healthcare.gov/api/v1 and takes an `apikey`; the default
`base_url` in `FederalSettings` is the host the HealthCare.gov site itself calls.
"""

from __future__ import annotations

import logging
from typing import Any

from ..base import Location, Scenario, ScrapeError, Scraper
from .parser import iter_plans
from .settings import FederalSettings

log = logging.getLogger("scrapers.federal")

RELATIONSHIPS = {"SELF": "Self", "SPOUSE": "Spouse", "CHILD": "Child"}


class FederalScraper(Scraper):
    name = "federal"
    settings_cls = FederalSettings
    uses_gender = True
    settings: FederalSettings

    async def resolve(self, location: Location, coverage_year: int) -> list[Location]:
        """The ZIP's counties in its state, from the site's own `/counties/by/zip` lookup.

        A ZIP that straddles a county line comes back with each of its counties, the
        main one first. `resolve_local` (inherited) is the offline alternative.
        """
        url = f"{self.base_url}/counties/by/zip/{location.zip_code}"
        data = await self.http.request_json("GET", url, params={"year": coverage_year})
        if not isinstance(data, dict) or "counties" not in data:
            raise ScrapeError(f"unexpected counties/by/zip response for {location.zip_code}: {str(data)[:200]}")
        return [
            Location(location.state, location.zip_code, county["fips"], county_name=county.get("name", ""))
            for county in data["counties"] or []
            if county.get("state") == location.state
        ]

    def build_payload(self, scenario: Scenario, limit: int, offset: int) -> dict[str, Any]:
        people = []
        for member in scenario.members:
            people.append(
                {
                    "aptc_eligible": True,
                    "age": member.age,
                    "has_mec": False,
                    "is_pregnant": member.pregnant,
                    "is_parent": member.relationship in ("SELF", "SPOUSE") and scenario.has_children,
                    "uses_tobacco": member.tobacco_user,
                    "gender": member.gender,
                    "relationship": RELATIONSHIPS[member.relationship],
                }
            )
        payload: dict[str, Any] = {
            "household": {
                "income": scenario.household_income,
                "people": people,
                "has_married_couple": scenario.has_spouse,
                "unemployment_received": "None",
            },
            "market": "Individual",
            "place": {
                "countyfips": scenario.location.county_fips,
                "state": scenario.location.state,
                "zipcode": scenario.location.zip_code,
            },
            "year": scenario.coverage_year,
            "filter": {"division": "HealthCare", "metal_design_types": None},
            "limit": limit,
            "offset": offset,
            "order": "asc",
            "sort": "premium",
            "suppressed_plan_ids": [],
        }
        if self.settings.aptc_override is not None:
            payload["aptc_override"] = self.settings.aptc_override
        return payload

    async def fetch_plans(self, scenario: Scenario, run_id: str) -> list[dict[str, Any]]:
        url = f"{self.base_url}/plans/search"
        page_size = self.settings.page_size
        plans: list[dict[str, Any]] = []
        offset = 0
        total: int | None = None
        while True:
            body = self.build_payload(scenario, page_size, offset)
            data = await self.http.request_json("POST", url, params={"year": scenario.coverage_year}, json_body=body)
            self.log_raw(run_id, scenario, url, body, data)
            if not isinstance(data, dict) or "plans" not in data:
                raise ScrapeError(f"unexpected plans/search response for {scenario.label()}: {str(data)[:200]}")
            page = list(iter_plans(data, url))
            total = int(data.get("total") or 0)
            plans.extend(page)
            offset += len(page)
            if not page or offset >= total:
                break
        if total is not None and len(plans) != total:
            log.warning("%s: expected %d plans, got %d", scenario.label(), total, len(plans))
        return plans
