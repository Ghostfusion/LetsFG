# fli Study — Design

**Status:** study only. No code from this document exists, and none may be written
without an owner decision — adding a provider is new scope under the working
agreement, and provider acquisition is the one thing
[`trvl-study-design.md`](trvl-study-design.md) §7 reserves to the owner (D10).

**Subject:** [`github.com/punitarani/fli`](https://github.com/punitarani/fli) — a
Google Flights client that reaches Google's *own* flight data by reverse
engineering, rather than by scraping HTML or renting a third-party API.

**Licence: MIT.** Unlike the trvl study (PolyForm Noncommercial — ideas only), MIT
means its *code* is legitimately borrowable, not just its patterns. That is a
material difference and it shapes §5.

> **MIT answers one question only: may we reuse fli's code under its copyright
> licence? It does *not* answer: may we access or automate Google's service this
> way?** Those are separate, and the second is the one with teeth (§6.4, §9). A
> permissive licence on the client is not permission from the service.

**Terminology — "free".** Wherever this document says fli is *free*, it means
**zero marginal API fee**: no per-search price and no subscription. It is not free
in total cost, and this study is explicit about that (§6.4): fli converts a cash
cost into maintenance, monitoring, breakage and legal exposure. The comparison that
matters is not "cheap versus expensive" but **cash cost down, engineering and
operational risk up**.

**Provenance:** static probe, 2026-10-03 — the repository metadata and README, the
published API reference (models, search), the author's project page, and the repo's
own file tree. **No request was made to Google, and nothing was installed or run.**
Where the repository contradicts itself, both readings are recorded rather than
resolved by guessing (§4.9, §4.10).

**Companion:** [`fli-study-implementation.md`](fli-study-implementation.md).

---

## 1. Why this repository is worth a study

We have three ways to reach fare data and are short of none of them:

| Source | Posture | Cost | Fragility |
|---|---|---|---|
| PFS lane (own product) | first-party | free to search | ours |
| Developer API lane (own product) | first-party | 200 free searches per booking, then $0.01 | ours |
| SerpApi provider ([design](serpapi-provider-design.md)) | ~~rented acquisition~~ — **declined 2026-10-03** | — | — |
| **fli** | **owned acquisition** | **zero marginal API fee** | **ours — and Google's** |

The SerpApi row is struck out: the owner declined that lane on 2026-10-03 (its design
§16 D1 is resolved **no**). That removal matters to the comparison in §6 — it takes the
**hedged, rented alternative** off the board, so the real choice is no longer "own
acquisition or rent it" but "own it, or do without that fare universe". F2 in
[`fli-study-implementation.md`](fli-study-implementation.md) §7 is unhedged in exactly
that sense.

fli is the only option in that table that is both free and *directly* sourced, and
it is the only one whose weaknesses are documented with measurements rather than
marketing. It is therefore worth studying for two independent reasons: as a
**candidate provider**, and as a **worked example of deep provider integration**
whose failure modes are exactly the ones our scanner must reason about honestly.

---

## 2. What it is, factually

| Fact | Value |
|---|---|
| Language / port | Python 3.10–3.13 (CI matrix); a 1:1 TypeScript port lives in-tree and publishes as `fli-js` |
| Licence | **MIT** |
| Traction | 3,205 stars · 399 forks · 19 open issues |
| Python distribution name | **`flights`** (`pip install flights`) — *not* `fli` |
| CLIs / servers | `fli` (CLI: `flights`, `dates`, `multi`), `fli-mcp` (stdio), `fli-mcp-http` (streamable HTTP) |
| Maintenance signals | Dependabot, ruff, uv, Docker + devcontainer, MkDocs site, `AGENTS.md`/`CLAUDE.md`, and a **`live-canary.yml`** workflow that watches for Google-side breakage |
| Test posture | Offline unit tests by default; live tests behind `.markers` gates (`pytest -m live --live`, plus a separate 100-case fuzz-gated live set); the transport encoder has golden dumps (`fli-js/scripts/dump_tfs_goldens.py`) |
| Auth | none — Google's endpoints require no key |
| Mandatory dependency shape | `pydantic`, plus HTTP clients (historically `curl_cffi` for browser impersonation, now a `httpx`-based client with a token-bucket limiter) |

Data files `data/airports.csv` and `data/airlines.csv` ship in-repo, so airport and
airline resolution is local (relevant to §8: we already depend on `airportsdata`).

**Bundle size and platform notes** are not documented here — that is a P0 probe item
(implementation plan), not a guess.

---

## 3. How it works, and why the transport changed

fli's original approach (author's write-up, Dec 2024–Feb 2025) called Google's
internal RPCs directly:

```
POST .../FlightsFrontendService/GetShoppingResults     # itineraries
POST .../FlightsFrontendService/GetCalendarGraph       # cheapest-dates graph
```

with browser impersonation (`curl_cffi`, `impersonate="chrome"`), a security-prefix
strip (`)]}'`), and index-mapped parsing of unlabelled nested arrays. That era
produced the documented index map (price at `data[1][0][-1]`, duration at
`data[0][9]`, airline/flight number at `fl[22]`, airports at `fl[3]`/`fl[6]`,
datetimes from separate date/time arrays) — a genuinely impressive piece of reverse
engineering, and a warning about how much of it is a *coincidence of one response
shape*.

**The transport has since been forced to change**, and this is the most important
fact in the study:

