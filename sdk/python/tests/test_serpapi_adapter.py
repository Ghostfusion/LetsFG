"""Adapter tests, driven by the committed live fixtures.

Deterministic and offline: every request goes through an injected opener that replays
a payload captured from the real provider (``tests/fixtures/serpapi/``, written by
``tests/test_serpapi_live.py``). That is what makes it possible to test the mapping,
the failure classifier, the ledger and the freshness rules without spending quota.

Covers implementation-plan P1.2 (duration invariant), P1.3 (credential redaction),
P1.4 (the 429 classifier), P1.5 (completeness vs coverage), P1.6 (price context is not
a baseline), P1.7 (enum traps and identity normalisation), P2.1 (module and registry),
P2.2 (mapping and provenance), P2.3 (ledger) and P2.4 (price context).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from letsfg.connectors.provider_contract import (
    LedgerCounters,
    ProviderContractError,
    ledger_counters_for,
)
from letsfg.connectors.provider_registry import REGISTRY, available_providers, get_provider, is_available
from letsfg.connectors.serpapi_google import (
    ENV_VAR,
    REJECTED_ENV_VARS,
    DealsRequest,
    DealsTravelDuration,
    ExploreTravelDuration,
    OneWayRequest,
    ProviderCredentialError,
    ProviderFailure,
    SelectedFlightsRequest,
    SerpApiGoogleProvider,
    SerpApiTransport,
    Stops,
    TravelExploreRequest,
    classify_429,
    classify_status,
    itinerary_identity,
    normalise_flight_number,
    parse_local_time,
    provider_configured,
    resolve_api_key,
    route_segment_identity,
    segment_identity,
    to_flight_offer,
)

FIXTURES = Path(__file__).parent / "fixtures" / "serpapi"
SDK_ROOT = Path(__file__).resolve().parents[1]
KEY = "test-key-not-a-real-secret"
OPTS: dict[str, Any] = dict(gl="us", hl="en")


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeOpener:
    """Replays queued responses and records the URLs it was asked for."""

    def __init__(self, *results: SimpleNamespace) -> None:
        self._results = list(results)
        self.urls: list[str] = []

    def __call__(self, url: str):
        self.urls.append(url)
        if not self._results:
            raise AssertionError("the adapter made more requests than the test queued")
        return self._results.pop(0)


def reply(payload: dict, status: int = 200, headers: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(status=status, payload=payload, headers=headers or {})


def provider_with(*results: SimpleNamespace) -> tuple[SerpApiGoogleProvider, FakeOpener]:
    opener = FakeOpener(*results)
    return SerpApiGoogleProvider(env={ENV_VAR: KEY}, opener=opener), opener


def one_way(**extra) -> OneWayRequest:
    base = dict(departure_id="CDG", arrival_id="JFK", outbound_date="2026-12-02", **OPTS)
    base.update(extra)
    return OneWayRequest(**base)


# ── P2.1 — module, registry and configuration ─────────────────────────────

def test_a_provider_does_not_exist_without_its_credential() -> None:
    with pytest.raises(ProviderCredentialError):
        SerpApiGoogleProvider(env={})
    assert provider_configured({}) is False
    assert provider_configured({ENV_VAR: "x"}) is True


def test_a_misfiled_serper_key_is_reported_not_read() -> None:
    """§14/D18 — the two vendors' names are one letter apart, and this is the guard."""
    for name in REJECTED_ENV_VARS:
        with pytest.raises(ProviderCredentialError) as error:
            resolve_api_key({name: "a-real-looking-value"})
        message = str(error.value)
        assert ENV_VAR in message and name in message
        assert "never falls back" in message


