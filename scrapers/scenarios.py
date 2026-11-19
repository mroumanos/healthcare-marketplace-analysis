"""Scenario generation: which ZIP codes, households and incomes to quote.

Site-independent. Each `Scenario` is handed to whichever scraper module the state
is configured to use.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import lru_cache
from itertools import product
from random import Random
from typing import TYPE_CHECKING, Iterator, Sequence

import addfips  # county name -> county FIPS code
import zipcodes  # ZIP code -> city / county / state

from .base import Location, Member, Scenario

if TYPE_CHECKING:
    from .config import Config

log = logging.getLogger("scrapers.scenarios")

# Federal poverty guidelines for the coverage year (48 contiguous states + DC).
# Verify against the current HHS guidelines before each coverage year; the
# config file can override both numbers under "fpl".
FPL_FIRST_PERSON = 15_960
FPL_EACH_ADDITIONAL = 5_680


def fpl(
    household_size: int,
    first_person: int = FPL_FIRST_PERSON,
    each_additional: int = FPL_EACH_ADDITIONAL,
) -> int:
    """Federal poverty level for a household of `household_size` people."""
    return first_person + each_additional * (household_size - 1)


# --- people ----------------------------------------------------------------


def _years_before(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year - years)
    except ValueError:  # Feb 29 in a non-leap year
        return d.replace(year=d.year - years, day=28)


def random_dob(age: int, rng: Random, as_of: date) -> date:
    """A date of birth for someone who is exactly `age` years old on `as_of`."""
    latest = _years_before(as_of, age)  # turns `age` on as_of
    earliest = _years_before(as_of, age + 1) + timedelta(days=1)  # turns `age + 1` tomorrow
    return earliest + timedelta(days=rng.randint(0, (latest - earliest).days))


GENDERS = ("Male", "Female")


def gen_household(
    age: int,
    num_dependents: int = 0,
    spouse: bool = False,
    *,
    coverage_year: int,
    rng: Random | None = None,
    gender: str = "Male",
) -> tuple[Member, ...]:
    """Primary applicant of `age` and `gender`, an optional spouse, and `num_dependents` children.

    The spouse is the other gender; dependents alternate Male, Female, Male, ...

    Without `rng` (the default) everyone is born on Jan 1, so each person is their
    nominal age for the whole coverage year no matter which effective date a site
    rates at; the spouse is the same age as the applicant and dependents are 10.
    With `rng`, birthdays are random within the age, the spouse is within 5 years,
    and dependents are 0-17.
    """
    as_of = date(coverage_year, 1, 1)

    def member(relationship: str, years: int, member_gender: str) -> Member:
        dob = random_dob(years, rng, as_of) if rng else date(coverage_year - years, 1, 1)
        return Member(relationship, years, dob, gender=member_gender)

    members = [member("SELF", age, gender)]
    if spouse:
        other = GENDERS[1 - GENDERS.index(gender)]
        members.append(member("SPOUSE", max(18, age + rng.randint(-5, 5)) if rng else age, other))
    for i in range(num_dependents):
        members.append(member("CHILD", rng.randint(0, 17) if rng else 10, GENDERS[i % 2]))
    return tuple(members)


# --- places ----------------------------------------------------------------


@lru_cache(maxsize=None)
def _fips_table() -> addfips.AddFIPS:
    return addfips.AddFIPS()


def _local_location(state: str, z: dict) -> Location:
    """Location for one `zipcodes` record; `county_fips` is "" when the county name has no FIPS code here."""
    county = z["county"]
    code = _fips_table().get_county_fips(county, state=state)
    if code is None and county.startswith("Municipality of "):  # "Municipality of Anchorage"
        code = _fips_table().get_county_fips(county.removeprefix("Municipality of ") + " Municipality", state=state)
    return Location(state, z["zip_code"], code or "", county_name=county)


def resolve_local(state: str, zip_code: str) -> Location | None:
    """ZIP -> county from the local ZIP database (`zipcodes`) and FIPS table (`addfips`), no request.

    The offline alternative to a site's own lookup (`FederalScraper.resolve`). It knows
    one county per ZIP, and returns None when the ZIP is unknown or its county name
    does not match a county of `state` (border ZIPs, new or oddly spelled counties).
    """
    for z in zipcodes.matching(zip_code):
        location = _local_location(state, z)
        if location.county_fips:
            return location
    return None


def gen_locations(state: str) -> Iterator[Location]:
    """Every active residential ZIP code in `state`, with its locally known county
    (`county_fips` is "" where the local tables cannot place it). These are the ZIPs
    a scraper's `resolve` turns into counties.
    """
    for z in zipcodes.filter_by(state=state, active=True, zip_code_type="STANDARD"):
        yield _local_location(state, z)


# --- scenarios -------------------------------------------------------------

ALL_ZIPS = "*"


@dataclass
class ScenarioGenerator:
    """Yields one `Scenario` per county × age × dependents × spouse × gender × income.

    Mirrors the `scenario` block of the config. `zips()` lists the ZIPs to cover; the
    run notebook resolves them to counties and stores those in `counties`, because
    quotes depend on the county, not the ZIP. Households are built once and reused
    across every county and income level.
    """

    state: str
    coverage_year: int
    ages: Sequence[int] = (30,)  # of the primary applicant
    num_dependents: Sequence[int] = (0,)
    spouse: Sequence[bool] = (False,)
    genders: Sequence[str] = ("Male",)  # of the primary applicant
    fpl_multipliers: Sequence[float] = (10.0,)
    zip_codes: Sequence[str] | str = ALL_ZIPS  # "*" = every residential ZIP in the state, one ZIP, or a list
    dob_mode: str = "fixed"  # "fixed" (Jan 1 birthdays) or "random" (seeded)
    seed: int = 0  # only used by dob_mode "random"
    max_household_size: int | None = None  # drop larger households (a module's limit)
    counties: list[Location] = field(default_factory=list)  # one Location per county to quote (any one of its ZIPs)
    fpl_first_person: int = FPL_FIRST_PERSON
    fpl_each_additional: int = FPL_EACH_ADDITIONAL
    _households: list[tuple[Member, ...]] | None = field(default=None, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.dob_mode not in ("fixed", "random"):
            raise ValueError(f"dob_mode must be 'fixed' or 'random', not {self.dob_mode!r}")
        unknown = [g for g in self.genders if g not in GENDERS]
        if unknown or not self.genders:
            raise ValueError(f"genders must be a non-empty list drawn from {GENDERS}, not {list(self.genders)!r}")

    @classmethod
    def from_config(cls, state: str, config: "Config") -> "ScenarioGenerator":
        """The config's `scenario` for one state, trimmed to what its module can use.

        A module that never sends gender gets only the first configured gender, since the
        others would repeat identical requests; a module with a household-size limit gets
        only the households within it.
        """
        from . import MODULES  # local import: the package imports this module

        module = MODULES[config.states[state].module]
        spec = config.scenario.model_dump()
        if not module.uses_gender and len(spec["genders"]) > 1:
            log.info(
                "%s: module %r does not send gender; quoting %s only instead of %s",
                state, module.name, spec["genders"][0], spec["genders"],
            )
            spec["genders"] = spec["genders"][:1]
        return cls(
            state=state,
            coverage_year=config.coverage_year,
            max_household_size=module.max_household_size,
            fpl_first_person=config.fpl.first_person,
            fpl_each_additional=config.fpl.each_additional,
            **spec,
        )

    def households(self) -> list[tuple[Member, ...]]:
        """Every age × dependents × spouse × gender household, built once per generator."""
        if self._households is None:
            rng = Random(self.seed) if self.dob_mode == "random" else None
            grid = [
                gen_household(age, dependents, has_spouse, coverage_year=self.coverage_year, rng=rng, gender=gender)
                for age, dependents, has_spouse, gender in product(
                    dict.fromkeys(self.ages), self.num_dependents, self.spouse, self.genders
                )
            ]
            if self.max_household_size is not None:
                kept = [members for members in grid if len(members) <= self.max_household_size]
                if len(kept) < len(grid):
                    log.warning(
                        "%s: dropped %d of %d households larger than %d (module limit)",
                        self.state, len(grid) - len(kept), len(grid), self.max_household_size,
                    )
                grid = kept
            self._households = grid
        return self._households

    def zips(self) -> list[Location]:
        """The ZIPs to cover: every residential ZIP of the state, or the configured ones among them."""
        zips = list(gen_locations(self.state))
        if self.zip_codes != ALL_ZIPS:
            wanted = {self.zip_codes} if isinstance(self.zip_codes, str) else set(self.zip_codes)
            zips = [z for z in zips if z.zip_code in wanted]
            if not zips:
                log.warning("%s: none of the %d configured zip_codes is a residential ZIP here", self.state, len(wanted))
        return zips

    def incomes(self, household_size: int) -> list[int]:
        level = fpl(household_size, self.fpl_first_person, self.fpl_each_additional)
        return [round(level * multiplier) for multiplier in self.fpl_multipliers]

    def count(self) -> int:
        return len(self.counties) * len(self.households()) * len(self.fpl_multipliers)

    def scenarios(self, location: Location) -> Iterator[Scenario]:
        """Every household × income for one location."""
        for members in self.households():
            for income in self.incomes(len(members)):
                yield Scenario(location, members, income, self.coverage_year)

    def __iter__(self) -> Iterator[Scenario]:
        for county in self.counties:
            yield from self.scenarios(county)