> Since **2026-08**, `GetShoppingResults` and `GetCalendarGraph` require an
> `x-goog-batchexecute-bgr` header that only the page's own JavaScript can produce.
> A plain HTTP client gets **HTTP 200 with no payload**.

fli's adaptation: issue `GET https://www.google.com/travel/flights?tfs=<protobuf>`
and read the results out of the page's inline **`AF_initDataCallback` blob keyed
`ds:1`**.

Three lessons fall out of that paragraph, and they apply to us regardless of
whether we ever adopt fli:

1. **Unilateral, silent breakage is the norm, not the exception.** The failure mode
   was a 200 with an empty payload — indistinguishable from "no flights" unless the
   client checks. Our `no_results` vs `timeout` distinction exists for exactly this;
   §8.1 is its provider-side twin.
2. **The adaptation authority is the responder, not us.** Response shape was
   controlled by Google; when the shape moved, an entire integration strategy had to
   be rewritten. Any design that depends on an undocumented response *layout* is
   borrowing stability it does not own.
3. **It was detected because someone looked.** A canary workflow and a
   "1 in 60 pages arrive with no blob" measurement are the difference between a
   library that adapts and one that rots.

The `tfs` encoder is the durable asset here: `FlightSearchFilters.encode()` produces
URL-quoted JSON of `[None, <nested filter structure>]`, with each filter mapped to a
specific field (e.g. seat type is field 9, passengers field 8). Field *numbers* in a
protobuf schema are far stickier than JSON array indices — which is precisely why the
encoder survived a transport rewrite that killed the parser's old assumptions.

---

## 4. Documented limitations, with the numbers attached

This section is the reason the study is worth writing. Every item is the provider's
own measured statement, and several are *directly* applicable to our scanner's
honesty rules.

### 4.1 Three filters cannot be expressed at all
`emissions`, `bags`, and `exclude_basic_economy` have **no `tfs` field** and cannot
be reconstructed from the decoded rows, so they are **dropped with a warning**.
(Emissions *data* still arrives per itinerary — `co2_emissions_g`,
`co2_emissions_typical_g`, `co2_emissions_delta_pct` — but the filter does not.)

### 4.2 Multi-city is unsupported
`SearchUnsupportedError` — Google loads those results client-side through the gated
RPC, so the page carries no rows. The documented workaround is to search each leg
separately.

### 4.3 Booking options are unavailable
`get_booking_options` calls the gated `GetBookingResults` and **raises
`SearchRejectedError`**. Per-flight booking **deep links** still work: they are built
offline from airports, dates and flight numbers via a deterministic `tfs` token
(`build_flight_booking_url`), so the same itinerary always yields the same URL with
no network round-trip.

### 4.4 Fewer rows than the old API
**~20–45 itineraries** per search, and — critically — *a client-side filter cannot
back-fill the list the way Google's server-side filter did*. Filtering after the
fetch is not equivalent to filtering before it.

### 4.5 Children and infants thin the results — sometimes to nothing
Measured by the provider (2026-09): JFK→LHR economy **23 rows for one adult, 16 with
an infant**; SFO→NRT business **9 rows for one or two adults, 0 with a child**. Extra
*adults* cost nothing.

> An empty result for such a search does not mean the route has no flights.

And, importantly, fli does **not** paper over which case occurred: `search()` warns
only when the *fetched page* came back with zero rows, not when the caller's own
airline/price/duration/window filter removed rows Google did return. That distinction
is exposed programmatically as **`sparse_passenger_mix`** (a property on both
`SearchFlights` and `SearchDates`), reset at the start of every call so a stale value
cannot leak.

### 4.6 Date searches cost one page fetch per date, and are bounded
There is **no calendar grid on the page**, so a range is priced date by date. Limits
and costs as documented:

- one `SearchDates.search` covers at most **93 dates**; wider raises `ValueError`
  (the API reference separately lists **`MAX_DAYS_PER_SEARCH = 61`** — see §4.9);
- **42 page fetches measured** for a range that never successfully loads, bounded at
  45, i.e. up to **~135 HTTP requests** once client retries multiply in, taking
  ~4 seconds — and the same whether the range is 30 or 93 days, because the breaker
  fires early. Unbroken, 93 dates would have cost **279 fetches / up to 837 HTTP
  requests**;
- peak memory: **several hundred MB** of pages and parsed JSON for 93 dates across
  10 workers;
- the breaker is `(5 + worker count) × 3`, so raising concurrency raises the bound
  proportionally.

### 4.7 The breaker's failure classes are kept separate — the detail that matters most

> A sweep that never manages to load a single page … gives up after a handful of
> dates rather than paying the retry budget on all of them. **Only pages served
> without results count towards that: a timeout or a dropped connection says nothing
> about the dates not yet tried, so those never abandon a sweep.**

And it disarms permanently on the first successful page — "even an empty one" — so it
*cannot* catch a sweep that is mostly timeouts around one lucky date, which is why
`SearchDates.search` separately raises when **nothing priced and at least half the
attempted dates never loaded**. A minority of failures alongside real results (or
alongside a confirmed-empty range, `None`) returns normally with one warning naming
the counts.

That is a small, precise failure taxonomy: *loaded-and-empty*, *never-loaded*, and
*partially-loaded* are three different states with three different consequences.

### 4.8 Intermittent empty pages, and the environment knobs
- **~1 request in 60** returns HTTP 200 with no `ds:1` blob; retried up to **twice**
  (0.5 s, then 1.5 s) before raising `SearchParseError`.
