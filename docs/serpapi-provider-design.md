# SerpApi Google Flights — Provider Design

**Status:** design only. No code from this document exists yet, and none may be
written until the owner answers the two gating decisions in §16 (D1 ownership,
D10 probe-first) — the working agreement forbids code changes that are not defect
fixes, and this is new scope.

**Provenance:** every provider fact below was read from SerpApi's own documentation
on 2026-10-03 (the engine pages, the price-insights and booking-options sub-pages,
the status/error-codes page, the Search Archive page, and the pricing page). Items
that could not be verified from documentation are marked **[UNVERIFIED]** rather
than guessed. **No live request was made** — there is no API key on this machine —
so no field shape here has been observed from a real response.

**Companion:** [`serpapi-provider-implementation.md`](serpapi-provider-implementation.md).

---

## 1. What this document decides

LetsFG currently reaches the market through two first-party lanes: PFS
(card-backed OAuth Bearer, `/api/*`) and the Developer API (`X-API-Key`,
`/developers/api/v1/*`). This document designs a **third, optional market
adapter** — SerpApi's Google Flights engines — behind the *same* client contracts
(`FlightOffer`, `price_status`, completeness, freshness, provenance), so that a
consumer of the client cannot tell which lane produced an observation except by
reading its provenance.

The adapter is a **provider**, not a lane: it cannot book, it cannot hold a fare,
and it must never present itself as able to.

---

## 2. Why a second provider

The LetsFG-only design has four capability gaps that SerpApi closes. Each is
traced to the document where the gap is currently acknowledged.

| Gap | Where it hurts | What SerpApi provides |
|---|---|---|
| **No pre-computed baseline** — "is this price unusually low?" requires us to build per-cohort baselines ourselves | [`first-class-fare-scanner-design.md`](first-class-fare-scanner-design.md) §7.1, the most expensive unsolved part of the scanner | `price_insights.typical_price_range` + `price_level` on every `google_flights` response, and `deals.average_price` + `discount_percentage` per destination |
| **No native flexible-date window** — `discover` prices are indicative and there is no date-range parameter anywhere | scanner §6 planner, which must fan out | `google_flights_deals` takes `outbound_date` as a **range** with `trip_length`/`travel_duration`; `google_travel_explore` takes `month` |
| **No way to pin an itinerary and re-price it** | scanner §6.5 verification step | `selected_flights_json` — pin an itinerary segment-by-segment by flight number + date |
| **A published failure contract is absent** — the limiter's behaviour is `UNKNOWN` pending the Q8 probe | [`trvl-study-design.md`](trvl-study-design.md) §6 Q8; the scanner's scheduler is blocked on it | SerpApi documents its status codes, its 429 causes, and what does and does not consume quota (§11) |

A fifth, weaker benefit: `google_travel_explore` generates destination pools from
interests (`beach`, `skiing`, `outdoors`, …) inside a six-month window, which the
`discover` operation cannot express at all.

---

## 3. Adapter boundary

```
scanner (separate repo)                     this repo (LetsFG client)
        │                                            │
        ├── PFS lane ────────────────► /api/*        │
        ├── Developer API lane ──────► /developers/api/v1/*
        └── SerpApi provider ─────────► serpapi.com/search   ← this document
                    │
                    └── normalised to FlightOffer / completeness / provenance
```

Rules:

1. **No new public MCP tool, no new REST route, no SDK surface change.** The
   adapter is an internal data source; it is exposed only through the existing
   client contracts.
2. **Optional.** Absent `SERPAPI_API_KEY`, the adapter is absent. Nothing in the
   core client may import it at module scope.
3. **No hard dependency.** The adapter uses the standard library's HTTP client —
   not SerpApi's `google-search-results` package. Rationale: that package is
   synchronous and would add a dependency the repo does not need for one optional
   lane.
4. **One direction only.** The adapter produces *observations*. Ranking,
   scoring, thresholds and alert policy live elsewhere (scanner §7, §9) — the
   provider is not allowed to know what "unusually cheap" means.

---

## 4. Verified provider facts

