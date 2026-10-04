# SerpApi Google Flights — Provider Design

**Status: ADOPTED — owner decision, 2026-10-03.** LetsFG will use SerpApi as an
**optional market provider adapter**. The lane was **declined and re-activated on the
same day**: the owner bought the Starter plan and reversed D1. The decline is kept in
§20 rather than erased, because the contracts argued during it are why this document is
implementation-ready — provider provenance, `ProviderPriceContext` as a claim rather
than a baseline, the client-side budget ledger of D20 and the fail-safe 429
classification of D4 were all tightened to survive a vendor, and they did.

**Measured account state at adoption (2026-10-03).** `GET https://serpapi.com/account`
returns `plan_id: starter_v4`, `plan_name: Starter Plan`, `searches_per_month: 1000`,
`plan_searches_left: 1000`, `this_month_usage: 0`,
`account_rate_limit_per_hour: 200`. That is the tier D12 recommended for continuous
operation, and it is the number §10's budget model is written against.

**What the decision reopens.** §16 **D1** (ownership) is answered **yes**. D10
(probe-first) is **binding again**: the two probes never run — P0.5's 429 bodies and the
empty-search half of P0.7 — are owed before the adapter, not cancelled. D12 (plan tier)
is **closed by purchase**. The Legal Shield question (§4.3) is live again as an optional
procurement. The credential convention of §14 is now a requirement with a live
consumer, and its measured misfiling (§14) is an action item rather than a footnote:
the key must be readable as `SERPAPI_KEY`.

**Provenance:** the provider facts below were read from SerpApi's own documentation on
2026-10-03 (the engine pages, the price-insights and booking-options sub-pages, the
status/error-codes page, the Search Archive page, the pricing page); items that could
not be verified from documentation are marked **[UNVERIFIED]** rather than guessed.
**Then, later the same day, a live probe ran:** a valid key was found on the machine
(misfiled as `SERPER_API_KEY`, §14), and seven searches on the Free plan measured the
cache, pin, price-context and coverage behaviour recorded in §17 and folded in
throughout this document. Where the two disagree, §17's measurements win, and the
**[UNVERIFIED]** markers the probe resolved were updated in place.

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

The LetsFG-only design has four capability gaps SerpApi can **contribute** to.
None of them is *closed* by the provider alone: every row below is a
**supplementary signal** whose semantics are unmeasured until the probe (§17) has
run. Each is traced to the document where the gap is currently acknowledged.

| Gap | Where it hurts | What SerpApi provides |
|---|---|---|
| **No provider-side price context** — "is this price unusually low?" requires us to build per-cohort baselines ourselves | [`first-class-fare-scanner-design.md`](first-class-fare-scanner-design.md) §7.1, the most expensive unsolved part of the scanner | A **`ProviderPriceContext`** (§9): `typical_price_range`, `price_level`, `price_history` on every `google_flights` response, and `average_price` + `discount_percentage` per destination. **Supplementary, not a replacement** for the cohort baseline until the provider's window and cohort semantics are measured |
| **No native flexible-date *discovery* window** — `discover` prices are indicative and there is no date-range parameter anywhere | scanner §6 planner, which must still fan out for authoritative search | `google_flights_deals` takes `outbound_date` as a **range** with `trip_length`/`travel_duration`; `google_travel_explore` takes `month`. This is deal/date-window **discovery**, not authoritative flexible-date itinerary search |
| **No way to pin an itinerary and re-price it** | scanner §6.5 verification step | `selected_flights_json` — pin an itinerary segment-by-segment by flight number + date |
| **A published failure contract is absent** — the limiter's behaviour is `UNKNOWN` pending the Q8 probe | [`trvl-study-design.md`](trvl-study-design.md) §6 Q8; the scanner's scheduler is blocked on it | SerpApi documents its status codes, its 429 causes, and what does and does not consume quota (§11) |

The capabilities are **not interchangeable**, and the difference decides what the
scanner may build on each:

```
LetsFG lanes                   flexible-date authoritative itinerary search   ✗
SerpApi deals / explore        flexible-date deal discovery                   ✓
SerpApi google_flights         specific-date itinerary observation            ✓
SerpApi selected_flights_json  itinerary pinning → verification candidate     ✓  (semantics unmeasured, §8)
```

So the scanner's primitive chain is unchanged — candidate → specific date →
authoritative search → observation — and deals/explore can only feed the first
step, as a richer form of what `discover` already does.

A fifth, weaker benefit: `google_travel_explore` generates destination pools from
interests inside a six-month window, which the `discover` operation cannot express at
all — **but not with the vocabulary this document first guessed.** Measured 2026-10-03:
`interest=beach` answers `400 Unsupported "beach" for interest.`, so the adapter encodes
**no** interest value, and none should be added until one is measured working. The same
probe found `arrival_area_id` is a kgmid (`/m…`), not an index, and that with a valid
area but no matching service the engine answers `200` with an `error` string and **no
result container at all** — so explore's populated shape remains unmeasured and nothing
is inferred from its absence (§12.2).

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
2. **Optional.** Absent `SERPAPI_KEY`, the adapter is absent. Nothing in the
   core client may import it at module scope.
3. **No hard dependency.** The adapter uses the standard library's HTTP client —
   not SerpApi's `google-search-results` package. Rationale: that package is
   synchronous and would add a dependency the repo does not need for one optional
   lane.
4. **One direction only.** The adapter produces *observations*. Ranking,
   scoring, thresholds and alert policy live elsewhere (scanner §7, §9) — the
   provider is not allowed to know what "unusually cheap" means.
5. **No manufacture from absence.** A field the provider omitted, or a mode the
   provider did not honour, is recorded as **unknown** — never inferred as
   `false`, `complete`, or "not available". Absence of evidence in a response is
   not evidence about the world, and this rule is what stops the adapter
   quietly acquiring provider-specific semantics.

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
- **An empty `Success` also carries a top-level `error` string — measured 2026-10-03.**
  A filter that matches nothing (`include_airlines` set to a code no airline uses)
  answers HTTP 200 with `search_metadata.status: Success`, zero itineraries **and**
  `"error": "Google Flights hasn't returned any results for this query."` The
  presence of `error` therefore does **not** mean failure. Classification reads
  `search_metadata.status` and nothing else (§11): an adapter that branched on
  `error` would report `failed`/`timeout` for a legitimate empty result and destroy
  the `no_results` distinction the scanner's coverage contract is built on.
- `search_metadata.id` is a durable handle for the **Search Archive**
  (`GET https://serpapi.com/searches/{id}.json`), which serves `json` or `html`
  for **31 days**, and answers `410 Gone` afterwards.
- `search_metadata` fields, **measured 2026-10-03** on the Free plan: `id`,
  `status`, `created_at`, `processed_at` (both `YYYY-MM-DD HH:MM:SS UTC`),
  `total_time_taken`, `google_flights_url`, `json_endpoint`, `markdown_endpoint`,
  `raw_html_file`, `prettify_html_file`. The timestamps are SerpApi's *processing*
  times, not Google's fare-generation time — so they support
  `observed_at_basis: provider_fetch` and never `provider` (§7).
