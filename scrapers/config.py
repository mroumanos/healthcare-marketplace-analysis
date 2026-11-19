"""The run config (`marketplaces.json`) as validated models.

The file says what to run: the coverage year, HTTP politeness and the scenario grid.
Which states to run, and with which module, comes from the file's `states` or from the
`states` passed to `load_config` (the run notebook does the latter). How a module talks to its site (endpoints, page size)
lives in `settings.py`; a state changes those only through its `"overrides"`.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .base import HttpConfig
from .scenarios import ALL_ZIPS, FPL_EACH_ADDITIONAL, FPL_FIRST_PERSON

_ZIP = re.compile(r"^\d{5}$")


class EnforcedBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")  # a misspelled or retired key is an error


class FplConfig(EnforcedBaseModel):
    """Federal poverty guideline for the coverage year: first person, each additional."""

    first_person: int = FPL_FIRST_PERSON
    each_additional: int = FPL_EACH_ADDITIONAL


class ScenarioConfig(EnforcedBaseModel):
    """The grid of quotes to request: every combination of the lists below, per ZIP."""

    zip_codes: list[str] | str = ALL_ZIPS  # "*" = every residential ZIP of the state; or one ZIP; or a list
    ages: list[int] = [30]  # of the primary applicant
    num_dependents: list[int] = [0]
    spouse: list[bool] = [False]
    genders: list[Literal["Male", "Female"]] = Field(default=["Male"], min_length=1)
    fpl_multipliers: list[float] = [10.0]  # household income as a multiple of the poverty level
    dob_mode: Literal["fixed", "random"] = "fixed"
    seed: int = 0  # only used by dob_mode "random"

    @field_validator("zip_codes")
    @classmethod
    def _check_zips(cls, value: list[str] | str) -> list[str] | str:
        if value == ALL_ZIPS:
            return value
        bad = [z for z in ([value] if isinstance(value, str) else value) if not _ZIP.match(z)]
        if bad:
            raise ValueError(f'expected "*", a 5-digit ZIP, or a list of 5-digit ZIPs; got {bad}')
        return value

    @field_validator("ages")
    @classmethod
    def _check_ages(cls, value: list[int]) -> list[int]:
        if any(not 0 <= age <= 120 for age in value):
            raise ValueError("ages must be between 0 and 120")
        return value

    @field_validator("num_dependents")
    @classmethod
    def _check_dependents(cls, value: list[int]) -> list[int]:
        if any(n < 0 for n in value):
            raise ValueError("num_dependents cannot be negative")
        return value


class StateConfig(EnforcedBaseModel):
    module: str  # a key of scrapers.MODULES
    overrides: dict[str, Any] = {}  # fields of that module's settings class, for this state only
    suffix: str = Field(default="", pattern=r"^[A-Za-z0-9_-]*$")  # run id is <timestamp>_<suffix>; default: the state code


class Config(EnforcedBaseModel):
    coverage_year: int
    output_dir: Path = Path("data/runs")
    http: HttpConfig
    fpl: FplConfig = FplConfig()
    scenario: ScenarioConfig = ScenarioConfig()
    states: dict[str, StateConfig] = {}  # usually given in run_scrapers.ipynb instead (load_config(states=...))