### 4.1 Engines

| Engine | Purpose | Booking? | Verified parameters (see §5) |
|---|---|---|---|
| `google_flights` | itinerary search + re-price + booking options | no | `departure_id`, `arrival_id`, `type`, `outbound_date`, `return_date`, `travel_class`, `multi_city_json`, `selected_flights_json`, `departure_token`, `booking_token`, `stops`, `include_airlines`/`exclude_airlines`, `bags`, `max_price`, `max_duration`, `outbound_times`, `return_times`, `layover_duration`, `exclude_conns`, `emissions`, `show_hidden`, `exclude_basic`, `deep_search`, `sort_by`, `adults`/`children`/`infants_*`, `gl`, `hl`, `currency`, `no_cache`, `async`, `output`, `json_restrictor` |
| `google_flights_deals` | "cheap destinations from X" + flexible windows | no | `departure_id`, `query`, `type`, `travel_class`, `outbound_date` (date **or range**), `return_date`, `travel_duration`, `trip_length`, `stops`, `include/exclude_airlines`, `max_price`, `max_duration`, `gl`, `hl`, `currency` |
| `google_travel_explore` | destination discovery | no | `departure_id` (**required**), `arrival_area_id`, `arrival_id`, `type`, `outbound_date`, `return_date`, `month`, `travel_duration`, `travel_class`, `travel_mode`, `interest`, `stops`, `include/exclude_airlines`, `bags`, `max_price`, `max_duration` |
| `google_flights_autocomplete` | location → kgmid join key | no | `q` (**required**), `exclude_regions`, `gl`, `hl` |
| `engine=google` + `flight_result` | flight **status** (gates, delays, terminals) | no | presented as a Google Search extraction key, sourced from Cirium |

All five are reached through one endpoint: `GET https://serpapi.com/search`.

### 4.2 Envelope and status

- A response carries `search_metadata.status`, which is `Queued` →
  `Processing` → `Success` | `Error`. **`Success` explicitly includes empty
  results** — an empty answer is not an error.
- A failed search still carries a top-level `error` string.
- `search_metadata.id` is a durable handle for the **Search Archive**
  (`GET https://serpapi.com/searches/{id}.json`), which serves `json` or `html`
  for **31 days**, and answers `410 Gone` afterwards.
- `search_metadata` timestamp fields (e.g. when a cached result was produced):
  **[UNVERIFIED]** — not documented on any page read, and it decides §7.

### 4.3 Cost, quota and throughput

| Plan | Price | Searches/mo | Throughput/hr |
|---|---|---|---|
| Free | $0 | 250 | 50 |
| Starter | $25/mo | 1,000 | 200 |
| Developer | $75/mo | 5,000 | 1,000 |
| Production | $150/mo | 15,000 | 3,000 |

- "Only **successful** searches are counted… Cached, errored, and failed searches
  are not." Results-count does not matter: 100 results or an empty set both count
  as one search — **so an empty result costs quota while a failure does not.**
- Cache lifetime is **1 hour**, keyed on the exact query plus all parameters;
  `no_cache=true` forces a fresh fetch and is therefore the *only* way to be sure
  a price was observed now.
- Throughput is a **guaranteed per-hour** figure; the docs advise spreading
  searches across the hour.
- `async=true` submits and returns, with retrieval later through the Search
  Archive; it cannot be combined with `no_cache`. It is not available on
  Ludicrous Speed accounts.
- Speed modes multiply price: Ludicrous Speed 2×, Ludicrous Speed Max 4×.
- **U.S. Legal Shield** (up to $2M, scraping/parsing indemnity) starts at the
  $150/mo Production plan. **ZeroTrace** (no retention of parameters, queries or
  results) is **enterprise-only** — the Cloud tiers.

### 4.4 Response fields, verified shapes

