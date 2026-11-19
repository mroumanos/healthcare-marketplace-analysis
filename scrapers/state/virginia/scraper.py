"""Virginia's Insurance Marketplace (enroll.marketplace.virginia.gov).

Three requests per scenario, all on one cookie jar because the site keys the
later steps to the earlier ones:

1. POST /hix/calculateSavings                          -> encryptedLeadId
2. GET  /hix/pre-screener/populateShoppingId?leadId=.. -> shoppingId
3. POST /plandisplay-api/plandisplay/getIndividualPlans -> data: [plans]

Step 2 advances the server-side session (JSESSIONID) to the shopping stage, and a
new calculateSavings on that session is redirected to /hix/error/pageExpired with a
401. Each scenario therefore gets its own cookie jar (`HttpClient.fresh_session`).
"""

from __future__ import annotations

import logging
from typing import Any

from ...base import Member, Scenario, ScopedHttp, ScrapeError, Scraper
from .parser import iter_plans
from .settings import VirginiaSettings

log = logging.getLogger("scrapers.virginia")


class VirginiaScraper(Scraper):
    name = "virginia"
    settings_cls = VirginiaSettings

    @staticmethod
    def member_payload(member: Member) -> dict[str, Any]:
        return {
            "dateOfBirth": member.dob.strftime("%m/%d/%Y"),
            "isTobaccoUser": member.tobacco_user,
            "isSeekingCoverage": member.seeking_coverage,
            "isNativeAmerican": member.native_american,
            "relationshipToPrimary": member.relationship,
            "isPregnant": member.pregnant,
            "citizenshipStatus": member.citizenship_status,
        }

    def build_payload(self, scenario: Scenario) -> dict[str, Any]:
        """Body for calculateSavings (the shape observed on the site)."""
        return {
            "countyCode": scenario.location.county_fips,
            "zipCode": scenario.location.zip_code,
            "familySize": scenario.household_size,
            "coverageYear": scenario.coverage_year,
            "householdIncome": str(scenario.household_income),
            "stateCode": scenario.location.state,
            "members": [self.member_payload(m) for m in scenario.members],
            "callToApiRequired": True,
            "showIncomeSection": True,
        }

    async def fetch_plans(self, scenario: Scenario, run_id: str) -> list[dict[str, Any]]:
        # A used session answers 401 "page expired" (see module docstring), so each
        # scenario runs on its own cookie jar; that also lets scenarios run side by side.
        async with self.http.fresh_session() as http:
            return await self._fetch_plans(http, scenario, run_id)

    async def _fetch_plans(self, http: ScopedHttp, scenario: Scenario, run_id: str) -> list[dict[str, Any]]:
        savings_url = f"{self.base_url}/hix/calculateSavings"
        savings_body = self.build_payload(scenario)
        savings = await http.request_json("POST", savings_url, json_body=savings_body)
        self.log_raw(run_id, scenario, savings_url, savings_body, savings)
        lead_id = savings.get("encryptedLeadId") if isinstance(savings, dict) else None
        if not lead_id:
            raise ScrapeError(f"no encryptedLeadId for {scenario.label()}: {str(savings)[:200]}")

        shopping_url = f"{self.base_url}/hix/pre-screener/populateShoppingId"
        shopping_params = {"leadId": lead_id, "coverageYear": scenario.coverage_year, "updateStage": "true"}
        shopping = await http.request_json("GET", shopping_url, params=shopping_params)
        self.log_raw(run_id, scenario, shopping_url, shopping_params, shopping)
        shopping_id = shopping.get("shoppingId") if isinstance(shopping, dict) else None
        if not shopping_id:
            raise ScrapeError(f"no shoppingId for {scenario.label()}: {str(shopping)[:200]}")

        plans_url = f"{self.base_url}/plandisplay-api/plandisplay/getIndividualPlans"
        plans_body = {"insuranceType": "HEALTH", "language": "en", "shoppingId": shopping_id}
        result = await http.request_json("POST", plans_url, json_body=plans_body)
        self.log_raw(run_id, scenario, plans_url, plans_body, result)
        if not isinstance(result, dict) or not result.get("success"):
            raise ScrapeError(f"getIndividualPlans failed for {scenario.label()}: {str(result)[:200]}")
        return list(iter_plans(result, plans_url))
