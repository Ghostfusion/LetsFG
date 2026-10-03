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
| SerpApi provider ([design](serpapi-provider-design.md)) | rented acquisition | $25–150+/mo, quota | theirs, hedged by Legal Shield |
| **fli** | **owned acquisition** | **free** | **ours — and Google's** |

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
dates not yet tried". Our budget ledger (SerpApi design §10) already separates billable
from non-billable; this adds the planner-side rule that *unattempted* work must not be
charged as *failed* work.

**P4. Per-date cost is explicit and modelled before the sweep starts.** One fetch per
date, a hard cap, a measured worst case, and a memory estimate (§4.6). Our planner
should refuse to plan a sweep whose cost it cannot state up front.

**P5. A deterministic, network-free deep link per itinerary.** `build_flight_booking_url`
turns airports + dates + flight numbers into a stable `tfs` URL, so a human can be
handed a link without any provider call. That is a clean separation of
*identification* from *fetching*, and it is exactly the shape our alert payloads want:
the alert carries a link the user can act on, and it never requires the alerting path
to call the provider again.

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

**P9. fli as a fourth data source** (after the two LetsFG lanes and the SerpApi
provider). Technically attractive, free, MIT; but it is *owned acquisition of an
unowned interface*, which is precisely the ownership question of D10. It needs the
owner, and it needs the P0 live probe first (§12).

**P10. The `tfs` encoder as a dependency.** MIT permits vendoring; the encoder is the
durable part (§3). But it encodes *Google's* schema, so it inherits Google's
unilateral-change risk, and adopting it means maintaining a protobuf field map.
Defer until a decision to own acquisition exists at all.

**P11. The `AF_initDataCallback` parse path.** Adopting the parser means adopting the
responsibility of noticing when Google changes the blob — a canary obligation we would
have to build (§3 lesson 3).

### 5.3 `[REJECT]` — no

**P12. fli as the *primary* fare source.** Its own numbers disqualify it: ~20–45 rows,
no multi-city, no booking options, three dropped filters, and **children/infants
thinning results to zero in premium cabins** (§4.5). A First-Class scanner whose
coverage silently depends on the passengers' ages is not a system we can reason about —
and the failure is invisible without the honest-empty distinction of §4.7.

**P13. Its MCP server or CLI as product surface.** We have our own (14 advertised MCP
tools, a CLI, two SDKs). Wrapping a second MCP server would duplicate a surface and
leak a third party's tool contract into ours.

**P14. Treating "the provider said 200" as coverage.** §4.4 is explicit that a
client-side filter cannot back-fill Google's server-side one, and §4.7 that empties
have causes. Any adoption must carry `coverage_mode`-style provenance (SerpApi design
§6/§7.1) rather than inheriting Google's implied total.

---

## 6. Provider comparison — what each source can and cannot answer

| Capability | PFS / Dev API (ours) | SerpApi provider | **fli** |
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

**The asymmetry that matters:** fli gives *prices now* with no baseline, and SerpApi
gives *baselines and context* as well as prices. Our scanner's hardest problem is the
baseline ([`first-class-fare-scanner-design.md`](first-class-fare-scanner-design.md)
§7.1), and fli contributes **nothing** to it — a "cheap flight today" from fli is an
observation, not a judgement. If both ever land, their roles are complementary rather
than competing: fli for free fresh observations and per-date sweeps, SerpApi for
price context — with neither allowed to become the baseline (§7.1, provider-context
rule).

---

## 7. What adopting fli would and would not change

**Would:** add a free, direct, low-latency observation source and a genuine per-date
sweep primitive; make our scan cost independent of a vendor's quota; give the planner
a provider that can price 30–93 dates in one bounded operation.

**Would not:** provide any baseline or "is this unusual" signal; enable booking
(never — booking remains PFS-only); replace the first-party engine; remove the need
for SerpApi's price context; or reduce our legal exposure. It would **add** a
maintenance obligation: an undocumented interface that changed once already in the
observed window, owned by Google, with no notice period.

---

## 8. Decision register

| # | Decision | Status |
|---|---|---|
| D1 | Record this study; adopt the *patterns* P1–P8 into our contracts and planner design | DECIDED |
| D2 | Do **not** treat fli as a primary fare source (P12) | DECIDED |
| D3 | Do **not** expose fli's MCP/CLI as product surface (P13) | DECIDED |
| D4 | Any fli adoption carries `coverage_mode`/provenance and never inherits an implied total (P14) | DECIDED |
| D5 | The **sweep breaker** (P2) and **loaded-empty vs never-loaded** (P1) are adopted as design requirements for the scanner's planner, independent of whether fli is ever used | DECIDED |
| D6 | fli as a **fourth, optional, owner-gated** provider lane | **OPEN — owner** (D10 ownership) |
| D7 | Whether to vendor the `tfs` encoder (MIT) or depend on `flights` | **OPEN — owner**, gated on D6 |
| D8 | No adoption before the P0 live probe: nothing here is measured from *our* network, region or account | **DECIDED (gating)** |
| D9 | Never make fli's thinning-with-children behaviour a silent coverage loss: a party with children/infants must produce `partial`, not `no_results` | DECIDED |
| D10 | The repository's own drifts (§4.9) are recorded as evidence *for* our `docs-claims` guard, not as a criticism to act on | DECIDED |

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

| Phase | Content | Gate |
|---|---|---|
| **P0 — live probe** | Install `flights` in a throwaway venv; run its own live set; then measure questions 1–5 from our environment | Owner go-ahead; it makes live requests to Google under our IP |
| **P1 — adopt the patterns** | P1, P2, P3, P4, P5, P6 into the scanner/planner contracts and their guards (docs first; guards are new test files) | Free of network; `OWNER` for new files (rule 4) |
| **P2 — optional provider** | A fli-backed adapter behind the existing provider contract, refusing to be primary | **D6 owner decision** |
| **P3 — vendoring** | Only if D6 is yes and D7 says vendor | Owner |

---

## 12. Traceability

| This document | Depends on / feeds |
|---|---|
| §1, §6 | [`serpapi-provider-design.md`](serpapi-provider-design.md) §2/§9 — the provider portfolio and why price context is not a baseline |
| §4.5, §4.7, §8 D1/D5/D9 | [`first-class-fare-scanner-design.md`](first-class-fare-scanner-design.md) §6 (planner cost and fan-out), §9 (alert honesty), and the envelope's `no_results` vs `timeout` rule in [`trvl-study-design.md`](trvl-study-design.md) §2.1 |
| §4.6 | scanner §6.6 — the budget reservation, now with a per-date cost shape |
| §4.9 | `test/docs-claims.test.mjs` — the guard this repository's drift argues for |
| §5.3 P14, §8 D4 | SerpApi design §6/§7.1 (`coverage_mode`, provider provenance) |
| §8 D6 | trvl study §7 ownership model (provider acquisition is owner-reserved) |
| §4.10, §5.1 P6 | the provider adapter contract, §13 of the SerpApi design |

---

## 13. Review record

- *Probe, 2026-10-03* — static read of the repository metadata, README, published API
  reference and author's write-up; no request made to Google and nothing installed.
  Findings: the transport rewrite and its measured consequences (§3, §4), the
  loaded-empty/never-loaded distinction (§4.7), the sweep breaker economy (§4.6), the
  absence of any price-context signal (§4.4, §6), and two internal documentation
  drifts (§4.9).
- *Pending* — the P0 live probe, and the D6/D7 owner decisions.