`price_insights` (confirmed on its own sub-page — the shape in the main API
page's example is truncated and must not be used as a contract):

```json
"price_insights": {
  "lowest_price": 1339,
  "price_level": "high",
  "typical_price_range": [570, 1050],
  "price_history": [[1691013600, 575], [1691100000, 575]]
}
```

- `typical_price_range` — two integers, **low bound and high bound**.
- `price_history` — array of `[unix_timestamp, price]` pairs.
- `lowest_price` — "the lowest price among the **returned flights**", i.e. the
  current result set, *not* a market low. This distinction must survive into our
  contracts (§9).
- `price_level` — a string; documented only as "price level of the
  `lowest_price`". Values seen: `low`, `high`. **Enum is [UNVERIFIED]** — do not
  switch on it.

`google_flights` itinerary fields (verified from the documented response example):
`best_flights[]` / `other_flights[]`, each with `flights[]` (segments:
`departure_airport{name,id,time}`, `arrival_airport{name,id,time}`, `duration`,
`airplane`, `airline`, `flight_number`, `travel_class`, `ticket_also_sold_by`,
`legroom`, `extensions[]`), `layovers[]{duration,name,id,overnight}`,
`total_duration`, `carbon_emissions{this_flight,typical_for_this_route,difference_percent}`,
`price`, `type`, `extensions[]`, `booking_token`, `departure_token`, and the
top-level `airports[]` block. Note that **`layovers` are given separately from
segment `duration`s** — which is exactly the shape that makes §12's defect
detectable.

`google_flights_deals` fields: `departure_informations{airport_name,airport_code,city,city_id,country,gps_coordinates}`
and `deals[]` with `destination_id` (kgmid), `name`, `country`, `price`,
`average_price`, `discount_percentage`, `flight_link`, `serpapi_flight_link`,
`thumbnail`, `start_date`, `end_date`, `departure_airport_code`,
`arrival_airport_code`, `flight_duration`, `stops`, `airline`, `airline_code`,
`description`, `highlights`.

`google_travel_explore` fields: `destinations[]` with `destination_id` (kgmid),
`name`, `country`, `gps_coordinates`, `thumbnail`, `destination_airport{code}`,
`start_date`, `flight_price`, `flight_duration`, `number_of_stops`, `airline`,
`airline_code`.

`booking_token` response: an array `booking_options[]`, each entry carrying
`together{book_with,…}`, a `separate_tickets: true` flag with per-leg
`book_with` sellers, plus supporting `selected_flights` and `baggage_prices`.
**Inner seller fields beyond `book_with` are [UNVERIFIED].**

`flight_result` (status): `title`, `flight_designator`, `route`, `airline`,
`airline_iata_code`, `available_dates[]`, `dates[]{date, metadata{airline_iata_code,
flight_number, origin, destination, status, departure_delay, arrival_delay},
departure_airport{time,scheduled_time,terminal,gate,id,city,status}, arrival_airport{…},
duration, source, source_url, codeshare, updated_at, status}`, sourced from Cirium.

---

## 5. Engine → operation mapping

The scanner's operations are `discover` (candidate generation), `search`
(observation) and `verify` (re-price). The mapping is normative: an adapter method
that is not listed here does not exist.

| Client operation | Engine + parameters | `source_operation` | `price_status` ceiling |
|---|---|---|---|
| `resolve_location` | `google_flights_autocomplete` `q` | — | n/a |
| `discover` (destination pool) | `google_travel_explore` (`month`, `interest`, `travel_mode`, `arrival_area_id`) | `discover` | `indicative` |
| `discover` (deals from an origin) | `google_flights_deals` (`outbound_date` range, `trip_length`) | `discover` | `indicative` |
| `search` | `google_flights` (`type`, dates, filters) | `search` | `observed` |
| `verify` | `google_flights` + `selected_flights_json` | `verify` | `verified` |
| next-leg / return leg | `google_flights` + `departure_token` | `search` | `observed` |
| booking options (referral) | `google_flights` + `booking_token` | `search` | `observed` — **never `verified`, never alertable as bookable** (§8) |
| schedule/ops context (optional) | `engine=google` + `flight_result` | — | never a price |

`deals.price` and `explore.flight_price` are **not** bookable and are not the
result of a search for a specific itinerary: they enter the store as `indicative`,
which the scanner already excludes from the bookable baseline and from alerting
(scanner §7.1).

---

## 6. Identity and provenance

- **Provider handles are request-scoped.** `departure_token` and `booking_token`
  are long opaque blobs (the documented example encodes the segment list). They
  are valid for the follow-up request that consumes them and are **never**
  durable identity, never a key in the observation store, and never persisted in
  an observation. This is the same rule the scanner already applies to the LetsFG
  offer `id` (scanner §5.2) — the API's id is observation-local.
- **kgmids are provider-local.** `autocomplete.id` and `deals.destination_id` are
  Wikidata Freebase ids (`/m/…`, `/g/…`). They are a *join key to the provider*,
  not a canonical city identity. Canonical identity stays IATA-based and
  itinerary/offer-graded as the scanner defines it; a kgmid is recorded in
  provenance, never used as `ItineraryIdentity`.
- **Provenance is mandatory.** An observation produced by this adapter records:
  `provider: "serpapi_google"`, the engine, `search_metadata.id` (the archive
  handle), the currency, and the query parameters that produced it. Without
  `search_metadata.id` the observation is not auditable and must not be stored.

---

## 7. Freshness, caching and `observed_at`

This is the sharpest contract risk in the whole adapter.

By default (`no_cache=false`) SerpApi may serve a response **up to one hour old**
that is byte-identical to a fresh one. Our contracts forbid manufacturing
provider observation time, and `observed_at_basis` may only be `provider` when the
provider actually tells us when the observation was made — which is
**[UNVERIFIED]** here.

Therefore:

1. **Any observation that may alert or verify is fetched with `no_cache=true`.**
   It costs a search; that is the correct price for a claim about "now".
2. **Cached responses may feed only non-alerting series** — baseline collection,
   destination exploration, trend history — and are recorded with
   `observed_at = client_receipt` time, which is honest: we received it now, and
   we cannot claim more.
3. **If the probe (§17) finds a trustworthy timestamp field**
   (`search_metadata` or equivalent), `observed_at_basis: provider` becomes
   available and rule 1 may be relaxed for verification — but only with the
   measured field recorded, never inferred from cache behaviour.
4. Until rule 3 resolves, **the adapter must not set `freshness` to a
   provider-dated value at all.**

The same discipline applies to coverage: `deep_search=false` (the default) is
documented as *not* matching the browser's results, and a separate `show_hidden`
parameter exists for "View more flights". A default search therefore cannot
support `completeness: complete`.

---

## 8. `price_status` mapping

The scanner's single lifecycle is `indicative | observed | verified | stale | unavailable`.
Mapping into it:

| Provider situation | Status | Reasoning |
|---|---|---|
| `deals.price`, `explore.flight_price` | `indicative` | Provider says the price is not a specific bookable itinerary |
| `google_flights` result, `deep_search=false` | `observed` (completeness `partial`) | Real search, incomplete coverage |
| `google_flights` + `selected_flights_json` re-price | `verified` | The authoritative path returned this itinerary again |
| `booking_options[]` sellers | `observed` | **Referral prices from OTAs.** They are a different product (an agency's fare), not our itinerary's fare |
| `Error` status, or the itinerary absent from results | `unavailable` | An event to record, never an alert |
| Older than the freshness window | `stale` | Per the scanner's window rules |

The `booking_options` row is a decision, not a detail: an OTA price shown as
`verified` would let the scanner alert on a fare it can neither hold nor book.
Booking through SerpApi is **impossible** — there is no booking endpoint — so this
adapter's ceiling is a *verified observation*, and the scanner's alert copy must
continue to say "verified N minutes ago", never "available".

Provider-supplied quality facts (`legroom`, `carbon_emissions.typical_for_this_route`,
`extensions`) are **provider assertions**: they may be stored with provenance and
shown to a human, but `extensions` is unstructured prose and **must never be
parsed into a decision**. `cabin_product_quality` stays `unknown` unless the
provider states it — no inference from airline or aircraft (scanner §7.2).

---

## 9. Baselines, and the one trap in `lowest_price`

`price_insights.typical_price_range` and `deals.average_price` are Google-computed
baselines. They are exactly the shape §7.1 of the scanner needs, and they arrive
per search rather than requiring us to accumulate a cohort. Two rules keep them
honest:

1. **`lowest_price` is the minimum of the returned set**, not a market minimum.
   It must never be used as a baseline, and must never be compared against a
   baseline that was computed from it.
2. **A baseline supplied by the provider is still a baseline.** It carries the
   provider's own definition, window and cohort — which are undocumented
   (**[UNVERIFIED]**: over what window `average_price` averages, and whether
   `price_history` is route-level or query-level). Until the probe answers that,
   these values are stored as *provider claims* with provenance and are **not**
   rankable or alertable inputs. The scanner's existing rule — the current
   observation must not participate in its own baseline — applies unchanged, and
   a provider baseline must be identified as such so its own contribution is not
   double-counted.

`carbon_emissions.typical_for_this_route` + `difference_percent` is the same
pattern for emissions, useful for `travel_quality_score` context.

---

## 10. Budget and quota

The scanner's budget model (a hard monthly reservation, never consumed by the
scanner itself) generalises to a **per-provider ledger**. SerpApi's rules change
the arithmetic:

| Event | Consumes quota? | Consequence for the ledger |
|---|---|---|
| Successful search with results | yes | normal unit |
| **Successful search with empty results** | **yes** | an honest `no_results` still costs — the scanner must not probe empty routes freely |
| Failed search (`Error`, 5xx, 429) | no | failures are cheap; retrying a *throughput* 429 is still wrong (§11) |
| Cache hit (identical query, within 1h) | no | free, but see §7 on `observed_at` |
| Search Archive retrieval | no | the 31-day archive is free to read |

Concurrency ceiling: the plan's **guaranteed searches per hour** (50 on Free,
200 on Starter) — not a per-second rate. The adapter must be shaped to *spread*
searches across the hour rather than burst.

