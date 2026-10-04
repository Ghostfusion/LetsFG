#!/usr/bin/env python3
"""Scan Google Flights (through SerpApi) for cheap premium-cabin round trips from ORD.

    python tools/flight_scan.py --dry-run     # print the plan and the search estimate
    python tools/flight_scan.py               # run the brief below
    python tools/flight_scan.py --max-price 6000 --verify 6   # widen, and fetch returns

The SDK path and the `.env` are resolved from this file, so it runs from any
working directory.

Defaults encode the brief:

    origin        ORD
    destinations  PEK, HKG, DLC
    departure     on or after 2026-10-29   (first candidate 2026-10-30)
    return        3-5 weeks after departure (offsets 21, 28, 35 days)
    stops         at most 1
    budget        <= USD 3000, round trip
    cabin         business, on the *longer* leg of each direction

The cabin requirement is on the long leg, not on every segment: a mixed-cabin
itinerary passes when the longest segment of each direction — the transpacific one,
here — is business. Length is the provider's per-segment block time in minutes, the
only measured length in the payload; it is the proxy for distance and is printed
next to each segment.

Measured 2026-10-03: First class with at most one stop on ORD-PEK/HKG/DLC returns
**no itineraries at all** (`error: "Google Flights hasn't returned any results for
this query."`) — no carrier sells a true First cabin on those routes. Business
returns candidates (29 on one date, cheapest $6,804), and the ones near the budget
are mixed-cabin: `ORD→SFO` in First connecting to `SFO→HKG` in Business. Those are
exactly the shape this filter accepts.

Why two requests per itinerary
------------------------------
Google Flights round trip is not one search. A `type=1` search returns *outbound*
options, each carrying a `departure_token` and the round-trip total price; the
matching return options come from a second request that repeats the search with
that token. SerpApi documents this on google-flights-api.md:

    "To obtain the returning flight information for Round Trip (1), you need to
     make another request using a departure_token."

So each candidate pair costs one search, every itinerary the script decides to
verify costs one more, and cached repeats are free.

Why this talks to the transport directly
----------------------------------------
The repo's frozen request models (`RoundTripRequest`, `NextLegRequest`) do not
carry `sort_by` or `max_price`, and `NextLegRequest` drops `travel_class`/`stops`
from the follow-up. Cabin is the whole point of this scan, so the two calls are
built explicitly here and routed through `SerpApiTransport`, which is the one
place the key touches the wire and the one place that redacts it.

Cost
----
Counted before the run and reported after it. `SERPAPI_KEY` is read from the
environment, or from a `.env` beside the repo root. The key is never printed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

# ── defaults ───────────────────────────────────────────────────────────────

ORIGIN = "ORD"
DESTINATIONS = ("PEK", "HKG", "DLC")
FIRST_DEPARTURE = "2026-10-29"          # first candidate is the day after this
DEP_STEP_DAYS = 7
DEP_COUNT = 8
RETURN_OFFSETS = (21, 28, 35)           # 3-5 weeks
MAX_PRICE = 3000.0
MAX_STOPS = 1                           # a ceiling, not a count
CABIN = "business"                      # required on the longer leg of each direction
DEFAULT_VERIFY = 12                     # how many outbounds get the follow-up request

#: SerpApi's `travel_class` request values.
TRAVEL_CLASS_PARAM = {"economy": 1, "premium": 2, "business": 3, "first": 4}
#: How a response segment spells each cabin. Prefix match; only measured spellings.
CABIN_ALIASES = {
    "economy": ("economy",),
    "premium": ("premium",),
    "business": ("business",),
    "first": ("first",),                # the provider writes "First Class"
}
#: SerpApi's `stops` is a ceiling: 0 = any, 1 = nonstop, 2 = <=1, 3 = <=2.
STOPS_PARAM = {0: 1, 1: 2, 2: 3, 3: 0}


def _repo_root() -> Path:
    """The checkout this script sits in, found from the file, not the caller."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "sdk" / "python" / "letsfg" / "connectors" / "serpapi_google.py").is_file():
            return parent
    return Path.cwd()