- **A cache hit is detectable, measured:** an identical repeat of a query returns
  the **same `search_metadata.id`** with the original `created_at`, so the adapter
  can distinguish "served from cache" from "fetched now" rather than guessing.

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
  **Measured 2026-10-03 (Starter plan, by the committed probe):** two
  empty-but-successful searches moved `this_month_usage` by 2, so this is evidence
  rather than vendor prose. It is the fact D9's ledger turns on.
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

**The account endpoint (measured 2026-10-03, on the Free plan).**
`GET https://serpapi.com/account?api_key=…` is **free** and returns `plan_id`,
`plan_name`, `searches_per_month`, `plan_searches_left`, `total_searches_left`,
`extra_credits`, `this_month_usage`, `this_hour_searches`, `last_hour_searches`,
`account_rate_limit_per_hour`, `plan_renewal_date`, `account_status`. Two measured
points the adapter depends on:

- `account_rate_limit_per_hour` read **250** on the Free plan, while this page's
  table documents Free throughput as **50/hr**. So 50 is the *guaranteed* figure
  and 250 the reported allowance; the adapter plans sleeps against the documented
  floor and treats the account value as a ceiling, never the reverse.
- The counters are **lagged** — across a measured batch, `this_month_usage` moved
  *backwards* by one when a cache hit occurred (§10). They are usable for coarse
  reconciliation, not for attributing a single call. **Measured directly
  2026-10-03:** the committed probe counts the searches it issues and compares that
  with the account's count; at one read the account was **six searches behind**.
  That is D20 confirmed from a single controlled run rather than from a batch
  anomaly, and it is why the ledger is client-side.

### 4.4 Response fields, verified shapes

`price_insights` (documented shape, **confirmed by measurement 2026-10-03**):

```json
"price_insights": {
  "lowest_price": 1380,
  "price_level": "low",
  "typical_price_range": [1450, 2000],
  "price_history": [[1785794400, 1375], [1785880800, 1376]]
}
```

- **`suggestions[]` (autocomplete), measured 2026-10-03.** Each suggestion carries a
  `type`, a `name` and a kgmid `id` (`/m/…`), plus an `airports[]` array whose entries
  carry the IATA `id`. So a city suggestion yields one location candidate per airport,
  each carrying the city's kgmid as provider-local provenance — the join key, never
  identity (§6).
- **`deals[]` (deals engine), measured 2026-10-03**, matching the documented field set:
  `destination_id` (kgmid), `name`, `country`, `price`, `average_price`,
  `discount_percentage`, `flight_link`, `serpapi_flight_link`, `thumbnail`,
  `outbound_date`, `return_date`, `departure_airport_code`, `arrival_airport_code`,
  `flight_duration`, `stops`, `airline`, `airline_code`, `description`, `highlights`.
  There is **no itinerary**, so a deal is a destination candidate with an indicative
  price, never an offer (§5).
- **`google_travel_explore`'s populated shape is still unmeasured.** With
  `arrival_area_id=/m/01f62` it answered `200` with
  `error: "Empty results for departure_id: "CDG" and arrival_area_id: "/m/01f62"."` and
  no result container; `interest=beach` was refused outright. The adapter therefore maps
  nothing from an explore response and reports `result_state: unknown` rather than
  reading an absence it cannot interpret.
- `typical_price_range` — two integers, the low and high bound.
- `price_history` — array of `[unix_timestamp, price]` pairs. **Measured
  granularity is daily** (consecutive steps exactly `86400` s), and the sampled
  series held **62 points spanning ~2 months**, ending at the query date. It is a
  *past* series, not a forecast.
- `lowest_price` — "the lowest price among the **returned flights**"; measured
  consistent with that (it equalled the cheapest itinerary in the same response),
  i.e. the current result set, *not* a market low. This distinction must survive
  into our contracts (§9).