Free tier reality check: 250 searches/month is **≈8/day**. A destination-pool
scanner over flexible dates cannot run on it; the cheapest viable tier for
scanner duty is Starter ($25 → 1,000/mo, ≈33/day). This is an owner cost decision,
not an engineering one.

---

## 11. Failure contract

SerpApi publishes what LetsFG leaves `UNKNOWN`, with one trap worth its own test:

| HTTP | Meaning | Client category | Action |
|---|---|---|---|
| 200 | success (may be empty) | — | empty list → `no_results`, `completeness: complete` |
| 200 (body `search_metadata.status: Error`, HTTP 503) | result could not be produced | `transient` | retry with backoff |
| 400 | bad request, missing parameter | `validation` | fix the request; never retry as-is |
| 401 | no valid API key | `auth_required` | stop; surface the missing key |
| 403 | key's account deleted / no permission | `business` | stop; needs a human |
| 404 | unknown resource | `validation` | fix |
| 410 | **archive search expired** (>31 days) | `business` | re-search; the handle is dead |
| **429** | **either** hourly throughput exceeded **or** out of searches | see below | see below |
| 500 / 503 | provider-side failure | `transient` | retry with backoff |

**The 429 trap.** One status code, two causes, distinguished only by the body
message (`"Your account has run out of searches."` vs a throughput message). The
consequences are opposite:

