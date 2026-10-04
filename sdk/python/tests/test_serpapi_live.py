"""Live acceptance probe for the SerpApi Google Flights provider lane.

Design: ``docs/serpapi-provider-design.md`` §17. This is P0.1/P0.5/P0.7 of
``docs/serpapi-provider-implementation.md``.

It is marked ``live``, so the deterministic Tier-1 gate (``pytest -m "not live"``,
the ``python-deterministic`` CI job) never fires it, and it refuses to run at all
unless **both** of these hold:

* ``SERPAPI_KEY`` is set. The adapter resolves exactly that name and refuses
  Serper-shaped variants (design §14, D18); the probe follows the same rule so a
  mis-filed key cannot make a local run look healthy.
* ``LETSFG_SERPAPI_PROBE=1`` is set. Without the explicit opt-in, the module
  skips even with a valid key, because every search here consumes quota.

Cost, paid once per run: about ten searches (design §17's single-digit estimate
plus the empty-result probe). The limiter probe (P0.5) costs far more and has its
own second opt-in, ``LETSFG_SERPAPI_PROBE_LIMITER=1``.

Two rules the code below exists to keep:

* **The key never leaves the process.** As a query parameter it is part of the
  request URL, so no URL is ever logged, printed, embedded in an exception, or
  written to a fixture.
* **Absence is not evidence** (design §3.5). A field the provider omitted is
  recorded as ``null`` in the capability profile, never inferred.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path

import pytest

pytestmark = pytest.mark.live

SEARCH_URL = "https://serpapi.com/search"
ACCOUNT_URL = "https://serpapi.com/account"
OPT_IN = "LETSFG_SERPAPI_PROBE"
LIMITER_OPT_IN = "LETSFG_SERPAPI_PROBE_LIMITER"
REDACTED = "[REDACTED]"
USER_AGENT = "LetsFG-SerpApiProbe/0.1 (+https://letsfg.co)"

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "serpapi"
PROFILE_PATH = FIXTURE_DIR / "capability_profile.json"

# Kept small enough to commit, large enough to test the parser against real
# multi-stop itineraries with layovers (P1.2).
MAX_ITINERARIES_KEPT = 3
HTTP_TIMEOUT = 90


# ── credential handling ────────────────────────────────────────────────────

def _api_key() -> str:
    key = os.environ.get("SERPAPI_KEY", "").strip()
    if not key:
        pytest.skip(
            "SERPAPI_KEY is not set. The adapter resolves exactly this name and "
            "never falls back to a Serper-shaped variant (design §14, D18)."
        )
    return key


@pytest.fixture(scope="module", autouse=True)
def _require_opt_in() -> None:
    if os.environ.get(OPT_IN, "") != "1":
        pytest.skip(
            f"{OPT_IN} is not set to 1. This probe spends quota on every search, "
            "so it never runs by accident — not in CI, not from a bare `pytest`."
        )


# Every /search call spends quota, so the probe counts its own calls. The account
# endpoint's counters are known to lag (§10, D20), and comparing issued-against-
# counted is the only way to see that from a single run.
_SEARCHES_ISSUED = 0


@pytest.fixture(scope="module")
def account_baseline(_require_opt_in) -> dict:
    """The account state before this module issues any search. Free (§4.3)."""
    return _account()


def _scrub(text: str) -> str:
    key = os.environ.get("SERPAPI_KEY", "")
    return text.replace(key, REDACTED) if key else text


def _redact(value):
    """Drop credential material wherever it appears in a provider payload."""
    if isinstance(value, dict):
        return {
            k: (REDACTED if k in {"api_key", "api_token"} else _redact(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact(v) for v in value]
    if isinstance(value, str):
        return _scrub(value)
    return value


def _request(params: dict) -> tuple[int, dict, dict]:
    """GET /search. Returns (status, redacted payload, headers).

    The URL — and therefore the key — never reaches a log, a message or a
    fixture. Every failure is re-raised with a redacted message.
    """
    key = _api_key()
    global _SEARCHES_ISSUED
    _SEARCHES_ISSUED += 1
    query = {k: v for k, v in params.items() if v is not None}
    query["api_key"] = key
    url = f"{SEARCH_URL}?{urllib.parse.urlencode(query)}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
            return response.status, _redact(json.loads(_scrub(response.read().decode()))), dict(response.headers)
    except urllib.error.HTTPError as error:
        body = _scrub(error.read().decode(errors="replace"))
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = {"_unparsed_body": body[:4000]}
        return error.code, _redact(payload), dict(error.headers)
    except urllib.error.URLError as error:
        raise AssertionError(f"transport failure: {type(error.reason).__name__}") from None


def _account() -> dict:
    """GET /account — free, and never counted against the plan (§4.3)."""
    key = _api_key()
    request = urllib.request.Request(
        f"{ACCOUNT_URL}?api_key={urllib.parse.quote(key)}",
        headers={"User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
        return _redact(json.loads(_scrub(response.read().decode())))


# ── fixture helpers ────────────────────────────────────────────────────────

def _search_payload(engine: str, **params) -> dict:
    payload = {"engine": engine}
    payload.update(params)
    status, body, headers = _request(payload)
    assert status == 200, f"{engine} returned HTTP {status}: {body}"
    return body


def _write_fixture(name: str, payload: dict, note: str) -> Path:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    path = FIXTURE_DIR / name
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    return path


def _trim(payload: dict) -> tuple[dict, str]:
    """Cap the itinerary lists so a fixture stays committable.

    Everything that decides a contract — ``search_metadata``, ``search_parameters``
    (redacted), ``price_insights``, the counts — is kept verbatim.
    """
    trimmed = json.loads(json.dumps(payload))
    kept, dropped = [], 0
    for key in ("best_flights", "other_flights"):
        rows = trimmed.get(key) or []
        kept.extend(rows[:MAX_ITINERARIES_KEPT])
        if len(rows) > MAX_ITINERARIES_KEPT:
            dropped += len(rows) - MAX_ITINERARIES_KEPT
            trimmed[key] = rows[:MAX_ITINERARIES_KEPT]
    note = (
        f"itinerary lists truncated to {MAX_ITINERARIES_KEPT} per list "
        f"({dropped} rows dropped); all other fields verbatim"
    )
    trimmed["_probe_note"] = note
    return trimmed, note


def _itineraries(payload: dict) -> list[dict]:
    return list(payload.get("best_flights") or []) + list(payload.get("other_flights") or [])


def _identity(itinerary: dict) -> list[str]:
    return [
        " ".join(str(flight.get(field, "")) for field in ("airline", "flight_number")).strip()
        for flight in itinerary.get("flights", [])
    ]


def _price(itinerary: dict) -> int | None:
    return itinerary.get("price")


def _row_counts(payload: dict) -> dict:
    return {
        "best_flights": len(payload.get("best_flights") or []),
        "other_flights": len(payload.get("other_flights") or []),
        "selected_flights": len(payload.get("selected_flights") or []),
    }


def _load(name: str) -> dict:
    path = FIXTURE_DIR / name
    if not path.exists():
        pytest.skip(f"{name} is missing — run the whole probe module, not one test")
    return json.loads(path.read_text(encoding="utf-8"))


def _departure_date() -> str:
    return (date.today() + timedelta(days=60)).isoformat()


def _one_way(**extra) -> dict:
    params = {
        "departure_id": "CDG",
        "arrival_id": "JFK",
        "type": 2,
        "outbound_date": _departure_date(),
        "currency": "EUR",
        "hl": "en",
    }
    params.update(extra)
    return params


# ── P0.2 / P0.4 — cache behaviour and the observation timestamp ────────────

def test_cache_behaviour_and_metadata() -> None:
    """§17 experiment A, plus P0.4: is a cache hit detectable, and is there a
    trustworthy observation timestamp?

    Order matters and is the design's: `Q`, `Q`, then `Q + no_cache=true`. A
    `no_cache` call does not populate the entry a later cached call reads, so
    putting it first (as an earlier version of this probe did) compares two
    different cache entries and answers a different question.
    """
    cached_first = _search_payload("google_flights", **_one_way())
    cached_second = _search_payload("google_flights", **_one_way())
    forced = _search_payload("google_flights", **_one_way(no_cache="true"))

    first_meta = cached_first.get("search_metadata", {})
    second_meta = cached_second.get("search_metadata", {})
    forced_meta = forced.get("search_metadata", {})

    observed = {
        "cache_detectable": bool(first_meta.get("id")),
        "cache_hit_same_search_id": bool(
            second_meta.get("id") and second_meta.get("id") == first_meta.get("id")
        ),
        "cache_hit_same_created_at": bool(
            second_meta.get("created_at") and second_meta.get("created_at") == first_meta.get("created_at")
        ),
        "no_cache_returns_new_search_id": forced_meta.get("id") != first_meta.get("id"),
        "provider_timestamp": (
            "created_at / processed_at (UTC) — provider FETCH time, not upstream observation time"
            if first_meta.get("created_at") and first_meta.get("processed_at")
            else None
        ),
        "metadata_keys": sorted(first_meta),
    }

    for name, payload in (("cache_cached_1.json", cached_first),
                          ("cache_cached_2.json", cached_second),
                          ("cache_no_cache.json", forced)):
        trimmed, note = _trim(payload)
        trimmed["_probe_observed"] = observed
        _write_fixture(name, trimmed, note)

    print(
        f"cache: cached pair shares id: {observed['cache_hit_same_search_id']} "
        f"(created_at shared: {observed['cache_hit_same_created_at']}); "
        f"no_cache differs: {observed['no_cache_returns_new_search_id']}; "
        f"metadata keys: {', '.join(observed['metadata_keys'])}"
    )
    assert observed["cache_detectable"]


# ── P0.3 — semantic equivalence of the pinned itinerary ────────────────────

def test_pinned_itinerary_semantics() -> None:
    """§17 experiment B: the pin answers *same itinerary? same fare? same price?*"""
    search = _search_payload("google_flights", **_one_way(no_cache="true"))
    itineraries = _itineraries(search)
    if not itineraries:
        pytest.skip("no itineraries on the probe route — nothing to pin")

    chosen = min(itineraries, key=lambda row: row.get("price") or 10**9)
    chosen_identity = _identity(chosen)
    chosen_price = _price(chosen)

    pin_status, pin, _pin_headers = _request(
        {
            "engine": "google_flights",
            "departure_id": "CDG",
            "arrival_id": "JFK",
            "outbound_date": _departure_date(),
            # Measured: without `type` the pin defaults to round trip and answers
            # 400 "`selected_flights_json` is missing the `return` flights array".
            "type": 2,
            "selected_flights_json": json.dumps(
                {
                    "outbound": [
                        {
                            "departure_id": segment.get("departure_airport", {}).get("id"),
                            "arrival_id": segment.get("arrival_airport", {}).get("id"),
                            "date": segment.get("departure_airport", {}).get("time", "")[:10],
                            "flight_number": str(segment.get("flight_number", "")).replace(" ", ""),
                        }
                        for segment in chosen.get("flights", [])
                    ]
                }
            ),
        }
    )

    pinned = (pin.get("selected_flights") or [None])[0]
    observed = {
        "pin_http_status": pin_status,
        "pin_status": pin.get("search_metadata", {}).get("status"),
        "pin_returns_selected_flights": bool(pin.get("selected_flights")),
        "pin_returns_price": bool(pinned and pinned.get("price") is not None),
        "pin_identity_matches_search": bool(pinned and _identity(pinned) == chosen_identity),
        "search_price": chosen_price,
        "pin_row_keys": sorted(pinned) if pinned else [],
    }

    trimmed, note = _trim(pin)
    trimmed["_probe_observed"] = observed
    trimmed["_probe_chosen_identity"] = chosen_identity
    _write_fixture("pin_selected_flights.json", trimmed, note)

    trimmed_search, search_note = _trim(search)
    trimmed_search["_probe_observed"] = {"source": "search A for the pin comparison"}
    _write_fixture("pin_source_search.json", trimmed_search, search_note)

    print(
        f"pin: selected_flights present: {observed['pin_returns_selected_flights']}; "
        f"carries a price: {observed['pin_returns_price']}; "
        f"identity matches: {observed['pin_identity_matches_search']}"
    )


# ── P0.8 — coverage delta and the duration invariant ───────────────────────

def test_coverage_delta_and_durations() -> None:
    """§17 item 6, re-checking §12.1 against live data."""
    standard = _search_payload("google_flights", **_one_way(no_cache="true"))
    deep = _search_payload("google_flights", **_one_way(no_cache="true", deep_search="true"))

    standard_rows = _itineraries(standard)
    deep_rows = _itineraries(deep)

    def layover_inclusive_matches(rows: list[dict]) -> tuple[int, int]:
        matched = total = 0
        for itinerary in rows:
            total_duration = itinerary.get("total_duration")
            if total_duration is None:
                continue
            total += 1
            segment_sum = sum(
                segment.get("duration") or 0 for segment in itinerary.get("flights", [])
            )
            layover_sum = sum(
                layover.get("duration") or 0 for layover in itinerary.get("layovers") or []
            )
            if abs(total_duration - (segment_sum + layover_sum)) <= 1:
                matched += 1
        return matched, total

    matched, total = layover_inclusive_matches(standard_rows)
    observed = {
        "coverage_delta_deep": len(deep_rows) - len(standard_rows),
        "standard_rows": len(standard_rows),
        "deep_rows": len(deep_rows),
        "duration_totals_match_own_segments": (matched == total) if total else None,
        "duration_samples": total,
        "duration_matches": matched,
    }

    for name, payload in (("coverage_standard.json", standard), ("coverage_deep.json", deep)):
        trimmed, note = _trim(payload)
        trimmed["_probe_observed"] = observed
        _write_fixture(name, trimmed, note)

    print(
        f"coverage: standard {len(standard_rows)} rows, deep {len(deep_rows)} rows "
        f"(delta {observed['coverage_delta_deep']}); durations matched "
        f"{matched}/{total}"
    )


# ── P0.6 — provider price context ──────────────────────────────────────────

def test_price_context_shape() -> None:
    """§17 item 3/4: the `price_level` enum, and `price_history`'s granularity."""
    payload = _load("coverage_standard.json")
    insights = payload.get("price_insights") or {}
    history = insights.get("price_history") or []

    def stamp_of(entry):
        """Measured shape: `price_history` rows are `[timestamp, price]` arrays."""
        if isinstance(entry, dict):
            return entry.get("timestamp")
        if isinstance(entry, (list, tuple)) and entry:
            return entry[0]
        return None

    stamps = [stamp for stamp in (stamp_of(entry) for entry in history) if isinstance(stamp, int)]
    steps = sorted({later - earlier for earlier, later in zip(stamps, stamps[1:])})
    observed = {
        "price_level_values": sorted({insights["price_level"]}) if insights.get("price_level") else None,
        "price_history_granularity_seconds": steps[0] if len(steps) == 1 else (steps or None),
        "price_history_points_observed": len(history) or None,
        "price_history_entry_shape": (
            f"array [{type(stamp_of(history[0])).__name__}, {type((history[0][1] if isinstance(history[0], list) else None)).__name__}]"
            if history else None
        ),
        "typical_price_range_present": bool(insights.get("typical_price_range")),
        "lowest_price_present": insights.get("lowest_price") is not None,
    }
    payload["_probe_observed"] = observed
    _write_fixture("price_context.json", payload, "copy of the standard-coverage fixture with the price-context observation attached")

    print(
        f"price context: level={observed['price_level_values']} "
        f"granularity={observed['price_history_granularity_seconds']}s "
        f"points={observed['price_history_points_observed']}"
    )


