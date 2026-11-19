"""Defaults for the `maryland` module; see `scrapers/settings.py` for how values are resolved."""

from __future__ import annotations

from ...settings import ModuleSettings, env_config


class MarylandSettings(ModuleSettings):
    model_config = env_config("maryland")

    base_url: str = "https://app.marylandhealthconnection.gov"