- `price_level` — values **measured across probes: `low`, `typical`, `high`**, and
  they behave as a band comparison against `typical_price_range` (measured:
  `lowest_price` 1380 below the range's low bound 1450 → `"low"`). It is still not
  a documented enum, so it is carried and displayed, never switched on.
- **The context is query-dependent.** The same route with `selected_flights_json`
  present returned a different context (`lowest_price` 2103,
  `typical_price_range` `[1900, 3700]`, `price_level` `typical`) than the unpinned
  query (`1380`, `[1450, 2000]`, `low`). A `ProviderPriceContext` therefore belongs
  to the **exact query that produced it** and must never be reused across request
  kinds.

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
| `verify` | `google_flights` + `selected_flights_json` | `verify` | **verification candidate only** — the operation may emit `verified` *only* when a comparison (§8.1) matches identity and price; until then the result stays `observed` |
| next-leg / return leg | `google_flights` + `departure_token` | `search` | `observed` |
| booking options (referral) | `google_flights` + `booking_token` | `search` | `observed` — **never `verified`, never alertable as bookable** (§8). **Not part of V1** (§13) |
| schedule/ops context (optional) | `engine=google` + `flight_result` | — | never a price |

`deals.price` and `explore.flight_price` are **not** bookable and are not the
result of a search for a specific itinerary: they enter the store as `indicative`,
which the scanner already excludes from the bookable baseline and from alerting
(scanner §7.1).

The third column is an **operation ceiling**, not a promise: every request also
carries its `request_kind` in provenance (§6), because the same engine can serve
`search` and `verify` and the stored observation must say which one produced it.

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
- **Provenance is mandatory, and it must record the request's capability mode.**
  An observation produced by this adapter records:

```
provider        "serpapi_google"
engine          google_flights | google_flights_deals | google_travel_explore | google_flights_autocomplete
search_id       search_metadata.id — the 31-day archive handle; without it the observation is not auditable and MUST NOT be stored
currency        the currency the price is denominated in
request_kind    discover | search | verify | next_leg | booking_options
cache_mode      cached | no_cache
coverage_mode   standard | deep | hidden_included | unknown   (§7.1)
retrieval_mode  synchronous | asynchronous
query           the minimised normalised query (§14) — never the raw request URL
```

  `cache_mode` and `coverage_mode` are what make a stored observation
  interpretable later. Without them, "was this fresh?" and "was this complete?"
  are not recoverable from the number, and a re-read of the history would silently
  assume the most flattering answers.

---

## 7. Freshness, caching and `observed_at`

This is the sharpest contract risk in the whole adapter, and the first version of
this document got it wrong by conflating two different facts. They are separate:

| Fact | Established by | **Not** established by it |
|---|---|---|
| **Provider fetch freshness** — a fresh upstream fetch was performed for this request, so the price is as current as the provider can make it | `no_cache=true` | when Google *generated* the underlying fare data |
| **Observation time** — when the underlying data was actually observed | a provider-stamped time in the response | anything, when the provider exposes no such time |

By default (`no_cache=false`) SerpApi may serve a response **up to one hour old**
that is byte-identical to a fresh one. `no_cache=true` removes the provider's
cache from the path — it does **not** convert a fetch time into an observation
time.

Therefore:

1. **Any observation that may alert or verify is fetched with `no_cache=true`.**
   It costs a search; that is the correct price for a claim about "now".
2. **Cached responses may feed only non-alerting series** — provider price
   context, destination exploration, trend history. They are never alert-eligible
   and they never verify anything.
3. `observed_at` is the **earliest trustworthy** timestamp we hold, and its basis
   is recorded explicitly. This adapter may emit exactly three values:

```
observed_at_basis = provider         the response carries a provider-stamped
                                     observation time (field unverified — §17)
observed_at_basis = provider_fetch   a fresh no_cache fetch happened at a known
                                     time, but the provider exposes no observation
                                     time of its own
observed_at_basis = client_receipt   no upstream time at all; we received it now
```

   `no_cache=true` alone justifies **`provider_fetch`** — never `provider`. The
   canonical definition of `observed_at` and its basis lives in
   [`trvl-study-design.md`](trvl-study-design.md) §2.1; this section adds the third
   value to that enum, because a fetch-fresh price is a real and useful thing that
   is still not an observation time.
4. **The adapter must never manufacture a provider-stamped time.** With no
   trustworthy timestamp, `provider_fetch` or `client_receipt` is the honest
   answer, and `freshness` follows from the basis: a `provider_fetch` observation
   may be `live`; a cached one may not.
5. **Measured 2026-10-03:** a provider *fetch* timestamp does exist
   (`created_at` / `processed_at`, UTC), and a cache hit returns the **original**
   one together with the same `search_metadata.id`. So `provider_fetch` is now
   evidence-backed rather than conventional — and, more usefully, the adapter can
   **detect** that it was served from cache, and must then refuse to treat the
   result as fresh no matter what it decides about `no_cache`. `provider` (an
   upstream observation stamp) remains unavailable: nothing in a measured response
   claimed to be one.

### 7.1 Completeness is relative to a declared coverage contract

`deep_search=false` (the default) is documented as *not* matching the browser's
results, and `show_hidden` exists for "View more flights". The honest framing is
therefore **not** "`deep_search=false` ⇒ `partial`" — that would bake a provider
switch into a universal contract. Coverage is measured against what the request
declared:

```
coverage_mode   standard         the provider's standard search (default)
                deep             deep_search=true
                hidden_included  show_hidden=true
                unknown          we cannot tell what the provider served
```

- `partial` describes **unmet declared coverage** — not "the provider has a deeper
  mode we did not ask for". A request declaring `standard` that returns the
  provider's standard result set has satisfied *its* contract.
- The moment the adapter claims exhaustiveness — "every flight Google Flights
  could display" — `standard` cannot support it, and `deep` alone may not either.
- `coverage_mode: unknown` cannot support `complete`.
- `deep_search` and `show_hidden` are recorded in provenance (§6) so a stored
  observation is re-readable with its coverage intact.

---

## 8. `price_status` mapping and verification

The scanner's single lifecycle is `indicative | observed | verified | stale | unavailable`.
Mapping into it:

| Provider situation | Status | Reasoning |
|---|---|---|
| `deals.price`, `explore.flight_price` | `indicative` | Provider says the price is not a specific bookable itinerary |
| `google_flights` result, `standard` coverage | `observed` | Real search; completeness per the declared coverage contract (§7.1) |
| `google_flights` + `selected_flights_json` | `observed` → **verification candidate** | Pinning is not proof; only the comparison in §8.1 may raise it |
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

### 8.1 Verification is a comparison, not an API mode

Pinning an itinerary is not proof that it still costs what we saw. The adapter's
verification operation therefore takes an expectation and returns a **result**:

```
VerificationRequest
    expected_itinerary_identity   # segments: flight numbers + dates (scanner §5.2)
    expected_price
    expected_currency
```

and emits exactly one of:

| Outcome | Condition | `price_status` |
|---|---|---|
| `verified` | same segments, same dates, same flight numbers, current price returned | `verified` |
| `price_changed` | same itinerary, **different** price | `observed` — the new price is an observation, not a confirmation of the old one |
| `substituted` | a different itinerary came back | `unavailable` for the expected itinerary; the substitute is a separate observation |
| `gone` | the expected itinerary is absent | `unavailable` |

Only the first row may set `verified`, and only the probe (§17) can establish that
`selected_flights_json` produces it rather than a substitution. "Same itinerary,
different price" is **not** verification — it is the single most valuable alert
signal this system can produce, and collapsing it into `verified` would destroy
exactly the event the scanner exists to catch.

**Measured 2026-10-03 — the pin does not carry a price.** `selected_flights_json`
on a real one-way itinerary returns HTTP 200 `Success` with a **different response
shape**: top-level `selected_flights[]`, `baggage_prices`, `booking_options`,
`price_insights` — and **no `best_flights` / `other_flights`**. The pinned entry
carries `flights[]`, `total_duration`, `carbon_emissions`, `booking_token` and
`airline_logo`, but **no `price` field at all**. Consequences:

- `verified` is **not reachable from the pin alone**: there is no returned price to
  compare with `expected_price`, so the first row above cannot fire as written.
- The workable design is therefore **fresh re-search + identity match + price
  comparison** — the "search B" form — which the probe found sound: identity
  (`B6 1408`, CDG→JFK, 2026-11-10) was stable across requests, and two searches
  seconds apart returned 1380 and 1378 EUR for the same itinerary, which is
  precisely the `price_changed` signal this row exists to detect.
- The pin's `booking_token` leads to `booking_options[]` — **OTA prices**, which
  §8/D5 already forbids treating as our itinerary's fare. The token route does not
  rescue verification; it changes the product.

---

## 9. Provider price context, and the one trap in `lowest_price`

`price_insights` and `deals.average_price` are **provider-computed price
context**, which this design names `ProviderPriceContext` — deliberately *not*
"the baseline":

```
ProviderPriceContext
    lowest_price            # minimum of the returned set — see rule 1
    price_level             # provider's word; enum unverified
    typical_price_range     # [low, high]
    price_history           # [timestamp, price] pairs
    average_price           # deals only
    discount_percentage     # deals only
```

**It is a supplementary signal, not a replacement for the scanner's cohort
baseline** (scanner §7.1). Google's "typical price" may describe a different
population — different cohort, different market definition, possibly a different
product — from our "historical observed bookable fare for this cohort". Wiring
`SerpApi says cheap` → `scanner says cheap` without understanding that
population would import an unstated model into the one part of the system most
able to produce confident nonsense.

Three rules keep it honest:

1. **`lowest_price` is the minimum of the returned set**, not a market minimum.
   It must never be used as a baseline, and never compared against one computed
   from it.
2. **Provider context is stored as provider claims**, with provenance, and is
   **not rankable or alertable** until the probe establishes its window and
   cohort (**[UNVERIFIED]**: what `average_price` averages over, whether
   `price_history` is route-level or query-level, and what `price_level`'s enum
   contains).
3. **The two must stay separable in the evaluation record**, so a deal can be
   replayed against our baseline alone, against provider context alone, and
   against both — that is how we will find out whether provider context is worth
   anything. It must never be folded into the baseline silently, and a provider
   context value must never contribute to the cohort it is being compared with.

`carbon_emissions.typical_for_this_route` + `difference_percent` is the same
pattern for emissions, useful for `travel_quality_score` context.

---

## 10. Budget and quota

The scanner's budget model (a hard monthly reservation, never consumed by the
scanner itself) generalises to a **per-provider ledger** — and the ledger must not
use one word for three different things:

