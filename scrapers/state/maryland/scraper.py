"""Maryland Health Connection (app.marylandhealthconnection.gov).

One form-encoded POST to `/hixmobile/public/preelig_complete` per scenario returns
every plan, with premiums and cost sharing, for the household. This is the request
the site's own anonymous "browse plans" (pre-eligibility) page makes.

Form fields, from a browser capture on 2026-09-30 of a one-person household:

    members[0].zipCode=20602   members[0].county=Charles   members[0].age=30
    members[0].income=100000   members[0].incomeType=AN    members[0].coverageYear=2026
    members[0].pregnant=false  members[0].isOOPSkiped=true members[0].coverageType=H

`county` is the county name without the word "County" ("Charles"); the independent
city is presumably "Baltimore City". Only the single-member layout was captured, so
this module refuses larger households rather than guess the field names.

Access note (2026-09-30): the site's Akamai edge answered every request from this
client with an "Access Denied" page (HTTP 403), including a plain GET of the public
home page, while the same request from a browser succeeds. The client identifies
itself honestly and does not imitate a browser, in line with the project rules. If
the site keeps refusing, ask Maryland Health Connection about programmatic access
or use their published plan data instead.
"""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

from ...base import Scenario, ScrapeError, Scraper
from .parser import iter_plans
from .settings import MarylandSettings

log = logging.getLogger("scrapers.maryland")

ENDPOINT = "/hixmobile/public/preelig_complete"
HEADERS = {"ClientSource": "web", "Accept": "application/json, text/plain, */*"}


def county_form_value(county_name: str) -> str:
    """ZIP-database county name -> the site's spelling: drop "County", title-case "City"."""
    name = county_name.strip()
    if name.lower().endswith(" county"):
        name = name[: -len(" county")]
    if name.lower().endswith(" city"):
        name = name[: -len(" city")] + " City"
    return name


class MarylandScraper(Scraper):
    name = "maryland"
    settings_cls = MarylandSettings
    max_household_size = 1  # only the single-member form layout was captured

    def build_form(self, scenario: Scenario) -> dict[str, str]:
        """Form body for preelig_complete, in the captured single-member layout."""
        if scenario.household_size != 1:
            raise ScrapeError(
                f"maryland: only one-person households are supported (got {scenario.household_size}); "
                "capture a multi-member request from the site to extend build_form"
            )
        member = scenario.applicant
        if not scenario.location.county_name:
            raise ScrapeError(f"maryland: {scenario.label()} has no county name; the form requires one")
        return {
            "members[0].zipCode": scenario.location.zip_code,
            "members[0].county": county_form_value(scenario.location.county_name),
            "members[0].age": str(member.age),
            "members[0].income": str(scenario.household_income),
            "members[0].incomeType": "AN",  # annual
            "members[0].coverageYear": str(scenario.coverage_year),
            "members[0].pregnant": "true" if member.pregnant else "false",
            "members[0].isOOPSkiped": "true",
            "members[0].coverageType": "H",  # health (not dental)
        }

    async def fetch_plans(self, scenario: Scenario, run_id: str) -> list[dict[str, Any]]:
        url = f"{self.base_url}{ENDPOINT}"
        form = self.build_form(scenario)
        try:
            data = await self.http.request_json("POST", url, form=form, headers=HEADERS)
        except aiohttp.ClientResponseError as err:
            if err.status == 403:
                raise ScrapeError(
                    f"maryland: HTTP 403 for {scenario.label()}; the site's edge (Akamai) is refusing this "
                    "client. See the module docstring; do not work around it."
                ) from err
            raise
        self.log_raw(run_id, scenario, url, form, data)
        if not isinstance(data, dict) or not isinstance(data.get("preelig"), dict):
            raise ScrapeError(f"maryland: unexpected response for {scenario.label()}: {str(data)[:200]}")
        return list(iter_plans(data, url))
