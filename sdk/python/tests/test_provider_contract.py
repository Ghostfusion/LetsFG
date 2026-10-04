"""Tier-1 guards for the frozen provider contract (implementation plan P1.1).

Deterministic and offline: these pin the *shape* of the contracts the SerpApi
adapter is written against, so a later edit cannot quietly re-open a claim that was
closed deliberately — "a cached read is fresh", "a search may report verified",
"an empty result under unknown coverage may alert", "the ledger may guess".

Design: ``docs/serpapi-provider-design.md`` §5, §6, §7, §7.1, §8, §8.1, §9, §10.
"""

from __future__ import annotations

import pytest

from letsfg.connectors.provider_contract import (
    COVERAGE_MODES,
    LEDGER_EVENTS,
    LEDGER_UNKNOWN_PROVIDER_SEARCHES,
    OBSERVED_AT_BASES,
    PRICE_STATUS_CEILING,
    PRICE_STATUS_FOR_OUTCOME,
    PROVENANCE_ENGINES,
    REQUEST_KINDS,
    LedgerCounters,
    Observation,
    PriceContextEntry,
    ProviderContractError,
    ProviderPriceContext,
    Provenance,
    VerificationResult,
    ledger_counters_for,
)


def provenance(**overrides) -> Provenance:
    base = dict(
        provider="serpapi_google",
        engine="google_flights",
        search_id="66f0d1a1e2b3c4d5",
        currency="EUR",
        request_kind="search",
        cache_mode="no_cache",
        coverage_mode="standard",
        observed_at_basis="provider_fetch",
        query="CDG-JFK 2026-12-02 one way",
    )
    base.update(overrides)
    return Provenance(**base)


# ── the axes are closed sets ───────────────────────────────────────────────

def test_the_axes_match_the_register() -> None:
    assert OBSERVED_AT_BASES == ("provider", "provider_fetch", "client_receipt")
    assert COVERAGE_MODES == ("standard", "deep", "hidden_included", "unknown")
    assert REQUEST_KINDS == ("discover", "search", "verify", "next_leg", "booking_options")
    assert len(PROVENANCE_ENGINES) == 4
    assert set(PRICE_STATUS_CEILING) == set(REQUEST_KINDS)
    assert PRICE_STATUS_FOR_OUTCOME == {
        "verified": "verified",
        "price_changed": "observed",
        "substituted": "unavailable",
        "gone": "unavailable",
    }


# ── provenance: the basis is not optional, and cache is not freshness ──────

def test_an_observation_cannot_exist_without_provenance() -> None:
    """The two fields the design calls mandatory cannot be omitted or blank."""
    with pytest.raises(TypeError):
        Observation(price_status="observed", result_state="has_results", row_count=1)  # type: ignore[call-arg]

    with pytest.raises(ProviderContractError):
        provenance(search_id="")
    with pytest.raises(ProviderContractError):
        provenance(coverage_mode="shallow")


def test_provider_basis_needs_a_provider_stamp() -> None:
    """§7 rule 4: a provider-stamped time is never manufactured from a client clock."""
    with pytest.raises(ProviderContractError):
        provenance(observed_at_basis="provider")
    assert provenance(
        observed_at_basis="provider", provider_observed_at="2026-10-03 09:12:44 UTC"
    ).freshness == "live"


def test_a_cached_read_cannot_be_fresh() -> None:
    """§7 rules 3 and 4 — the failure this whole axis exists to prevent."""
    with pytest.raises(ProviderContractError):
        provenance(cache_mode="cached", observed_at_basis="provider_fetch")
    cached = provenance(cache_mode="cached", observed_at_basis="client_receipt")
    assert cached.freshness == "unknown"
    assert not cached.may_alert_or_verify


def test_only_a_fresh_fetch_may_alert_or_verify() -> None:
    assert provenance().may_alert_or_verify
    assert not provenance(
        cache_mode="cached", observed_at_basis="client_receipt"
    ).may_alert_or_verify


# ── observations: the ceiling, and what an empty result may claim ──────────

def test_a_discovery_price_may_not_be_stored_as_an_observation() -> None:
    """§5: `deals.price` is indicative; calling it observed is the trap."""
    discovery = provenance(request_kind="discover")
    assert Observation(discovery, "indicative", "has_results", 3).price_status == "indicative"
    with pytest.raises(ProviderContractError):
        Observation(discovery, "observed", "has_results", 3)


def test_only_a_verification_may_report_verified() -> None:
    with pytest.raises(ProviderContractError):
        Observation(provenance(request_kind="search"), "verified", "has_results", 1)
    assert Observation(
        provenance(request_kind="verify"), "verified", "has_results", 1
    ).price_status == "verified"


