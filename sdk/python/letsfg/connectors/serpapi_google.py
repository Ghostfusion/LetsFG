"""SerpApi Google Flights provider adapter.

Design: ``docs/serpapi-provider-design.md`` §5 (operation ceilings), §6
(provenance), §7 (freshness), §7.1 (coverage), §8/§8.1 (``price_status`` and
verification), §9 (``ProviderPriceContext``), §10 (the ledger), §11 (failure
contract), §12.1–§12.3 (the traps), §13 (this interface), §14 (credentials).
Plan: ``docs/serpapi-provider-implementation.md`` P2.1/P2.2/P2.3/P2.4.

What this module refuses to do, by design:

* **No booking, no holding, no ranking, no alerting** (§13). Offers come back in
  provider order carrying their own status; nothing here decides which is better.
* **No provider arithmetic.** Segment and route durations are left at zero for the
  model layer to compute from the airport-local timestamps — §12.1's invariant, and
  exactly the defect the retired Google-flights connector shipped (its totals dropped
  every layover; see the validators in ``letsfg/models/flights.py``).
* **No parsing of ``extensions``**, which is unstructured prose (§8).
* **No credential fallback.** The key is read from ``SERPAPI_KEY`` and nowhere else;
  a Serper-shaped name is reported as a misfiling rather than silently accepted
  (§14, D18).
* **No URL anywhere.** The key is a query parameter, so the request URL never reaches
  a log, an exception or a fixture (§14, D8).
* **No inference from absence.** An empty payload is never read as an unserved route,
  and a shape that was never measured is never guessed at (below).

Standard-library HTTP only — SerpApi's own client package is never imported (D14).

**Measured shapes this module is written against** (fixtures in
``tests/fixtures/serpapi/``): ``google_flights`` search and pin, ``deals``,
``autocomplete``, and the account endpoint. Two shapes are deliberately *not* used:
``google_travel_explore`` produced no container at all for the parameters tried (its
populated shape is unmeasured), and the explore interaction was refused outright for
``interest=beach``, so no interest vocabulary is encoded here.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum
from hashlib import sha1
from typing import Any, Callable, Mapping, Optional, Sequence

from letsfg.connectors.provider_contract import (
    LedgerCounters,
    Observation,
    ProviderContractError,
    ProviderPriceContext,
    Provenance,
    VerificationResult,
    ledger_counters_for,
)
from letsfg.models.flights import FlightOffer, FlightRoute, FlightSegment

__all__ = [
    "ENV_VAR",
    "REJECTED_ENV_VARS",
    "PROVIDER_NAME",
    "SEARCH_ENDPOINT",
    "ACCOUNT_ENDPOINT",
    "ProviderCredentialError",
    "ProviderFailure",
    "TransportResult",
    "SerpApiTransport",
    "Stops",
    "DealsTravelDuration",
    "ExploreTravelDuration",
    "OneWayRequest",
    "RoundTripRequest",
    "SelectedFlightsRequest",
    "NextLegRequest",
    "TravelExploreRequest",
    "DealsRequest",
    "LocationCandidate",
    "DestinationCandidate",
    "SearchResult",
    "ProviderCapabilities",
    "SerpApiGoogleProvider",
    "resolve_api_key",
    "provider_configured",
    "classify_status",
    "classify_429",
    "normalise_flight_number",
    "segment_carrier",
    "segment_identity",
    "itinerary_identity",
    "route_segment_identity",
    "parse_local_time",
    "to_flight_offer",
    "price_context_from",
]


PROVIDER_NAME = "serpapi_google"
SEARCH_ENDPOINT = "https://serpapi.com/search"
ACCOUNT_ENDPOINT = "https://serpapi.com/account"

#: §14 — the vendor's documented name, and only this one.
ENV_VAR = "SERPAPI_KEY"
#: Names one letter away that are never consulted, in either direction.
REJECTED_ENV_VARS = ("SERPER_API_KEY", "SERPER_KEY")

USER_AGENT = "LetsFG-SDK/1.0.3 (+https://letsfg.co)"
_REDACTED = "[REDACTED]"
_HTTP_TIMEOUT = 90


# ── credentials ────────────────────────────────────────────────────────────

class ProviderCredentialError(RuntimeError):
    """The lane's credential is missing or misfiled. Never a bare 401 (§14)."""


def resolve_api_key(env: Optional[Mapping[str, str]] = None) -> str:
    """Return ``SERPAPI_KEY``, or explain precisely what is wrong.

    A Serper-shaped name holding a value is reported as a *misfiling* rather than
    silently accepted: the two vendors' names differ by one letter, one machine has
    already carried both, and a key that works locally under the wrong name fails
    wherever else this is deployed (§14, D18).
    """
    source = os.environ if env is None else env
    key = (source.get(ENV_VAR) or "").strip()
    if key:
        return key
    misfiled = [name for name in REJECTED_ENV_VARS if (source.get(name) or "").strip()]
    hint = (
        f" {', '.join(misfiled)} holds a value — SerpApi's key is often filed under "
        "that Serper.dev name, but this adapter will not read it (D18); move it to "
        f"{ENV_VAR}."
        if misfiled
        else ""
    )
    raise ProviderCredentialError(
        f"{ENV_VAR} is not set. This provider reads exactly {ENV_VAR} from the "
        f"environment and never falls back to a Serper-shaped name.{hint}"
    )


