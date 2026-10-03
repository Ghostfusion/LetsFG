# fli Study — Implementation Plan

**Status:** plan only. **Nothing in this document has been implemented**, and nothing
was installed, run, or requested from Google during the study. Code items are gated on
an owner decision — adding a provider is new scope under the working agreement.

Companion: [`fli-study-design.md`](fli-study-design.md). Section references in `§`
point there.

---

## 1. How to read this plan

| Tag | Meaning |
|---|---|
| `PROBE` | A live measurement. Needs an install and/or live requests to Google under our IP. |
| `DOCS` | Documentation only. |
| `CODE` | New code or tests. **Requires an owner go-ahead** (rule 4). |
| `OWNER` | A decision only the owner can take (ownership, legal exposure, dependency). |
| `GUARD` | A test that pins a documented claim so it cannot drift silently. |
| `DEFERRED` | Recorded, deliberately not done yet, with the reason. |

The study's value is mostly in patterns, and patterns cost nothing to adopt: **P1**
(loaded-empty vs never-loaded), **P2** (sweep breaker), **P3** (failure classes are not
interchangeable), **P4** (state the sweep cost before running it), **P5**
(deterministic deep link), **P6** (per-request instances), **P7** (environment knobs),
**P8** (golden encoders, gated live tests).

---

## 2. Phase P0 — live probe (`PROBE`, gating)

Design §10 lists five unanswered questions; every one of them needs a request from
*our* environment. This phase is the analogue of the SerpApi probe, and it is gated
for the same reason: it consumes someone else's infrastructure under our identity.

### P0.1 Install and run its own live set in a throwaway venv `PROBE` `OWNER`

**Change:** a venv outside the repo (`python -m venv`, `pip install flights`), then the
project's own live gate (`pytest -m live --live`) against its checkout, using
`--currency`/`--language`/`--country` set to our realistic values.

**Acceptance:** the provider's own live tests pass or fail *observably* from this
network, with the raw output recorded. **Never** installed into the repo's environment
and never added to `sdk/python/pyproject.toml` — that is a dependency decision (D7).