- *Throughput* 429 → `rate_limited` (transient). Back off, respect any
  `Retry-After`, then resume.
- *Quota* 429 → `budget_exhausted` (terminal for the run). Retrying can never
  help; only a plan renewal or an early renewal can.

The adapter MUST therefore classify 429 by reading the message, and that parsing
MUST be pinned by a test with both recorded bodies. It is the one place where
provider prose is load-bearing and is the sole exception to the "never parse
prose into a decision" rule in §8 — an explicit, tested exception.

SerpApi does **not** document a `Retry-After` header in its own contract (the
429 blog post is generic guidance whose example is illustrative), so **[UNVERIFIED]**:
honour `Retry-After` if present, otherwise exponential backoff with jitter, with a
bounded attempt count.

`no_results` versus `timeout` stays non-negotiable: an empty `Success` is a
`no_results` with `completeness: complete`; a failed search is `timeout`/`failed`
with `completeness: blocked`. And per the positive-evidence rule, an empty result
is **not** evidence that a route is unserved — only evidence that this query,
these filters, this time returned nothing.

---

## 12. Pitfalls already paid for, and the traps that remain

### 12.1 Measured on our own production data (this repo)

The repo once ran a `serpapi_google` connector, and its output was measured:

- **Durations.** `sdk/python/letsfg/models/flights.py` records that **1,766 route
  totals from `serpapi_google` were the sum of segment flight times with every
  layover dropped**, in a 110-search production sample (2026-08-26). Concrete
  cases: a BCN→BEG→SOF return with a 45-minute connection published as 3h50m
  instead of 4h35m; a 15h50m two-stop as 4h. After the SDK began recomputing
  gate-to-gate from timezone-aware airport times, its result matched the segments'
  own numbers on **99.5% of serpapi_google legs**.