def test_importing_the_registry_does_not_import_the_adapter() -> None:
    """§3.2 — "optional" means not loaded, not "loaded but disabled"."""
    code = (
        "import sys\n"
        "from letsfg.connectors import provider_registry as r\n"
        "assert 'letsfg.connectors.serpapi_google' not in sys.modules, 'adapter imported eagerly'\n"
        "assert r.available_providers({}) == (), r.available_providers({})\n"
        "print('ok')\n"
    )
    env = {k: v for k, v in os.environ.items() if k != ENV_VAR}
    completed = subprocess.run(
        [sys.executable, "-c", code], cwd=SDK_ROOT, capture_output=True, text=True, env=env,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "ok"


def test_the_registry_is_empty_without_a_credential_and_yields_one_with_it() -> None:
    assert "serpapi_google" in REGISTRY
    assert available_providers({}) == ()
    assert is_available("serpapi_google", {ENV_VAR: KEY}) is True
    with pytest.raises(ProviderCredentialError):
        get_provider("serpapi_google", {})
    with pytest.raises(KeyError):
        get_provider("no_such_provider", {ENV_VAR: KEY})
    built = get_provider("serpapi_google", {ENV_VAR: KEY})
    assert built.NAME == "serpapi_google"


def test_the_declared_surface_excludes_booking_options() -> None:
    """§13 — referral navigation is not modelled in V1, and not accidentally available."""
    capabilities = SerpApiGoogleProvider.capabilities
    assert capabilities.operations == ("resolve_location", "discover", "search", "verify", "next_leg")
    assert capabilities.booking_options is False
    assert capabilities.books is False and capabilities.holds_fares is False
    assert capabilities.ranks is False and capabilities.alerts is False
    provider, _ = provider_with()
    assert not hasattr(provider, "booking_options")


def test_a_disabled_provider_refuses_without_sending_anything() -> None:
    provider, opener = provider_with()
    provider.disable()
    assert provider.health == "DISABLED"
    with pytest.raises(ProviderFailure):
        provider.search(one_way())
    assert opener.urls == [], "a disabled provider must not reach the network"


# ── P2.2 — mapping, provenance, freshness and the cache refusal ────────────

def test_search_maps_the_recorded_fixture() -> None:
    payload = load("coverage_standard.json")
    provider, _ = provider_with(reply(payload))
    result = provider.search(one_way())

    # The probe trims each itinerary list to three rows before committing a fixture
    # (they are ~20 KB each), so the committed payload holds 6 of the 16 rows the
    # provider returned; `_probe_observed` in the fixture records the real counts.
    expected_rows = len(payload.get("best_flights") or []) + len(payload.get("other_flights") or [])
    observed_rows = (payload.get("_probe_observed") or {}).get("standard_rows")
    assert expected_rows == 6 and observed_rows == 16, "the fixture's trimming changed"

    assert len(result.offers) == expected_rows
    assert result.observation.row_count == expected_rows
    assert result.observation.provenance.search_id == payload["search_metadata"]["id"]
    assert result.observation.provenance.cache_mode == "no_cache"
    assert result.observation.provenance.observed_at_basis == "provider_fetch"
    assert result.observation.provenance.coverage_mode == "standard"

    offer = result.offers[0]
    assert offer.source == "serpapi_google" and offer.currency == "EUR"
    assert offer.price > 0
    segment = offer.outbound.segments[0]
    assert segment.airline == "AA" and segment.flight_no == "AA43"
    assert segment.origin == "CDG" and segment.destination == "JFK"
    assert segment.cabin_class == "economy"
    assert segment.aircraft
    assert offer.outbound.stopovers == len(offer.outbound.segments) - 1


def test_a_deep_search_is_declared_as_deep() -> None:
    provider, _ = provider_with(reply(load("coverage_deep.json")))
    deep = provider.search(one_way(deep_search=True))
    assert deep.observation.provenance.coverage_mode == "deep"
    hidden_provider, _ = provider_with(reply(load("coverage_deep.json")))
    hidden = hidden_provider.search(one_way(show_hidden=True))
    assert hidden.observation.provenance.coverage_mode == "hidden_included"


def test_a_replayed_search_id_is_a_cache_hit_and_not_fresh() -> None:
    """§7 — the second read of the same archive handle is not a new observation."""
    payload = load("cache_cached_1.json")
    provider, _ = provider_with(reply(payload), reply(payload))

    first = provider.search(one_way())
    assert first.cache_hit_detected is False
    assert first.observation.provenance.may_alert_or_verify is True
    assert first.observation.may_alert is True

    second = provider.search(one_way())
    assert second.cache_hit_detected is True
    assert second.observation.provenance.cache_mode == "cached"
    assert second.observation.provenance.observed_at_basis == "client_receipt"
    assert second.observation.provenance.may_alert_or_verify is False
    assert second.observation.may_alert is False
    assert second.ledger.billable_searches == 0
    assert provider.ledger == LedgerCounters(2, 1, 1)


def test_not_asking_for_a_fresh_fetch_is_not_fresh() -> None:
    """§7 rule 2 — provider price context may ride the cache; an observation may not."""
    provider, _ = provider_with(reply(load("deals.json")))
    discovery = provider.discover(DealsRequest(departure_id="CDG", **OPTS))
    assert discovery.observation.provenance.cache_mode == "cached"
    assert discovery.observation.provenance.may_alert_or_verify is False


def test_an_observation_without_an_archive_handle_is_refused() -> None:
    payload = load("coverage_standard.json")
    payload.pop("search_metadata")
    provider, _ = provider_with(reply(payload))
    with pytest.raises(ProviderContractError):
        provider.search(one_way())


def test_the_parser_branches_on_request_kind() -> None:
    """§12.2 — the pin answers under `selected_flights`; the search keys do not."""
    pin = load("pin_selected_flights.json")
    source = load("pin_source_search.json")
    provider, _ = provider_with(reply(pin), reply(source))

    pinned = provider.pin_itinerary(
        SelectedFlightsRequest(
            departure_id="CDG", arrival_id="JFK", outbound_date="2026-12-02",
            outbound_segments=tuple(
                {"departure_id": "CDG", "arrival_id": "JFK", "date": "2026-12-02", "flight_number": "AA43"}
                for _ in range(1)
            ),
        )
    )
    assert pinned is not None
    expected_identity = itinerary_identity(pin["selected_flights"][0])
    assert pinned == expected_identity
    assert pinned and pinned[0].startswith(expected_identity[0].split("@")[0])

    # The same payload read as a *search* yields nothing: the keys are different, not
    # the content. This is the mistake that once read the pin as an empty result.
    assert pin.get("selected_flights") and not pin.get("best_flights")


def test_a_pinned_row_has_no_price_and_so_is_not_an_offer() -> None:
    """§8.1 — a pin is a verification candidate, and inventing a price would be a lie."""
    pin = load("pin_selected_flights.json")
    rows = pin["selected_flights"]
    with pytest.raises(ProviderContractError) as error:
        to_flight_offer(rows[0], currency="EUR")
    assert "no price" in str(error.value)


def test_a_search_result_carries_its_price_context_as_a_claim() -> None:
    provider, _ = provider_with(reply(load("coverage_standard.json")))
    result = provider.search(one_way())
    context = result.price_context
    assert context is not None
    assert context.lowest_price == 348
    assert context.price_level == "low"
    assert context.typical_price_range == (350, 710)
    assert len(context.price_history) == 62
    assert context.price_history[0][1] == 324


# ── P1.2 — the duration invariant ──────────────────────────────────────────

def test_the_provider_total_cannot_reintroduce_the_dropped_layover() -> None:
    """§12.1/D2 — the defect the retired connector shipped, made structurally impossible.

    The fixture is real; one field is then doctored to the historical defect — a
    `total_duration` that is the sum of the flight times with the connection left out.
    The adapter ignores that field entirely, so the mapped route still spans
    gate-to-gate and the layover survives.
    """
    payload = load("coverage_standard.json")
    rows = list(payload.get("best_flights") or []) + list(payload.get("other_flights") or [])
    multi_stop = [row for row in rows if len(row.get("flights") or []) > 1]
    assert multi_stop, "the fixture must contain a multi-stop itinerary for this guard"

    row = multi_stop[0]
    flight_minutes = sum(segment.get("duration") or 0 for segment in row["flights"])
    layovers = row.get("layovers") or []
    layover_minutes = sum(layover.get("duration") or 0 for layover in layovers)
    assert layover_minutes > 0, "the chosen row must actually have a layover to lose"
    assert flight_minutes < flight_minutes + layover_minutes

    row["total_duration"] = flight_minutes  # the historical defect, injected
    for segment in row["flights"]:
        segment["duration"] = 0  # and the provider's segment arithmetic thrown away too

    offer = to_flight_offer(row, currency="EUR")
    route = offer.outbound
    flight_seconds = sum(segment.duration_seconds for segment in route.segments)
    assert all(segment.duration_seconds > 0 for segment in route.segments), (
        "the per-segment durations are computed from the timestamps, not carried over"
    )
    assert route.total_duration_seconds > flight_seconds, (
        "the layover must be inside the published total — this is the exact defect §12.1 records"
    )
    assert route.total_duration_seconds != flight_minutes * 60, (
        "and it is not the provider's dropped-layover number either"
    )


# ── P1.3 — the key and the URL never surface ───────────────────────────────

def test_a_transport_failure_carries_neither_the_key_nor_the_url() -> None:
    def failing_opener(url: str):
        raise urllib.error.URLError(ConnectionRefusedError(111, "refused"))

    transport = SerpApiTransport(KEY, opener=failing_opener)
    with pytest.raises(ProviderFailure) as error:
        transport.search({"engine": "google_flights"})
    text = str(error.value)
    assert KEY not in text
    assert "serpapi.com" not in text and "api_key" not in text


def test_an_echoed_key_is_redacted_from_the_payload() -> None:
    """The account endpoint returns the key itself; it must not survive the transport."""
    payload = {"account_id": "x", "api_key": KEY, "nested": [{"api_key": KEY}], "note": f"see {KEY}"}
    transport = SerpApiTransport(KEY, opener=lambda url: reply(payload))
    returned = transport.account()
    assert KEY not in json.dumps(returned)
    assert returned["api_key"] == "[REDACTED]"
    assert returned["nested"][0]["api_key"] == "[REDACTED]"


def test_no_fixture_contains_the_key() -> None:
    """The probe writes fixtures; this is the guard that they stay clean."""
    for path in FIXTURES.glob("*.json"):
        assert KEY not in path.read_text(encoding="utf-8")


# ── P1.4 — the fail-safe 429 classifier ────────────────────────────────────

def test_the_three_way_429_classification_is_fail_safe() -> None:
    """§11/D4. The two wordings are the documented ones; P0.5's measured bodies are still
    owed, and until they exist this table is the contract."""
    assert classify_429({"error": "Your account has run out of searches."}) == "budget_exhausted"
    assert classify_429({"error": "You have exceeded your hourly throughput limit."}) == "rate_limited"
    assert classify_429({"error": "Too many requests per hour."}) == "rate_limited"
    assert classify_429({"error": "something nobody has seen before"}) == "rate_limit_unknown"
    assert classify_429({}) == "rate_limit_unknown", "an empty body must never be guessed"


def test_a_body_naming_both_causes_is_terminal() -> None:
    body = {"error": "You have exceeded your hourly rate limit and run out of searches."}
    assert classify_429(body) == "budget_exhausted"


def test_the_status_table_and_that_two_hundred_with_an_error_is_still_success() -> None:
    assert classify_status(200, {"error": "Google Flights hasn't returned any results for this query."}) is None
    assert classify_status(400) == "validation"
    assert classify_status(401) == "auth_required"
    assert classify_status(403) == "business"
    assert classify_status(410) == "business"
    assert classify_status(500) == "transient"
    assert classify_status(503) == "transient"


def test_retry_after_is_honoured_when_present_and_never_required() -> None:
    provider, _ = provider_with(
        reply({"error": "Too many requests per hour."}, status=429, headers={"Retry-After": "30"}),
        reply({"error": "Too many requests per hour."}, status=429),
    )
    with pytest.raises(ProviderFailure) as first:
        provider.search(one_way())
    assert first.value.category == "rate_limited"
    assert first.value.retryable is True and first.value.retry_after == 30.0

    with pytest.raises(ProviderFailure) as second:
        provider.search(one_way())
    assert second.value.retry_after is None, "the header is a signal, never a dependency"


def test_a_quota_429_is_terminal_and_moves_health_to_untrusted() -> None:
    provider, _ = provider_with(reply({"error": "Your account has run out of searches."}, status=429))
    with pytest.raises(ProviderFailure) as error:
        provider.search(one_way())
    assert error.value.category == "budget_exhausted"
    assert error.value.terminal is True and error.value.retryable is False
    assert provider.health == "UNTRUSTED"
    assert provider.ledger.billable_searches == 0, "a refused request is not billable (§10)"


def test_a_transient_failure_degrades_but_does_not_untrust() -> None:
    provider, _ = provider_with(reply({}, status=503))
    with pytest.raises(ProviderFailure):
        provider.search(one_way())
    assert provider.health == "DEGRADED"


# ── P1.5 — completeness answers to the declared coverage ───────────────────

def test_an_empty_success_is_not_confirmed_empty_under_unknown_coverage() -> None:
    """§7.1 — the two cells that used to be conflated."""
    empty = load("empty_search.json")
    provider, _ = provider_with(reply(empty), reply(empty))

    standard = provider.search(one_way())
    assert standard.observation.result_state == "confirmed_empty"
    assert standard.observation.row_count == 0
    assert standard.observation.may_alert is False
    assert standard.error_field_present is True, (
        "the provider puts an `error` string on an empty Success — measured, and the "
        "reason classification reads search_metadata.status instead"
    )

    unknown = provider.discover(TravelExploreRequest(departure_id="CDG", **OPTS))
    assert unknown.observation.result_state == "unknown", (
        "explore's populated shape is unmeasured, so an empty answer is not evidence"
    )


# ── P1.6 / P2.4 — provider price context cannot become a baseline ──────────

def test_price_context_is_a_claim_with_no_way_to_rank_it() -> None:
    provider, _ = provider_with(reply(load("coverage_standard.json")))
    context = provider.search(one_way()).price_context
    assert context is not None
    assert context.is_rankable is False
    assert not isinstance(context, dict)
    with pytest.raises(TypeError):
        context < 100  # type: ignore[operator]
    assert (context == 100) is False, (
        "equality is allowed and is always False: a claim is not a number, and there "
        "is no ordering to rank it with"
    )
    assert not any(hasattr(context, name) for name in ("mean", "median", "sort_key", "as_baseline"))


# ── P1.7 — enum traps, request models and identity normalisation ───────────

def test_stops_is_named_rather_than_passed_as_a_count() -> None:
    assert (int(Stops.ANY), int(Stops.NONSTOP), int(Stops.AT_MOST_ONE), int(Stops.AT_MOST_TWO)) == (0, 1, 2, 3)
    assert one_way(stops=Stops.NONSTOP).to_params()["stops"] == 1
    assert one_way().to_params()["stops"] is None


def test_the_two_travel_duration_enums_number_differently() -> None:
    """§12.2 — same integers, different meanings; sharing one enum would be a bug."""
    assert DealsTravelDuration.WEEKEND == 2 and ExploreTravelDuration.WEEKEND == 1
    assert DealsTravelDuration.ONE_WEEK == 1 and ExploreTravelDuration.ONE_WEEK == 2
    assert DealsTravelDuration is not ExploreTravelDuration


def test_the_request_models_make_illegal_combinations_unrepresentable() -> None:
    """§12.3 — a one-way request has no return date to fill in wrongly."""
    one = one_way()
    assert not hasattr(one, "return_date")
    assert not hasattr(one, "departure_token")
    assert one.to_params()["type"] == 2
    assert "return_date" not in one.to_params()

    round_trip = OneWayRequest(**{**dict(departure_id="CDG", arrival_id="JFK", outbound_date="2026-12-02", **OPTS)})
    assert not hasattr(round_trip, "selected_flights_json")

    pin = SelectedFlightsRequest(
        departure_id="CDG", arrival_id="JFK", outbound_date="2026-12-02",
        outbound_segments=({"departure_id": "CDG", "arrival_id": "JFK", "date": "2026-12-02", "flight_number": "AA43"},),
    )
    params = pin.to_params()
    assert json.loads(params["selected_flights_json"]) == {
        "outbound": [{"departure_id": "CDG", "arrival_id": "JFK", "date": "2026-12-02", "flight_number": "AA43"}]
    }
    assert params["type"] == 2, "a one-way pin must say it is one-way (measured 400 otherwise)"
    assert not hasattr(pin, "booking_token") and not hasattr(pin, "multi_city_json")


def test_flight_number_formatting_matches_across_the_two_response_shapes() -> None:
    """§12.2 — the pin writes `B6 1408` where the search writes `B61408`."""
    assert normalise_flight_number("B6 1408") == normalise_flight_number("B61408") == "B61408"

    raw = {"flight_number": "B6 1408", "departure_airport": {"id": "CDG", "time": "2026-12-02 11:25"},
           "arrival_airport": {"id": "JFK", "time": "2026-12-02 13:50"}}
    mapped = to_flight_offer(
        {"price": 100, "flights": [raw]}, currency="EUR",
    ).outbound.segments[0]
    assert segment_identity(raw) == route_segment_identity(mapped)
    assert segment_identity(raw) == "B61408@2026-12-02 CDG-JFK"


def test_the_local_time_parser_accepts_what_the_provider_sends() -> None:
    parsed = parse_local_time("2026-12-02 11:25")
    assert parsed is not None and parsed.isoformat() == "2026-12-02T11:25:00"
    with_seconds = parse_local_time("2026-12-02T11:25:30")
    assert with_seconds is not None and with_seconds.second == 30
    assert parse_local_time("") is None and parse_local_time(None) is None
    assert parse_local_time("not a time") is None


# ── P2.3 — the ledger ──────────────────────────────────────────────────────

def test_the_ledger_separates_requests_searches_and_billable_searches() -> None:
    assert ledger_counters_for("success_with_results") == LedgerCounters(1, 1, 1)
    assert ledger_counters_for("cache_hit") == LedgerCounters(1, 0, 0)

    provider, _ = provider_with(
        reply(load("coverage_standard.json")),
        reply(load("coverage_standard.json")),  # same search id → a replayed handle
        reply({}, status=503),
    )
    provider.search(one_way())
    provider.search(one_way())
    with pytest.raises(ProviderFailure):
        provider.search(one_way())

    assert provider.ledger == LedgerCounters(provider_requests=3, provider_searches=1, billable_searches=1)


def test_the_account_endpoint_is_available_for_reconciliation_only() -> None:
    provider, _ = provider_with(reply({"plan_id": "starter_v4", "this_month_usage": 7, "account_rate_limit_per_hour": 200}))
    account = provider.account()
    assert account["plan_id"] == "starter_v4"
    assert provider.ledger.billable_searches == 0, "reading the account spends nothing and counts nothing"