# ── P0.7 (second half) — is an empty-but-successful search billable? ───────

def test_empty_search_billing(account_baseline: dict) -> None:
    """§17 item 5, plus a classification trap this probe found.

    `include_airlines` carrying a code no airline uses filters every itinerary out,
    so the provider answers HTTP 200 with `search_metadata.status: Success` and no
    rows — **and a top-level `error` string anyway** (measured). An empty success is
    therefore not distinguishable from a failure by the presence of `error`; only
    `search_metadata.status` decides (§11, and the adaptation recorded in §4.2).

    Two empty searches, not one: the account counters lag (§10, D20), so a single
    delta of zero cannot be told apart from "not counted yet". The premise is
    asserted rather than assumed — an earlier version of this probe measured two
    searches that were *not* empty and reported a billability verdict that meant
    nothing.
    """
    usage_before = _account().get("this_month_usage")
    first = _search_payload("google_flights", **_one_way(no_cache="true", include_airlines="ZZ"))
    second = _search_payload("google_flights", **_one_way(no_cache="true", include_airlines="YX"))
    time.sleep(5)
    immediate = _account()
    time.sleep(15)
    settled = _account()

    usage_immediate = immediate.get("this_month_usage")
    usage_settled = settled.get("this_month_usage")

    def empty_of(payload: dict) -> bool:
        rows = _row_counts(payload)
        return sum(rows[key] for key in ("best_flights", "other_flights")) == 0

    def error_of(payload: dict) -> str | None:
        return payload.get("error")

    delta = (
        usage_settled - usage_before
        if isinstance(usage_before, int) and isinstance(usage_settled, int)
        else None
    )
    baseline_usage = account_baseline.get("this_month_usage")
    expected_total = (
        baseline_usage + _SEARCHES_ISSUED if isinstance(baseline_usage, int) else None
    )
    counter_lag = (
        expected_total - usage_immediate
        if expected_total is not None and isinstance(usage_immediate, int)
        else None
    )

    observed = {
        "both_empty": empty_of(first) and empty_of(second),
        "statuses": [
            first.get("search_metadata", {}).get("status"),
            second.get("search_metadata", {}).get("status"),
        ],
        "error_field_present_on_empty_success": [error_of(first), error_of(second)],
        "row_counts": _row_counts(first),
        "usage_before": usage_before,
        "usage_immediate": usage_immediate,
        "usage_settled": usage_settled,
        "usage_delta_for_two_empty_searches": delta,
        "searches_issued_this_run": _SEARCHES_ISSUED,
        "counter_lag_searches_at_first_read": counter_lag,
        # Ambiguity is preserved: `>= 1` is proof of billing; zero under a lagging
        # counter is not proof of the opposite, so it stays null rather than false.
        "empty_search_billable": (delta >= 1) if isinstance(delta, int) else None,
    }

    trimmed, note = _trim(first)
    trimmed["_probe_observed"] = observed
    _write_fixture("empty_search.json", trimmed, note)

    print(
        f"empty search: statuses={observed['statuses']} empty={observed['both_empty']} "
        f"error field={observed['error_field_present_on_empty_success']!r} "
        f"usage {usage_before} → {usage_immediate} → {usage_settled} "
        f"(delta {delta} for 2 searches, counter lag {counter_lag}); "
        f"billable: {observed['empty_search_billable']}"
    )
    assert observed["both_empty"], "the empty-result premise failed — the filter returned rows"
    assert all(status == "Success" for status in observed["statuses"]), (
        "the empty-result case must be a provider Success, not an error"
    )