```
ProviderRequest   an HTTP request we made
ProviderSearch    a search the provider actually performed (cache hits are 0)
BillableSearch    a search the provider counted against our monthly quota
```

| Event | ProviderRequest | ProviderSearch | BillableSearch | Note |
|---|---|---|---|---|
| Successful search with results | 1 | 1 | **1** | normal unit |
| **Successful search with empty results** | 1 | 1 | **1** | an honest `no_results` still costs — the scanner must not probe empty routes freely |
| Failed search (`Error`, 5xx, 429) | 1 | 0–1 | **0** | failures are cheap; retrying a *throughput* 429 is still wrong (§11) |
| Cache hit (identical query, within 1h) | 1 | 0 | **0** | free, but see §7 — free is not fresh |
| Search Archive retrieval | 1 | 0 | **0** | the 31-day archive is free to read |

Concurrency ceiling: the plan's **guaranteed searches per hour** (50 on Free,
200 on Starter) — not a per-second rate. The adapter must be shaped to *spread*
searches across the hour rather than burst.

**Measured 2026-10-03 — do not derive the ledger from the account endpoint.**
Reading `this_month_usage` around individual calls is not reliable. In a controlled
sequence: a fresh search moved it `6→7`; an **identical repeat (cache hit) moved it
`7→6`** — the counter lagged and settled downward once the cache hit was
recognised — and `no_cache` then moved it `7→8`, with `plan_searches_left`
mirroring each move in reverse. Therefore:

- the ledger's counters are **derived client-side** from our own classification of
  the request (fresh → billable; exact repeat within 1 h → not; `no_cache` →
  billable; failure → not), which is deterministic and unit-testable;
- the account endpoint is used for **coarse periodic reconciliation** — a drift
  alarm, not the per-call source of truth;
- the pricing rule is nonetheless confirmed *in direction*: a cache hit consumed
  nothing, and an exact repeat returned the same `search_metadata.id`.

Free tier reality check: 250 searches/month is **≈8/day**, so it cannot sustain
broad or continuous scanning. It *is* sufficient for development, shadow
collection (scanner §11 P2.1–P2.2) and narrowly scoped monitoring at a low
cadence. **Starter ($25 → 1,000/mo, ≈33/day) is the first tier at which modest
continuous scanning is practical** — which tier is worth paying for is an owner
cost decision, not an engineering one, and depends on the planner's cadence.

---

## 11. Failure contract

SerpApi publishes what LetsFG leaves `UNKNOWN`, with one trap worth its own test:

| HTTP | Meaning | Client category | Action |
|---|---|---|---|
| 200 | success (may be empty) | — | empty list → `status: no_results`; completeness **per the declared coverage contract** (§7.1) — never `complete` automatically |
| 503 (body carries `search_metadata.status: Error`) | the provider could not produce a result | `transient` | retry with backoff |
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

- *Throughput* 429 → `rate_limited` (transient). Back off, then resume.
- *Quota* 429 → `budget_exhausted` (terminal for the run). Retrying can never
  help; only a renewal can.

Classification is **fail-safe, not clever**:

```
429 + the known quota message        → budget_exhausted     (terminal)
429 + a known throughput indication  → rate_limited         (transient, back off)
429 + any unrecognised body          → rate_limit_unknown   (transient, bounded backoff,
                                                            body retained for fixture capture)
```

An unrecognised body must **never** be guessed into either bucket: the unknown
case is surfaced, so a provider wording change cannot silently hide quota
exhaustion. Classification is pinned by fixture tests (§17) — this is the single
sanctioned prose-parsing site in the adapter, and the explicit, tested exception
to §3's no-manufacture rule.

**`Retry-After` is an optional signal, never a contract.** SerpApi does not promise
the header (the 429 blog post is generic guidance whose example is illustrative),
so the adapter honours it **when present** and never depends on its presence:

```
if Retry-After present:  honour it (seconds or HTTP-date)
else:                    bounded exponential backoff with jitter
```

Implementing this does not wait on the probe; the probe only establishes whether
SerpApi actually sends the header.

`no_results` versus `timeout` stays non-negotiable: an empty `Success` is a
`no_results`, and a failed search is `timeout`/`failed` with
`completeness: blocked`. An empty success does **not** automatically mean
`complete` — completeness still answers to the declared coverage contract (§7.1),
because "the provider processed my query" and "the universe was exhaustively
searched" are different claims. And per the positive-evidence rule, an empty
result is **not** evidence that a route is unserved — only evidence that this
query, these filters, this time returned nothing.

**And `error` is not a failure signal.** An empty `Success` carries a top-level
`error` string too (§4.2, measured), so the classifier reads
`search_metadata.status` — never the presence of `error`, which would turn every
legitimate empty result into a `timeout` and lose the only distinction the coverage
contract has to work with.

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
- **Live re-check, 2026-10-03:** across five sampled itineraries (1–3 segments,
  CDG→JFK, 2026-11-10), SerpApi's `total_duration` **equalled**
  `sum(segment durations) + sum(layover durations)` in every case — the provider's
  own field is **correct today**, and the historical defect does not reproduce. The
  1,766-affected-totals measurement stands as history, but this document must not
  imply the provider currently drops layovers. The invariant below is kept anyway:
  it costs nothing, it protects against a provider-side regression, and it removes
  any need to trust an aggregate we can compute ourselves.

**Design consequence:** the adapter MUST supply `FlightSegment` records with
origin/destination/arrival times and MUST NOT pass a provider total through
unchecked. The existing Pydantic validators
(`FlightSegment.backfill_duration_seconds`,
`FlightRoute.backfill_total_duration_seconds`) already repair this — so the
adapter's job is to *not defeat them*: no pre-filled totals, no
`model_construct` shortcuts.

> **Adapter invariant (permanent).** A provider aggregate duration is **never
> authoritative** when segment timestamps are available. The model layer owns
> segment duration, layover duration, route duration and timezone normalisation;
> the provider supplies timestamps, not arithmetic. This is a contract test
> (implementation plan P1.2), not a code comment.

### 12.2 Traps in the provider's own interface

