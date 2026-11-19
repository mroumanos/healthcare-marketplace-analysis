"""Defaults for the `federal` module; see `scrapers/settings.py` for how values are resolved."""

from __future__ import annotations

from ..settings import ModuleSettings, env_config


class FederalSettings(ModuleSettings):
    model_config = env_config("federal")

    # The host the HealthCare.gov site itself calls. The documented public API is
    # https://marketplace.api.healthcare.gov/api/v1 and takes an `apikey`.
    base_url: str = "https://marketplace-int.api.healthcare.gov/api/v1"
    page_size: int = 10  # the API returns at most 10 plans per page whatever is asked
    aptc_override: float | None = None  # sent as `aptc_override` when set