- The regression is pinned by `sdk/python/tests/test_duration_timezone.py`
  ("serpapi_google publishes the sum of flight times, dropping the layover"), which
  is green and not quarantined.

**Design consequence:** the adapter MUST supply `FlightSegment` records with
origin/destination/arrival times and MUST NOT pass a provider total through
unchecked. The existing Pydantic validators
(`FlightSegment.backfill_duration_seconds`,
`FlightRoute.backfill_total_duration_seconds`) already repair this — so the
adapter's job is to *not defeat them*: no pre-filled totals, no
`model_construct` shortcuts.

### 12.2 Traps in the provider's own interface

| Trap | Detail | Mitigation |
|---|---|---|
| `stops` is not a count | `0`=any, `1`=nonstop, `2`=≤1 stop, `3`=≤2 stops | named constants in the adapter; never pass a raw count |
| `travel_duration` means different things per engine | deals: `1`=1 week, `2`=Weekend, `3`=2 weeks. explore: `1`=Weekend, `2`=1 week, `3`=2 weeks | separate enums; never share one |
| `return_date`/`return_times` applicability | `return_date` forbidden for one-way and multi-city; `return_times` round-trip only; `return_date` required for round trip `type=1` | construct requests by type, not by field-merge |
| Mutual exclusions | `include_airlines` ⊥ `exclude_airlines`; `departure_token` ⊥ `booking_token`; `selected_flights_json` ⊥ tokens and ⊥ `multi_city_json`; `no_cache` ⊥ `async`; `query` ⊥ `travel_duration`/`trip_length` | one request builder per mode, with assertions |
| `exclude_basic` is narrow | only `gl=us` **and** `travel_class=1` | don't offer it as a general filter |
| Provider-side filtering is lossy | docs: if `max_duration` returns nothing, "try increasing it by up to 200 minutes to account for route-specific scheduling variances" | never conclude "no such flight" from a filtered empty set — a filtered empty result is `partial`, not `complete` |
| `deep_search`/`show_hidden` change coverage | default search ≠ browser results | coverage honour (§7) |
| `type=3` ignores `outbound_date` | multi-city dates live inside `multi_city_json` | builder-per-type |
| `total_duration` in the documented example is incoherent | 820 minutes shown for a 90-minute flight with a 90-minute layover | never trust the field; recompute (§12.1) — and treat the doc example as illustrative, as the provider itself warns |

---

## 13. The adapter contract

An implementation satisfies this interface (naming follows the repo's existing
`search_local` style; the concrete module is chosen in the implementation doc):

| Method | Engine | Returns |
|---|---|---|
| `resolve_location(query)` | autocomplete | candidate locations with IATA codes **and** kgmid, marked provider-local |
| `discover(origin, window, constraints)` | travel_explore and/or deals | `indicative` observations + destination candidates, with kgmid recorded in provenance |
| `search(request)` | google_flights | a list of `FlightOffer`s, `price_status: observed`, with completeness |
| `verify(itinerary)` | google_flights + `selected_flights_json` | at most one `FlightOffer` with `price_status: verified`, or an explicit absence |
| `next_leg(request)` | google_flights + `departure_token` | second-leg offers (round trip / multi-city) |
| `booking_options(itinerary)` | google_flights + `booking_token` | referral sellers, `observed`, never bookable |

