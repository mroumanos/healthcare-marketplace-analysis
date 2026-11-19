"""Shared types and HTTP plumbing for the Marketplace scrapers.

A site module turns a `Scenario` (where, who, how much income, which year) into that
site's own request(s). Every response is logged as received, with the scenario and
request that produced it, to the run's raw JSONL. Turning those responses into table
rows is not done here: see `compile_csvs.ipynb`.
"""

from __future__ import annotations

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Callable

import aiohttp

from .settings import ModuleSettings

log = logging.getLogger("scrapers")

RETRY_STATUSES = {429, 500, 502, 503, 504}


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_run_id(suffix: str = "") -> str:
    """UTC timestamp, plus `_<suffix>` when one is given (e.g. `20261117T150000Z_ak-full`)."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}_{suffix}" if suffix else stamp


class ScrapeError(RuntimeError):
    """A site answered, but not with what the parser expected."""


# --- scenario inputs -------------------------------------------------------


@dataclass(frozen=True)
class Member:
    """One person on the application."""

    relationship: str  # SELF | SPOUSE | CHILD
    age: int  # as of the coverage start date
    dob: date
    gender: str = "Male"  # required by some sites; ACA premiums do not vary by gender
    seeking_coverage: bool = True
    tobacco_user: bool = False
    native_american: bool = False
    pregnant: bool = False
    citizenship_status: str = "US_CITIZEN"


@dataclass(frozen=True)
class Location:
    state: str
    zip_code: str
    county_fips: str
    county_name: str = ""  # as the ZIP database spells it, e.g. "Charles County", "Baltimore city"


@dataclass(frozen=True)
class Scenario:
    """One quote request, independent of which site will answer it."""

    location: Location
    members: tuple[Member, ...]
    household_income: int
    coverage_year: int

    @property
    def state(self) -> str:
        return self.location.state

    @property
    def household_size(self) -> int:
        return len(self.members)

    @property
    def applicant(self) -> Member:
        return self.members[0]

    @property
    def has_spouse(self) -> bool:
        return any(m.relationship == "SPOUSE" for m in self.members)

    @property
    def num_dependents(self) -> int:
        return sum(m.relationship == "CHILD" for m in self.members)

    @property
    def has_children(self) -> bool:
        return self.num_dependents > 0

    def label(self) -> str:
        return (
            f"{self.state} {self.location.zip_code} age={self.applicant.age} {self.applicant.gender}"
            f" spouse={'yes' if self.has_spouse else 'no'} dependents={self.num_dependents}"
            f" income={self.household_income}"
        )


# --- HTTP ------------------------------------------------------------------


@dataclass
class HttpConfig:
    user_agent: str
    timeout_seconds: float = 30.0
    delay_seconds: float = 1.0  # pause before each request, per concurrency slot
    max_retries: int = 3
    backoff_seconds: float = 2.0  # doubled on each retry
    max_concurrency: int = 1  # requests in flight at once, per client (one client per module/host)

    def __post_init__(self) -> None:
        if self.max_concurrency < 1:
            raise ValueError(f"max_concurrency must be at least 1, not {self.max_concurrency}")


class _Retry(Exception):
    pass


class HttpClient:
    """Polite JSON client: descriptive User-Agent, timeout, exponential backoff on
    429/5xx and connection errors, and at most `max_concurrency` requests in flight.
    Every request first waits `delay_seconds` in its slot, so the request rate is at
    most `max_concurrency / delay_seconds` per second. Use one client per host so
    hosts are throttled separately.
    """

    def __init__(self, config: HttpConfig):
        self.config = config
        self.request_count = 0
        self.peak_in_flight = 0  # most requests ever in flight at once (to check the limit holds)
        self._in_flight = 0
        self._connector: aiohttp.TCPConnector | None = None
        self._session: aiohttp.ClientSession | None = None
        self._slots = asyncio.Semaphore(config.max_concurrency)

    def _new_session(self) -> aiohttp.ClientSession:
        """A session with its own cookie jar on the client's shared connection pool."""
        return aiohttp.ClientSession(
            connector=self._connector,
            connector_owner=False,
            timeout=aiohttp.ClientTimeout(total=self.config.timeout_seconds),
            headers={"User-Agent": self.config.user_agent, "Accept": "application/json"},
        )

    async def __aenter__(self) -> "HttpClient":
        self._connector = aiohttp.TCPConnector()
        self._session = self._new_session()
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None
        if self._connector is not None:
            await self._connector.close()
            self._connector = None

    @asynccontextmanager
    async def fresh_session(self) -> AsyncIterator["ScopedHttp"]:
        """A handle whose requests share one new, empty cookie jar.

        For sites that key a multi-step flow to a session cookie: each flow gets its
        own server-side session, so flows can run side by side. Requests still count
        against this client's concurrency limit, delay and retries.
        """
        if self._connector is None:
            raise RuntimeError("use `async with HttpClient(...) as http:`")
        session = self._new_session()
        try:
            yield ScopedHttp(self, session)
        finally:
            await session.close()

    async def request_json(self, method: str, url: str, **kwargs: Any) -> Any:
        """Send a request on the client's default session and decode its JSON body.

        Keyword arguments: `params` (query string), `json_body` (sent as JSON), `form`
        (sent as application/x-www-form-urlencoded) and `headers` (added to the
        session defaults for this request only).
        """
        if self._session is None:
            raise RuntimeError("use `async with HttpClient(...) as http:`")
        return await self._request(self._session, method, url, **kwargs)

    async def _request(
        self,
        session: aiohttp.ClientSession,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        form: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        query = {k: str(v) for k, v in (params or {}).items()}
        async with self._slots, self._count_in_flight():
            for attempt in range(self.config.max_retries + 1):
                if self.request_count:
                    await asyncio.sleep(self.config.delay_seconds)
                self.request_count += 1
                try:
                    async with session.request(
                        method, url, params=query, json=json_body, data=form, headers=headers
                    ) as res:
                        if res.status in RETRY_STATUSES:
                            raise _Retry(f"HTTP {res.status}")
                        res.raise_for_status()
                        return await res.json(content_type=None)
                except (_Retry, aiohttp.ClientConnectionError, TimeoutError) as err:
                    if attempt >= self.config.max_retries:
                        raise ScrapeError(
                            f"{method} {url} failed after {attempt + 1} attempts: {err}"
                        ) from err
                    wait = self.config.backoff_seconds * 2**attempt
                    log.warning("%s %s: %s; retrying in %.0fs", method, url, err, wait)
                    await asyncio.sleep(wait)
        raise AssertionError("unreachable")

    @asynccontextmanager
    async def _count_in_flight(self) -> AsyncIterator[None]:
        self._in_flight += 1
        self.peak_in_flight = max(self.peak_in_flight, self._in_flight)
        try:
            yield
        finally:
            self._in_flight -= 1


class ScopedHttp:
    """`HttpClient.request_json` bound to one cookie jar; see `HttpClient.fresh_session`."""

    def __init__(self, client: HttpClient, session: aiohttp.ClientSession):
        self._client = client
        self._session = session

    async def request_json(self, method: str, url: str, **kwargs: Any) -> Any:
        return await self._client._request(self._session, method, url, **kwargs)


# --- scraper contract ------------------------------------------------------


class Scraper(ABC):
    """One Marketplace site. Subclasses set `name` and `settings_cls` and implement `fetch_plans`."""

    name: str = ""
    settings_cls: type[ModuleSettings] = ModuleSettings
    uses_gender: bool = False  # True when the site's request carries the applicant's gender
    max_household_size: int | None = None  # None = any size the scenario iterator produces

    def __init__(
        self,
        http: HttpClient,
        settings: ModuleSettings | dict[str, Any] | None = None,
        raw_sink: Callable[[dict[str, Any]], None] | None = None,
    ):
        """`settings` is a ready `settings_cls` instance, or overrides to build one from."""
        self.http = http
        self.settings = settings if isinstance(settings, ModuleSettings) else self.settings_cls(**(settings or {}))
        self.raw_sink = raw_sink

    @property
    def base_url(self) -> str:
        return self.settings.base_url

    # --- ZIP -> county: two interchangeable methods ---

    def resolve_local(self, location: Location) -> list[Location]:
        """The ZIP's county from the local ZIP database and FIPS table (no request): one or none."""
        from .scenarios import resolve_local  # local import: scenarios imports this module

        found = resolve_local(location.state, location.zip_code)
        return [found] if found else []

    async def resolve(self, location: Location, coverage_year: int) -> list[Location]:
        """The ZIP's counties in its state, one `Location` each. Sites with their own
        lookup override this; the default is `resolve_local`.
        """
        return self.resolve_local(location)

    @abstractmethod
    async def fetch_plans(self, scenario: Scenario, run_id: str) -> list[dict[str, Any]]:
        """Request every plan the site offers for `scenario`, logging each response.

        Returns the site's own plan objects, unchanged (the caller only counts them).
        """

    def log_raw(self, run_id: str, scenario: Scenario, url: str, request: Any, response: Any) -> None:
        if self.raw_sink is None:
            return
        self.raw_sink(
            {
                "run_id": run_id,
                "retrieved_at_utc": utc_now(),
                "source": self.name,
                "url": url,
                "scenario": asdict(scenario),
                "request": request,
                "response": response,
            }
        )

# --- output ----------------------------------------------------------------


class RunWriter:
    """One run's output: `<run_id>.raw.jsonl`, one line per response, plus
    `<run_id>.config.json`, the config the run used. Lines are flushed as they arrive
    so an interrupted run keeps what it fetched. Runs never overwrite each other.
    """

    def __init__(self, output_dir: Path | str, run_id: str, *, config: dict[str, Any] | None = None):
        self.output_dir = Path(output_dir)
        self.run_id = run_id
        self.raw_path = self.output_dir / f"{run_id}.raw.jsonl"
        self.config_path = self.output_dir / f"{run_id}.config.json"
        self.response_count = 0
        self._config = config
        self._raw_file = None

    def __enter__(self) -> "RunWriter":
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._raw_file = self.raw_path.open("x")  # "x": never replace an earlier run
        if self._config is not None:
            self.config_path.write_text(json.dumps(self._config, indent=2))
        return self

    def __exit__(self, *exc: object) -> None:
        if self._raw_file is not None:
            self._raw_file.close()

    def write_raw(self, entry: dict[str, Any]) -> None:
        self._raw_file.write(json.dumps(entry, default=str) + "\n")
        self._raw_file.flush()
        self.response_count += 1