| Trap | Detail | Mitigation |
|---|---|---|
| `stops` is not a count | `0`=any, `1`=nonstop, `2`=≤1 stop, `3`=≤2 stops | named constants in the adapter; never pass a raw count |
| `travel_duration` means different things per engine | deals: `1`=1 week, `2`=Weekend, `3`=2 weeks. explore: `1`=Weekend, `2`=1 week, `3`=2 weeks | separate enums; never share one |
| `return_date`/`return_times` applicability | `return_date` forbidden for one-way and multi-city; `return_times` round-trip only; `return_date` required for round trip `type=1` | construct requests by type, not by field-merge |
| Mutual exclusions | `include_airlines` ⊥ `exclude_airlines`; `departure_token` ⊥ `booking_token`; `selected_flights_json` ⊥ tokens and ⊥ `multi_city_json`; `no_cache` ⊥ `async`; `query` ⊥ `travel_duration`/`trip_length` | one request builder per mode, with assertions |
| `exclude_basic` is narrow | only `gl=us` **and** `travel_class=1` | don't offer it as a general filter |
| Provider-side filtering is lossy | docs: if `max_duration` returns nothing, "try increasing it by up to 200 minutes to account for route-specific scheduling variances" | never conclude "no such flight" from a filtered empty set — a filtered empty result is `partial`, not `complete` |
| `deep_search` is **not** a superset | measured 2026-10-03: `deep_search=true` on the same query returned **16** itineraries vs **18** standard, and took 6.87 s vs 1.26 s (≈5.5×). Counts also move between fetches — two searches seconds apart differed by 2 EUR on the same itinerary | never treat `deep` as strictly superior to `standard`; record `coverage_mode` and let completeness answer to the *declared* contract (§7.1) |
| `type=3` ignores `outbound_date` | multi-city dates live inside `multi_city_json` | builder-per-type |
| **`flight_number` formatting varies by response shape** | measured: the pin response returned `"B6 1408"` (with a space) where the search response returned `"B61408"` | normalise (strip whitespace, uppercase) before any identity comparison; never use the raw string as identity |
| **A pinned request changes the response shape, not just the content** | measured: `selected_flights_json` returns `selected_flights` / `baggage_prices` / `booking_options` / `price_insights` and **no** `best_flights`/`other_flights`, and the pinned entry has **no `price`** (§8.1) | parser branches on `request_kind`; the verifier must not expect a price from the pin |
| `total_duration` in the *documented example* is incoherent | 820 minutes shown for a 90-minute flight with a 90-minute layover — while the **live** field matched its own segments in five of five samples (§12.1) | treat the doc example as illustrative (the provider says so itself); keep recomputing, so the correctness of a given response never depends on trusting it |
| **`error` appears on an empty `Success`** | measured 2026-10-03: a filter matching nothing returns HTTP 200, `search_metadata.status: Success`, zero rows, **and** a top-level `error` string | classify on `search_metadata.status` only (§4.2, §11); never branch on `error` |
| **`max_price` filters only `best_flights`** | measured: `max_price=1` returned 0 `best_flights` but **3** `other_flights`, so the response is not empty | an empty case must be built with `include_airlines` (a code no airline uses); a price cap is not a way to make one, and an "empty" fixture built that way measures nothing |
| **`selected_flights_json` is an object, and a one-way pin needs `type`** | measured: the pin answers 400 "should be an object with `outbound` (and optional `return`) keys", and under the default round-trip type answers 400 "is missing the `return` flights array" | build `{"outbound": [...]}` and pin a one-way itinerary with `type=2`; the request builder owns both rules (§12.3) |
| **`arrival_area_id` is a kgmid, not an index** | measured 2026-10-03: `arrival_area_id="1"` answers 400 "should start with `/m` or `/g`" | pass a kgmid from autocomplete; never a numeric area code |
| **`interest` has a vocabulary this document guessed wrong** | measured: `interest=beach` answers 400 "Unsupported `beach` for interest." | encode no interest value until one is measured working — the adapter passes the parameter through and lets the failure contract classify the refusal |
| **An explore answer with no results carries no container at all** | measured: `200` + `error: "Empty results for …"` and no list key | never read explore's absence as "unserved"; its populated shape is unmeasured, so the state is `unknown`, not `confirmed_empty` |

---

### 12.3 Prefer types to assertions

The mutual exclusions above should be enforced by a **request model per mode**
rather than by runtime checks where the language allows it:

```
OneWayRequest | RoundTripRequest | MultiCityRequest
SelectedFlightsRequest | BookingOptionsRequest | NextLegRequest
```

An invalid combination should be *unrepresentable* — `return_date` on a one-way
request, both tokens at once — not merely caught by an assertion that a later edit
can bypass. Where a discriminated union is impractical, one builder per mode with
the assertions inside it is the acceptable fallback; a single function taking every
parameter and hoping is not.

---

## 13. The adapter contract

An implementation satisfies this interface (naming follows the repo's existing
`search_local` style; the concrete module is chosen in the implementation doc):

| Method | Engine | Returns |
|---|---|---|
| `resolve_location(query)` | autocomplete | candidate locations with IATA codes **and** kgmid, marked provider-local |
| `discover(origin, window, constraints)` | travel_explore and/or deals | `indicative` observations + destination candidates, with kgmid recorded in provenance |
| `search(request)` | google_flights | a list of `FlightOffer`s, `price_status: observed`, with completeness |
| `verify(request)` | google_flights + `selected_flights_json` | a **`VerificationResult`** (§8.1): `verified` \| `price_changed` \| `substituted` \| `gone` — never a bare offer claiming `verified` |
| `next_leg(request)` | google_flights + `departure_token` | second-leg offers (round trip / multi-city) |
| ~~`booking_options(itinerary)`~~ | google_flights + `booking_token` | **Not in V1** (see below) |

Non-negotiable properties: every returned offer carries provenance
(§6) and a status at or below its ceiling (§5); the adapter never books, never
holds, never ranks, never alerts; absent configuration it raises the same
"credential missing" error shape the other lanes use.

**Provider-contract additions (2026-10-03, from the fli study).** This adapter is a
provider like any other, so it inherits the shared provider contract: a
machine-readable **capability declaration**, the **`coverage_mode` × `result_state`**
pair with its legal combinations (canonical statement in
[`trvl-study-design.md`](trvl-study-design.md) §2.1), and a **health state** that can
quarantine it without failing a scanner run
([`fli-study-design.md`](fli-study-design.md) §6.2/§6.3/§6.5). Its own characteristic
hazard differs — a 429 whose cause must be parsed, rather than a 200 whose payload
never arrived — but the *routing* is identical, and the scanner must be able to disable
this provider too without reading the resulting absence as market information.

**`booking_options` is out of V1** (§5, §8). It answers "which agency sells this
itinerary, and for how much" — a **referral/merchant** semantic domain the scanner
does not otherwise use. The OTA price is a different product, we cannot book
through SerpApi at all, and it must never alert. If LetsFG ever wants referral
navigation for a human, it arrives as a separate optional enrichment —
`get_booking_options(itinerary) → ReferralOptions`, deliberately **outside**
`FlightOffer` — so referral economics can never leak into fare evaluation. Until
that is actually wanted, the endpoint stays unmodelled rather than speculative.

Public exposure goes through the existing sanitiser
(`to_public_offer`, which masks owner airline and strips sensitive conditions) —
the adapter must not build its own public shape.

---

## 14. Credentials and hygiene