Non-negotiable properties: every returned offer carries provenance
(§6) and a status at or below its ceiling (§5); the adapter never books, never
holds, never ranks, never alerts; absent configuration it raises the same
"credential missing" error shape the other lanes use.

Public exposure goes through the existing sanitiser
(`to_public_offer`, which masks owner airline and strips sensitive conditions) —
the adapter must not build its own public shape.

---

## 14. Credentials and hygiene

- The key is read from **`SERPAPI_API_KEY`** — environment only. It is **never**
  written to `~/.letsfg/config.json` (contrast: the PFS/Developer credentials have
  a store because the product owns them; a third-party scraping key does not
  belong there). Working agreement rule 1.
- SerpApi takes the key as a **query parameter**, so it is present in the request
  URL. Therefore: **never log a full request URL**, never include the URL in an
  exception message, and never surface it in a user-facing error or a report.
  This is a concrete leak vector, not a hypothetical one — the existing PFS/Dev
  lanes pass credentials in headers, so this adapter is the first place in the
  repo where that rule is load-bearing.
- Retention: ZeroTrace is enterprise-only, so search parameters and results are
  retained by SerpApi on all plans we would realistically use. Any decision to route
  user-specific queries (dates, destinations tied to a person) through this lane
  must acknowledge that. Fare observations remain domain data (D11), stored by us,
  not telemetry.

---

## 15. Non-goals

- **No booking.** SerpApi cannot book; this adapter cannot hold a fare; nothing
  here changes the PFS booking flow.
- **No replacement of the search engine.** Acquiring Google Flights data from a
  third party is *renting* market access, not owning it (see D10 in
  [`trvl-study-design.md`](trvl-study-design.md) §7). The first-party engine is
  not deprecated by this.
- **No new client surface:** no MCP tool, no public REST route, no `LetsFG`
  client method beyond the internal provider interface.
- **No scoring, no thresholds, no alerts** — scanner §7/§9 own that.
- **No `flight_result` integration in v1.** Flight *status* is a different product
  (schedule reliability, delays) and is not needed by a fare scanner; recorded
  here as available, deliberately unused.
- **No hotels.** SerpApi's hotel engines are out of scope for this document.

---

## 16. Decision register

| # | Decision | Status |
|---|---|---|
| D1 | Add SerpApi as an **optional market provider adapter** inside this repo, behind the existing `FlightOffer` contracts, with no new public surface and no core dependency | **OPEN — owner** (it is provider acquisition, the one thing [`trvl-study-design.md`](trvl-study-design.md) D10 reserves to the owner) |
| D2 | Never trust a provider-supplied duration total; recompute from segments and let the existing validators own the result | DECIDED (forced by §12.1) |
| D3 | `no_cache=true` for every observation that may alert or verify; cached responses only for non-alerting series, with `observed_at_basis: client_receipt` | DECIDED |
| D4 | Classify 429 by body message into `rate_limited` (transient) vs `budget_exhausted` (terminal); pin both bodies in a test | DECIDED |
| D5 | Booking options are referral: `observed`, never `verified`, never alertable as bookable | DECIDED |
| D6 | `departure_token`/`booking_token` are request-scoped handles: never persisted, never identity | DECIDED |
| D7 | kgmid is provider-local provenance, not canonical identity | DECIDED |
| D8 | Key from the environment only; never log a request URL (key is a query parameter) | DECIDED |
| D9 | A per-provider budget ledger; empty-but-successful results consume quota, failures and cache hits do not | DECIDED |
| D10 | No adapter code until the probe (§17) has run — the caching/freshness rules cannot be finished without it | **DECIDED (gating)** |
| D11 | Provider baselines (`typical_price_range`, `average_price`) are stored as provider claims; not rankable or alertable until their window/cohort is measured | DECIDED |
| D12 | Plan tier is an owner cost decision; Free (≈8 searches/day) is insufficient for scanner duty | **OPEN — owner** |
| D13 | `flight_result` (flight status) is out of scope for v1 | DECIDED |
| D14 | The adapter must not import SerpApi's client package; standard library HTTP only | DECIDED |

