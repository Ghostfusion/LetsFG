"""Frozen provider-contract types for the SerpApi Google Flights lane.

Design: ``docs/serpapi-provider-design.md`` §6 (provenance), §7 (freshness),
§7.1 (coverage), §8/§8.1 (``price_status`` and verification), §9
(``ProviderPriceContext``), §10 (the ledger's three counters).

This is the **contract freeze** of implementation-plan P1.1: it is pure — no I/O,
no provider names beyond the ones the contracts themselves are about, no network —
and it exists so that the adapter (P2) is written against measured behaviour rather
than against a plausible reading of the documentation.

Three properties are structural rather than conventional, which is the whole point:

* An observation **cannot** be constructed without an ``observed_at_basis`` and a
  ``coverage_mode``: both are required fields of :class:`Provenance`, and an
  observation without provenance does not typecheck.
* ``observed_at_basis = "provider"`` cannot be claimed without an actual
  provider-stamped time, ``"provider_fetch"`` cannot be claimed for a cached read,
  and a cached read can only be ``"client_receipt"`` — so the one value the provider
  does not expose (§7 rule 5) is unreachable by accident.
* A verification cannot be represented as a bare offer: :class:`VerificationResult`
  forces exactly one of four outcomes, and only ``verified`` may carry
  ``price_status: verified``.

``ProviderPriceContext`` is deliberately not a mapping type, carries no comparison
or ordering operators, and is never accepted where the baseline is built (§9 rule 1):
``lowest_price`` is the minimum of a returned set, which is not a market minimum.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

__all__ = [
    "ObservedAtBasis",
    "CoverageMode",
    "RequestKind",
    "CacheMode",
    "RetrievalMode",
    "PriceStatus",
    "VerificationOutcome",
    "ResultState",
    "PROVENANCE_ENGINES",
    "PRICE_STATUS_CEILING",
    "PRICE_STATUS_FOR_OUTCOME",
    "FRESHNESS_BASES",
    "ProviderContractError",
    "Provenance",
    "Observation",
    "VerificationResult",
    "PriceContextEntry",
    "ProviderPriceContext",
    "LedgerCounters",
    "LEDGER_EVENTS",
    "ledger_counters_for",
]


# ── the axes ───────────────────────────────────────────────────────────────
# Each is a closed set. The adapter may emit exactly these values; anything else
# is a contract change, not a judgement call, so it is rejected at construction.

ObservedAtBasis = Literal["provider", "provider_fetch", "client_receipt"]

#: §7 rule 3. ``provider`` is currently unreachable in practice — nothing in a
#: measured response claims to be an upstream observation time — and it stays in
#: the enum because the rule is about *any* upstream, not about this one.
OBSERVED_AT_BASES: tuple[str, ...] = ("provider", "provider_fetch", "client_receipt")

#: §7.1. Completeness answers to the mode the request *declared*, never to a
#: provider switch.
CoverageMode = Literal["standard", "deep", "hidden_included", "unknown"]
COVERAGE_MODES: tuple[str, ...] = ("standard", "deep", "hidden_included", "unknown")

#: §5/§6. Which operation produced a stored observation.
RequestKind = Literal["discover", "search", "verify", "next_leg", "booking_options"]
REQUEST_KINDS: tuple[str, ...] = ("discover", "search", "verify", "next_leg", "booking_options")

#: §7. Whether the provider's own cache was bypassed.
CacheMode = Literal["cached", "no_cache"]
CACHE_MODES: tuple[str, ...] = ("cached", "no_cache")

#: §4.3 — ``async=true`` retrieval through the Search Archive.
RetrievalMode = Literal["synchronous", "asynchronous"]

#: The scanner's single price lifecycle (§8).
PriceStatus = Literal["indicative", "observed", "verified", "stale", "unavailable"]

#: §8.1 — the four outcomes of a verification comparison.
VerificationOutcome = Literal["verified", "price_changed", "substituted", "gone"]

#: The coverage half of the shared provider contract (fli study §6.2).
ResultState = Literal["has_results", "confirmed_empty", "unknown"]

#: §4.1 — the engines reachable through the one endpoint.
PROVENANCE_ENGINES: tuple[str, ...] = (
    "google_flights",
    "google_flights_deals",
    "google_travel_explore",
    "google_flights_autocomplete",
)

#: §5's operation ceiling. A request kind may emit a status **at or below** its
#: ceiling, never above it: ``verify`` is a candidate, not a conclusion.
PRICE_STATUS_CEILING: dict[str, str] = {
    "discover": "indicative",
    "search": "observed",
    "verify": "observed",
    "next_leg": "observed",
    "booking_options": "observed",
}

#: The order the ceiling is enforced in. ``verified`` is absent on purpose: it is
#: not a higher grade of ``observed``, it is the output of the §8.1 comparison, and
#: it is gated by the request kind instead.
_RANKED_STATUSES: tuple[str, ...] = ("indicative", "observed")

#: §8.1's table. Only a matched comparison may raise a status to ``verified``;
#: everything else records what was seen.
PRICE_STATUS_FOR_OUTCOME: dict[str, str] = {
    "verified": "verified",
    "price_changed": "observed",
    "substituted": "unavailable",
    "gone": "unavailable",
}

#: §7 rule 4 — freshness follows from the basis. A cached read may never be live.
FRESHNESS_BASES: dict[str, str] = {
    "provider": "live",
    "provider_fetch": "live",
    "client_receipt": "unknown",
}


class ProviderContractError(ValueError):
    """A contract was constructed with a value or combination it cannot have."""


def _require_member(value: str, allowed: tuple[str, ...], field_name: str) -> None:
    if value not in allowed:
        raise ProviderContractError(
            f"{field_name}={value!r} is not one of {allowed}; the contract is frozen "
            "in the design's register, so widening it is a deliberate edit there first."
        )


# ── provenance ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Provenance:
    """§6. Mandatory on every observation this provider produces.

    ``search_id`` is the 31-day archive handle: without it an observation is not
    auditable and **must not be stored** (§6), so its absence is an error rather
    than a default. ``observed_at_basis`` and ``coverage_mode`` are required for
    the same reason one level up — a price with no basis and no coverage cannot be
    re-read honestly later.
    """

    provider: str
    engine: str
    search_id: str
    currency: str
    request_kind: str
    cache_mode: str
    coverage_mode: str
    observed_at_basis: str
    retrieval_mode: str = "synchronous"
    query: str = ""
    provider_observed_at: Optional[str] = None
    kgmid: Optional[str] = None

    def __post_init__(self) -> None:
        _require_member(self.engine, PROVENANCE_ENGINES, "engine")
        _require_member(self.request_kind, REQUEST_KINDS, "request_kind")
        _require_member(self.cache_mode, CACHE_MODES, "cache_mode")
        _require_member(self.coverage_mode, COVERAGE_MODES, "coverage_mode")
        _require_member(self.observed_at_basis, OBSERVED_AT_BASES, "observed_at_basis")
        _require_member(self.retrieval_mode, ("synchronous", "asynchronous"), "retrieval_mode")
        if not self.search_id:
            raise ProviderContractError(
                "search_id is required: an observation without the archive handle is "
                "not auditable and must not be stored (design §6)."
            )
        if self.observed_at_basis == "provider" and not self.provider_observed_at:
            raise ProviderContractError(
                "observed_at_basis='provider' needs a provider-stamped time. Nothing "
                "measured claims to be one, so this value is unreachable by accident "
                "and must not be manufactured from a client clock (design §7 rule 4)."
            )
        if self.observed_at_basis == "provider_fetch" and self.cache_mode != "no_cache":
            raise ProviderContractError(
                "observed_at_basis='provider_fetch' means a fresh upstream fetch "
                "happened, which only no_cache establishes (design §7 rule 3)."
            )
        if self.cache_mode == "cached" and self.observed_at_basis != "client_receipt":
            raise ProviderContractError(
                "a cached read cannot claim freshness: its basis is client_receipt "
                "(design §7 rules 2 and 4)."
            )

    @property
    def freshness(self) -> str:
        return FRESHNESS_BASES[self.observed_at_basis]

    @property
    def may_alert_or_verify(self) -> bool:
        """§7 rule 1/2 — only a fresh fetch may feed an alert or a verification."""
        return self.cache_mode == "no_cache" and self.freshness == "live"


# ── observations ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Observation:
    """One provider answer, with its provenance and its declared coverage.

    ``price_status`` is checked against §5's ceiling for the request kind that
    produced it, so a discovery price can never be stored as an observation.
    """

    provenance: Provenance
    price_status: str
    result_state: str
    row_count: int
    currency_observed: str = ""

    def __post_init__(self) -> None:
        _require_member(self.price_status, ("indicative", "observed", "verified", "stale", "unavailable"), "price_status")
        _require_member(self.result_state, ("has_results", "confirmed_empty", "unknown"), "result_state")
        if self.price_status == "verified":
            # §5: a verification's ceiling is a *candidate*; only the §8.1 comparison
            # may raise it, and only a verification request can carry that comparison.
            if self.provenance.request_kind != "verify":
                raise ProviderContractError(
                    f"price_status='verified' from request_kind="
                    f"{self.provenance.request_kind!r}: only a verification may emit it (§5, §8)."
                )
        elif self.price_status in _RANKED_STATUSES:
            ceiling = PRICE_STATUS_CEILING[self.provenance.request_kind]
            if _RANKED_STATUSES.index(self.price_status) > _RANKED_STATUSES.index(ceiling):
                raise ProviderContractError(
                    f"price_status={self.price_status!r} exceeds the ceiling {ceiling!r} for "
                    f"request_kind={self.provenance.request_kind!r} (design §5)."
                )
        if self.result_state == "confirmed_empty" and self.row_count != 0:
            raise ProviderContractError(
                "result_state='confirmed_empty' with rows present is a contradiction, "
                "and an empty answer under a degraded coverage_mode must not be called "
                "confirmed_empty at all (§7.1)."
            )
        if self.result_state == "confirmed_empty" and self.provenance.coverage_mode == "unknown":
            raise ProviderContractError(
                "coverage_mode='unknown' cannot support a confirmed-empty result "
                "(design §7.1): we do not know what the provider served."
            )

    @property
    def may_alert(self) -> bool:
        """An alert asserts a conclusion about the market; every half must hold.

        ``verified`` is alert-eligible — the alert copy says "verified N minutes
        ago" (§8) — but ``indicative`` never is: it is not a bookable itinerary.
        """
        return (
            self.provenance.may_alert_or_verify
            and self.price_status in ("observed", "verified")
            and self.result_state == "has_results"
            and self.provenance.coverage_mode != "unknown"
        )


# ── verification ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class VerificationResult:
    """§8.1 — a comparison, never an API mode.

    The expected itinerary is part of the result, so "verified" cannot be recorded
    without saying *what* was expected: a bare offer claiming ``verified`` is
    unrepresentable, which is the point.
    """

    outcome: str
    expected_identity: tuple[str, ...]
    expected_price: Optional[int]
    expected_currency: str
    observed_price: Optional[int] = None
    observed_identity: Optional[tuple[str, ...]] = None
    note: str = ""

    def __post_init__(self) -> None:
        _require_member(self.outcome, ("verified", "price_changed", "substituted", "gone"), "outcome")
        if not self.expected_identity:
            raise ProviderContractError(
                "a verification without an expected itinerary identity cannot be "
                "checked, so it is not a verification (design §8.1)."
            )
        if self.outcome == "verified":
            if self.observed_price is None:
                raise ProviderContractError(
                    "no measured response returns a price from the pin, so 'verified' "
                    "requires a returned price to compare (design §8.1, D19)."
                )
            if self.expected_price is not None and self.observed_price != self.expected_price:
                raise ProviderContractError(
                    "same itinerary with a different price is 'price_changed' — the "
                    "single most valuable alert signal here — never 'verified' (§8.1)."
                )
            if self.observed_identity is not None and tuple(self.observed_identity) != tuple(self.expected_identity):
                raise ProviderContractError("'verified' requires the same itinerary (§8.1).")

    @property
    def price_status(self) -> str:
        return PRICE_STATUS_FOR_OUTCOME[self.outcome]


# ── provider price context ─────────────────────────────────────────────────

@dataclass(frozen=True)
class PriceContextEntry:
    """§9. A provider claim with its provenance, kept separable by construction."""

    value: float
    provider: str
    search_id: str
    coverage_mode: str
    cache_mode: str


@dataclass(frozen=True)
class ProviderPriceContext:
    """§9 — **not** the scanner's baseline.

    ``lowest_price`` is the minimum of the returned set, not a market minimum, so
    this type deliberately has no ordering, no comparison, and no arithmetic: there
    is nothing here to rank or alert with. It is a claim by the provider about its
    own cohort and window, and the scanner's baseline subsystem remains the only
    author of a baseline decision.
    """

    search_id: str
    provider: str
    coverage_mode: str
    cache_mode: str
    lowest_price: Optional[float] = None
    price_level: Optional[str] = None
    typical_price_range: Optional[tuple[float, float]] = None
    price_history: tuple[tuple[int, float], ...] = ()
    average_price: Optional[float] = None
    discount_percentage: Optional[float] = None
    notes: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.search_id:
            raise ProviderContractError("provider price context is a claim and needs the search it came from (§9).")
        _require_member(self.coverage_mode, COVERAGE_MODES, "coverage_mode")
        _require_member(self.cache_mode, CACHE_MODES, "cache_mode")

    # No __lt__, __eq__ against numbers, no sum/mean helpers on purpose: §9 rule 2
    # keeps this non-rankable and non-alertable until the probe measures its window
    # and cohort, and the probe has established neither.

    @property
    def is_rankable(self) -> bool:
        """Always false today, and a *property* rather than a field so it cannot be
        flipped by constructing one differently. §9 rule 2."""
        return False


# ── the ledger ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class LedgerCounters:
    """§10 — three counters, because one word for three things is how budgets lie."""

    provider_requests: int
    provider_searches: int
    billable_searches: int

    def __add__(self, other: "LedgerCounters") -> "LedgerCounters":
        return LedgerCounters(
            self.provider_requests + other.provider_requests,
            self.provider_searches + other.provider_searches,
            self.billable_searches + other.billable_searches,
        )


#: §10's table, verbatim. ``None`` for a counter the table gives as a range: a
#: failed search's ``ProviderSearch`` is "0–1" because whether the provider
#: performed a search before failing is not observable client-side, and absence of
#: evidence is not evidence (design §3.5).
LEDGER_EVENTS: dict[str, LedgerCounters] = {
    "success_with_results": LedgerCounters(1, 1, 1),
    "success_empty": LedgerCounters(1, 1, 1),
    "failed": LedgerCounters(1, 0, 0),
    "cache_hit": LedgerCounters(1, 0, 0),
    "archive_retrieval": LedgerCounters(1, 0, 0),
}

#: Events whose ``provider_searches`` cannot be determined from the client side.
LEDGER_UNKNOWN_PROVIDER_SEARCHES: frozenset[str] = frozenset({"failed", "archive_retrieval"})


def ledger_counters_for(event: str, *, cache_hit: bool = False) -> LedgerCounters:
    """Count one event.

    ``cache_hit`` is a separate argument because a cache hit is only *detectable*
    from the response (a repeated ``search_metadata.id``, §4.2), not from the
    request: the caller decides, and that decision is exactly what the ledger
    records. A successful search the provider served from cache is not billable.
    """
    if cache_hit and event in {"success_with_results", "success_empty"}:
        return LEDGER_EVENTS["cache_hit"]
    try:
        return LEDGER_EVENTS[event]
    except KeyError:
        raise ProviderContractError(
            f"{event!r} is not a ledger event. The table in design §10 is normative: "
            "an event that is not in it has no agreed cost, so it must not be counted "
            f"by guessing. Known: {tuple(sorted(LEDGER_EVENTS))}."
        ) from None
