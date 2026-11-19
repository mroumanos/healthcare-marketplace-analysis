"""Per-module settings: the defaults every state on a module shares.

Each scraper module has one settings class, in its own package (`federal/settings.py`,
`state/virginia/settings.py`, ...), built on `ModuleSettings` below. A value is
resolved in this order, first match wins:

1. the state's `"overrides"` in `marketplaces.json`
   (`"AK": {"module": "federal", "overrides": {"page_size": 5}}`)
2. an environment variable, `MARKETPLACE_<MODULE>_<FIELD>`
   (`MARKETPLACE_FEDERAL_BASE_URL=...`), from the process or a `.env` file in the
   working directory
3. the default written on the class

Unknown fields are rejected, so a misspelled override fails when the config loads
rather than being ignored.
"""

from __future__ import annotations

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def env_config(module: str) -> SettingsConfigDict:
    """`model_config` for a module's settings class: env prefix `MARKETPLACE_<MODULE>_`."""
    return SettingsConfigDict(
        env_prefix=f"MARKETPLACE_{module.upper()}_",
        env_file=".env",
        extra="forbid",
        frozen=True,
    )


class ModuleSettings(BaseSettings):
    """Base for a module's settings. Every module talks to one `base_url`."""

    base_url: str

    @field_validator("base_url")
    @classmethod
    def _no_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")