**Risk:** Google may serve the consent interstitial or nothing at all from our IP;
that is a finding, not a failure (#1 in design §10).

### P0.2 Measure coverage in our actual cohort `PROBE`

**Change:** First-Class, long-haul, specific dates — not the provider's economy
examples. Count rows per search, wall time, and whether results look complete.

**Acceptance:** a row-count distribution and latency figure we can put next to the
SerpApi measurement (20–45 rows claimed by the provider; 16–18 measured on SerpApi for
a similar query — see [`serpapi-provider-design.md`](serpapi-provider-design.md) §17).

### P0.3 Settle the date-range cap `PROBE`

**Change:** exercise a range wider than 61 and wider than 93 days; record which bound
raises `ValueError`.

**Acceptance:** design §4.9's contradiction is resolved with the observed bound, or it
stays recorded as unresolved with the failing input named.

### P0.4 Measure the sweep economics ourselves `PROBE`

**Change:** a 30-date and a 93-date sweep, recording page fetches, HTTP requests (after
retries), wall time, and peak memory against the provider's claimed 42 fetches /
~135 requests / ~4 s / several hundred MB (§4.6).

**Acceptance:** numbers we can plan against, at a concurrency we choose deliberately
rather than the provider's default 10 workers.

### P0.5 Sustained-load behaviour `PROBE` `OWNER`

**Change:** determine what Google does to a client that sustains the allowed rate.

**Acceptance:** a recorded answer, *or* an explicit decision not to find out (this is
the probe that could get an IP throttled; it needs its own go-ahead, exactly like the
SerpApi 429 experiment).

### P0.6 Inspect the blob for price-context fields `PROBE`

**Change:** check whether the inline `AF_initDataCallback` payload carries a price
graph/history that fli's models do not surface (§10 question 5).

**Acceptance:** yes/no with the raw field path, or "not determined". If yes, it is the
only way fli could ever contribute to the baseline problem — and it would still be a
**provider price context**, never our baseline (SerpApi design §9/D15).

---

## 3. Phase P1 — adopt the patterns (`DOCS`, then `GUARD`)

Independent of D6: these improve our design whatever we decide about fli.

### P1.1 Loaded-empty vs never-loaded, as a contract `DOCS` `CODE`

**Files:** [`first-class-fare-scanner-design.md`](first-class-fare-scanner-design.md)
§6.3/§6.6 and §9; envelope reference in
[`trvl-study-design.md`](trvl-study-design.md) §2.1; later a guard.

**Change:** state the provider-side obligation from study §4.5/§4.7 — the *reason* an
empty result occurred (page served with zero rows · request never returned a page ·
our own filters removed rows the provider did return) is a recorded fact, not an
inference from emptiness. A party containing a child or infant may never be reported as
`no_results` when the provider thins results client-side (design D9).

**Acceptance:** design text updated, and a `GUARD` test that the three reasons are
distinct in the envelope and cannot collapse into one.

**Precedent:** this is the same rule as our existing `no_results` ≠ `timeout`, applied
one layer out.

### P1.2 A sweep breaker with stated arithmetic `DOCS`

**Files:** scanner §6 (planner), §6.6 (budget).

**Change:** adopt the shape of study §4.6/§4.7 — a bound proportional to workers, a
disarm on the first successful load, a separate raise when *mostly timeouts around
isolated successes*, and the rule that unattempted work is never charged as failed
work (P3).

**Acceptance:** the planner can describe, before it runs, what it will do when a
provider is blocked, and the description matches the breaker's real arithmetic.

### P1.3 State the sweep cost before running it `DOCS`

**Change:** the planner must be able to state fetches, requests-after-retries, wall
time and memory for a proposed fan-out, refusing plans whose cost it cannot state
(P4) — the same discipline as the SerpApi budget ledger, applied to fan-out.

**Acceptance:** the planner section names the cost model and the refusal condition.

### P1.4 Deterministic deep links in alert payloads `DOCS` `DEFER`

**Change:** where an alert needs a "go look at this itinerary" link, prefer a link
built locally from airports + dates + flight numbers (study P5) over one that requires
another provider call. Deferred until an alert path exists to carry it (scanner §8).

**Acceptance:** recorded as a design requirement with the blocker named, not as a
half-built link builder.

---

## 4. Phase P2 — optional provider (`CODE`, `OWNER`, gated on D6)

Only if the owner decides fli is a lane we own. Nothing here is designed in detail
until then, deliberately: the ownership question comes first, and the study's job was
to make that question answerable rather than to presume its answer.

### P2.1 Adapter behind the existing provider contract `CODE` `OWNER`

**Files:** an adapter module beside the SerpApi one; the provider registry.

**Change:** implement the same interface and provenance as the SerpApi adapter
(design §6 of that document): `request_kind`, `coverage_mode`, minimised query,
per-request instance (study P6). Status ceiling: `observed`; **no** `verified` claim
without a comparison, **no** baseline contribution, **never** primary (P12).

**Acceptance:** the shared provider conformance suite (SerpApi plan P2.5) passes on the
new adapter; a test proves it cannot be selected as the primary source, and that a
children/infants party yields `partial`, never `no_results`.

### P2.2 Dependency or vendoring `OWNER`

**Change:** decide between depending on `flights` and vendoring the MIT encoder
(study P10/D7).

**Acceptance:** a recorded decision; if "depend", the version is pinned with the usual
supply-chain checks, and the **name collision is handled explicitly** — the PyPI
distribution is `flights`, while `fli` on PyPI is an unrelated project (a real
dependency-confusion hazard, not a hypothetical one).

---

## 5. Phase P3 — out of scope

Scanner wiring (planner → scheduler → observation store → verification → alerting)
belongs to the scanner repository. This repo's obligation ends at the provider
contract, as with the SerpApi plan.

---

## 6. Observations recorded, not acted on

### O-1 The `live-canary` idea `DEFERRED`

The subject repository runs a scheduled workflow that watches for Google-side breakage
— a canary for an interface someone else controls. Our own provider contract will face
the same problem if we ever own acquisition, and the SerpApi plan's P0 is the
same idea in manual form. Recorded as a pattern to reuse; deferred because it needs a
live provider to watch.

### O-2 Its own documentation drift `DEFERRED`

Study §4.9: the README's transport section and the API reference disagree, and the
date cap is stated two ways. Nothing to fix in our repo — recorded because it is
evidence *for* `test/docs-claims.test.mjs`, which exists to make exactly that drift
fail CI. No action beyond citation.

---

## 7. Owner decisions required

| # | Decision | Blocks | Design ref |
|---|---|---|---|
| F1 | **Run the P0 probe** against Google under our IP, including whether to risk P0.5's sustained-load test | All of P0 | §10, §11 |
| F2 | **Is direct acquisition (any owned scraper, fli or otherwise) acceptable** given its legal posture — unhedged, versus SerpApi's Legal Shield from $150/mo? | D6, all of P2 | §6, §9 |
| F3 | **Depend on `flights` or vendor the encoder**, if F2 is yes | P2.2 | D7 |
| F4 | Whether the adopt-the-patterns work (P1) should proceed now — it is valuable regardless of F2 | P1 | D1/D5 |

---

## 8. Traceability

| Item | Design ref | Depends on | Verification |
|---|---|---|---|
| P0.1 | §10.1, §4.8 | F1 | live-set output recorded from our network |
| P0.2 | §4.4, §6 | F1 | row-count + latency vs the SerpApi measurement |
| P0.3 | §4.9 | F1 | the bound raising `ValueError` is identified |
| P0.4 | §4.6 | F1 | fetches/requests/time/memory measured |
| P0.5 | §4.6, §10.4 | F1 | answer recorded, or explicitly declined |
| P0.6 | §4.4, §10.5 | F1 | field path, or "not determined" |
| P1.1 | §4.5, §4.7, D9 | — | three empty-reasons distinguishable; child/infant → `partial` |
| P1.2 | §4.6, §4.7 | — | breaker arithmetic documented and matched |
| P1.3 | §4.6 | — | cost stated before the sweep; refusal condition named |
| P1.4 | §5.1 P5 | alert path | requirement recorded, blocker named |
| P2.1 | §5.3 P12, D4 | F2, D6 | conformance suite; cannot be primary; child/infant `partial` |
| P2.2 | §5.2 P10, D7 | F3 | decision recorded; name collision handled |

---

## 9. Resolution log

| Item | Status | Evidence |
|---|---|---|
| Study documents | **Done** | This plan and its companion, in the MkDocs nav; `node --test test/docs-claims.test.mjs` and `python -m mkdocs build` green |
| P0.1–P0.6 live probe | **Not started** | Needs F1; no request has been made to Google |
| P1.1–P1.4 pattern adoption | **Not started** | Awaits F4 (and rule 4 for the guard files) |
| P2.1–P2.2 provider / vendoring | **Not started** | Needs F2 and F3 |
| P3 scanner wiring | **Out of scope** | Other repository |
| O-1, O-2 | **Deferred** | Recorded above with reasons |

---

## 10. Order of work

1. **F4** — decide whether to adopt the patterns now. They cost nothing, they are
   independent of the ownership question, and they make the scanner's planner honest
   about failure whether or not fli is ever used.
2. **F2** — the ownership and legal decision. Everything about fli as a *provider*
   waits here, and no amount of measuring substitutes for it.
3. **F1 → P0** — if F2 is yes, probe from our own network before designing anything on
   top of it; the repository's own history (§3) is a demonstration of what happens when
   an undocumented interface moves.
4. **P2** only after P0, and only behind the existing provider contract.
