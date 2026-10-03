# SerpApi Google Flights — Implementation Plan

**Status:** plan only. **Nothing in this document has been implemented.** Every
item below is `NOT STARTED`, and every code item is gated on an owner decision —
the working agreement forbids code changes that are not defect fixes, and adding a
provider is new scope.

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
scaffold or a green unit test that does not exercise the real provider.

---

## 2. Phase P0 — probe the provider (`PROBE`, gating)

The adapter's freshness rules cannot be finished without this. Design §7 D10
makes it a gate, not an optimisation.

### P0.1 A `live`-marked probe script `PROBE` `OWNER`

**Files:** new `sdk/python/tests/test_serpapi_live.py` (or a script beside
`sdk/python/google_checkout_live_sweep.py`'s location if tests are the wrong home).

**Change:** the eight measurements in design §17, each recording the raw response
as a committed fixture. The script MUST:
- refuse to run unless `SERPAPI_API_KEY` is set **and** an explicit opt-in env flag
  is set (`LETSFG_SERPAPI_PROBE=1`), so no workflow and no casual `pytest` run can
  fire it;
- be marked `live` so the existing `pytest -m "not live"` exclusion covers it;
- print a redacted request summary (never the URL — the key is a query parameter,
  design §14 D8);
- fetch the account/plan endpoint before and after the quota-consuming probes.

**Acceptance:** the fixture set exists; each **[UNVERIFIED]** marker in design §4
is either resolved with the observed value or re-stated with the measurement that
failed to resolve it. **Cost:** single-digit searches plus the deliberate
empty-result probe.

**Risk:** consumes quota on the owner's key; the empty-result probe spends a search
to prove a search is spent.

### P0.2 Resolve `search_metadata` and decide `observed_at` `PROBE`

**Change:** from P0.1's fixtures, decide whether a trustworthy provider timestamp
exists. If yes, `observed_at_basis: provider` becomes available and design §7 rule
3 is written; if no, rule 1 (`no_cache=true` for anything alertable) stands
permanently.

**Acceptance:** one paragraph in the design replacing "**[UNVERIFIED]**", citing the
fixture file and the observed key set.

### P0.3 Record the 429 bodies and `Retry-After` `PROBE`

**Change:** capture both 429 causes (throughput; quota exhausted) as verbatim
bodies, plus whether `Retry-After` is present.

**Acceptance:** two recorded bodies usable directly as test fixtures for P1.2.

### P0.4 Resolve the baseline windows (feeds D11) `PROBE`

**Change:** determine `price_history`'s granularity/horizon and what window
`deals.average_price` averages over.

**Acceptance:** if unresolved, D11 stays as written — provider baselines remain
non-rankable — and the design says so explicitly rather than implying they are
usable.

---

## 3. Phase P1 — contracts and guards that need no network (`CODE`, `OWNER`)

Cheap, high-value, and provable with recorded fixtures. Still new test files, so
still gated.

### P1.1 Enum-trap constants and assertions `CODE`

**Files:** the adapter's request-builder module (P2.1) plus its test.

**Change:** named constants for `stops` (`0`/`1`/`2`/`3` →
any/nonstop/≤1/≤2) and **two separate** `travel_duration` enums, because deals and
explore number them differently (design §12.2). Assertions that
`include_airlines ⊥ exclude_airlines`, `departure_token ⊥ booking_token`,
`selected_flights_json ⊥ {tokens, multi_city_json}`, `no_cache ⊥ async`.

**Acceptance:** a test that fails if either enum is shared, or if a raw stop count
can be passed through. **Why it matters:** both traps are silent — they produce a
valid request with the wrong meaning.

### P1.2 429 classification `CODE`

**Files:** adapter error-mapping module + test.

**Change:** map 429 to `rate_limited` (transient) or `budget_exhausted`
(terminal-for-the-run) by message, using P0.3's recorded bodies as fixtures; map
400/401/403/404/410/5xx per design §11. Honour `Retry-After` when present, else
bounded exponential backoff with jitter.

**Acceptance:** both 429 fixtures classify correctly; a *quota* 429 is never
retried; a *throughput* 429 is retried and then resumed. This is the single
sanctioned prose-parsing site (design §11) and the test is what makes it
sanctioned.

### P1.3 Credential redaction `CODE`

**Files:** the adapter's HTTP layer + test.

**Change:** no log line, exception message or report may contain the request URL
or the `api_key` value; the adapter uses the env var only and never writes the key
to a config store.

**Acceptance:** a test that forces a transport error and asserts the key does not
appear in the exception text or captured logs. **Why:** the key travels in the
query string, so a naive `raise f"... {url}"` leaks it — this is the first lane in
the repo where that rule is load-bearing (design §14).

### P1.4 Completeness honesty `CODE`

**Change:** a filtered or non-deep search returns `completeness: partial`;
only an unfiltered, `deep_search=true` + `show_hidden=true` search may claim
`complete`. An empty-but-successful response is `no_results` with the completeness
that its coverage allows — a filtered empty set is never `complete` (design §12.2 —
the provider's own "increase `max_duration` by up to 200 minutes" advice proves the
filters are lossy).

**Acceptance:** a table-driven test over the four cases; no case in which a
provider-side filter can produce `complete`.

### P1.5 Docs-claims additions `DOCS`

**Files:** `test/docs-claims.test.mjs`; design §4/§10 tables.

**Change:** assert that the two new documents are present in the MkDocs nav, and
that the provider's quota/limit table still matches what the docs claim (a
`PARKED_TEST_MODULES`-style pin is the precedent; use the cheapest equivalent that
would actually catch a stale copy).

