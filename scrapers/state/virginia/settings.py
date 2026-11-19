"""Defaults for the `virginia` module; see `scrapers/settings.py` for how values are resolved."""

from __future__ import annotations

from ...settings import ModuleSettings, env_config


class VirginiaSettings(ModuleSettings):
    model_config = env_config("virginia")

    base_url: str = "https://enroll.marketplace.virginia.gov"
