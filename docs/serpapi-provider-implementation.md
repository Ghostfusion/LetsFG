# SerpApi Google Flights — Implementation Plan

**Status: ACTIVE — owner decision, 2026-10-03: LetsFG will use SerpApi** (design §16 D1
resolved **yes**, reversing the same day's decline, after the owner purchased the
Starter plan — measured in the design's front matter: 1000 searches/month, 200/hour).
P0's two unrun probes are re-opened and gate the code; P1 and P2 are live, in that order;
P3 remains another repository's scope. The cancelling header this replaced is kept in
§9's resolution log rather than erased, because the plan did not change when the decision
did — only its status did.

Companion: [`serpapi-provider-design.md`](serpapi-provider-design.md). Section
references in `§` point there.

---

## 1. How to read this plan

| Tag | Meaning |
|---|---|
| `PROBE` | A measurement against the live provider. Needs a key and an explicit opt-in flag; consumes quota. |
| `CODE` | New code. **Requires an owner go-ahead** (rule 4). |
| `DOCS` | Documentation only. |
| `DEFECT` | An existing defect this work touches. |
| `OWNER` | A decision or an action only the owner can take (spend, credential, legal). |
| `DEFERRED` | Recorded, deliberately not done yet, with the reason. |

Promise discipline: no item may be marked done on the strength of a plan, a
scaffold, or a green unit test that does not exercise the real provider.

**What changed in review #1 (2026-10-03).** P1 is now a **contract freeze** between
evidence and implementation rather than a set of incidental unit tests; the probe
gained the two decisive experiments (cache behaviour, `selected_flights_json`
semantic equivalence) and must emit a machine-readable **capability profile**;
verification became a comparison with four outcomes; completeness became relative
to a declared coverage contract; the 429 classifier became fail-safe; and provider
price context got its own type instead of being called a baseline.

---

## 2. Phase P0 — provider contract probe (`PROBE`, gating)

Design §17. The adapter's freshness, verification and completeness rules cannot be
finished without this, so it is a **gate**, not an optimisation.

**Status 2026-10-03: partly satisfied, and re-opened.** A valid SerpApi key was found on
this machine (filed under a Serper-shaped name — see design §14), and the decisive
experiments ran for 7 searches on the Free plan (account usage 1 → 8): cache behaviour
(P0.2), `search_metadata` (P0.4), pin semantics (P0.3), price-context shape (P0.6),
coverage delta and the duration re-check (P0.8), and a controlled billing sequence
(P0.7, partially). Results and the filled capability profile are in design §17.
**Still open, and now owed: P0.5 (429 bodies, `Retry-After`) and the billability of an
empty-but-successful search (P0.7).** They need deliberately quota-consuming requests,
which the Starter plan (1000/month, 0 used) makes affordable. They were cancelled for
part of 2026-10-03 while the lane was declined and are live again with D1 resolved
**yes**: the capability profile's `null`s are filled by them, not kept.

### P0.1 A `live`-marked probe script and its capability profile `PROBE` `OWNER`

**Files:** new `sdk/python/tests/test_serpapi_live.py`, plus a committed
capability-profile JSON. (The old `google_checkout_live_sweep.py` neighbourhood this
pointed at was deleted earlier the same day — see §6/D-1 — and stays deleted: the
replacement is a new live test written against the current contract, not a revival of the
connector-era sweep.)

**Change:** the experiments in P0.2–P0.8, each recording raw responses as fixtures.
The script MUST:
- refuse to run unless `SERPAPI_KEY` is set **and** an explicit opt-in env flag is
  set (`LETSFG_SERPAPI_PROBE=1`), so neither CI nor a casual `pytest` run can fire
  it;
- be marked `live`, so the existing `pytest -m "not live"` exclusion covers it;
- print a redacted request summary — **never the URL** (the key is a query
  parameter, design §14);
- fetch the account/plan endpoint before and after the quota-consuming probes.

**Acceptance:** fixtures committed; the capability profile written with a citation
for every `true`/`false` and an explicit `null` for every contract still blocked;
every **[UNVERIFIED]** marker in design §4 resolved or re-stated as open.

### P0.2 Cache behaviour on one query `PROBE`

**Change:** `Q`, `Q` again, `Q + no_cache=true`; record `search_metadata.id`,
timestamps, price and itinerary identity.

**Acceptance:** a definite answer to *is a cache hit detectable?* and *does any
timestamp move?* This decides design §7 rule 5 and the capability profile's
`cache_detectable` / `provider_timestamp` fields.

### P0.3 Semantic equivalence of `selected_flights_json` `PROBE`

**Change:** search A → choose itinerary I → `selected_flights_json(I)` → compare
against search B on segment identity, dates, flight numbers, carrier, cabin, price.

**Acceptance:** answers **same itinerary? same fare? same price?** — not "HTTP
200". This is what makes design §8.1's `verified` outcome reachable or permanently
unreachable, and it is the single most important experiment in P0.

**Measured 2026-10-03 — answered, with a negative:** the pin returns the itinerary
under **`selected_flights`** (a different response shape, no `best_flights` /
`other_flights`) and carries **no price**, so `verified` is unreachable from the pin
alone. Identity *was* stable across requests. Consequence, now design D19:
verification is implemented as **fresh re-search + identity match + price
comparison**, and the parser branches on `request_kind` (P1.1, P2.2).

### P0.4 `search_metadata` and the observation timestamp `PROBE`

**Change:** the full key set on a fresh and a cache-hit response; is there a
trustworthy provider-stamped time? (Decides whether `observed_at_basis: provider`
ever exists, design §7.)

**Acceptance:** if yes, one paragraph of the design cites the fixture field; if no,
`provider_fetch` stands as the strongest claim available and the design says so.

### P0.5 The 429 bodies, and `Retry-After` `PROBE`

**Change:** capture both 429 causes verbatim — the quota wording and the throughput
wording — plus whether `Retry-After` is present.

**Acceptance:** two recorded bodies usable directly as the fixtures for P1.4, and a
recorded yes/no on `Retry-After`. Deliberate quota consumption must be accepted for
this probe, or the quota case stays unmeasured and `rate_limit_unknown` carries it.

### P0.6 Provider price-context windows `PROBE`

**Change:** determine `price_history`'s granularity and horizon, and what window
`deals.average_price` averages over; and the `price_level` enum.

**Acceptance:** if unresolved, `ProviderPriceContext` stays non-rankable and
non-alertable (design §9 D15) and the profile says `null` — it is **not** promoted
on a guess.

### P0.7 Quota accounting `PROBE`

**Change:** is an empty-but-successful search billable? (account endpoint before and
after). Confirm that a failing search is not.

**Acceptance:** the three counters in design §10 are correct as written, or the
plan's ledger table is corrected with evidence.

### P0.8 Coverage delta `PROBE`

**Change:** the same query with and without `deep_search` / `show_hidden`: how many
itineraries appear, and does `total_duration` disagree with its own segments?

**Acceptance:** a measured `coverage_delta_deep`, and a live re-check of the
duration defect in design §12.1.

---

## 3. Phase P1 — contract freeze and guards (`CODE`, `OWNER`)

Turns measured evidence into frozen types and the tests that hold them. Free of
network; the new test files are authorized with D1 (rule 4 satisfied).

### P1.1 Freeze the contract types `CODE`

**Files:** the adapter's types module (P2.1) or a shared contract module.

**Change:** encode the decided axes as types, from design §7/§8/§8.1/§9/§10:
`observed_at_basis` (three values), `coverage_mode` (four), the four verification
outcomes, `ProviderPriceContext`, and the three ledger counters.

**Acceptance:** no call site can construct an observation without a basis and a
coverage mode; a verification result cannot be represented as a bare `FlightOffer`.
**Why:** these are precisely the fields review #1 found being over-claimed in prose;
types make the over-claim require a deliberate act.

### P1.2 The duration invariant `CODE`

**Change:** a regression test that a multi-stop fixture whose provider `total_duration`
drops the layover still yields a route total that **includes** it (design §12.1).

**Acceptance:** fails if the adapter ever pre-fills a total or uses
`model_construct` to bypass the validators. Existing precedent:
`sdk/python/tests/test_duration_timezone.py`.

### P1.3 Credential redaction `CODE`

**Change:** no log line, exception message, metric label, trace attribute or
telemetry detail may contain the request URL or the key; the key is read from
`SERPAPI_KEY` only and never written to a config store.

**Acceptance:** a test that forces a transport error and asserts the key is absent
from the exception text **and** from captured logs/metrics/trace output.

### P1.4 The 429 classifier `CODE`

**Change:** fail-safe classification per design §11: known quota body →
`budget_exhausted` (terminal); known throughput indication → `rate_limited`;
anything unrecognised → `rate_limit_unknown` (transient, bounded backoff, body
retained). Honour `Retry-After` when present, never require it.

**Acceptance:** both recorded bodies (P0.5) classify correctly; an unknown body
lands in `rate_limit_unknown` and is **never** guessed; a quota 429 is never
retried.

### P1.5 Completeness relative to declared coverage `CODE`

**Change:** completeness derives from the declared `coverage_mode`, not from a
provider switch: a `standard`-declared search that returns the standard result set
is not `partial` merely because a deeper mode exists; a filtered or `unknown`-
coverage result can never be `complete`; an empty success is `no_results` with
completeness per the declared contract.

**Acceptance:** a table-driven test over the cases, including the two that review #1
caught: `deep_search=false` ≠ `partial`, and empty success ≠ `complete`.

### P1.6 `lowest_price` is not a baseline `CODE`

**Change:** a contract test pinning design §9 rule 1 — `ProviderPriceContext.lowest_price`
may never be used as, or compared against, a baseline.

**Acceptance:** the invariant is asserted structurally (the type does not reach the
baseline path) rather than by convention.

### P1.7 Enum traps and typed request models `CODE`

**Change:** named constants for `stops` (`0`/`1`/`2`/`3`) and **two separate**
`travel_duration` enums (deals vs explore number them differently); a request model
per mode (design §12.3) so `return_date` on a one-way request, or both tokens at
once, cannot be constructed; and an **identity normaliser** — measured, the pin
response returns `"B6 1408"` where the search response returns `"B61408"`, so
whitespace and case must be normalised before any itinerary comparison (design
§12.2).

**Acceptance:** a test that fails if either enum is shared or if a raw stop count
can be passed through; invalid combinations unrepresentable rather than asserted.

### P1.8 Docs-claims additions `DOCS`

**Files:** `test/docs-claims.test.mjs`; design §4/§10 tables.

**Change:** assert the quota/limit table and the provider's documented facts still
match what the docs claim, and that the two documents are in the MkDocs nav.

**Acceptance:** `node --test test/docs-claims.test.mjs` green, and red if one side
of a documented pair is edited alone.

---

## 4. Phase P2 — the adapter (`CODE`, D1 resolved yes)

### P2.1 Module and provider registry `CODE` `OWNER`

**Files:** `sdk/python/letsfg/connectors/serpapi_google.py`; a provider registry
that is empty without `SERPAPI_KEY`.

**Note:** the path is historically taken — a `serpapi_google` connector existed here
before the connectors were removed, and its measured defects live in the comments of
`sdk/python/letsfg/models/flights.py`. Reusing the name keeps the historical link;
no code is shared with the old module.

**Acceptance:** importing the client without the key does not import the adapter;
the registry exposes exactly the methods in design §13 (`booking_options` excluded).

### P2.2 Mapping to `FlightOffer` `CODE`

**Change:** segments → `FlightSegment`/`FlightRoute` with **no pre-filled totals**
(P1.2); a parser that **branches on `request_kind`**, because a pinned request
returns a different shape (`selected_flights`, no price — design §8.1/D19); full
provenance per design §6 (`request_kind`, `cache_mode`, `coverage_mode`,
`retrieval_mode`, minimised query, `search_metadata.id`); a **cache-hit refusal**
that will not report a replayed `search_metadata.id` as a fresh observation;
status at or below the design §5 ceiling; public exposure through the existing
`to_public_offer`.

**Acceptance:** a recorded multi-stop fixture produces a layover-inclusive total and
a complete provenance block; an observation missing `search_metadata.id` is refused.

### P2.3 Budget ledger `CODE`

**Change:** `ProviderRequest` / `ProviderSearch` / `BillableSearch` per design §10,
**derived client-side** from our own request classification — measured, the account
counters lag and settle *backwards* on a cache hit, so they cannot attribute a
single call (design D20). The account endpoint is used only as a periodic drift
alarm.

**Acceptance:** a test proving `budget_exhausted` is terminal for the run, that cache
hits and failures are not billable, and that the scanner's interactive reserve is
never touched.

### P2.4 `ProviderPriceContext` `CODE`

**Change:** carry provider price fields as `ProviderPriceContext`, stored as
provider claims, separable in the evaluation record, non-rankable and non-alertable
until P0.6 resolves their semantics.

**Acceptance:** a test that a `ProviderPriceContext` value cannot reach the baseline
path or the alert path.

### P2.5 Provider conformance suite `CODE`

**Change:** one suite run against **every** provider (both existing lanes and this
one): status ceiling, provenance present, freshness basis never fabricated,
`no_results` ≠ `timeout`, completeness relative to declared coverage, public shape
sanitised.

**Acceptance:** green on the existing lanes **before** the adapter is registered, so
the suite cannot be written to fit the newcomer.

---

## 5. Phase P3 — scanner wiring (`OTHER REPO`)

Out of scope here. Belongs to the scanner repository: planner → scheduler →
observation store → verification → alerting
([`first-class-fare-scanner-design.md`](first-class-fare-scanner-design.md) §11).
This repo's obligation ends at the provider contract.

---

## 6. Dead weight and deferrals found while writing this

### D-1 The unrunnable SerpApi sweep script `DEFECT` — **FIXED 2026-10-03 (deleted)**

**Evidence:** `sdk/python/google_checkout_live_sweep.py` imported
`letsfg.connectors.serpapi_google.SerpApiGoogleConnectorClient`,
`letsfg.connectors.checkout_engine`, and `playwright` — **all three gone**
(`sdk/python/letsfg/connectors/` now holds only `__init__.py`, `airport_tz.py`,
`auth.py`). It could not run, and nothing else in the repo referenced
`serpapi_google_ota`.

**Also:** four committed live-sweep artifacts
(`google_checkout_live_sweep_results.json`, `_results_v2.json`,
`_japan_longhaul.json`, `_longhaul_london_us.json`, ≈51 KB total).

**Resolution:** all five files deleted with `git rm` in one commit, once the vendor
decision (design §16 D1) had removed the last reason to keep them. Nothing referenced
the script or the artifacts except this document.

**A sibling found by the same test.** Verifying the deletion meant scanning every
tracked top-level `sdk/python/*.py` for imports of removed `letsfg.connectors.*`
modules. That turned up `sdk/python/_jetstar_checkout_validate.py` (1.6 KB, tracked,
**zero references**) — dead by the identical criterion, importing
`letsfg.connectors.booking_base`, `checkout_engine` and `jetstar`. Deleted in the same
commit. The scan now returns only two things: the deliberately parked test modules
(`sdk/python/conftest.py`), and `sdk/python/letsfg/client.py`'s `try/except
ImportError` mappings — a separate, still-open finding.

### D-2 No automated proof that the sweep artifacts are stale `DEFECT` — **FIXED 2026-10-03 (moot)**

Related to D-1: nothing pinned those JSON files to the connector that produced them, so
they would have silently rotted if kept. The risk was removed with the files rather than
guarded around — which is the cheaper half of that trade, and the correct one once
nothing was going to read them.

---

## 7. Owner decisions required

| # | Decision | Blocks | Design ref |
|---|---|---|---|
| O1 | **Is SerpApi a provider lane we own?** — **ANSWERED 2026-10-03: YES**, on re-decision. The lane was declined in the morning and re-activated when the owner purchased a plan; all of P1–P2 is unblocked with it | All of P1–P2 — **open** | §16 D1 |
| O2 | **Provide a key for the probe** — **ANSWERED: provided.** Measured against `GET /account`: Starter (`starter_v4`), 1000 searches/month, 0 used, 200/hour. **Action still owed:** it sits under `SERPER_API_KEY` in this machine's `.env`, a name the adapter must refuse by D18 — file it as `SERPAPI_KEY` | P0.5 / P0.7 | §17, design §14 |
| O3 | **Which plan tier** — **ANSWERED: Starter**, purchased 2026-10-03. D12 is closed by purchase rather than by argument | — | §16 D12 |
| O4 | **Legal Shield** — **live again as an optional procurement.** The indemnity hedges *this* lane's scraping exposure, and the lane is active. P1/P2 do not wait on it; the exposure exists regardless of whether it is bought | Procurement decision | §4.3 |
| O5 | **Fate of the dead sweep script and its four JSON artifacts** — **settled 2026-10-03: deleted, and staying deleted.** Nothing referenced them, neither could run, and the re-activated lane writes a *new* live test (P0.1) rather than reviving the connector-era sweep | ~~D-1~~ — closed | §6 above |

---

## 8. Traceability

| Item | Design ref | Depends on | Verification |
|---|---|---|---|
| P0.1 | §17 | O2 | fixtures + capability profile committed |
| P0.2 | §7 | P0.1 | `cache_detectable` / `provider_timestamp` answered |
| P0.3 | §8.1 | P0.1 | same-itinerary / same-price answered |
| P0.4 | §7 | P0.2 | `observed_at_basis: provider` exists or is ruled out |
| P0.5 | §11 | P0.1 | two 429 bodies + `Retry-After` recorded |
| P0.6 | §9, D15 | P0.1 | windows documented or explicitly `null` |
| P0.7 | §10 | P0.1 | empty-is-billable confirmed |
| P0.8 | §12.1 | P0.1 | coverage delta + duration re-check |
| P1.1 | §7, §8.1, §9, §10 | P0 | contract types frozen |
| P1.2 | §12.1 | — | layover-inclusive total |
| P1.3 | §14 | — | no key in text/logs/metrics/trace |
| P1.4 | §11, D4 | P0.5 | both bodies classify; unknown → `rate_limit_unknown` |
| P1.5 | §7.1, §11, D17 | — | table-driven completeness cases |
| P1.6 | §9, D15 | — | `lowest_price` cannot reach the baseline |
| P1.7 | §12.2, §12.3 | — | shared enum / raw stop count fails the test |
| P1.8 | §4, §10 | — | `node --test test/docs-claims.test.mjs` |
| P2.1 | §13, D1 | O1, P1.1 | import-without-key test |
| P2.2 | §5, §6, §12.1 | P2.1, P1.2 | multi-stop fixture + provenance block |
| P2.3 | §10, D9 | P2.1 | ledger test |
| P2.4 | §9, D15 | P2.1, P0.6 | context cannot reach baseline or alert |
| P2.5 | §13 | P2.1, P2.2 | green on existing lanes first |

---

## 9. Resolution log

| Item | Status | Evidence |
|---|---|---|
| Design + implementation documents (incl. review #1 revision) | **Done** | This document and its companion, in the MkDocs nav; `node --test test/docs-claims.test.mjs` and `python -m mkdocs build` green. **Re-activated 2026-10-03** on D1 = yes: the status headers, gates and this log were restored rather than rewritten, and the decline record is kept |
| P0 probe | **Partly done; two probes owed.** P0.2, P0.3, P0.4, P0.6, P0.8 and a partial P0.7 were measured (7 searches, Free plan, usage 1 → 8). **P0.5 and the empty-search half of P0.7 are open again** with the adoption, now on the Starter plan | Design §17; fixtures are committed with P0.1 |
| P1.1–P1.8 contract freeze + guards | **Open** — authorized by D1, no longer withdrawn | — |
| P2.1–P2.5 adapter | **Open** — authorized by D1 | O1 answered **yes** |
| P3 scanner wiring | **Out of scope** | Other repository |
| D-1, D-2 sweep leftovers | **Fixed 2026-10-03 — deleted, and not revived** | The script, four artifacts and the sibling `_jetstar_checkout_validate.py` were removed; the re-activated lane writes a new live test instead |

---

## 10. Order of work

**Active.** With D1 resolved yes, the order below applies as written — and it is the same
order the plan always carried, so nothing had to change when the decision did:

1. **P0.5 and P0.7** — the two probes that never ran, on the Starter plan. They decide
   P1.4 (the 429 classifier's fixtures) and P2.3 (whether an empty success is billable),
   so they come first: D10 gates the adapter on the probe having run.
2. **P1.1–P1.7** — the contract freeze and its guards, free of network.
3. **P2.1–P2.5** — the adapter, with the conformance suite written against the
   **existing first-party lanes first** (P2.5).
4. **P3** — scanner wiring, in the other repository.

P2.5's ordering is the part that does not bend: a suite written after the adapter exists
would be written to fit it.