- **`FLI_SOCS_COOKIE`** — EU/EEA IPs are redirected to Google's consent interstitial,
  which serves no `ds:1` blob; the client sends a pre-accepted `SOCS` cookie by
  default and allows it to be overridden or emptied.
- **`FLI_CA_BUNDLE` / `CURL_CA_BUNDLE` / `REQUESTS_CA_BUNDLE`** — for TLS-intercepting
  corporate proxies; a bad bundle raises `SearchCertificateError`, which is
  deliberately **not retried**, and the bundle is read once per worker thread.

### 4.9 Two places where the repository contradicts itself
Recorded, not resolved:

- **Date-range cap.** The README says at most **93** dates per search; the API
  reference lists `MAX_DAYS_PER_SEARCH = 61`. One of them is stale; only running it
  settles which.

  > **Implementation rule: no production sweep may rely on a range cap until the
  > runtime-observed cap has been established.** The chain is forced —
  > README says 93 · reference says 61 → **P0 determines actual behaviour** →
  > adapter constant → test → docs. Nobody may write `MAX_DAYS = 93` from the README,
  > and the constant must not exist in a plan until a measurement produced it.
- **Transport documentation drift.** The README's "Search transport" section states
  the RPC is gated and the client reads the page blob, while the API reference still
  presents `BASE_URL = .../GetShoppingResults` and `BOOKING_URL = .../GetBookingResults`
  as the class's live endpoints, and `get_booking_options` is still described as a
  public method with a "live-token limitation" while the README says it *raises*.

A repository that documents its own breakage this well and *still* has two stale
surfaces is not a criticism of the author — it is the strongest available argument
for our own `docs-claims` test (`test/docs-claims.test.mjs`), which exists to make
exactly this drift fail CI instead of being discovered by a reader.

### 4.10 Concurrency
`SearchFlights` is **not thread-safe**: it caches the last shopping-session id (for
booking-token derivation) and `sparse_passenger_mix`, so two concurrent searches on
one instance race. The documented remedies: a fresh instance per request (what its own
MCP server and CLI do), or pass `session_id` explicitly. The HTTP client is likewise
described as not thread-safe for concurrent `post`/`get` across threads, with a
**global token bucket at 10 req/sec** arbitrating.

---

## 5. Pattern classification

### 5.1 `[ADOPT]` — patterns to take into our design and guards

**P1. Separate "loaded and empty" from "never loaded".** fli distinguishes a fetched
page with zero rows from a request that never returned a page, and warns only in the
first case (§4.5, §4.7). Our envelope already separates `no_results` from `timeout`;
this study adds the provider-side obligation: *the reason a result set is empty must be
recorded as a distinct, machine-readable fact*, not inferred from emptiness.

**P2. A sweep breaker with the right arithmetic.** `(5 + workers) × 3`, disarmed by the
first successful page, plus a separate raise for "mostly timeouts around one lucky
date". Our scanner's Search Planner (§6) fans out over dates and destinations and
currently has *no* such breaker — a blocked provider would burn the whole run's budget
before failing. This is the single most portable idea in the repository.

**P3. Failure classes are not interchangeable when accounting for cost.** fli counts
only *empty-but-served* pages against its breaker; timeouts "say nothing about the
dates not yet tried". Our budget ledger already separates billable from non-billable;
this adds the planner-side rule that *unattempted* work must not be charged as *failed*
work — now stated directly in
[`first-class-fare-scanner-design.md`](first-class-fare-scanner-design.md) §6.7, since
the SerpApi lane that first articulated it has been declined.

**P4. Per-date cost is explicit and modelled before the sweep starts.** One fetch per
date, a hard cap, a measured worst case, and a memory estimate (§4.6). Our planner
should refuse to plan a sweep whose cost it cannot state up front.

**P5. A deterministic, network-free itinerary link per observation.**
`build_flight_booking_url` turns airports + dates + flight numbers into a stable
`tfs` URL, so a human can be handed a link without any provider call. That is a clean
separation of *identification* from *fetching*, and it is exactly the shape our alert
payloads want: the alert carries a link the user can act on, and it never requires the
alerting path to call the provider again.

> **Terminology is strict here: this is a *Google Flights itinerary link* (a discovery
> deep link). It is never called a "booking link" in our code or docs.** A name that
> reads as "this fare can be booked" would eventually be consumed as booking
> authority, and **booking authority belongs to the PFS lane alone** (§6.5). fli
> cannot book; a link that happens to open a page with a "Continue" button is not a
> booking capability, and naming it one is how that distinction dies.

**P6. Non-thread-safe clients get a fresh instance per request.** Their own MCP server
and CLI do this rather than sharing state. Our provider adapters must state their
thread-safety contract explicitly, and default to per-request instances.

**P7. Environment knobs for the ugly realities.** Consent-region cookies and corporate
TLS interception are *configuration*, named and documented, with the unretryable case
called out. If we ever own acquisition, these are the two variables we will need.

**P8. Golden tests for an encoder, live tests behind a marker.** Encoding is tested
offline against dumps; anything needing the real provider is gated and separate. That
is the same split our repo already uses (`-m "not live"`), and it is the reason their
transport rewrite was survivable.

### 5.2 `[DEFER]` — real, but not now, and not ours to decide

