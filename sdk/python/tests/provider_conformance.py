"""Provider conformance suite (implementation plan P2.5).

**Scope decision, 2026-10-03 — adapter-scoped.** The plan's acceptance clause asked for
this suite to be green on "the existing first-party lanes" before the newcomer was
registered. Those lanes are the PFS and Developer API clients: they are the *product's
own* market access, not rented observations, and they expose no provider contract at
all — no provenance, no `coverage_mode`, no `price_status`, no verification outcome
(`client.py` works in the dataclass models in `letsfg/models/__init__.py`). Giving them
one would mean either inventing semantics they do not have or rebuilding two working
clients around a contract only one lane needs. The design's §3 boundary already says
why: a provider is an *external, optional, auditable* source behind our contracts, and
the first-party lanes are what everything else is measured against. So the suite is
scoped to adapters, the exemption is stated in the design, and the criteria below are
written from the design's own statements rather than from any adapter's shape.

**This module is the suite.** It holds the criteria and returns violations; any adapter
whose outputs satisfy the frozen contract can be run through it. It deliberately takes
*values* (observations, price contexts, offers) rather than a provider object, so it
cannot be written to fit one adapter's internals — the thing the original clause was
trying to prevent.

The six criteria, each traceable to a section:

1. `status_ceiling` — §5: an operation may emit a status at or below its ceiling, and
   only a `verify` request may carry `verified`.
2. `provenance_present` — §6: every observation carries the archive handle, a basis, a
   coverage mode and a request kind.
3. `freshness_never_fabricated` — §7: `provider` needs a provider stamp, a cached read
   is `client_receipt`, and freshness is required before anything alertable.
4. `no_results_is_not_timeout` — §11: an empty success is never a failure, and a
   failure never masquerades as an empty result.
5. `completeness_relative_to_declared_coverage` — §7.1: `unknown` coverage cannot
   support a confirmed-empty or a complete answer, and a declared mode is recorded.
6. `public_shape_sanitised` — §13: public exposure goes through the existing sanitiser,
   and the adapter builds no public shape of its own.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from letsfg.connectors.provider_contract import (
    CACHE_MODES,
    COVERAGE_MODES,
    OBSERVED_AT_BASES,
    PRICE_STATUS_CEILING,
    REQUEST_KINDS,
    Observation,
    ProviderPriceContext,
)

__all__ = ["CRITERIA", "ConformanceViolation", "check_status_ceiling", "check_provenance_present",
           "check_freshness_never_fabricated", "check_no_results_is_not_timeout",
           "check_completeness_relative_to_declared_coverage", "check_public_shape_sanitised",
           "run_conformance"]

CRITERIA: tuple[str, ...] = (
    "status_ceiling",
    "provenance_present",
    "freshness_never_fabricated",
    "no_results_is_not_timeout",
    "completeness_relative_to_declared_coverage",
    "public_shape_sanitised",
)

_RANKED = ("indicative", "observed")


class ConformanceViolation(AssertionError):
    """A provider output violates one of the frozen criteria."""

    def __init__(self, criterion: str, detail: str) -> None:
        super().__init__(f"{criterion}: {detail}")
        self.criterion = criterion
        self.detail = detail


def _require(condition: bool, criterion: str, detail: str) -> None:
    if not condition:
        raise ConformanceViolation(criterion, detail)


# ── 1. §5 — the operation ceiling ──────────────────────────────────────────

def check_status_ceiling(observation: Observation) -> None:
    provenance = observation.provenance
    _require(
        provenance.request_kind in PRICE_STATUS_CEILING,
        "status_ceiling",
        f"request_kind {provenance.request_kind!r} has no declared ceiling",
    )
    status = observation.price_status
    if status == "verified":
        _require(
            provenance.request_kind == "verify",
            "status_ceiling",
            f"price_status 'verified' from request_kind {provenance.request_kind!r}",
        )
        return
    if status in _RANKED:
        ceiling = PRICE_STATUS_CEILING[provenance.request_kind]
        _require(
            _RANKED.index(status) <= _RANKED.index(ceiling),
            "status_ceiling",
            f"price_status {status!r} exceeds the {ceiling!r} ceiling for "
            f"request_kind {provenance.request_kind!r}",
        )


# ── 2. §6 — provenance is mandatory and complete ───────────────────────────

def check_provenance_present(observation: Observation) -> None:
    provenance = observation.provenance
    for field, allowed in (
        ("observed_at_basis", OBSERVED_AT_BASES),
        ("coverage_mode", COVERAGE_MODES),
        ("cache_mode", CACHE_MODES),
        ("request_kind", REQUEST_KINDS),
    ):
        _require(getattr(provenance, field) in allowed, "provenance_present",
                 f"{field}={getattr(provenance, field)!r} is outside its closed set")
    _require(bool(provenance.search_id), "provenance_present",
             "no archive handle: the observation is not auditable and must not be stored")
    _require(bool(provenance.provider) and bool(provenance.engine), "provenance_present",
             "the provider and engine that produced the observation are required")


# ── 3. §7 — freshness is never fabricated ─────────────────────────────────

def check_freshness_never_fabricated(observation: Observation) -> None:
    provenance = observation.provenance
    if provenance.observed_at_basis == "provider":
        _require(
            bool(provenance.provider_observed_at),
            "freshness_never_fabricated",
            "a provider-stamped basis without a provider-stamped time",
        )
    if provenance.cache_mode == "cached":
        _require(
            provenance.observed_at_basis == "client_receipt",
            "freshness_never_fabricated",
            "a cached read claiming a fresher basis than client_receipt",
        )
    if provenance.observed_at_basis == "provider_fetch":
        _require(
            provenance.cache_mode == "no_cache",
            "freshness_never_fabricated",
            "a provider_fetch basis that did not bypass the provider's cache",
        )
    if observation.may_alert:
        _require(
            provenance.freshness == "live" and provenance.cache_mode == "no_cache",
            "freshness_never_fabricated",
            "an alertable observation that is not a fresh fetch",
        )


# ── 4. §11 — no_results is not timeout ────────────────────────────────────

def check_no_results_is_not_timeout(observation: Observation) -> None:
    if observation.result_state == "confirmed_empty":
        _require(observation.row_count == 0, "no_results_is_not_timeout",
                 "a confirmed-empty result carrying rows")
        _require(observation.price_status not in ("stale", "unavailable"),
                 "no_results_is_not_timeout",
                 "an empty result is a no_results, never a failure status")
    if observation.result_state == "has_results":
        _require(observation.row_count > 0, "no_results_is_not_timeout",
                 "a result with rows but an empty row count")


# ── 5. §7.1 — completeness answers to the declared coverage ───────────────

def check_completeness_relative_to_declared_coverage(observation: Observation) -> None:
    coverage = observation.provenance.coverage_mode
    _require(coverage in COVERAGE_MODES, "completeness_relative_to_declared_coverage",
             f"coverage_mode {coverage!r} is not declared")
    if coverage == "unknown":
        _require(observation.result_state != "confirmed_empty",
                 "completeness_relative_to_declared_coverage",
                 "unknown coverage cannot support a confirmed-empty result")
    if observation.provenance.request_kind == "discover":
        _require(observation.price_status == "indicative",
                 "completeness_relative_to_declared_coverage",
                 "discovery prices are indicative and cannot be observations")


# ── 6. §13 — the public shape is the sanitiser's, not the adapter's ───────

def check_public_shape_sanitised(*, provider: Any, offer: Any, public_offer: Any) -> None:
    _require(not hasattr(provider, "to_public_offer"), "public_shape_sanitised",
             "the adapter exposes its own public shape; masking must have one implementation")
    _require(public_offer.id == offer.id, "public_shape_sanitised",
             "the sanitised offer must keep the offer's identity")
    _require(public_offer.is_locked is True, "public_shape_sanitised",
             "a public offer must be locked until unlocked through the product")
    _require(public_offer.owner_airline != offer.owner_airline, "public_shape_sanitised",
             "the validating carrier must be masked to a category")
    _require(
        all(segment.flight_no == "" for segment in public_offer.outbound.segments),
        "public_shape_sanitised",
        "flight numbers must be withheld until unlock",
    )


# ── the suite ─────────────────────────────────────────────────────────────

def run_conformance(
    *,
    observations: Sequence[Observation],
    price_contexts: Sequence[Optional[ProviderPriceContext]] = (),
    provider: Any = None,
    offer: Any = None,
    public_offer: Any = None,
    failures: Sequence[Exception] = (),
) -> Mapping[str, list[str]]:
    """Run every criterion over the supplied evidence.

    Returns a report mapping each criterion to the violations found, so a caller can
    see *which* contract failed rather than only that something did. ``failures`` are
    the exceptions the provider raised where a result was not produced: an empty
    success must arrive as an observation and a failure must not, which is how
    criterion 4 is checked from both sides.
    """
    from letsfg.connectors.serpapi_google import ProviderFailure

    report: dict[str, list[str]] = {name: [] for name in CRITERIA}
    for observation in observations:
        for check in (
            check_status_ceiling,
            check_provenance_present,
            check_freshness_never_fabricated,
            check_no_results_is_not_timeout,
            check_completeness_relative_to_declared_coverage,
        ):
            try:
                check(observation)
            except ConformanceViolation as violation:
                report[violation.criterion].append(violation.detail)

    if price_contexts:
        for context in price_contexts:
            if context is None:
                continue
            if context.is_rankable:
                report["completeness_relative_to_declared_coverage"].append(
                    "provider price context must not be rankable (§9)"
                )

    for failure in failures:
        if not isinstance(failure, ProviderFailure):
            report["no_results_is_not_timeout"].append(
                f"a non-provider exception escaped: {type(failure).__name__}"
            )

    if provider is not None and offer is not None and public_offer is not None:
        try:
            check_public_shape_sanitised(provider=provider, offer=offer, public_offer=public_offer)
        except ConformanceViolation as violation:
            report[violation.criterion].append(violation.detail)

    return report