# ── P0.5 — the 429 bodies. Opt-in twice, and the second one is expensive ───

def test_limiter_429_bodies() -> None:
    """§17 item 2, feeds D4/P1.4.

    Provoking the hourly limiter means sending requests until the account's
    throughput ceiling refuses one; the account value is a *ceiling*, the
    documented floor is lower (design §4.3), so the count needed is not known in
    advance. That is why this carries its own opt-in on top of the module one.
    """
    if os.environ.get(LIMITER_OPT_IN, "") != "1":
        pytest.skip(
            f"{LIMITER_OPT_IN} is not set to 1. Provoking a 429 can consume a large "
            "slice of the monthly plan and needs an explicit owner decision "
            "(implementation plan P0.5)."
        )

    ceiling = _account().get("account_rate_limit_per_hour") or 60
    budget = int(os.environ.get("LETSFG_SERPAPI_PROBE_LIMITER_MAX", str(ceiling + 5)))

    bodies = []
    for attempt in range(1, budget + 1):
        status, payload, headers = _request(
            {"engine": "google_flights", **_one_way(outbound_date=(date.today() + timedelta(days=61)).isoformat())}
        )
        if status == 429:
            bodies.append(
                {
                    "attempt": attempt,
                    "http_status": status,
                    "body": payload,
                    "body_text": json.dumps(payload)[:2000],
                    "retry_after": headers.get("Retry-After"),
                    "headers_seen": sorted(headers),
                }
            )
            break
        time.sleep(0.5)
    else:
        pytest.skip(f"no 429 within {budget} requests — the ceiling was not reached")

    observed = {
        "retry_after_present": any(row["retry_after"] for row in bodies),
        "retry_after_value": next((row["retry_after"] for row in bodies if row["retry_after"]), None),
        "bodies": bodies,
    }
    _write_fixture("limiter_429.json", observed, "verbatim 429 body and headers, redacted")
    print(f"429 after {bodies[0]['attempt']} requests; Retry-After: {observed['retry_after_value']!r}")