def test_an_empty_result_under_unknown_coverage_cannot_alert() -> None:
    """§7.1 + fli study F6: partial or unknown coverage cannot support a conclusion."""
    unknown = provenance(coverage_mode="unknown")
    assert not Observation(unknown, "observed", "has_results", 2).may_alert
    with pytest.raises(ProviderContractError):
        Observation(unknown, "observed", "confirmed_empty", 0)

    empty = Observation(provenance(coverage_mode="standard"), "observed", "confirmed_empty", 0)
    assert not empty.may_alert, "an empty result is not something to alert on"
    with pytest.raises(ProviderContractError):
        Observation(provenance(), "observed", "confirmed_empty", 4)


def test_a_cached_observation_may_not_alert() -> None:
    cached = provenance(cache_mode="cached", observed_at_basis="client_receipt")
    assert not Observation(cached, "observed", "has_results", 5).may_alert
    assert Observation(provenance(), "observed", "has_results", 5).may_alert


# ── verification: the comparison, not a mode ───────────────────────────────

IDENTITY = ("B6 1408", "BA 1520")


def test_verification_requires_an_expectation() -> None:
    with pytest.raises(ProviderContractError):
        VerificationResult("gone", (), 1380, "EUR")


def test_verified_needs_a_returned_price_that_matches() -> None:
    """§8.1/D19 — the pin returns no price, so this is the only route to `verified`."""
    with pytest.raises(ProviderContractError):
        VerificationResult("verified", IDENTITY, 1380, "EUR")
    with pytest.raises(ProviderContractError):
        VerificationResult("verified", IDENTITY, 1380, "EUR", observed_price=1378)
    with pytest.raises(ProviderContractError):
        VerificationResult(
            "verified", IDENTITY, 1380, "EUR", observed_price=1380,
            observed_identity=("B6 9999", "BA 1520"),
        )
    ok = VerificationResult("verified", IDENTITY, 1380, "EUR", observed_price=1380,
                            observed_identity=IDENTITY)
    assert ok.price_status == "verified"


def test_a_price_move_is_an_observation_not_a_verification() -> None:
    """The single most valuable alert signal here, and the one `verified` would destroy."""
    moved = VerificationResult("price_changed", IDENTITY, 1380, "EUR", observed_price=1298)
    assert moved.price_status == "observed"
    assert moved.price_status != "verified"
    assert VerificationResult("substituted", IDENTITY, 1380, "EUR").price_status == "unavailable"
    assert VerificationResult("gone", IDENTITY, 1380, "EUR").price_status == "unavailable"


# ── provider price context is a claim, never a baseline ────────────────────

def test_price_context_cannot_be_ranked() -> None:
    """§9 rules 1 and 2 — nothing here may become a baseline or an alert."""
    context = ProviderPriceContext(
        search_id="66f0d1a1e2b3c4d5",
        provider="serpapi_google",
        coverage_mode="standard",
        cache_mode="no_cache",
        lowest_price=348,
        price_level="low",
        typical_price_range=(350, 710),
    )
    assert context.is_rankable is False
    assert not hasattr(context, "mean")
    # No ordering is defined on the type itself: `object.__lt__` exists on every
    # object, so the meaningful check is that the operations fail and that the type
    # does not declare them.
    for operator in ("__lt__", "__le__", "__gt__", "__ge__"):
        assert operator not in ProviderPriceContext.__dict__
    with pytest.raises(TypeError):
        context < 1000  # type: ignore[operator]

    with pytest.raises(ProviderContractError):
        ProviderPriceContext("", "serpapi_google", "standard", "no_cache")


def test_a_context_entry_keeps_its_provenance() -> None:
    entry = PriceContextEntry(
        value=348, provider="serpapi_google", search_id="abc", coverage_mode="standard",
        cache_mode="no_cache",
    )
    assert entry.value == 348 and entry.search_id == "abc"


# ── the ledger counts three different things ───────────────────────────────

def test_the_ledger_table_is_the_one_in_the_design() -> None:
    assert LEDGER_EVENTS["success_with_results"] == LedgerCounters(1, 1, 1)
    assert LEDGER_EVENTS["success_empty"] == LedgerCounters(1, 1, 1), (
        "an empty result still costs quota — measured 2026-10-03, design §4.3"
    )
    assert LEDGER_EVENTS["cache_hit"] == LedgerCounters(1, 0, 0)
    assert LEDGER_EVENTS["archive_retrieval"] == LedgerCounters(1, 0, 0)
    assert LEDGER_UNKNOWN_PROVIDER_SEARCHES, "the 0-1 cells must be marked, not invented"


def test_a_cache_hit_is_not_billable() -> None:
    assert ledger_counters_for("success_with_results", cache_hit=True) == LedgerCounters(1, 0, 0)
    assert ledger_counters_for("success_empty", cache_hit=True).billable_searches == 0
    assert ledger_counters_for("success_with_results").billable_searches == 1


def test_an_unknown_event_is_refused_rather_than_guessed() -> None:
    with pytest.raises(ProviderContractError):
        ledger_counters_for("probably_free")
    with pytest.raises(ProviderContractError):
        ledger_counters_for("")


def test_counters_add() -> None:
    total = ledger_counters_for("success_with_results") + ledger_counters_for("cache_hit")
    assert total == LedgerCounters(2, 1, 1)