REPO_ROOT = _repo_root()
SDK_CANDIDATES = (REPO_ROOT / "sdk" / "python",)
ENV_CANDIDATES = (REPO_ROOT / ".env", Path.cwd() / ".env")


# ── setup ──────────────────────────────────────────────────────────────────

def _load_env(paths) -> None:
    """Fill os.environ from the first .env that exists. Existing values win."""
    for path in paths:
        if not path or not Path(path).is_file():
            continue
        for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, _, value = line.partition("=")
            name = name.strip()
            value = value.strip().strip('"').strip("'")
            if name and name not in os.environ:
                os.environ[name] = value
        return


def _add_sdk(paths) -> Path:
    for candidate in paths:
        if candidate and (candidate / "letsfg" / "connectors" / "serpapi_google.py").is_file():
            sys.path.insert(0, str(candidate))
            return candidate
    raise SystemExit(
        "could not find sdk/python. Run this from the repository root, or pass "
        "--sdk-path <repo>/sdk/python."
    )


# ── payload reading ────────────────────────────────────────────────────────

def _cabin_matches(cabin: str, wanted: str) -> bool:
    text = cabin.strip().lower()
    return any(text.startswith(alias) for alias in CABIN_ALIASES[wanted])


def _segments(row) -> list[dict]:
    out = []
    for seg in row.get("flights") or []:
        departure = seg.get("departure_airport") or {}
        arrival = seg.get("arrival_airport") or {}
        out.append({
            "flight": " ".join(str(seg.get("flight_number") or "").split()),
            "airline": str(seg.get("airline") or ""),
            "from": str(departure.get("id") or ""),
            "to": str(arrival.get("id") or ""),
            "depart": str(departure.get("time") or ""),
            "arrive": str(arrival.get("time") or ""),
            "cabin": str(seg.get("travel_class") or "").strip(),
            "aircraft": str(seg.get("airplane") or ""),
            "duration_minutes": int(seg.get("duration") or 0),
        })
    return out


def _direction(row, wanted_cabin: str) -> dict:
    segments = _segments(row)
    cabins = [seg["cabin"] for seg in segments]
    longest = max((seg["duration_minutes"] for seg in segments), default=0)
    # Ties are all "the longer leg", so any one of them in cabin satisfies it.
    longest_cabins = [seg["cabin"] for seg in segments if seg["duration_minutes"] == longest]
    return {
        "segments": segments,
        "cabins": cabins,
        "longest_segment_minutes": longest,
        "longest_segment_cabins": longest_cabins,
        "long_leg_ok": bool(longest_cabins) and any(
            _cabin_matches(cabin, wanted_cabin) for cabin in longest_cabins
        ),
        "cabin_legs": sum(1 for cabin in cabins if _cabin_matches(cabin, wanted_cabin)),
        "stops": max(len(segments) - 1, 0),
        "route": "→".join([segments[0]["from"]] + [seg["to"] for seg in segments]) if segments else "",
        "depart": segments[0]["depart"] if segments else "",
        "arrive": segments[-1]["arrive"] if segments else "",
        "price": row.get("price"),
        "total_duration_minutes": row.get("total_duration"),
    }


def _rows(payload) -> list[dict]:
    return list(payload.get("best_flights") or []) + list(payload.get("other_flights") or [])


def _metadata(payload) -> tuple[str, str]:
    meta = payload.get("search_metadata") or {}
    return str(meta.get("id") or ""), str(meta.get("status") or "")


# ── requests ───────────────────────────────────────────────────────────────

def _search_params(origin, dest, departure, returning, cabin, stops_ceiling, no_cache) -> dict:
    return {
        "engine": "google_flights",
        "type": 1,
        "departure_id": origin,
        "arrival_id": dest,
        "outbound_date": departure,
        "return_date": returning,
        "travel_class": TRAVEL_CLASS_PARAM[cabin],
        "stops": STOPS_PARAM[stops_ceiling],
        "currency": "USD",
        "gl": "us",
        "hl": "en",
        "adults": 1,
        "sort_by": 2,                      # price
        "no_cache": "true" if no_cache else None,
    }