# ── the capability profile ─────────────────────────────────────────────────

def test_write_capability_profile() -> None:
    """P0.1's primary artefact: one machine-readable profile, every value citing
    the fixture that showed it, `null` for anything the probe could not decide."""
    account = _account()
    sources = {name: _load(name) for name in (
        "cache_cached_1.json",
        "cache_cached_2.json",
        "pin_selected_flights.json",
        "coverage_standard.json",
        "coverage_deep.json",
        "price_context.json",
        "empty_search.json",
    )}

    def observed_of(name: str) -> dict:
        return sources[name].get("_probe_observed", {})

    cache = observed_of("cache_cached_1.json")
    pin = observed_of("pin_selected_flights.json")
    coverage = observed_of("coverage_standard.json")
    empty = observed_of("empty_search.json")
    limiter = None
    limiter_path = FIXTURE_DIR / "limiter_429.json"
    if limiter_path.exists():
        limiter = json.loads(limiter_path.read_text(encoding="utf-8"))

    observed = {
        "cache_detectable": cache.get("cache_detectable"),
        "cache_hit_same_search_id": cache.get("cache_hit_same_search_id"),
        "provider_timestamp": cache.get("provider_timestamp"),
        "retry_after_present": limiter.get("retry_after_present") if limiter else None,
        "selected_flights_same_itinerary": pin.get("pin_identity_matches_search"),
        "selected_flights_returns_price": pin.get("pin_returns_price"),
        "price_level_values": observed_of("price_context.json").get("price_level_values"),
        "price_history_granularity_seconds": observed_of("price_context.json").get(
            "price_history_granularity_seconds"
        ),
        "price_history_points_observed": observed_of("price_context.json").get(
            "price_history_points_observed"
        ),
        "empty_search_billable": empty.get("empty_search_billable"),
        "coverage_delta_deep": coverage.get("coverage_delta_deep"),
        # Only ever false when the run *caught* the counter behind the number of
        # searches it had issued; a run that saw the counter agree cannot establish
        # reliability from one sample, and D20 measured it failing across a batch.
        "ledger_from_account_endpoint_reliable": (
            False if (empty.get("counter_lag_searches_at_first_read") or 0) > 0 else None
        ),
        "duration_totals_match_own_segments": coverage.get("duration_totals_match_own_segments"),
    }

    citations = {
        "cache_detectable": "cache_cached_1.json search_metadata.id",
        "cache_hit_same_search_id": "cache_cached_2.json vs cache_cached_1.json id and created_at",
        "provider_timestamp": "cache_cached_1.json search_metadata.created_at/processed_at",
        "retry_after_present": (
            "limiter_429.json headers" if limiter else "not probed — needs the limiter opt-in (P0.5)"
        ),
        "selected_flights_same_itinerary": "pin_selected_flights.json vs pin_source_search.json",
        "selected_flights_returns_price": "pin_selected_flights.json selected_flights[0].price",
        "price_level_values": "price_context.json price_insights.price_level",
        "price_history_granularity_seconds": "price_context.json price_insights.price_history timestamps",
        "price_history_points_observed": "price_context.json price_insights.price_history length",
        "empty_search_billable": "empty_search.json usage delta for two empty no_cache searches",
        "coverage_delta_deep": "coverage_deep.json vs coverage_standard.json row counts",
        "ledger_from_account_endpoint_reliable": (
            "empty_search.json counter_lag_searches_at_first_read — searches issued "
            "this run minus the usage the account had counted; null when the counter "
            "agreed, because one agreeing sample does not establish reliability"
        ),
        "duration_totals_match_own_segments": "coverage_standard.json total_duration vs segments + layovers",
    }

    profile = {
        "provider": "serpapi_google",
        "probed_at": date.today().isoformat(),
        "probe": "sdk/python/tests/test_serpapi_live.py",
        "probe_route": {"departure_id": "CDG", "arrival_id": "JFK", "outbound_date": _departure_date()},
        "account": {
            "plan_id": account.get("plan_id"),
            "plan_name": account.get("plan_name"),
            "searches_per_month": account.get("searches_per_month"),
            "account_rate_limit_per_hour": account.get("account_rate_limit_per_hour"),
            "plan_searches_left": account.get("plan_searches_left"),
        },
        "observed": observed,
        "citations": citations,
        "unresolved": sorted(key for key, value in observed.items() if value is None),
    }

    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    PROFILE_PATH.write_text(json.dumps(profile, indent=2) + "\n", encoding="utf-8")

    print(f"capability profile written to {PROFILE_PATH.name}; unresolved: {profile['unresolved']}")
    assert profile["account"]["plan_id"], "the account endpoint must answer with a plan"