**P9. fli as a fourth data source** — **REJECTED 2026-10-03** when F2 declined owned
acquisition. Technically attractive, free, MIT; but it is *owned acquisition of an
unowned interface*, which was exactly the ownership question of D10, and the owner
answered it no. The reasoning above is kept rather than deleted, because it is the
assessment any future candidate of the same shape would have to displace.

**P10. The `tfs` encoder as a dependency.** MIT permits vendoring; the encoder is the
durable part (§3). But it encodes *Google's* schema, so it inherits Google's
unilateral-change risk, and adopting it means maintaining a protobuf field map.
Defer until a decision to own acquisition exists at all.

**P11. The `AF_initDataCallback` parse path.** Adopting the parser means adopting the
responsibility of noticing when Google changes the blob — a canary obligation we would
have to build (§3 lesson 3).

### 5.3 `[REJECT]` — no

**P12. fli is rejected as the *primary* fare source for the First-Class scanner.**
Not "its numbers disqualify it" in general — 20–45 itineraries is not inherently
disqualifying for every application. The argument is specific, and it is the
conjunction that decides it:

```
First-Class scanner
  +  coverage-sensitive alerts
  +  parties with children/infants (measured down to 0 rows in premium cabins)
  +  multi-city (raises)
  +  booking options (unavailable by design)
  +  any price context at all (absent)
        ↓
insufficient as the PRIMARY source for this system
```

It remains a perfectly usable Google Flights client — for a narrower question than
ours (§6.3 makes that explicit as a capability declaration rather than an opinion).

**P13. Its MCP server or CLI as product surface.** We have our own (14 advertised MCP
tools, a CLI, two SDKs). Wrapping a second MCP server would duplicate a surface and
leak a third party's tool contract into ours.

**P14. Treating "the provider said 200" as coverage.** §4.4 is explicit that a
client-side filter cannot back-fill Google's server-side one, and §4.7 that empties
have causes. Any adoption must carry `coverage_mode`-style provenance (§6.2 here;
canonical statement in [`trvl-study-design.md`](trvl-study-design.md) §2.1) rather than
inheriting Google's implied total.

---

## 6. Provider comparison and integration contract