def _return_params(origin, dest, departure, returning, token, cabin, stops_ceiling) -> dict:
    """The follow-up. Cabin and stops are repeated so the returns come back in cabin.

    SerpApi ignores date/filter parameters for `booking_token`, not for
    `departure_token`, so the filters still apply here and are worth sending.
    """
    params = _search_params(origin, dest, departure, returning, cabin, stops_ceiling, no_cache=False)
    params.pop("no_cache", None)
    params.pop("sort_by", None)
    params["departure_token"] = token
    return params


# ── the scan ───────────────────────────────────────────────────────────────

def scan(args) -> dict:
    from letsfg.connectors.serpapi_google import (  # noqa: E402  (path set above)
        ProviderFailure,
        SerpApiTransport,
        failure_for,
        resolve_api_key,
    )

    key = resolve_api_key()                 # raises with a precise message if misfiled
    transport = SerpApiTransport(key, timeout=args.timeout)

    departures = [
        (date.fromisoformat(args.after) + timedelta(days=1 + i * args.dep_step)).isoformat()
        for i in range(args.dep_count)
    ]
    pairs = [
        (dest, departure, (date.fromisoformat(departure) + timedelta(days=offset)).isoformat())
        for dest in args.destinations
        for departure in departures
        for offset in args.return_offsets
    ]
    estimated = len(pairs) + min(args.verify, len(pairs))

    report = {
        "origin": args.origin,
        "destinations": list(args.destinations),
        "departures": departures,
        "return_offsets_days": list(args.return_offsets),
        "max_price": args.max_price,
        "max_stops": args.max_stops,
        "cabin": args.cabin,
        "cabin_requirement": f"the longer leg of each direction in {args.cabin} class",
        "planned_pairs": len(pairs),
        "estimated_searches": estimated,
        "requests_issued": 0,
        "cache_hits": 0,
        "failures": [],
        "candidates": [],
        "results": [],
    }

    if args.dry_run:
        report["dry_run"] = True
        return report

    seen_ids: set[str] = set()
    seen_candidates: set[tuple] = set()

    def fetch(params):
        report["requests_issued"] += 1
        result = transport.search(params)
        if result.status != 200:
            raise failure_for(result.status, result.payload, result.headers)
        search_id, status = _metadata(result.payload)
        if status and status != "Success":
            raise ProviderFailure("transient", 200, detail=f"search_metadata.status={status}")
        if search_id and search_id in seen_ids:
            report["cache_hits"] += 1
        seen_ids.add(search_id)
        return result.payload

    # step 1 — outbound options, one search per date pair
    for dest, departure, returning in pairs:
        label = f"{dest} {departure}→{returning}"
        try:
            payload = fetch(
                _search_params(args.origin, dest, departure, returning, args.cabin,
                               args.max_stops, args.no_cache),
            )
        except ProviderFailure as failure:
            report["failures"].append({"pair": label, "stage": "search", "reason": str(failure)})
            continue
        except Exception as error:                      # transport refused, etc.
            report["failures"].append({"pair": label, "stage": "search", "reason": f"{type(error).__name__}: {error}"})
            continue

        rows = _rows(payload)
        for row in rows:
            outbound = _direction(row, args.cabin)
            price = row.get("price")
            if price is None:
                continue
            # Google lists the same itinerary under both `best_flights` and
            # `other_flights`, so the identity, not the container, decides.
            identity = (
                dest, departure, returning, float(price),
                tuple((seg["flight"], seg["from"], seg["to"], seg["depart"]) for seg in outbound["segments"]),
            )
            if identity in seen_candidates:
                continue
            seen_candidates.add(identity)
            report["candidates"].append({
                "destination": dest,
                "departure": departure,
                "return": returning,
                "round_trip_price": float(price),
                "within_budget": float(price) <= args.max_price,
                "outbound": outbound,
                "long_leg_ok": outbound["long_leg_ok"],
                "stops_ok": outbound["stops"] <= args.max_stops,
                "departure_token": str(row.get("departure_token") or ""),
            })
        if not rows:
            report["failures"].append({
                "pair": label, "stage": "search", "reason": "no itineraries",
                "provider_error_field": str(payload.get("error") or ""),
            })

    promising = [
        candidate for candidate in report["candidates"]
        if candidate["within_budget"] and candidate["long_leg_ok"]
        and candidate["stops_ok"] and candidate["departure_token"]
    ]
    promising.sort(key=lambda candidate: candidate["round_trip_price"])

    # step 2 — return options for the cheapest promising outbounds
    for candidate in promising[: args.verify]:
        label = f"{candidate['destination']} {candidate['departure']}→{candidate['return']}"
        try:
            payload = fetch(
                _return_params(
                    args.origin, candidate["destination"], candidate["departure"],
                    candidate["return"], candidate["departure_token"], args.cabin,
                    args.max_stops,
                ),
            )
        except ProviderFailure as failure:
            report["failures"].append({"pair": label, "stage": "returns", "reason": str(failure)})
            continue
        except Exception as error:
            report["failures"].append({"pair": label, "stage": "returns", "reason": f"{type(error).__name__}: {error}"})
            continue

        for row in _rows(payload):
            inbound = _direction(row, args.cabin)
            if inbound["long_leg_ok"] and inbound["stops"] <= args.max_stops:
                report["results"].append({
                    "destination": candidate["destination"],
                    "departure": candidate["departure"],
                    "return": candidate["return"],
                    "round_trip_price": candidate["round_trip_price"],
                    "price_note": (
                        "the outbound option's price, which SerpApi's round-trip response "
                        "reports as the total; the return request quotes its own price below"
                    ),
                    "return_option_price": inbound["price"],
                    "outbound": candidate["outbound"],
                    "inbound": inbound,
                })
                break

    report["results"].sort(key=lambda result: result["round_trip_price"])
    return report