**Acceptance:** `node --test test/docs-claims.test.mjs` green, and red if the nav
entry or a documented limit is removed without updating the other side.

---

## 4. Phase P2 — the adapter (`CODE`, gated on design D1)

### P2.1 Module and provider registry `CODE` `OWNER`

**Files:** `sdk/python/letsfg/connectors/serpapi_google.py`; a provider registry /
factory that is empty without `SERPAPI_API_KEY`.

**Note:** this module path is historically taken — a `serpapi_google` connector
existed here before the connectors were removed, and its measured defects are
recorded in `sdk/python/letsfg/models/flights.py`. Reusing the name is deliberate
(preserves the historical link) but the new module shares no code with the old one.

**Acceptance:** importing the client without the key does not import the adapter;
the registry exposes exactly the methods in design §13.

### P2.2 Mapping to `FlightOffer` `CODE`

**Change:** map segments to `FlightSegment`/`FlightRoute` **without** setting
totals, so the existing validators compute gate-to-gate durations (design §12.1,
D2); record provenance (provider, engine, `search_metadata.id`, currency, query
params); set `price_status` at the design §5 ceiling; run public exposure through
the existing `to_public_offer`.

**Acceptance:** a recorded `google_flights` fixture for a multi-stop itinerary
yields a route total that **includes** the layover — i.e. a regression test that
would fail if the provider's own total were ever trusted.

### P2.3 Budget ledger `CODE`

**Change:** per-provider ledger honouring design §10: success-with-results and
success-with-empty consume; failures and cache hits do not; throughput ceiling is
the plan's searches/hour.

**Acceptance:** a test proving `budget_exhausted` is terminal for the run and that
the scanner's reserve is never touched.

### P2.4 Provider conformance suite `CODE`

**Change:** one conformance suite run against every provider (the two existing
lanes and this one), asserting the shared properties: status ceiling, provenance
present, freshness basis never fabricated, `no_results` ≠ `timeout`, public shape
sanitised.

**Acceptance:** the suite passes for the existing lanes **before** the adapter is
registered, so it cannot be written to fit the newcomer.

---

## 5. Phase P3 — scanner wiring (`OTHER REPO`)

Out of scope here. Belongs to the scanner repository described in
[`first-class-fare-scanner-design.md`](first-class-fare-scanner-design.md) §11;
this repo's obligation ends at the provider contract.

---

## 6. Dead weight and deferrals found while writing this

### D-1 The unrunnable SerpApi sweep script `DEFECT` `DEFERRED`