| Capability | PFS / Dev API (ours) | SerpApi — **declined** | **fli** |
|---|---|---|---|
| Specific-date itineraries with prices | ✓ | ✓ | ✓ (~20–45 rows) |
| Flexible-date *discovery* | ✗ (fan-out only) | ✓ (deals ranges, `month`) | ✓ (~1 fetch/date, ≤93) |
| **Price context / baseline signal** | ✗ | ✓ `price_insights`, `deals.average_price` | **✗ — none** |
| Re-price / pin an itinerary | ✓ (search again) | ✓-ish (pin returns **no price**) | ✓ (search again) |
| Booking / holding a fare | ✓ **this is the only lane that can** | ✗ | ✗ |
| Multi-city | ✓ | ✓ (`multi_city_json`) | ✗ `SearchUnsupportedError` |
| Booking options / OTA referral | ✓ | ✓ (`booking_options`) | ✗ `SearchRejectedError` |
| Cost per search | free (quota'd) | $0.01+ / plan | free |
| Legal exposure of scraping | none (our product) | hedged from $150/mo (Legal Shield) | **unhedged, our liability** |
| Breakage risk | ours | theirs, contractual | **Google's, unilateral** |
| Maintenance burden | ours | theirs | **theirs, but MIT — can be abandoned** |

The SerpApi column above is retained because it is the counterfactual this study
measured against, not because it is reachable — that lane was declined on 2026-10-03
(design §16 D1), so no row in it is available to us.

**The asymmetry that matters — and it survives the decision:** fli gives *prices now*
with no baseline. It was SerpApi that would have contributed baselines and context, and
that lane is gone. Our scanner's hardest problem is the baseline
([`first-class-fare-scanner-design.md`](first-class-fare-scanner-design.md) §7.1), and
fli contributes **nothing** to it — a "cheap flight today" from fli is an observation,
not a judgement. The honest statement of the portfolio is therefore: **no external
provider supplies price context at all**, and the baseline is built from our own
observation store. That is not a loss, because §7.1 already requires the baseline to be
"historical observed bookable fare" from *our* observations — a provider's context was
always an optional contributor, never a dependency. Any future contributor stays a
claim, never the baseline (§6.1).

### 6.1 Two questions, not one: acquisition and context

The comparison above collapses into two different concepts, and keeping them apart is
the whole provider architecture:

```
Acquisition   "what fares can I observe?"          ← every provider can answer this
Context       "what does this fare mean?"          ← only some can help, and none may decide
```

> **Invariant (frozen): no provider may establish a fare baseline merely because it
> returned a price.**

That sentence exists to prevent one specific future mistake:

```
fli says $8,500  →  scanner treats $8,500 as normal  →  a false "normal" verdict
```

An observation is evidence about *a fare*; a baseline is a claim about *a population*.
The scanner's baseline subsystem owns that claim
([`first-class-fare-scanner-design.md`](first-class-fare-scanner-design.md) §7.1). The
**`ProviderPriceContext`** rule was worked out against the SerpApi lane before that lane
was declined ([`serpapi-provider-design.md`](serpapi-provider-design.md) §9/D15), and it
is stated as a general contract precisely so it outlives the vendor: a provider's price
context carries the *provider's* cohort and window, not ours, so it is a claim — never
our baseline. A provider that helps with context is a *contributor to* an evaluation,
never its author.

### 6.2 Coverage and result state are two fields, not one

The envelope already separates execution from coverage
([`trvl-study-design.md`](trvl-study-design.md) §2.1 — the **canonical** statement of
the pair and its legal combinations; the table below is the provider-side expansion of
it). The pair exists because a single enum cannot express
"we asked and got nothing" *and* "we could not ask properly" — and the difference
between provider coverage, result count and the scanner's own conclusion is exactly
where a false "no flights" is born:

```
coverage_mode   complete | partial | unavailable
result_state    results | confirmed_empty | unavailable
```

| `coverage_mode` | `result_state` | Valid | Means |
|---|---|---|---|
| `complete` | `results` | ✓ | the declared scope was searched and produced rows |
| `complete` | `confirmed_empty` | ✓ | the declared scope was searched and produced nothing |
| `partial` | `results` | ✓ | rows, under narrowed or degraded coverage |
| `partial` | `confirmed_empty` | ✓ | nothing under narrowed/degraded coverage — **not** absence |
| `unavailable` | `unavailable` | ✓ | we learned nothing: timeout, block, parse failure |
| `unavailable` | `results` / `confirmed_empty` | **✗** | contradictory — no usable evidence cannot carry a result |
| `complete` | `unavailable` | **✗** | contradictory |

> **Hard invariant: `no_results` is prohibited as a scanner-level conclusion when
> provider evidence indicates coverage degradation.** `{status: no_results,
> coverage_mode: partial}` is semantically contradictory, and an adapter allowed to
> emit it will eventually produce "there are no flights on this route" when the
> scanner simply could not see them (§4.5, D9).

### 6.3 Capabilities are declared, not discovered by trying

A coverage-sensitive scanner must ask "can this provider answer this question?"
**before** spending anything, rather than trying and interpreting the failure (§6.2).
Every provider therefore declares a machine-readable capability model:

```yaml
provider_capabilities:
  specific_date_search:  true
  date_range_search:     true        # 1 operation per date
  multi_city:            false       # SearchUnsupportedError
  first_class:           true
  children:              degraded    # measured: fewer rows, sometimes zero (§4.5)
  infants:               degraded
  booking_options:       false       # SearchRejectedError
  booking:               false       # never — PFS owns booking (§6.5)
  price_context:         false       # no insight/history types in the models
  itinerary_links:       true        # deterministic, offline (§5.1 P5)
  server_side_filters:   partial     # three filters dropped (§4.1)
```

`degraded` is deliberately **not** `false`: children and infants work — they narrow
coverage. The difference between "cannot" and "can, with reduced coverage" is the
difference between a refusal and a `partial` label (§6.2). The planner consumes this
model; it never infers capability from an error message.

### 6.4 Cost is a provider capability too

The planner must not assume one unit of work equals one request
([`first-class-fare-scanner-design.md`](first-class-fare-scanner-design.md) §6.1):

```yaml
request_cost:
  max_units_per_operation: <measured>   # 93 claimed by the README, 61 by the reference (§4.9)
  requests_per_unit:       1
  max_retry_multiplier:    <measured>   # up to 3× HTTP per unit
  units_per_hour:          <measured>
  requires_consent_cookie: true         # EU/EEA (§4.8)
```

Every number comes from P0 — never from a README, never from another project's formula.

**Two breakers, not one, and neither copied.** A single breaker cannot answer both
"should I keep trying dates?" and "is this provider still trustworthy?":

```
search coverage breaker   "keep trying?"     counts only evidence-bearing outcomes
provider health breaker   "trustworthy?"     counts contract-level anomalies
```

Adopt the **concept** of a bounded sweep breaker; never adopt fli's arithmetic
(`(5 + workers) × 3`, §4.6), which reflects *its* worker model. Encoding it would make
our scanner architecture depend on one provider's implementation. The split is
normative for our provider contract; the constants are ours to calibrate, and the
planner owns only the global budget and safety limits.

### 6.5 Provider health, fallback, and the absolute boundaries

fli's characteristic failure is unusual enough to need its own state: **HTTP 200 with
no expected payload** means *"Google's contract may have changed"*, not *"no flights"*.
So a provider carries health, and the scanner routes on it instead of reading a
degraded provider's emptiness as market information:

```
HEALTHY ──repeated parse failures──▶ DEGRADED ──canary failure──▶ UNTRUSTED ──operator──▶ DISABLED
```

Failure routing — normative, and the reason fli would be genuinely useful rather than
"another scraper":

```
fli request
 ├── results          → accept observation
 ├── confirmed_empty  → accept as absence ONLY if coverage_mode = complete (§6.2)
 ├── partial          → never conclude absence; never alert as absence (§6.2, D12)
 ├── timeout          → per-search retry inside the provider's own budget
 ├── parse failure     → provider-health event, NOT a search result
 └── unavailable      → fallback permitted per D11 (owner decision)
```

**Absolute boundaries** — frozen, and the point of this section:

```
fli  ├── NEVER booking authority          (PFS owns booking; §5.1 P5)
     ├── NEVER baseline authority          (no provider establishes a baseline; §6.1)
     ├── NEVER implicit complete coverage  (coverage is declared and graded; §6.2)
     └── NEVER turns a provider failure into no_results   (§6.2, D9)
```

### 6.6 Provider admission gate

D6 stops being a judgement call and becomes a checklist. No provider reaches
production until all ten hold:

| # | Admission criterion |
|---|---|
| 1 | The target cohort works — First-Class, long-haul, specific dates |
| 2 | Coverage semantics verified: every `coverage_mode` × `result_state` cell behaves per §6.2 |
| 3 | Failure taxonomy verified: every class in §6.5 is distinguishable |
| 4 | The unit cap is **measured**, not read (§4.9, §6.4) |
| 5 | Request cost measured, including the retry multiplier |
| 6 | Concurrency behaviour established — its client is not thread-safe (§4.10) |
| 7 | A parser failure is detected as a provider-health event, not an empty result |
| 8 | Itinerary-link construction verified deterministic (§5.1 P5) |
| 9 | The provider can be **disabled without failing a scanner run** |
| 10 | No provider result can be interpreted as booking authority (§6.5) |

**The economic model, stated plainly** (this is the honest version of "free"):

```
rented acquisition (SerpApi — declined)   cash cost ↑   engineering/maintenance ↓   legal exposure hedged
owned acquisition  (fli)                  cash cost ↓   engineering, monitoring, breakage and legal exposure ↑
```

The comparison is now lopsided in a way worth stating: the hedged column **cannot be
bought**, by owner decision. Choosing fli is therefore choosing the unhedged side of this
table outright, not choosing it over a purchasable alternative — which is precisely what
F2 asks.

### 6.7 Rented routes to Google Flights: Serper.dev, measured

F2 settled *owned* acquisition. The rented equivalent is a vendor that already
carries Google Flights data, and the question there is not price but permission.
**Serper.dev was measured on 2026-10-03** — key `SERPER_KEY` in this machine's
`.env`, the name one letter from the SerpApi key's
([`serpapi-provider-design.md`](serpapi-provider-design.md) §14) — and it is **not
a route to Google Flights at any endpoint**:

| Measurement | Request | Observed |
|---|---|---|
| No flights vertical | `POST https://google.serper.dev/{flights,travel,hotels}` | `404 {"message":"Not found"}` for all three; of the surface probed, only `/search` answers |
| A fare query carries no fare data | `POST /search`, `q="flights Gdansk to Barcelona"` | Top-level keys `organic`, `peopleAlsoAsk`, `relatedSearches` only — no price, no itinerary, no flight widget |
| Google is refused by the scraper | `POST https://scrape.serper.dev`, `{"url":"https://www.google.com/travel/flights?q=…"}` | `400 Invalid "url" parameter - Google is not allowed` — on both the `scrape.` host and `google.serper.dev/scrape` |
| The refusal is Google-specific | Same endpoint, `kayak.com/flights/GDN-BCN/2026-06-15` | `200`, page text returned — a policy on the domain, not a broken endpoint |

Two consequences, both of which would otherwise be rediscovered:

- **The prices in a fare query are third-party marketing copy, not fares.** The
  organic snippets do carry numbers ("$24", "PLN109*", "start at $28") — Skyscanner,
  Kiwi and airline landing pages rendered as search snippets. They have no
  itinerary, no date binding and no seller identity, so under the §6.1 invariant
  they are not even an observation, let alone a baseline; a funnel that harvested
  them would be building on advertising text.
- **"Rent the Google Flights graph" has no vendor we hold.** The class of vendor
  that could supply it — a SERP or scraping API — excludes Google by name here. So
  both routes to that fare universe terminate: owned acquisition (fli) was
  declined, and the rented one refuses the domain outright. This does not reopen
  F2; it removes the hedge the §6.6 economic table showed F2 choosing *against*,
  which leaves "do without that fare universe" as the only available answer rather
  than one of two preferences.

> Scope of what was measured: one key, one route, one day, one query per endpoint.
> It establishes that **no flights vertical exists** and that **Google is refused**;
> it does not enumerate every Serper endpoint. The refusal message is Serper's own.

---

## 7. What adopting fli would and would not change

**Would:** add a free, direct, low-latency observation source and a genuine per-date
sweep primitive; make our scan cost independent of a vendor's quota; give the planner
a provider that can price 30–93 dates in one bounded operation.

**Would not:** provide any baseline or "is this unusual" signal; enable booking
(never — booking remains PFS-only); replace the first-party engine; supply any price
context (and with the rented lane declined, nothing external does); or reduce our legal
exposure. It would **add** a
maintenance obligation: an undocumented interface that changed once already in the
observed window, owned by Google, with no notice period.

---

## 8. Decision register

| # | Decision | Status |
|---|---|---|
| D1 | Record this study; adopt the *patterns* P1–P8 into our contracts and planner design | DECIDED |
| D2 | fli is **not the primary fare source for the First-Class scanner** (P12) — the conjunction in §5.3 decides it, not a general judgement about the library | DECIDED |
| D3 | Do **not** expose fli's MCP/CLI as product surface (P13) | DECIDED |
| D4 | Any provider adoption carries the `coverage_mode` × `result_state` pair and its legal combinations (§6.2); no provider inherits an implied total (P14) | DECIDED |
| D5 | The **sweep breaker** (P2) and **loaded-empty vs never-loaded** (P1) are adopted as design requirements, but as **two** breakers — coverage and provider health (§6.4/§6.5) — independent of whether fli is ever used | DECIDED |
| D6 | fli as a **fourth, optional, owner-gated** provider lane — gated on the capability declaration (§6.3) and all ten admission criteria (§6.6), so the decision is a checklist rather than a judgement call | **RESOLVED — NO (2026-10-03).** F2 declined owned acquisition, so P0 never ran and fli is **not admitted**. The gate itself stands: it is what a future candidate would have to pass |
| D7 | Dependency boundary, three options: **(A)** depend on `flights`; **(B)** vendor only the required encoder subset under MIT; **(C)** reimplement the minimal encoding behind our own contract. **In all three, fli's internal types never appear in our provider contract** — the boundary is LetsFG → our provider interface → adapter → fli, never LetsFG → `flights.SearchFlights` → everything fli | **RESOLVED — (C) (2026-10-03)**, recorded while the lane is not admitted: no dependency, no name-collision hazard, and the encoder is ours when Google moves the interface. Inert until D6 could ever be yes |
| D8 | No adoption before the P0 probe, which must be **acceptance-test-shaped** (§10, implementation plan §2): nothing here is measured from *our* network, region or account, and **no production sweep may rely on a range cap until the runtime-observed cap is established** (§4.9) | **DECIDED (gating)** |
| D9 | Never make fli's thinning-with-children behaviour a silent coverage loss: a party with children/infants must produce `partial`, not `no_results` — and `no_results` is prohibited at scanner level whenever coverage is degraded (§6.2) | DECIDED |
| D10 | The repository's own drifts (§4.9) are recorded as evidence *for* our `docs-claims` guard, not as a criticism to act on | DECIDED |
| D11 | **Provider health and fallback:** does a fli failure automatically permit fallback to our own PFS lane — the only fallback that exists, the rented lane having been declined on 2026-10-03 — and which failure classes **quarantine** a provider versus trigger an ordinary per-search retry? (§6.5) | **RESOLVED — no automatic fallback (2026-10-03).** A failing provider surfaces as `DEGRADED` and the run reports reduced coverage; silently substituting another lane would change the evidence basis of the comparison, which is what the coverage contract exists to prevent |
| D12 | **May an observation with `coverage_mode = partial` participate in an alert?** Recommendation: **no**, unless the alert itself states that its basis is partial coverage | **RESOLVED — no (2026-10-03).** An alert asserts a conclusion about the market, and partial coverage cannot support it; the qualified variant was rejected because the qualification would have to survive into every delivery surface |
| D13 | Provider failure classification, concurrency limits, retry policy and sweep characteristics belong to the **provider** contract; the planner owns only the global budget and safety limits (§6.4) — fli's `(5 + workers) × 3` is never encoded as a scanner rule | DECIDED |
| D14 | The capability declaration (§6.3) and the admission gate (§6.6) are the objective prerequisites for D6; a provider that fails any of the ten is not admitted, regardless of how attractive its cost is | DECIDED |

---

## 9. Non-goals

- **No code, no dependency, no install** from this study (rule 4; and no live request
  was made to Google).
- **No replacement** of the first-party engine, the PFS booking lane or the developer
  lane.
- **No opinion on fli's internal quality beyond what is documented** — this probe read
  the public surface, not the implementation line by line.
- **No scraping in our own name.** If acquisition is ever owned, it is a decision with
  legal consequences that this document deliberately does not take.

---

## 10. Open questions the study cannot answer

1. **Does the page transport work from our network, region and IP?** The EU/EEA
   consent interstitial and corporate TLS interception are both environmental
   (§4.8); our own environment is untested.
2. **93 or 61 dates?** (§4.9).
3. **Rows and latency in our actual target cohort** — First-Class, long-haul, specific
   dates — as opposed to the provider's economy examples.
4. **Behaviour under sustained load**: the token bucket is 10 req/sec, but nothing
   documents what Google does to a client that sustains it, and `live-canary` exists
   precisely because that answer is not stable.
5. **Does the inline blob expose any price-context fields** fli does not surface
   (Google's own price graph is in the Flights UI)? The models carry no
   insight/history type, but the blob's full contents are not exhaustively documented.

Each maps to a P0 item in the implementation plan.

---

## 11. Priority and gating

**The gate order is not negotiable, and one half of it is easy to miss.**

```
F2 = YES ──▶ F1 / P0 acceptance protocol ──▶ PASS → fli may be admitted as an
    │                                         optional observation provider
    │                                      └ FAIL → fli rejected as a provider
    └ F2 = NO ──▶ no probe. Do not run P0.
```

> **ANSWERED 2026-10-03: F2 = NO.** The owner declined owned acquisition. **No request
> was ever made to Google**, P0 will not run, and fli is **not admitted** as a provider
> (D6 resolved **no**). The retained value below is what the study was for; the
> acquisition mechanism is rejected, on the same footing as the rented lane.
>
> The decision was taken with the **hedged alternative already declined** (SerpApi, its
> design §16 D1) — so it was made unhedged, which is the honest form of the question:
> there was no vendor left to buy the legal posture from, and "no" means this fare
> universe is simply not observed by us rather than rented instead.

The "NO" branch forbids the probe for a reason that is not procedural tidiness:
**the probe is itself an exercise of the owned acquisition that F2 exists to
authorize.** Querying Google's undocumented interface from our IP *is* the act in
question — measuring it before deciding whether we are willing to do it would decide
it by doing it. That is why the answer had to come first, and why nothing was measured.

F2 was a **business, legal and ownership decision, not a technical one.** The technical
case for probing was already made by this study; the open question was whether we were
willing to own the consequences of querying an undocumented Google interface directly
(§6.6, §9), and the answer is that we are not.

**If F2 is NO, the study has still paid for itself** — nothing here is wasted:

```
retained   P1 contracts · failure taxonomy · the two breakers · coverage semantics ·
           planner cost model · capability declaration · admission gate
rejected   fli as an acquisition mechanism
```

**F2 = NO closes this study**, and nothing in it is reopened by that: the acquisition
mechanism is rejected, P0 never runs, and the P2 provider work is withdrawn. Should the
question ever be revisited, it starts from the answers already recorded here — D6 (**not
admitted**, and what admission would require), D7 (**C**: reimplement the minimal
encoding behind our own contract), D11 (**no automatic fallback**) and D12 (**partial
coverage may not alert**) — rather than from a blank page.

| Phase | Content | Gate |
|---|---|---|
| **P0 — live probe** | The acceptance protocol in [`fli-study-implementation.md`](fli-study-implementation.md) §2: named cohorts A–G, explicit pass/fail per probe, and measurements for cap, cost, concurrency and health behaviour | Owner go-ahead; it makes live requests to Google under our IP |
| **P1 — adopt the patterns** | P1, P2, P3, P4, P5, P6 into the scanner/planner contracts and their guards (docs first; guards are new test files) — **done 2026-10-03**, extended by this review to *two* breakers | Free of network; `OWNER` for new files (rule 4) |
| **P2 — optional provider** | A fli-backed **observation** provider behind the existing provider contract: capability-declared (§6.3), coverage-aware (§6.2), independently disableable, and never authoritative for booking or baseline inference (§6.5/§6.6) | **D6 owner decision**, on the admission gate |
| **P3 — vendoring** | Only if D6 is yes and D7 says vendor | Owner |

---

## 12. Traceability

| This document | Depends on / feeds |
|---|---|
| §1, §6 | [`serpapi-provider-design.md`](serpapi-provider-design.md) §2/§9 — the provider portfolio and why price context is not a baseline. **That lane is declined**; it is cited as the source of the contract, not as a reachable provider |
| §4.5, §4.7, §8 D1/D5/D9 | [`first-class-fare-scanner-design.md`](first-class-fare-scanner-design.md) §6 (planner cost and fan-out), §9 (alert honesty), and the envelope's `no_results` vs `timeout` rule in [`trvl-study-design.md`](trvl-study-design.md) §2.1 |
| §4.6 | scanner §6.6 — the budget reservation, now with a per-date cost shape |
| §4.9 | `test/docs-claims.test.mjs` — the guard this repository's drift argues for |
| §5.3 P14, §8 D4 | [`trvl-study-design.md`](trvl-study-design.md) §2.1 — the canonical `coverage_mode` × `result_state` statement; the SerpApi design (declined) stated the provider-provenance half first |
| §8 D6 | trvl study §7 ownership model (provider acquisition is owner-reserved) |
| §4.10, §5.1 P6 | the provider adapter contract, §13 of the SerpApi design — that lane is declined, but the contract is lane-independent |

---

## 13. Review record

- *Probe, 2026-10-03* — static read of the repository metadata, README, published API
  reference and author's write-up; no request made to Google and nothing installed.
  Findings: the transport rewrite and its measured consequences (§3, §4), the
  loaded-empty/never-loaded distinction (§4.7), the sweep breaker economy (§4.6), the
  absence of any price-context signal (§4.4, §6), and two internal documentation
  drifts (§4.9).
- *Review #1, 2026-10-03 — study approved, implementation held*, applied. Verdict:
  **approve the study, do not approve P2 yet**; stop expanding the factual research and
  tighten the contracts. The ten must-change items all landed: **P12** reworded to
  "rejected as primary *for the First-Class scanner*" with the conjunction made explicit
  (§5.3); **coverage formalised** as `coverage_mode` × `result_state` with legal
  combinations and a prohibition on `no_results` under degraded coverage (§6.2);
  **two breakers** split — search coverage vs provider health (§6.4/§6.5), with fli's
  arithmetic explicitly *not* encoded (D13); **P0 turned into acceptance tests** (plan
  §2, cohorts A–G, per-probe pass/fail); **the 61/93 cap gated** on measurement (§4.9,
  D8); **a capability declaration** added (§6.3); **fallback and quarantine policy**
  added with the health state machine (§6.5); **MIT ≠ Google permission** stated in the
  front matter; **D11** (health/fallback) and **D12** (partial coverage and alerts)
  added as owner decisions, plus D13/D14. Also folded in: the acquisition-vs-context
  invariant and "no provider establishes a baseline by returning a price" (§6.1); the
  admission gate (§6.6); strict terminology — *itinerary link*, never "booking link"
  (§5.1 P5); "zero marginal API fee" instead of "free" (front matter, §6.6); and D7
  widened to three options with fli's types forbidden in our contract.
- *Probe, Serper.dev, 2026-10-03* — live, five requests on the key in this machine's
  `.env`. Serper.dev is not a route to Google Flights at any endpoint: no flights
  vertical (`404` on `/flights`, `/travel`, `/hotels`), a fare query returning only
  organic marketing snippets, and the scraper refusing Google by name
  (`400 Invalid "url" parameter - Google is not allowed`). Recorded as §6.7 with the
  scope of the evidence; F2 is not reopened by it.
- *Pending* — the P0 acceptance protocol (it needs live requests to Google from our
  network), and the owner decisions D6, D7, D11, D12.