# ── output ─────────────────────────────────────────────────────────────────

def _cabins(direction) -> str:
    counts: dict[str, int] = {}
    for cabin in direction["cabins"]:
        counts[cabin] = counts.get(cabin, 0) + 1
    return ", ".join(f"{count}×{cabin}" for cabin, count in counts.items()) or "unknown"


def _print_direction(tag, direction, indent="    ") -> None:
    stop_text = "nonstop" if direction["stops"] == 0 else f"{direction['stops']} stop"
    print(f"{indent}{tag}  {direction['route']}  [{_cabins(direction)}]  {stop_text}")
    longest = direction["longest_segment_minutes"]
    for segment in direction["segments"]:
        mark = "← longer leg" if segment["duration_minutes"] == longest else ""
        print(f"{indent}      {segment['flight']:<9} {segment['from']}→{segment['to']:<4} "
              f"{segment['cabin']:<16} {segment['duration_minutes']:>4} min  "
              f"{segment['depart']} → {segment['arrive']}  {mark}")


def render(report) -> None:
    print(f"origin {report['origin']}  →  {', '.join(report['destinations'])}")
    print(f"departures checked: {', '.join(report['departures'])}")
    print(f"return offsets (days): {report['return_offsets_days']}  ·  "
          f"budget ≤ ${report['max_price']:,.0f}  ·  ≤{report['max_stops']} stop  ·  "
          f"{report['cabin']} on the longer leg")
    print(f"date pairs: {report['planned_pairs']}  ·  estimated searches: {report['estimated_searches']}")

    if report.get("dry_run"):
        print("\ndry run — no requests issued.")
        return

    print(f"\nrequests issued: {report['requests_issued']} (cache hits: {report['cache_hits']})")
    print(f"outbound candidates: {len(report['candidates'])}  ·  "
          f"round trips passing every filter: {len(report['results'])}")

    if report["results"]:
        print("\n" + "=" * 78)
        for result in report["results"]:
            nights = (date.fromisoformat(result["return"]) - date.fromisoformat(result["departure"])).days
            print(f"\n${result['round_trip_price']:,.0f}  {report['origin']}⇄{result['destination']}  "
                  f"{result['departure']} → {result['return']}  ({nights} days)")
            _print_direction("out ", result["outbound"])
            _print_direction("ret ", result["inbound"])
            if result["return_option_price"] is not None and \
                    float(result["return_option_price"]) != result["round_trip_price"]:
                print(f"      (return request quoted ${float(result['return_option_price']):,.0f})")
    else:
        print("\nNo round trip passed every filter. Cheapest outbound candidates seen:")
        groups: dict[tuple, dict] = {}
        for candidate in report["candidates"]:
            key = (
                candidate["destination"], candidate["departure"], candidate["return"],
                candidate["round_trip_price"], candidate["outbound"]["route"],
                tuple(candidate["outbound"]["cabins"]),
            )
            groups.setdefault(key, {"candidate": candidate, "count": 0})["count"] += 1
        for entry in sorted(groups.values(), key=lambda item: item["candidate"]["round_trip_price"])[:8]:
            candidate = entry["candidate"]
            flags = []
            if not candidate["within_budget"]:
                flags.append("over budget")
            if not candidate["long_leg_ok"]:
                flags.append(f"longer leg not in {report['cabin']}")
            if not candidate["stops_ok"]:
                flags.append("too many stops")
            same = f"  (+{entry['count'] - 1} later departures)" if entry["count"] > 1 else ""
            longest = candidate["outbound"]["longest_segment_minutes"]
            print(f"  ${candidate['round_trip_price']:,.0f}  {candidate['destination']} "
                  f"{candidate['departure']}→{candidate['return']}  "
                  f"{candidate['outbound']['route']} [{_cabins(candidate['outbound'])}]  "
                  f"longer leg {longest} min  "
                  f"{'; '.join(flags) or 'verified outbound'}{same}")

    if report["failures"]:
        print(f"\n{len(report['failures'])} pair(s) produced no usable answer:")
        for failure in report["failures"][:10]:
            detail = f" ({failure['provider_error_field']})" if failure.get("provider_error_field") else ""
            print(f"  {failure['pair']}  [{failure['stage']}]  {failure['reason']}{detail}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--origin", default=ORIGIN)
    parser.add_argument("--destinations", default=",".join(DESTINATIONS))
    parser.add_argument("--after", default=FIRST_DEPARTURE, help="first departure is the day after this")
    parser.add_argument("--dep-step", type=int, default=DEP_STEP_DAYS)
    parser.add_argument("--dep-count", type=int, default=DEP_COUNT)
    parser.add_argument("--return-days", default=",".join(str(offset) for offset in RETURN_OFFSETS),
                        help="comma-separated return offsets, in days")
    parser.add_argument("--max-price", type=float, default=MAX_PRICE)
    parser.add_argument("--max-stops", type=int, choices=(0, 1, 2, 3), default=MAX_STOPS)
    parser.add_argument("--cabin", choices=tuple(TRAVEL_CLASS_PARAM), default=CABIN,
                        help="the cabin the longer leg of each direction must be in")
    parser.add_argument("--verify", type=int, default=DEFAULT_VERIFY,
                        help="how many promising outbounds get the return-leg request")
    parser.add_argument("--no-cache", action="store_true", help="force fresh searches (billable)")
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--sdk-path", default="", help="path to <repo>/sdk/python")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="print the plan and exit")
    args = parser.parse_args()

    _load_env(ENV_CANDIDATES)
    _add_sdk([Path(args.sdk_path)] if args.sdk_path else SDK_CANDIDATES)

    args.destinations = tuple(part.strip().upper() for part in args.destinations.split(",") if part.strip())
    args.return_offsets = tuple(int(part.strip()) for part in args.return_days.split(",") if part.strip())

    report = scan(args)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        render(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