- The key is read from **`SERPAPI_KEY`** — environment only. That is the name
  SerpApi's own current SDK documents (`os.getenv("SERPAPI_KEY")`, verified
  2026-10-03), and adopting the vendor's name deliberately avoids inventing a
  private convention that contradicts their examples. It is **never** written to
  `~/.letsfg/config.json` (contrast: the PFS/Developer credentials have a store
  because the product owns them; a third-party scraping key does not belong
  there). Working agreement rule 1.
- **Names one letter apart, and a measured misfiling.** This lane's variable is
  `SERPAPI_KEY` (SerpApi.com); Serper.dev's are `SERPER_API_KEY` / `SERPER_KEY`.
  Measured in this repository's own `.env` on 2026-10-03: the **SerpApi** key was
  filed under `SERPER_API_KEY` while `SERPER_KEY` held a genuine Serper key — the
  two vendors' values sitting under names that suggest the opposite. The adapter
  therefore resolves **exactly** `SERPAPI_KEY`, must never fall back to a
  Serper-shaped name, and must fail with an explicit "wrong or missing credential"
  message rather than a bare 401, because a mis-filed key is the likeliest failure
  of this adapter's entire configuration surface. **Now live (adoption, 2026-10-03):**
  the key at this machine's `.env:SERPER_API_KEY` is a genuine SerpApi key — verified
  against the account endpoint, Starter plan — so the fix is to file it under
  `SERPAPI_KEY`. The adapter will not read the Serper-shaped name, by D18, and that
  refusal is the point: a fallback would have made the misfiling work here and fail
  wherever else it is deployed.
- SerpApi takes the key as a **query parameter**, so it is present in the request
  URL. Therefore: **never log a full request URL**, never include the URL in an
  exception message, and never surface it in a user-facing error or a report.
  This is a concrete leak vector, not a hypothetical one — the existing PFS/Dev
  lanes pass credentials in headers, so this adapter is the first place in the
  repo where that rule is load-bearing. The test must assert absence from the
  exception text **and** from log lines, metric labels, trace attributes and
  telemetry detail (implementation plan P1.3).
- **Query minimisation, and two different "queries".** There are two artefacts and
  they are not the same thing:

```
provider request   what we send — may carry transient detail the provider needs
local provenance   what we keep — the minimum normalised query required for
                   reproducibility: origin, destination, dates, cabin, pax counts,
                   filters actually applied
```

  The observation store keeps the **second**, never the raw URL (which contains the
  key and possibly more detail than the evaluation needs). Travel intent
  (dates, destinations, party size) is sensitive in aggregate even when each field
  is innocuous, so the store holds what replay requires and nothing more.
- Retention: ZeroTrace is enterprise-only, so search parameters and results are
  retained by SerpApi on every plan we would realistically use. Routing
  user-specific queries through this lane means accepting that; fare observations
  themselves remain domain data (D11) stored by us, not telemetry.

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
| D1 | Add SerpApi as an **optional market provider adapter** inside this repo, behind the existing `FlightOffer` contracts, with no new public surface and no core dependency | **RESOLVED — YES** (owner, 2026-10-03): SerpApi is **adopted**. The lane was declined earlier the same day and re-activated when the owner purchased the Starter plan. D10 still gates code on the probe, and the lane-independent rules in D3–D9 and D15–D20 apply unchanged |
| D2 | Never trust a provider-supplied duration total; recompute from segments and let the existing validators own the result | DECIDED (forced by §12.1) |
| D3 | `no_cache=true` for every observation that may alert or verify; cached responses only for non-alerting series. Basis is `provider_fetch` (never `provider`) unless a provider timestamp is measured (§7) | DECIDED |
| D4 | Classify 429 fail-safe: known quota message → `budget_exhausted` (terminal); known throughput indication → `rate_limited`; **anything unrecognised → `rate_limit_unknown`** (transient), never guessed. Fixture-pinned | DECIDED |
| D5 | Booking options are referral: `observed`, never `verified`, never alertable as bookable — and **out of V1** (§13) | DECIDED |
| D6 | `departure_token`/`booking_token` are request-scoped handles: never persisted, never identity | DECIDED |
| D7 | kgmid is provider-local provenance, not canonical identity | DECIDED |
| D8 | Key from the environment only; never log a request URL (key is a query parameter) | DECIDED |
| D9 | A per-provider budget ledger; empty-but-successful results consume quota, failures and cache hits do not | DECIDED |
| D10 | No adapter code until the probe has run. **Partly satisfied 2026-10-03** (§17): freshness, cache, pinning, price context and coverage were measured. **Re-opened with the adoption**: P0.5 (429 bodies) and the empty-search half of P0.7 are the two gates still unrun, and they bind P1.4 (the 429 classifier) and P2.3 (the ledger) | **DECIDED (gating) — live** |
| D11 | Provider price context (`typical_price_range`, `average_price`) is stored as provider claims; not rankable or alertable until its window/cohort is measured (naming and separation rule in D15) | DECIDED |
| D12 | Plan tier is an owner cost decision. Free (≈8 searches/day) cannot sustain broad or continuous scanning but is sufficient for development, shadow collection and narrowly scoped monitoring; Starter was the minimum paid tier recommended for continuous operation | **CLOSED — Starter purchased** (owner, 2026-10-03). Measured: 1000 searches/month, 200/hour, 0 used at adoption. Enough for shadow collection before any alerting, which is what the tier recommendation was for |
| D13 | `flight_result` (flight status) is out of scope for v1 | DECIDED |
| D14 | The adapter must not import SerpApi's client package; standard library HTTP only | DECIDED |
| D15 | Provider price fields are a **`ProviderPriceContext`**, not the scanner's baseline: stored as provider claims, non-rankable and non-alertable until their window/cohort is measured, and separable in the evaluation record (§9) | DECIDED |
| D16 | Freshness is two facts, not one: **provider fetch freshness** vs **observation time**. `observed_at_basis` gains a third value, `provider_fetch`, and `observed_at` for a `no_cache` fetch is never reported as `provider` (§7) | DECIDED |
| D17 | Completeness is measured against a **declared coverage contract** (`coverage_mode`), not against a provider switch; an empty success is not automatically `complete` (§7.1, §11) | DECIDED |
| D18 | The key's environment name is the vendor's documented **`SERPAPI_KEY`**, not a private invention; the local provenance keeps a minimised query, never the raw URL (§14) | DECIDED |
| D19 | **Verification is a fresh re-search plus identity match, not `selected_flights_json`** — the pin returns the itinerary without a price (§8.1, measured), and its `booking_token` route leads to OTA referral prices, which D5 excludes | DECIDED (on measurement) |
| D20 | The budget ledger is **derived client-side**; the account endpoint is a reconciliation signal only, because its counters lag and settle backwards on cache hits (§10, measured) | DECIDED (on measurement) |

---

## 17. Open probes (must run before code)

> **Re-opened 2026-10-03 with the lane** (§16 D1 resolved **yes**, the same day it was
> declined). The measurements already recorded below stand as evidence; what is owed now
> is the two probes that never ran — **P0.5 (the 429 bodies and `Retry-After`)** and the
> **empty-search half of P0.7** — because D10 gates adapter code on the probe having
> run. Their `null`s in the capability profile are the plan's P0 acceptance criteria,
> not permanent facts.