def provider_configured(env: Optional[Mapping[str, str]] = None) -> bool:
    source = os.environ if env is None else env
    return bool((source.get(ENV_VAR) or "").strip())


# ── failure contract (§11) ─────────────────────────────────────────────────

#: The §11 table as client categories. ``None`` means "no failure".
STATUS_CATEGORIES: dict[int, str] = {
    400: "validation",
    401: "auth_required",
    403: "business",
    404: "validation",
    410: "business",
    500: "transient",
    503: "transient",
}

#: §11's two 429 causes, distinguishable only by body wording.
QUOTA_429_MARKERS = ("run out of searches", "out of searches", "no searches left")
THROUGHPUT_429_MARKERS = (
    "per hour", "hourly", "hour limit", "too many requests", "rate limit", "exceeded your",
)


class ProviderFailure(RuntimeError):
    """A classified provider failure. Carries the category; never the URL."""

    def __init__(
        self, category: str, http_status: int, detail: str = "", *, retry_after: Optional[float] = None
    ) -> None:
        super().__init__(f"{PROVIDER_NAME} {http_status}: {category}{f' ({detail})' if detail else ''}")
        self.category = category
        self.http_status = http_status
        self.detail = detail
        self.retry_after = retry_after

    @property
    def retryable(self) -> bool:
        return self.category in {"transient", "rate_limited", "rate_limit_unknown"}

    @property
    def terminal(self) -> bool:
        """``budget_exhausted`` cannot be rescued by retrying, and neither can auth (§11)."""
        return self.category in {"budget_exhausted", "auth_required", "business"}


def _body_text(payload: Any) -> str:
    if isinstance(payload, Mapping):
        return str(payload.get("error") or payload.get("message") or "")
    return str(payload or "")


def _retry_after_seconds(headers: Mapping[str, str]) -> Optional[float]:
    """Honour ``Retry-After`` when present, never require it (§11)."""
    for name in ("Retry-After", "retry-after"):
        value = (headers or {}).get(name)
        if value:
            try:
                return float(int(str(value).strip()))
            except (TypeError, ValueError):
                return None
    return None


def classify_429(payload: Any, headers: Optional[Mapping[str, str]] = None) -> str:
    """Fail-safe three-way classification (§11, D4).

    Order matters: a body naming both causes is treated as terminal rather than
    retried, because retrying a quota exhaustion can never help.
    """
    body = _body_text(payload).lower()
    if any(marker in body for marker in QUOTA_429_MARKERS):
        return "budget_exhausted"
    if any(marker in body for marker in THROUGHPUT_429_MARKERS):
        return "rate_limited"
    return "rate_limit_unknown"


def classify_status(
    status: int, payload: Any = None, headers: Optional[Mapping[str, str]] = None
) -> Optional[str]:
    """Map an HTTP status to a client category, or ``None`` when there is none.

    ``200`` is success even when the payload carries an ``error`` string: an empty
    result does, on ``google_flights`` **and** on ``google_travel_explore`` (both
    measured 2026-10-03, §4.2), so ``error`` is not a failure signal and only the
    status decides.
    """
    if status == 200:
        return None
    if status == 429:
        return classify_429(payload, headers)
    if status in STATUS_CATEGORIES:
        return STATUS_CATEGORIES[status]
    return "transient" if status >= 500 else "business"


def failure_for(status: int, payload: Any = None, headers: Optional[Mapping[str, str]] = None) -> ProviderFailure:
    category = classify_status(status, payload, headers) or "transient"
    return ProviderFailure(
        category, status,
        detail=_body_text(payload)[:200],
        retry_after=_retry_after_seconds(headers or {}),
    )


# ── transport ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TransportResult:
    status: int
    payload: Any
    headers: Mapping[str, str] = field(default_factory=dict)


