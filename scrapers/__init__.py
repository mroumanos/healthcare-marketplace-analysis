"""Marketplace scraper modules and the registry that maps config names to them.

Each site is a package: `federal/` for the HealthCare.gov platform, `state/<name>/` for
a state-operated platform, each holding `scraper.py` (a `Scraper` subclass with a unique
`name`) and `settings.py` (its defaults). Add a site by writing such a package and
listing its scraper in `MODULES` and its parser in `PARSERS`; the config then points states at it by that name.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Iterator

from pydantic import ValidationError

from .base import (
    HttpClient,
    HttpConfig,
    Location,
    Member,
    RunWriter,
    Scenario,
    ScrapeError,
    Scraper,
    new_run_id,
    utc_now,
)
from .config import Config, FplConfig, ScenarioConfig, StateConfig
from .federal import FederalScraper, FederalSettings
from .federal import parser as federal_parser
from .scenarios import ScenarioGenerator, fpl, gen_household, gen_locations
from .settings import ModuleSettings
from .state.maryland import MarylandScraper, MarylandSettings
from .state.maryland import parser as maryland_parser
from .state.virginia import VirginiaScraper, VirginiaSettings
from .state.virginia import parser as virginia_parser

MODULES: dict[str, type[Scraper]] = {
    FederalScraper.name: FederalScraper,
    MarylandScraper.name: MarylandScraper,
    VirginiaScraper.name: VirginiaScraper,
}

# Module name -> the function that iterates the plan objects in one of its responses.
PARSERS: dict[str, Callable[..., Iterator[dict[str, Any]]]] = {
    FederalScraper.name: federal_parser.iter_plans,
    MarylandScraper.name: maryland_parser.iter_plans,
    VirginiaScraper.name: virginia_parser.iter_plans,
}


def iter_raw(path: Path | str) -> Iterator[dict[str, Any]]:
    """Each logged response in a run's raw JSONL, as a dict:
    `{run_id, retrieved_at_utc, source, url, scenario, request, response}`.
    """
    with Path(path).open() as raw:
        for line in raw:
            yield json.loads(line)


def iter_plans(entry: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Each plan object in one raw entry, using the parser of the site it came from."""
    return PARSERS[entry["source"]](entry["response"], entry["url"])


def module_settings(config: Config, state: str) -> ModuleSettings:
    """Settings for `state`'s module: its overrides, then environment, then class defaults."""
    spec = config.states[state]
    return MODULES[spec.module].settings_cls(**spec.overrides)


def load_config(path: Path | str, states: dict[str, dict[str, Any]] | None = None) -> Config:
    """Read and validate the JSON config, including each state's module and overrides.

    `states`, when given, replaces the file's `states`: `{"AK": {"module": "federal"}, ...}`,
    each optionally with `"overrides"` and a run id `"suffix"`.
    """
    data = json.loads(Path(path).read_text())
    if states is not None:
        data["states"] = states
    config = Config.model_validate(data)
    for state, spec in config.states.items():
        if spec.module not in MODULES:
            raise ValueError(f"{path}: state {state} uses unknown module {spec.module!r}; known: {sorted(MODULES)}")
        try:
            module_settings(config, state)
        except ValidationError as err:
            raise ValueError(f"{path}: state {state} has invalid overrides for module {spec.module!r}: {err}") from err
    return config


def state_run_id(config: Config, state: str) -> str:
    """A new run id for one state's run: `<UTC timestamp>_<suffix>`, the suffix defaulting to the state code."""
    return new_run_id(config.states[state].suffix or state)


def make_scraper(state: str, http: HttpClient, config: Config, **kwargs: Any) -> Scraper:
    """Instantiate the scraper `state` is configured to use, with that state's settings."""
    return MODULES[config.states[state].module](http, module_settings(config, state), **kwargs)


def config_snapshot(config: Config, state: str | None = None) -> dict[str, Any]:
    """The config as JSON-ready data plus the module settings each state resolved to.

    Module defaults live in code and the environment, so the file alone does not say
    what a run used; `RunWriter` saves this next to the run's raw responses. With
    `state`, only that state is included (a run of one state).
    """
    snapshot = config.model_dump(mode="json")
    if state is not None:
        snapshot["states"] = {state: snapshot["states"][state]}
    snapshot["resolved_module_settings"] = {
        name: module_settings(config, name).model_dump(mode="json") for name in snapshot["states"]
    }
    return snapshot


__all__ = [
    "MODULES",
    "PARSERS",
    "Config",
    "FederalScraper",
    "FederalSettings",
    "FplConfig",
    "HttpClient",
    "HttpConfig",
    "Location",
    "MarylandScraper",
    "MarylandSettings",
    "Member",
    "ModuleSettings",
    "RunWriter",
    "Scenario",
    "ScenarioGenerator",
    "ScenarioConfig",
    "ScrapeError",
    "Scraper",
    "StateConfig",
    "VirginiaScraper",
    "VirginiaSettings",
    "config_snapshot",
    "fpl",
    "gen_household",
    "gen_locations",
    "iter_plans",
    "iter_raw",
    "load_config",
    "make_scraper",
    "module_settings",
    "new_run_id",
    "state_run_id",
    "utc_now",
]