One script, `-m live`-marked, refusing to run without an explicit env flag, on a
key the owner provides. It measures — recording raw responses as evidence — and
the two experiments that decide the contracts come first:

**A. Cache behaviour on the same query.** `Q`, then `Q` again, then
`Q + no_cache=true`, recording `search_metadata.id`, any timestamps, the price and
the itinerary identity for each:

```
same query, cached   → same search_id?  same timestamps?  same price?
same query, no_cache → new search_id?   new timestamp?    different price?
```

This is what establishes the freshness model (§7): whether a cache hit is
detectable at all, and whether any timestamp actually moves.

**B. Semantic equivalence of `selected_flights_json`.** Not "did it return 200",
but: search A → choose itinerary I → `selected_flights_json(I)` → search B, then
compare segment identity, dates, flight numbers, carrier, cabin and price. It must
answer **same itinerary? same fare? same price?** — that is what decides whether
`verified` (§8.1) is ever reachable, and it is the difference between a
verification primitive and an itinerary-pinning curiosity.

Then:

1. `search_metadata`'s full key set on a fresh and on a **cache-hit** search: is
   there a trustworthy observation timestamp? (Decides §7 rule 5.)
2. Whether a **429 carries `Retry-After`**, and the exact body each of the two 429
   causes produces — the quota wording and the throughput wording (feeds D4).
3. `price_level`'s full enum across several routes (feeds §4.4).
4. `price_history`'s real granularity and horizon, and `deals.average_price`'s
   window — route-level or query-level? (Feeds §9/D15.)
5. Whether an empty-result search really consumes quota (`GET` the account
   endpoint before and after) — the ledger's correctness depends on it (§10).
6. Coverage: the same query with and without `deep_search`/`show_hidden` — how many
   itineraries appear, and does `total_duration` disagree with its own segments
   (re-checking §12.1 against live data)?