def _redact(value: Any, secret: str = "") -> Any:
    if isinstance(value, Mapping):
        return {k: (_REDACTED if k in {"api_key", "api_token"} else _redact(v, secret)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(v, secret) for v in value]
    if isinstance(value, str) and secret:
        return value.replace(secret, _REDACTED)
    return value


class SerpApiTransport:
    """The only place the key touches the wire, and the only place that must not leak it.

    ``opener`` is injectable so the offline tests drive the adapter from committed
    fixtures with no network. Whatever the opener is, the URL — which carries the
    key — is never logged, echoed into an exception, or stored.
    """

    def __init__(
        self, api_key: str, *, opener: Optional[Callable[[str], Any]] = None, timeout: int = _HTTP_TIMEOUT
    ) -> None:
        if not api_key:
            raise ProviderCredentialError(f"{ENV_VAR} is empty; refusing to send a request without it.")
        self._key = api_key
        self._opener = opener
        self._timeout = timeout

    def _get(self, endpoint: str, params: Mapping[str, Any]) -> TransportResult:
        query = {k: v for k, v in params.items() if v is not None}
        query["api_key"] = self._key
        url = f"{endpoint}?{urllib.parse.urlencode(query)}"
        if self._opener is not None:
            try:
                raw = self._opener(url)
            except (urllib.error.URLError, OSError) as error:
                # The injected path must behave exactly like the real one: a transport
                # failure surfaces as a classified failure with no URL in it (§14).
                reason = getattr(error, "reason", error)
                raise ProviderFailure("transient", 0, detail=type(reason).__name__) from None
            return TransportResult(
                getattr(raw, "status", 200),
                _redact(getattr(raw, "payload", raw), self._key),
                dict(getattr(raw, "headers", {}) or {}),
            )
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                body = self._scrub(response.read().decode(errors="replace"))
                return TransportResult(response.status, _redact(json.loads(body), self._key), dict(response.headers))
        except urllib.error.HTTPError as error:
            body = self._scrub(error.read().decode(errors="replace"))
            try:
                payload: Any = json.loads(body)
            except json.JSONDecodeError:
                payload = {"_unparsed_body": body[:4000]}
            return TransportResult(error.code, _redact(payload, self._key), dict(error.headers))
        except urllib.error.URLError as error:
            # Only the reason is surfaced: the URL must not be (§14).
            raise ProviderFailure("transient", 0, detail=type(error.reason).__name__) from None

    def _scrub(self, text: str) -> str:
        return text.replace(self._key, _REDACTED) if self._key else text

    def search(self, params: Mapping[str, Any]) -> TransportResult:
        return self._get(SEARCH_ENDPOINT, params)

    def account(self) -> Any:
        """The account endpoint. Free, and a drift alarm only (§10, D20)."""
        return self._get(ACCOUNT_ENDPOINT, {}).payload


# ── request models (§12.2/§12.3) ───────────────────────────────────────────
# One class per mode, so an illegal combination is unrepresentable rather than
# asserted: a `OneWayRequest` has no return-date field to fill in wrongly, and a
# `SelectedFlightsRequest` cannot carry a booking token as well.

class Stops(IntEnum):
    """§12.2 — the provider's ``stops`` is a ceiling, not a count."""
    ANY = 0
    NONSTOP = 1
    AT_MOST_ONE = 2
    AT_MOST_TWO = 3


class DealsTravelDuration(IntEnum):
    """``google_flights_deals`` numbering — deliberately separate from explore's."""
    ONE_WEEK = 1
    WEEKEND = 2
    TWO_WEEKS = 3


class ExploreTravelDuration(IntEnum):
    """``google_travel_explore`` numbering — the same integers, different meanings."""
    WEEKEND = 1
    ONE_WEEK = 2
    TWO_WEEKS = 3


@dataclass(frozen=True)
class _BaseRequest:
    departure_id: str
    currency: str = "EUR"
    gl: str = "us"
    hl: str = "en"
    adults: int = 1
    children: int = 0

    def common(self) -> dict[str, Any]:
        return {
            "departure_id": self.departure_id,
            "currency": self.currency,
            "gl": self.gl,
            "hl": self.hl,
            "adults": self.adults,
            "children": self.children or None,
        }


@dataclass(frozen=True)
class OneWayRequest(_BaseRequest):
    arrival_id: str = ""
    outbound_date: str = ""
    travel_class: Optional[int] = None
    stops: Optional[Stops] = None
    include_airlines: Optional[str] = None
    exclude_airlines: Optional[str] = None
    no_cache: bool = True
    deep_search: bool = False
    show_hidden: bool = False

    def to_params(self) -> dict[str, Any]:
        params = self.common()
        params.update({
            "engine": "google_flights",
            "arrival_id": self.arrival_id,
            "type": 2,
            "outbound_date": self.outbound_date,
            "travel_class": self.travel_class,
            "stops": int(self.stops) if self.stops is not None else None,
            "include_airlines": self.include_airlines,
            "exclude_airlines": self.exclude_airlines,
            "no_cache": "true" if self.no_cache else None,
            "deep_search": "true" if self.deep_search else None,
            "show_hidden": "true" if self.show_hidden else None,
        })
        return params


@dataclass(frozen=True)
class RoundTripRequest(_BaseRequest):
    arrival_id: str = ""
    outbound_date: str = ""
    return_date: str = ""  # required by the provider for type=1 (§12.2)
    travel_class: Optional[int] = None
    stops: Optional[Stops] = None
    include_airlines: Optional[str] = None
    exclude_airlines: Optional[str] = None
    no_cache: bool = True
    deep_search: bool = False

    def to_params(self) -> dict[str, Any]:
        params = self.common()
        params.update({
            "engine": "google_flights",
            "arrival_id": self.arrival_id,
            "type": 1,
            "outbound_date": self.outbound_date,
            "return_date": self.return_date,
            "travel_class": self.travel_class,
            "stops": int(self.stops) if self.stops is not None else None,
            "include_airlines": self.include_airlines,
            "exclude_airlines": self.exclude_airlines,
            "no_cache": "true" if self.no_cache else None,
            "deep_search": "true" if self.deep_search else None,
        })
        return params


@dataclass(frozen=True)
class SelectedFlightsRequest(_BaseRequest):
    """§12.2 — ``{"outbound": [...]}``, and a one-way pin must say so.

    Measured 2026-10-03: the pin rejects a bare array ("should be an object with
    ``outbound`` and optional ``return`` keys"), and under the default round-trip
    type it rejects a one-way pin ("is missing the ``return`` flights array").
    """
    arrival_id: str = ""
    outbound_date: str = ""
    outbound_segments: tuple[Mapping[str, str], ...] = ()
    return_segments: Optional[tuple[Mapping[str, str], ...]] = None

    def to_params(self) -> dict[str, Any]:
        params = self.common()
        payload: dict[str, Any] = {"outbound": [dict(segment) for segment in self.outbound_segments]}
        if self.return_segments:
            payload["return"] = [dict(segment) for segment in self.return_segments]
        params.update({
            "engine": "google_flights",
            "arrival_id": self.arrival_id,
            "outbound_date": self.outbound_date,
            "selected_flights_json": json.dumps(payload),
            "type": 1 if self.return_segments else 2,
        })
        return params


@dataclass(frozen=True)
class NextLegRequest(_BaseRequest):
    arrival_id: str = ""
    outbound_date: str = ""
    departure_token: str = ""

    def to_params(self) -> dict[str, Any]:
        params = self.common()
        params.update({
            "engine": "google_flights",
            "arrival_id": self.arrival_id,
            "outbound_date": self.outbound_date,
            "departure_token": self.departure_token,
        })
        return params


@dataclass(frozen=True)
class TravelExploreRequest:
    departure_id: str
    arrival_area_id: Optional[str] = None  # a kgmid, measured: must start with /m or /g
    month: Optional[str] = None
    interest: Optional[str] = None
    travel_mode: Optional[str] = None
    currency: str = "EUR"
    gl: str = "us"
    hl: str = "en"

    def to_params(self) -> dict[str, Any]:
        return {
            "engine": "google_travel_explore",
            "departure_id": self.departure_id,
            "arrival_area_id": self.arrival_area_id,
            "month": self.month,
            "interest": self.interest,
            "travel_mode": self.travel_mode,
            "currency": self.currency,
            "gl": self.gl,
            "hl": self.hl,
        }


@dataclass(frozen=True)
class DealsRequest:
    departure_id: str
    outbound_date: Optional[str] = None
    trip_length: Optional[int] = None
    travel_duration: Optional[DealsTravelDuration] = None
    currency: str = "EUR"
    gl: str = "us"
    hl: str = "en"

    def to_params(self) -> dict[str, Any]:
        return {
            "engine": "google_flights_deals",
            "departure_id": self.departure_id,
            "outbound_date": self.outbound_date,
            "trip_length": self.trip_length,
            "travel_duration": int(self.travel_duration) if self.travel_duration is not None else None,
            "currency": self.currency,
            "gl": self.gl,
            "hl": self.hl,
        }


# ── parsing ────────────────────────────────────────────────────────────────

_TIME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})(?::(\d{2}))?")