---

## 17. Open probes (must run before code)

One script, `-m live`-marked, refusing to run without an explicit env flag, on a
key the owner provides. It measures — and records raw responses as evidence:

1. `search_metadata`'s full key set on a fresh and on a **cache-hit** search:
   is there a trustworthy observation timestamp? (Decides §7 rule 3.)
2. Whether a **429 carries `Retry-After`**, and which message body each of the two
   429 causes produces (feeds D4's test fixtures).
3. `price_level`'s full enum across several routes (feeds §4.4).
4. `price_history`'s real granularity and horizon, and `deals.average_price`'s
   window — do they look route-level or query-level? (Feeds D11.)
5. Whether an empty-result search really consumes quota (`GET` the account
   endpoint before and after) — the ledger's correctness depends on it.
6. Whether `selected_flights_json` re-prices the *same* itinerary or silently
   substitutes (the verification primitive's whole value).
7. `booking_options[]`'s seller field set, to replace the [UNVERIFIED] in §4.4.
8. Coverage: the same query with and without `deep_search`/`show_hidden` — how
   many itineraries appear, and does `total_duration` disagree with its own
   segments (re-checking §12.1 against live data)?

Cost: single-digit searches, plus quota consumed by the deliberate empty-result
probe. Free tier (250/mo) is enough. Acceptance: a raw-response fixture set
committed alongside the probe's findings, and every **[UNVERIFIED]** marker in
this document resolved or re-stated with evidence.

---

## 18. Priority and gating

| Phase | Content | Gate |
|---|---|---|
| **P0** | Probe above → resolve the [UNVERIFIED] markers; then finish D3/D11 text | Owner supplies a key and accepts quota consumption |
| **P1** | Contract tests that need no network: enum traps (§12.2), 429 classification (D4), credential redaction (D8) | Free — these are pure functions; still `OWNER` for new test files under rule 4 |
| **P2** | The adapter module, mapping tables, budget ledger | **D1 owner decision required** |
| **P3** | Scanner wiring (other repository) | Outside this repo |

Everything from P2 onward is new scope. Under the working agreement it needs an
explicit owner go-ahead, and the implementation plan records it that way.

---

## 19. Traceability

| This document | Depends on / feeds |
|---|---|
| §2, §9 | [`first-class-fare-scanner-design.md`](first-class-fare-scanner-design.md) §7.1 (baseline), §6 (planner, flexible dates) |
| §7 | scanner §5.4 (freshness), and the envelope's `freshness`/`observed_at_basis` invariant |
| §8 | scanner §5.4 (`price_status`), §7.2 (`cabin_product_quality` neutrality) |
| §11 | [`trvl-study-design.md`](trvl-study-design.md) §6 Q8 (LetsFG's own limiter is still `UNKNOWN`); the `no_results` vs `timeout` rule |
| §12.1 | `sdk/python/letsfg/models/flights.py` invariants; `sdk/python/tests/test_duration_timezone.py` |
| §12.2, §14 | [`flight-finder-study-implementation.md`](flight-finder-study-implementation.md) defect-class items (guards that pin provider quirks) |
| §13 | [`architecture-guide.md`](architecture-guide.md) — the provider boundary belongs there, not in a new design surface (trvl study D13) |
| §16 D1 | trvl study §7 ownership model (provider acquisition is owner-reserved) |

---

## 20. Review record

- *Probe, 2026-10-03* — first pass over SerpApi's documented surface after an
  external summary of it was found to be right in structure and wrong on
  flexible dates, silent on `selected_flights_json`, and silent on cost and
  quota. Findings folded into §4, §5, §10, §12.2. No live request was made.
- *Pending* — the probe in §17, and the owner decisions D1 and D12.