7. *(Deferred with §13's V1 exclusion)* `booking_options[]`'s seller field set —
   probe only if referral navigation is actually wanted.

**Output: a provider capability profile, not just resolved markers.** The probe's
primary artefact is machine-readable, because the implementation contract is
derived from it:

```json
{
  "provider": "serpapi_google",
  "probed_at": "2026-10-03",
  "account": { "plan_id": "free", "searches_per_month": 250, "account_rate_limit_per_hour": 250 },
  "observed": {
    "cache_detectable": true,
    "cache_hit_same_search_id": true,
    "provider_timestamp": "created_at / processed_at (UTC) — provider FETCH time, not upstream observation time",
    "retry_after_present": null,
    "selected_flights_same_itinerary": true,
    "selected_flights_returns_price": false,
    "price_level_values": ["low", "typical", "high"],
    "price_history_granularity_seconds": 86400,
    "price_history_points_observed": 62,
    "empty_search_billable": null,
    "coverage_delta_deep": -2,
    "ledger_from_account_endpoint_reliable": false,
    "duration_totals_match_own_segments": true
  }
}
```

**The committed artefact** is `sdk/python/tests/fixtures/serpapi/capability_profile.json`,
written by `sdk/python/tests/test_serpapi_live.py`; the block above is the shape, the
file is the evidence. It carries a `citations` map giving the fixture behind every
value and an `unresolved` list, so a `null` can never be mistaken for a `false`.

**Re-measured 2026-10-03 on the Starter plan by that probe** (24 searches of 1000):
the cache pair shares both `search_metadata.id` and `created_at` while `no_cache`
returns a new id; the pin returns `selected_flights` with **no price** and a matching
identity; `price_history` is 62 `[timestamp, price]` rows at exactly 86400 s;
durations matched their own segments in **16 of 16** samples; `deep_search` returned
the same 16 rows as standard on this route (delta **0**, against **-2** measured on
the Free plan) so the delta is query-dependent and never a superset; two
empty-but-successful searches were **billed** (usage +2); and the account counter was
**six searches behind** the number the probe had issued at the read taken during the
empty-search experiment. Only `retry_after_present` remains `null`, and only because
P0.5 needs the limiter provoked.

**Measured 2026-10-03** (Free plan, **7 searches spent** — account usage moved
1 → 8, leaving 242 of 250). The two decisive experiments returned:

- **Cache (A):** an identical repeat returned the **same `search_metadata.id`**
  and the original `created_at` ⇒ cache hits are detectable. `no_cache=true`
  produced a new id, a fresh `created_at`, **and a different price two seconds
  later (1380 → 1378 EUR for the same itinerary)** — the first hard evidence that
  the cache can hide a price move, and the reason §7 rule 1 is not optional.
- **Pinning (B):** `selected_flights_json` returns the pinned itinerary with
  **no price** (§8.1), so `verified` is unreachable from the pin; the reachable
  design is fresh re-search + identity match, where identity was stable
  (`B6 1408`, CDG→JFK, 2026-11-10) modulo the `flight_number` formatting trap
  (§12.2).
- **Timestamps (P0.4):** `created_at`/`processed_at` exist ⇒ `provider_fetch` is
  evidence-backed; nothing claims to be an upstream observation ⇒ `provider`
  stays unavailable.
- **Price context (P0.6):** daily `price_history` (86400 s steps), 62 points over
  ~2 months, `price_level` ∈ {`low`,`typical`,`high`}, and the context is
  **query-dependent** (§4.4).
- **Coverage (P0.8):** `deep_search=true` returned *fewer* itineraries (16 vs 18)
  at ≈5.5× the latency ⇒ not a superset (§12.2).
- **Durations (P0.8):** provider totals matched their own segments in 5 of 5
  samples ⇒ the historical defect does not reproduce (§12.1).

**Additional shapes measured 2026-10-03, closing gaps the original probe list did not
name** (4 searches): `google_flights_autocomplete` (`suggestions[]` → `airports[]`, §4.4)
and `google_flights_deals` (`deals[]`, §4.4) are now verified and their fixtures are
committed alongside the rest. `google_travel_explore` was **not** captured: its
populated container is still unmeasured, `interest=beach` was refused, `arrival_area_id`
turned out to be a kgmid, and an area with no matching service answers `200` with an
`error` string and no container at all. Those three refusals are recorded as traps
(§12.2) because each would otherwise be rediscovered as a "bug" in our own parser.

**Still open:** the 429 bodies and `Retry-After` (P0.5) alone. It needs a request
deliberately refused by the provider's limiter, which is why it waits on an explicit
owner go-ahead and has its own second opt-in in the probe
(`LETSFG_SERPAPI_PROBE_LIMITER=1`). Every other **[UNVERIFIED]** marker in this
document is resolved — including the empty-result billing question, which the
committed probe settled by measurement.

Every `null` above is a contract still blocked, and a `true`/`false` must cite the
fixture that showed it. Cost: single-digit searches, plus whatever the
empty-result probe consumes. Free tier (250/mo) is enough. Acceptance: the fixture
set, the capability profile, and every **[UNVERIFIED]** marker in this document
either resolved with evidence or re-stated as still open.

---

## 18. Priority and gating

| Phase | Content | Gate |
|---|---|---|
| **P0 — provider contract probe** | §17: freshness/cache, timestamp, 429 bodies, `selected_flights_json` semantics, price-context windows, quota accounting → the **capability profile** | **Satisfied 2026-10-03**: key supplied, Starter plan purchased, and P0.1/P0.2/P0.3/P0.4/P0.6/P0.7/P0.8 landed as committed fixtures and a capability profile. **P0.5 (the 429 bodies) is the only one left**, and only because provoking a refusal needs an owner decision |
| **P1 — contract freeze** | Turn the measured evidence into frozen contracts: the four axes, `price_status` + verification outcomes, `observed_at_basis`, completeness/coverage, provenance, `ProviderPriceContext`, ledger counters — plus the pure-function guards that encode them (enum traps §12.2, duration invariant §12.1, 429 classification D4, credential redaction §14). **P1.1 landed 2026-10-03** in `sdk/python/letsfg/connectors/provider_contract.py`, guarded by `sdk/python/tests/test_provider_contract.py` | Free of network; new test files are authorized with D1 |
| **P2 — the adapter** | Module, request models, mapping, provenance, budget ledger, error mapping | **D1 resolved yes (2026-10-03)** — unblocked, after P1 |
| **P3 — scanner wiring** | planner → scheduler → observation store → verification → alerting | Outside this repo |

P1 is deliberately a **freeze**, not a backlog: it is the step between evidence and
implementation, so the adapter is written against measured behaviour rather than
against a plausible reading of the documentation. With D1 resolved yes, both P1 and P2
are authorized; the plan's order of work applies, and the conformance suite is written
against the **existing first-party lanes** first so the newcomer has to fit rather than
the other way round.

---

## 19. Traceability

| This document | Depends on / feeds |
|---|---|
| §2, §9 | [`first-class-fare-scanner-design.md`](first-class-fare-scanner-design.md) §7.1 (baseline), §6 (planner, flexible dates) |
| §7, D16 | [`trvl-study-design.md`](trvl-study-design.md) §2.1 — the canonical `observed_at`/`observed_at_basis` definition, extended here with `provider_fetch`; scanner §5.4 (freshness) |
| §7.1, §11, D17 | the envelope's completeness axis (trvl study §2.1, §2.4) — `partial` means unmet *declared* coverage |
| §8, §8.1 | scanner §5.4 (`price_status`), §5.2 (itinerary identity), §6.5 (verification); `price_changed` is an alert signal, not a verification |
| §9, D15 | scanner §7.1 — provider price context is a separate series, never a baseline substitute |
| §11 | [`trvl-study-design.md`](trvl-study-design.md) §6 Q8 (LetsFG's own limiter is still `UNKNOWN`); the `no_results` vs `timeout` rule |
| §12.1 | `sdk/python/letsfg/models/flights.py` invariants; `sdk/python/tests/test_duration_timezone.py` |
| §12.2, §12.3, §14 | [`flight-finder-study-implementation.md`](flight-finder-study-implementation.md) defect-class items (guards that pin provider quirks) |
| §13 | [`architecture-guide.md`](architecture-guide.md) — the provider boundary belongs there, not in a new design surface (trvl study D13) |
| §16 D1 | trvl study §7 ownership model (provider acquisition is owner-reserved) |

---

## 20. Review record

- *Probe, 2026-10-03* — first pass over SerpApi's documented surface after an
  external summary of it was found to be right in structure and wrong on
  flexible dates, silent on `selected_flights_json`, and silent on cost and
  quota. Findings folded into §4, §5, §10, §12.2. No live request was made.
- *Review #1, 2026-10-03 — architecture approved, semantics corrected*, applied in
  full. The recurring fault was **semantic overclaim**: the first version stated
  provider capabilities as though they were established contracts. Changes:
  §2 no longer claims the gaps are *closed* (price context is supplementary,
  flexible dates are *discovery*, and the capability grid states what is and is
  not authoritative); §3 gained the no-manufacture-from-absence rule; §5 demotes
  `verify` to a **verification candidate**; §6 records `cache_mode`/`coverage_mode`/
  `request_kind`/`retrieval_mode`; §7 was rewritten around **two facts** (provider
  fetch freshness ≠ observation time) with a third `observed_at_basis` value and a
  coverage contract rather than "`deep_search=false` ⇒ partial"; §8.1 defines
  verification as a **comparison** with four outcomes, so `price_changed` is not
  silently called `verified`; §9 renames provider baselines to
  **`ProviderPriceContext`** and makes them separable, non-rankable claims; §10
  splits the ledger into request/search/billable; §11 fixes the `200`/`503`
  contradiction, makes 429 classification **fail-safe** (`rate_limit_unknown`),
  makes `Retry-After` an optional signal, and stops an empty success being
  auto-`complete`; §12.1 elevates the duration rule to a permanent invariant and
  §12.3 prefers typed request models to assertions; §13 takes `booking_options` out
  of V1; §14 adopts the vendor's **`SERPAPI_KEY`** name and adds query
  minimisation; §16 adds D15–D18 and revises D3/D4/D5/D12; §17 puts cache and
  semantic-equivalence experiments first and emits a machine-readable capability
  profile; §18 makes P1 a contract **freeze**. The mandated five are all in:
  fetch-vs-observation freshness, verification-as-candidate, completeness relative
  to declared coverage, fail-safe 429 (with the HTTP contradiction fixed), and
  provider price context kept out of the baseline.
- **The probe ran, 2026-10-03** (Free plan key, 7 searches spent — usage 1 → 8 of
  250): cache behaviour, `search_metadata` timestamps, the pin's semantics,
  price-context shape and granularity, coverage under `deep_search`, the duration
  re-check, and a controlled billing sequence. Results and the filled capability profile are in
  §17; the facts landed in §4.2/§4.3/§4.4, §7, §8.1, §10 and §12.
  **One interim reading in this document's own working notes was wrong and is
  corrected here:** the pinned search was first read as returning "zero
  itineraries", because the *unpinned* response keys (`best_flights` /
  `other_flights`) were assumed. The pin returns its result under
  **`selected_flights`** with no price — a different shape, not an empty result.
  The correction is recorded rather than quietly fixed, because the same mistake
  is exactly what the `request_kind` parser branch (P1.1/P2.2) exists to prevent.
- *Resolved, 2026-10-03* — **the lane was declined** before the last two probes could
  run (P0.5's 429 bodies, P0.7's empty-search billing), so **D1 is answered no** and D12
  is moot. Nothing in this document is pending: §16 records the closure, and §2 and §17
  are retained as the record of what would have been measured and why.
- *Re-activated, 2026-10-03* — the owner purchased the SerpApi **Starter** plan and
  reversed D1 the same day it was declined. Measured account state is in the front
  matter; §16 D1/D10/D12, §17 and §18 were reopened in place, and the implementation
  plan's status, owner-decision table and order of work were restored with them. The
  decline record immediately above is kept deliberately: it is why the contracts are the
  shape they are, and it is the state the code would regress to if any of them were
  loosened.