_CABIN_CLASSES = {
    "economy": "economy",
    "premium economy": "premium",
    "premium": "premium",
    "business": "business",
    "first": "first",
}


def parse_local_time(value: Any) -> Optional[datetime]:
    """Parse the provider's airport-local time, which is naive and may omit seconds.

    Measured shape: ``"2026-12-02 11:25"``. Deliberately *not* localised here — the
    model layer owns airport-local handling and timezone arithmetic (§12.1).
    """
    if not isinstance(value, str):
        return None
    match = _TIME_RE.match(value.strip())
    if not match:
        return None
    day, clock, seconds = match.group(1), match.group(2), match.group(3) or "00"
    return datetime.fromisoformat(f"{day}T{clock}:{seconds}")


def normalise_flight_number(value: Any) -> str:
    """§12.2 — ``"B6 1408"`` and ``"B61408"`` are the same flight."""
    return re.sub(r"\s+", "", str(value or "")).upper()


def segment_carrier(flight_number: Any) -> str:
    """The carrier code the flight number encodes, e.g. ``"AA43"`` → ``"AA"``."""
    match = re.match(r"^([A-Z0-9]{2})", normalise_flight_number(flight_number))
    return match.group(1) if match else ""


def segment_identity(segment: Mapping[str, Any]) -> str:
    """Identity from the fields the provider's two response shapes agree on."""
    departure = str((segment.get("departure_airport") or {}).get("time", ""))[:10]
    origin = str((segment.get("departure_airport") or {}).get("id", ""))
    destination = str((segment.get("arrival_airport") or {}).get("id", ""))
    return f"{normalise_flight_number(segment.get('flight_number'))}@{departure} {origin}-{destination}"


def route_segment_identity(segment: FlightSegment) -> str:
    """The same string as :func:`segment_identity`, for an already-mapped segment.

    The two must agree: a verification compares an expectation taken from one
    against an observation taken from the other, and a formatting difference would
    read as a substitution that never happened (§12.2).
    """
    return (
        f"{normalise_flight_number(segment.flight_no)}"
        f"@{segment.departure.date().isoformat()} {segment.origin}-{segment.destination}"
    )