**Evidence:** `sdk/python/google_checkout_live_sweep.py` imports
`letsfg.connectors.serpapi_google.SerpApiGoogleConnectorClient`,
`letsfg.connectors.checkout_engine`, and `playwright` — **all three are gone**
(`sdk/python/letsfg/connectors/` now holds only `__init__.py`, `airport_tz.py`,
`auth.py`). It cannot run, and nothing else in the repo references
`serpapi_google_ota`.

**Also:** four committed live-sweep artifacts
(`google_checkout_live_sweep_results.json`, `_results_v2.json`,
`_japan_longhaul.json`, `_longhaul_london_us.json`, ≈51 KB total).

**Status:** raised to the owner on 2026-10-03; no decision taken. Recorded as a
deferral, not silently fixed — deleting them is destructive and they are not mine
to remove. If the answer is "delete", it is a one-commit defect fix; if it is
"keep", the script should at least stop looking runnable (a header note).

### D-2 No automated proof that the sweep artifacts are stale `DEFECT` `DEFERRED`

Related to D-1: nothing pins those JSON files to the connector that produced them,
so they will silently rot if kept. Deferred with D-1 — the decision is the same
one.

---

## 7. Owner decisions required

| # | Decision | Blocks | Design ref |
|---|---|---|---|
| O1 | **Is SerpApi a provider lane we own?** (provider acquisition is owner-reserved) | All of P2 | §16 D1 |
| O2 | **Provide a key for the probe**, accepting quota consumption on it | P0 | §17 |
| O3 | **Which plan tier**, if any — Free (≈8 searches/day) cannot do scanner duty; Starter is ≈33/day at $25/mo | Cost model | §16 D12 |
| O4 | **Legal Shield**: the scraping indemnity starts at $150/mo, and ZeroTrace (no retention) is enterprise-only — accept, or route only non-personal queries through this lane | Procurement | §4.3, §14 |
| O5 | **Fate of the dead sweep script and its four JSON artifacts** | D-1 | §6 above |

---

## 8. Traceability

| Item | Design ref | Depends on | Verification |
|---|---|---|---|
| P0.1 | §17 | O2 | probe fixtures committed |
| P0.2 | §7 | P0.1 | the `[UNVERIFIED]` marker is replaced |
| P0.3 | §11 | P0.1 | two recorded 429 bodies |
| P0.4 | §9, D11 | P0.1 | documented or explicitly unresolved |
| P1.1 | §12.2 | — | test fails if enums are shared |
| P1.2 | §11, D4 | P0.3 | both 429 fixtures classify correctly |
| P1.3 | §14, D8 | — | transport-error test asserts no key in text/logs |
| P1.4 | §7, §12.2 | — | table-driven; no filtered path yields `complete` |
| P1.5 | §4, §10 | — | `node --test test/docs-claims.test.mjs` |
| P2.1 | §13, D1 | O1 | import-without-key test |
| P2.2 | §5, §12.1, D2 | P2.1 | multi-stop fixture total includes the layover |
| P2.3 | §10, D9 | P2.1 | budget ledger test |
| P2.4 | §13 | P2.1, P2.2 | conformance suite green on existing lanes first |

---

## 9. Resolution log

| Item | Status | Evidence |
|---|---|---|
| Design + implementation documents, MkDocs nav | **Done** | This document and its companion, listed in `mkdocs.yml`; `node --test test/docs-claims.test.mjs` and `python -m mkdocs build` green |
| P0 probe | **Not started** | Needs O2 |
| P1.1–P1.5 guards | **Not started** | Gated by rule 4 (new files) |
| P2 adapter | **Not started** | Gated by O1 |
| P3 scanner wiring | **Out of scope** | Other repository |
| D-1, D-2 sweep leftovers | **Deferred** | Raised 2026-10-03; awaiting O5 |

---

## 10. Order of work

1. **O1, O2, O3, O4** — owner decisions. Nothing else moves without them.
2. **P0** — probe, and close every `[UNVERIFIED]` in the design.
3. **P1** — the guards that need no network (cheapest real value in this plan).
4. **P2** — the adapter, starting with the conformance suite against the *existing*
   lanes so the newcomer has to fit, not the other way round.
5. **D-1/O5** — settle the leftovers whenever the owner answers; it is independent
   of everything above.