def itinerary_identity(row: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(segment_identity(segment) for segment in row.get("flights") or [])


def route_identity(route: FlightRoute) -> tuple[str, ...]:
    return tuple(route_segment_identity(segment) for segment in route.segments)


def itinerary_rows(payload: Mapping[str, Any], request_kind: str) -> list[Mapping[str, Any]]:
    """§12.2 — a pinned request answers under ``selected_flights``, not the search keys.

    The parser branches on ``request_kind`` because the *shape* changes, not merely
    the content; reading the wrong key is how the pin was once mis-read as an empty
    result (design §20).
    """
    if request_kind == "verify":
        return list(payload.get("selected_flights") or [])
    return list(payload.get("best_flights") or []) + list(payload.get("other_flights") or [])


def cabin_class_for(travel_class: Any) -> str:
    return _CABIN_CLASSES.get(str(travel_class or "").strip().lower(), "economy")


def offer_id(identity: Sequence[str]) -> str:
    """An observation-local id derived from the itinerary, never from the request.

    Provider handles are request-scoped and are not identity (§6); the scanner's
    identity is itinerary-graded, so the id is a digest of the normalised segments.
    """
    return "spg_" + sha1("|".join(identity).encode()).hexdigest()[:16]


def to_flight_offer(row: Mapping[str, Any], *, currency: str) -> FlightOffer:
    """Map one provider itinerary to a ``FlightOffer``.

    Durations are left at zero on purpose: the segment and route validators compute
    them from the airport-local timestamps, and pre-filling them is what §12.1/D2
    forbids — the retired connector's totals silently dropped every layover.
    """
    segments: list[FlightSegment] = []
    for raw in row.get("flights") or []:
        flight_number = raw.get("flight_number")
        departure = parse_local_time((raw.get("departure_airport") or {}).get("time"))
        arrival = parse_local_time((raw.get("arrival_airport") or {}).get("time"))
        if departure is None or arrival is None:
            raise ProviderContractError(
                "segment without parsable airport-local times cannot be mapped: the model "
                "layer derives duration from them, and a segment without them would be "
                "silently duration-less (design §12.1)."
            )
        segments.append(FlightSegment(
            airline=segment_carrier(flight_number),
            airline_name=str(raw.get("airline") or ""),
            flight_no=normalise_flight_number(flight_number),
            origin=str((raw.get("departure_airport") or {}).get("id") or ""),
            destination=str((raw.get("arrival_airport") or {}).get("id") or ""),
            origin_city=str((raw.get("departure_airport") or {}).get("name") or ""),
            destination_city=str((raw.get("arrival_airport") or {}).get("name") or ""),
            departure=departure,
            arrival=arrival,
            cabin_class=cabin_class_for(raw.get("travel_class")),
            aircraft=str(raw.get("airplane") or ""),
        ))
    if not segments:
        raise ProviderContractError("an itinerary with no segments is not an offer.")

    route = FlightRoute(segments=segments, stopovers=max(len(segments) - 1, 0))
    carriers = [segment.airline for segment in segments if segment.airline]
    price = row.get("price")
    if price is None:
        raise ProviderContractError(
            "this itinerary carries no price. A pinned request never does (§8.1), so a "
            "pin is a verification candidate, not an observation."
        )
    return FlightOffer(
        id=offer_id(itinerary_identity(row)),
        price=float(price),
        currency=currency,
        outbound=route,
        airlines=list(dict.fromkeys(carriers)),
        owner_airline=carriers[0] if carriers else "",
        source=PROVIDER_NAME,
        source_tier="paid",
        conditions={},  # `extensions` is prose, never parsed into a decision (§8)
    )


def price_context_from(
    payload: Mapping[str, Any], *, search_id: str, coverage_mode: str, cache_mode: str
) -> Optional[ProviderPriceContext]:
    """§9 — the provider's own price context, kept as a claim.

    Returned as ``ProviderPriceContext``, which has no ordering and no arithmetic:
    ``lowest_price`` is the minimum of a returned set, not a market minimum.
    """
    insights = payload.get("price_insights")
    if not isinstance(insights, Mapping):
        return None
    history = tuple(
        (int(entry[0]), float(entry[1]))
        for entry in (insights.get("price_history") or [])
        if isinstance(entry, (list, tuple)) and len(entry) == 2
    )
    typical = insights.get("typical_price_range")
    return ProviderPriceContext(
        search_id=search_id,
        provider=PROVIDER_NAME,
        coverage_mode=coverage_mode,
        cache_mode=cache_mode,
        lowest_price=float(insights["lowest_price"]) if insights.get("lowest_price") is not None else None,
        price_level=str(insights["price_level"]) if insights.get("price_level") is not None else None,
        typical_price_range=(
            (float(typical[0]), float(typical[1]))
            if isinstance(typical, (list, tuple)) and len(typical) == 2
            else None
        ),
        price_history=history,
    )


# ── result and capability shapes ───────────────────────────────────────────

@dataclass(frozen=True)
class LocationCandidate:
    """A resolved place. ``kgmid`` is provider-local provenance, never identity (§6)."""
    iata: str
    name: str
    city: str = ""
    kgmid: str = ""
    provider_local_id: bool = True


@dataclass(frozen=True)
class DestinationCandidate:
    """A deal row: a destination and an **indicative** price, not an itinerary (§5).

    Nothing here is bookable and nothing here may alert; the price is the provider's
    "cheap destination" claim.
    """
    kgmid: str
    name: str
    country: str = ""
    arrival_airport_code: str = ""
    indicative_price: Optional[float] = None
    average_price: Optional[float] = None
    discount_percentage: Optional[float] = None
    outbound_date: str = ""
    return_date: str = ""
    stops: Optional[int] = None
    airline: str = ""


@dataclass(frozen=True)
class ProviderCapabilities:
    """Declared, never discovered by trying (fli study §6.3)."""
    name: str = PROVIDER_NAME
    engines: tuple[str, ...] = (
        "google_flights", "google_flights_deals", "google_travel_explore",
        "google_flights_autocomplete",
    )
    operations: tuple[str, ...] = ("resolve_location", "discover", "search", "verify", "next_leg")
    books: bool = False
    holds_fares: bool = False
    ranks: bool = False
    alerts: bool = False
    supplies_price_context: bool = True
    coverage_modes: tuple[str, ...] = ("standard", "deep", "hidden_included", "unknown")
    #: §13 — referral navigation is not modelled in V1, and this is how that is said.
    booking_options: bool = False


@dataclass(frozen=True)
class SearchResult:
    """One answer: the observation, the offers, and what arrived with them.

    ``raw_payload`` is the provider's own (already redacted) response. It is kept
    because a fixture-driven test has to be able to assert on what actually arrived,
    and because the parse branch on ``request_kind`` is only checkable against it.
    """

    observation: Observation
    offers: list[FlightOffer] = field(default_factory=list)
    price_context: Optional[ProviderPriceContext] = None
    destination_candidates: list[DestinationCandidate] = field(default_factory=list)
    ledger: LedgerCounters = field(default_factory=lambda: LedgerCounters(1, 1, 1))
    cache_hit_detected: bool = False
    error_field_present: bool = False
    location_candidates: list[LocationCandidate] = field(default_factory=list)
    raw_payload: Mapping[str, Any] = field(default_factory=dict)


# ── the adapter (§13) ──────────────────────────────────────────────────────

class SerpApiGoogleProvider:
    """SerpApi's Google Flights engines behind the frozen contract (§13).

    ``booking_options`` is deliberately absent: it is a referral/merchant domain the
    scanner does not otherwise use, and V1 excludes it (§5, §13).
    """

    NAME = PROVIDER_NAME
    capabilities = ProviderCapabilities()

    def __init__(
        self,
        *,
        env: Optional[Mapping[str, str]] = None,
        transport: Optional[SerpApiTransport] = None,
        opener: Optional[Callable[[str], Any]] = None,
    ) -> None:
        self._transport = transport or SerpApiTransport(resolve_api_key(env), opener=opener)
        self._seen_search_ids: set[str] = set()
        self._ledger = LedgerCounters(0, 0, 0)
        self._health = "HEALTHY"

    # ── state ─────────────────────────────────────────────────────────────

    @property
    def health(self) -> str:
        """``HEALTHY → DEGRADED → UNTRUSTED → DISABLED`` (fli study §6.5)."""
        return self._health

    @property
    def ledger(self) -> LedgerCounters:
        return self._ledger

    def disable(self) -> None:
        """A provider must be disableable without failing a run (fli study §6.6 #9)."""
        self._health = "DISABLED"

    def _record(self, event: str, *, cache_hit: bool = False) -> LedgerCounters:
        counted = ledger_counters_for(event, cache_hit=cache_hit)
        self._ledger = self._ledger + counted
        return counted

    def _degrade(self, category: str) -> None:
        """Provider **health**, which a parse failure may touch — coverage is separate (§6.4)."""
        if category in {"budget_exhausted", "auth_required"}:
            self._health = "UNTRUSTED"
        elif category in {"rate_limit_unknown", "transient"} and self._health == "HEALTHY":
            self._health = "DEGRADED"

    def _fail(self, result: TransportResult) -> ProviderFailure:
        failure = failure_for(result.status, result.payload, result.headers)
        self._record("failed")
        self._degrade(failure.category)
        return failure

    # ── the operations (§13) ──────────────────────────────────────────────

    def resolve_location(self, query: str, *, gl: str = "us", hl: str = "en") -> SearchResult:
        """`google_flights_autocomplete`. Returns candidates, not an observation.

        Measured shape (2026-10-03): ``suggestions[]``, each with a ``type``, a
        ``name`` and a kgmid ``id``, plus an ``airports[]`` array whose entries carry
        the IATA ``id``. A city suggestion therefore yields one candidate per airport,
        each carrying the city's kgmid as provider-local provenance (§6).
        """
        result = self._transport.search({
            "engine": "google_flights_autocomplete", "q": query, "gl": gl, "hl": hl,
        })
        if result.status != 200:
            raise self._fail(result)
        payload = result.payload if isinstance(result.payload, Mapping) else {}
        candidates: list[LocationCandidate] = []
        for suggestion in payload.get("suggestions") or []:
            if not isinstance(suggestion, Mapping):
                continue
            kgmid = str(suggestion.get("id") or "")
            airports = suggestion.get("airports") or []
            if airports:
                for airport in airports:
                    if not isinstance(airport, Mapping):
                        continue
                    candidates.append(LocationCandidate(
                        iata=str(airport.get("id") or ""),
                        name=str(airport.get("name") or ""),
                        city=str(airport.get("city") or ""),
                        kgmid=kgmid,
                    ))
            elif re.fullmatch(r"[A-Z]{3}", str(suggestion.get("id") or "")):
                candidates.append(LocationCandidate(
                    iata=str(suggestion["id"]), name=str(suggestion.get("name") or ""),
                ))
        return SearchResult(
            observation=Observation(
                provenance=Provenance(
                    provider=PROVIDER_NAME,
                    engine="google_flights_autocomplete",
                    search_id=str((payload.get("search_metadata") or {}).get("id") or "autocomplete"),
                    currency="",
                    request_kind="discover",
                    cache_mode="cached",
                    coverage_mode="unknown",
                    observed_at_basis="client_receipt",
                    query=f"q={query}",
                ),
                price_status="indicative",
                result_state="has_results" if candidates else "unknown",
                row_count=len(candidates),
            ),
            location_candidates=candidates,
        )

    def discover(self, request: Any) -> SearchResult:
        """Destination pools and deal windows. Prices are `indicative` (§5).

        Two engines, and the difference is measured rather than assumed: the
        ``deals`` container is verified (2026-10-03), while
        ``google_travel_explore`` returned **no container at all** for the parameters
        tried, so nothing is inferred from an explore response being empty — its
        populated shape is unmeasured and this method does not guess at one.
        """
        params = request.to_params()
        engine = str(params.get("engine") or "")
        is_deals = engine == "google_flights_deals"
        result = self._perform(
            params,
            request_kind="discover",
            coverage_mode="standard",
            price_status="indicative",
            no_cache_requested=False,
        )
        payload = result.raw_payload
        candidates = _destination_candidates(payload) if is_deals else []
        if is_deals:
            # The `deals` container is measured, so an empty one is a real empty
            # result for the declared query — unlike explore, below.
            result_state = "has_results" if candidates else "confirmed_empty"
        else:
            # Explore's populated shape is unmeasured: an empty answer here is not
            # evidence about the market, so the state is unknown rather than empty.
            result_state = "unknown"
        return SearchResult(
            observation=Observation(
                provenance=result.observation.provenance,
                price_status="indicative",
                result_state=result_state,
                row_count=len(candidates),
            ),
            offers=[],
            price_context=result.price_context,
            destination_candidates=candidates,
            ledger=result.ledger,
            cache_hit_detected=result.cache_hit_detected,
            error_field_present=result.error_field_present,
        )

    def search(self, request: Any) -> SearchResult:
        coverage = "deep" if getattr(request, "deep_search", False) else "standard"
        if getattr(request, "show_hidden", False):
            coverage = "hidden_included"
        return self._perform(
            request.to_params(),
            request_kind="search",
            coverage_mode=coverage,
            no_cache_requested=bool(getattr(request, "no_cache", False)),
        )

    def next_leg(self, request: NextLegRequest) -> SearchResult:
        return self._perform(
            request.to_params(),
            request_kind="next_leg",
            coverage_mode="standard",
            no_cache_requested=True,
        )

    def pin_itinerary(self, request: SelectedFlightsRequest) -> Optional[tuple[str, ...]]:
        """Return the pinned itinerary's identity, or ``None`` if the pin missed.

        The pin proves **presence, never price**: a measured pin response carries no
        ``price`` field at all (§8.1), so it cannot raise anything to ``verified``.
        :meth:`verify` therefore re-searches instead — the same one-search cost, and
        it yields a price to compare — which is D19. This method exists because the
        pin's shape is real and the parser must branch on it; it is not on the
        verification path.
        """
        result = self._perform(
            request.to_params(),
            request_kind="verify",
            coverage_mode="standard",
            no_cache_requested=False,
        )
        rows = itinerary_rows(result.raw_payload, "verify")
        if not rows:
            return None
        return itinerary_identity(rows[0])

    def verify(
        self,
        *,
        request: Any,
        expected_identity: Sequence[str],
        expected_price: Optional[float] = None,
        expected_currency: str = "",
    ) -> VerificationResult:
        """§8.1/D19 — a fresh re-search plus an identity match.

        Returns one of the four outcomes, never a bare offer claiming ``verified``.
        """
        result = self.search(request)
        expected = tuple(str(part) for part in expected_identity)
        for offer in result.offers:
            identity = route_identity(offer.outbound)
            if identity != expected:
                continue
            if expected_price is not None and float(offer.price) != float(expected_price):
                return VerificationResult(
                    "price_changed", expected, float(expected_price), expected_currency,
                    observed_price=float(offer.price), observed_identity=identity,
                )
            return VerificationResult(
                "verified", expected,
                float(expected_price) if expected_price is not None else None,
                expected_currency, observed_price=float(offer.price), observed_identity=identity,
            )
        # Same route, different itineraries: the expectation was substituted rather
        # than satisfied, which is a different fact from the route being gone.
        outcome = "substituted" if result.offers else "gone"
        return VerificationResult(
            outcome, expected,
            float(expected_price) if expected_price is not None else None,
            expected_currency,
        )

    def account(self) -> Any:
        """Reconciliation only. The counters lag and settle backwards (§10, D20)."""
        return self._transport.account()

    # ── request plumbing ──────────────────────────────────────────────────

    def _perform(
        self,
        params: Mapping[str, Any],
        *,
        request_kind: str,
        coverage_mode: str,
        price_status: str = "observed",
        no_cache_requested: bool = True,
    ) -> SearchResult:
        if self._health == "DISABLED":
            raise ProviderFailure("business", 0, detail="provider is DISABLED; no request was sent")
        transport_result = self._transport.search(params)
        if transport_result.status != 200:
            raise self._fail(transport_result)
        return self._result_from(
            transport_result.payload,
            request_kind=request_kind,
            coverage_mode=coverage_mode,
            price_status=price_status,
            no_cache_requested=no_cache_requested,
        )

    def _result_from(
        self,
        payload: Mapping[str, Any],
        *,
        request_kind: str,
        coverage_mode: str,
        price_status: str,
        no_cache_requested: bool,
    ) -> SearchResult:
        metadata = payload.get("search_metadata") or {}
        search_id = str(metadata.get("id") or "")
        if not search_id:
            # §6: without the archive handle an observation is not auditable, and
            # Provenance refuses to exist without it, so this must not be stored.
            raise ProviderContractError(
                "the response carries no search_metadata.id, so the observation is not "
                "auditable and must not be stored (design §6)."
            )
        status = str(metadata.get("status") or "")
        if status and status != "Success":
            self._record("failed")
            self._degrade("transient")
            raise ProviderFailure("transient", 200, detail=f"search_metadata.status={status}")

        repeated = search_id in self._seen_search_ids
        self._seen_search_ids.add(search_id)
        if not no_cache_requested or repeated:
            # §7: a cached read is never fresh. Not asking for a fresh fetch is the
            # first way to be cached; a replayed search id is the second, and it is
            # why a cache hit cannot be reported as a new observation (P2.2).
            cache_mode, basis = "cached", "client_receipt"
        else:
            cache_mode, basis = "no_cache", "provider_fetch"

        rows = itinerary_rows(payload, request_kind)
        currency = str((payload.get("search_parameters") or {}).get("currency") or "")
        # A pinned request returns its itinerary under `selected_flights` with no
        # price (§8.1, measured), so it cannot yield offers: it is a verification
        # candidate, and mapping it would have to invent the price it does not carry.
        offers = [] if request_kind == "verify" else [to_flight_offer(row, currency=currency) for row in rows]

        provenance = Provenance(
            provider=PROVIDER_NAME,
            engine=str((payload.get("search_parameters") or {}).get("engine") or "google_flights"),
            search_id=search_id,
            currency=currency,
            request_kind=request_kind,
            cache_mode=cache_mode,
            coverage_mode=coverage_mode,
            observed_at_basis=basis,
            query=_minimised_query(payload),
            provider_observed_at=str(metadata.get("created_at") or "") or None,
        )
        result_state = "has_results" if rows else "confirmed_empty"
        if not rows and coverage_mode == "unknown":
            # §7.1: an empty answer under unknown coverage is not a confirmed empty.
            result_state = "unknown"
        observation = Observation(
            provenance=provenance,
            price_status=price_status,
            result_state=result_state,
            row_count=len(rows),
            currency_observed=currency,
        )
        counted = self._record("success_empty" if not rows else "success_with_results", cache_hit=repeated)
        return SearchResult(
            observation=observation,
            offers=offers,
            price_context=price_context_from(
                payload, search_id=search_id, coverage_mode=coverage_mode, cache_mode=cache_mode
            ),
            ledger=counted,
            cache_hit_detected=repeated,
            error_field_present=bool(payload.get("error")),
            raw_payload=payload,
        )


def _minimised_query(payload: Mapping[str, Any]) -> str:
    """§6/§14 — the minimised query, never the raw request URL."""
    params = payload.get("search_parameters") or {}
    keep = ("engine", "departure_id", "arrival_id", "outbound_date", "return_date", "type", "currency", "month")
    return " ".join(f"{key}={params[key]}" for key in keep if params.get(key))


def _destination_candidates(payload: Mapping[str, Any]) -> list[DestinationCandidate]:
    """Map the measured ``deals`` container (§4.4). No itinerary, no offer (§5)."""
    candidates: list[DestinationCandidate] = []
    for row in payload.get("deals") or []:
        if not isinstance(row, Mapping):
            continue
        candidates.append(DestinationCandidate(
            kgmid=str(row.get("destination_id") or ""),
            name=str(row.get("name") or ""),
            country=str(row.get("country") or ""),
            arrival_airport_code=str(row.get("arrival_airport_code") or ""),
            indicative_price=float(row["price"]) if row.get("price") is not None else None,
            average_price=float(row["average_price"]) if row.get("average_price") is not None else None,
            discount_percentage=(
                float(row["discount_percentage"]) if row.get("discount_percentage") is not None else None
            ),
            outbound_date=str(row.get("outbound_date") or ""),
            return_date=str(row.get("return_date") or ""),
            stops=int(row["stops"]) if row.get("stops") is not None else None,
            airline=str(row.get("airline") or ""),
        ))
    return candidates
