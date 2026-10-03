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
| `ACCEPT` | A pass/fail criterion on a probe — the answer is yes or no, not a note. |
| `DOCS` | Documentation only. |
| `CODE` | New code or tests. **Requires an owner go-ahead** (rule 4). |
| `OWNER` | A decision only the owner can take (ownership, legal exposure, dependency). |
| `GUARD` | A test that pins a contract so it cannot drift silently. |
| `DEFERRED` | Recorded, deliberately not done yet, with the reason. |

**What review #1 changed here.** P0 became an **acceptance protocol** with named cohorts
and pass/fail per probe instead of exploratory testing; the 93-vs-61 cap became a gate
that blocks a production sweep rather than a curiosity; provider **capability
declaration**, **health/quarantine** and an **admission gate** became prerequisites for
D6; and a third dependency option was added to D7.

---

## 2. Phase P0 — acceptance protocol (`PROBE`, gating)

> **Gated on F2, and the gate is not cosmetic: this protocol must not be executed
> until F2 is answered — and "no" means it does not run at all.**
> Executing it *is* the owned acquisition F2 authorizes: querying Google's
> undocumented interface from our IP is the act in question, so measuring it before
> deciding whether we want to do it would decide it by doing it (design §11).

The question P0 exists to answer, in one line:

> **Can fli reliably observe the specific First-Class fare universe LetsFG cares about
> — and if it can, exactly what coverage guarantees may we honestly make?**

The probes below are the smallest set that answers it. Each has a **pass criterion**;
a probe that produces a note instead of a verdict has not been run properly.

### 2.1 Cohorts — representative failure surfaces, not dozens of cases

| # | Cohort | Why it exists |
|---|---|---|
| **A** | JFK→LHR · 1 adult · Economy | the ordinary path; establishes a working baseline |
| **B** | JFK→LHR · 1 adult · **First** | the target cabin — the only one that matters for admission |
| **C** | JFK→LHR · 2 adults + 1 child · First | the measured thinning case (§4.5) — must report coverage, not emptiness |
| **D** | JFK→LHR · 2 adults + 1 infant · First | the second thinning case; distinct because a lap infant prices differently |
| **E** | US→EU · First · long-haul, multi-stop | the actual scanner workload, not a short-haul toy route |
| **F** | date sweep: 30 / 61 / 93 days | establishes the real cap and the real cost |
| **G** | a route/date known to produce no results | the negative case: must be distinguishable from a failure |

### 2.2 Probes and pass criteria

| # | Probe | Pass criterion |
|---|---|---|
| 1 | Economy search (A) | returns a parseable itinerary with a price |
| 2 | First-Class long-haul (B, E) | returns parseable itineraries with prices |
| 3 | Child party (C) | **coverage is reported correctly** — `partial` or an explicit thinning signal, never a bare empty |
| 4 | Infant party (D) | same as 3, independently |
| 5 | Empty route (G) | `confirmed_empty` is distinguished from `unavailable` |
| 6 | Timeout simulation | **never becomes `no_results`** (§6.2) |
| 7 | Date sweep (F) | the actually supported range is established |
| 8 | 61 vs 93 (§4.9) | the discrepancy is resolved; the adapter constant is derived from the measurement |
| 9 | Repeated requests | failure rate measured against the provider's own "~1 in 60" (§4.8) |
| 10 | Itinerary link | reconstructs deterministically for a returned itinerary (§5.1 P5) |
| 11 | EU/consent behaviour | the actual behaviour from our network is established (§4.8) |
| 12 | TLS interception | behaviour under an intercepting proxy is established (§4.8) |
| 13 | Parse failure | becomes a **provider error**, not an empty result (§6.5) |
| 14 | Concurrent requests | safe adapter usage is established — its client is not thread-safe (§4.10) |

Every probe records the raw response as evidence, and every **ACCEPT** verdict cites it.

### 2.3 The cap gate (blocks production sweeps)

> **No production sweep may rely on a range cap until the runtime-observed cap is
> established** (design §4.9, D8). README says 93 · reference says 61 → probe 8
> measures → adapter constant → test → docs. Writing `MAX_DAYS = 93` from a README is
> forbidden, and the constant must not exist before a measurement produced it.

### 2.4 Cost and capability measurement

**Change:** measure what §6.4 of the design calls `request_cost`, per cohort F:
`max_units_per_operation`, `requests_per_unit`, `max_retry_multiplier`,
`units_per_hour`, wall time and peak memory. Choose concurrency deliberately — not the
provider's default 10 workers.

**ACCEPT:** five cost numbers and a capability block that can be written into the
provider declaration (§6.3) without a single `<measured>` placeholder left.

### 2.5 Health and quarantine behaviour

**Change:** characterise the failure classes so that §6.5's routing can be implemented
rather than guessed — in particular, confirm that a 200-with-no-payload is detectable as
a *contract* anomaly rather than indistinguishable from an empty result.

**ACCEPT:** each class in design §6.5 is reproducible on demand (fault injection or an
observed instance), and the health transition it maps to is named.

### 2.6 Install and run its own live set in a throwaway venv `PROBE` `OWNER`

**Change:** a venv **outside the repo** (`python -m venv`, `pip install flights`), then
the project's own live gate (`pytest -m live --live`) against its checkout, with locale
parameters set to our realistic values.

**ACCEPT:** the live set passes or fails observably from this network, with raw output
recorded. **Never** installed into the repo's environment and never added to
`sdk/python/pyproject.toml` — that is a dependency decision (D7).

**Risk:** Google may serve the consent interstitial or nothing at all from our IP. That
is a finding, not a failure.

### 2.7 Sustained-load behaviour `PROBE` `OWNER`

**Change:** determine what Google does to a client that sustains the allowed rate.
**ACCEPT:** a recorded answer, *or* an explicit decision not to find out. This is the
probe that could get an IP throttled, so it needs its own go-ahead — exactly like the
SerpApi 429 experiment.

---

## 3. Phase P1 — adopt the patterns (**done**, 2026-10-03, docs only; extended by review #1)

Independent of D6: these improve our design whatever we decide about fli.

**Status: landed as documentation** — the empty-reason taxonomy in
[`trvl-study-design.md`](trvl-study-design.md) §2.1 (the envelope's owner), and the
planner/alert rules in
[`first-class-fare-scanner-design.md`](first-class-fare-scanner-design.md) §6.1, §6.7,
§8 and decision 8 of §12. Review #1 extended the adoption to:

- **two breakers instead of one** — search coverage vs provider health (design §6.4/§6.5),
  and fli's `(5 + workers) × 3` explicitly *not* encoded as a scanner rule (D13);
- the **attempt-state taxonomy** at planner level (`loaded_with_results` ·
  `loaded_empty` · `rejected` · `timeout` · `transport_error` · `parse_error`), so a
  sweep reports epistemic states rather than a failure count — a timeout is not
  evidence of absence, and a parse error means *we do not know what the provider
  returned*;
- the **prohibition on `no_results` under degraded coverage** (design §6.2, D9);
- **capability declarations** (design §6.3) as the interface the planner consumes.

**No `GUARD` test landed with any of them**, deliberately: there is no behaviour yet to
test, and asserting that three empty-reasons are "distinct" in an envelope that does not
implement them is a test of documentation prose — which this repository's own
verification rules forbid. Guards land **with the implementations** that support them,
and each is listed against its item below.

### P1.1 Loaded-empty vs never-loaded — **done (docs)**
Envelope contract: [`trvl-study-design.md`](trvl-study-design.md) §2.1. Scanner
application: §6.7. **The `GUARD` lands with the envelope implementation.**

### P1.2 Sweep breaker, split in two — **done (docs)**
Scanner §6.7 (coverage breaker) and design §6.4/§6.5 (the split, plus the provider's
own health breaker). **The planner guard lands with the planner.**

### P1.3 State the sweep cost before running it — **done (docs)**
Scanner §6.1: five cost dimensions including the retry multiplier and peak memory, with
the refusal condition; design §6.4 moves the per-provider numbers into a capability
block so no provider's arithmetic becomes a scanner rule.

### P1.4 Deterministic itinerary links — **recorded (docs), still deferred**
Requirement in scanner §8 (never fetched at alert time), blocker named: there is no
alert path yet to carry it, and the builder belongs with provider work.

---

## 4. Phase P2 — optional provider (`CODE`, `OWNER`, gated on D6 + the admission gate)

A fli-backed **observation** provider behind the existing provider contract:
capability-declared, coverage-aware, independently disableable, and never authoritative
for booking or baseline inference (design §6.5/§6.6).

### P2.1 The admission gate is the acceptance criterion `ACCEPT`

All ten criteria in design §6.6, verified against P0 evidence. **A provider that fails
any one is not admitted**, however attractive its cost — that is the point of turning D6
into a checklist.

### P2.2 Capability declaration and coverage provenance `CODE`

**Files:** the provider contract module beside the SerpApi adapter; the provider registry.

**Change:** implement `provider_capabilities` (design §6.3), the
`coverage_mode` × `result_state` pair with its legal combinations (design §6.2), the
failure routing of design §6.5, and per-request instances (its client is not
thread-safe).

**ACCEPT:** the shared provider conformance suite (SerpApi plan P2.5) passes; a
test proves the illegal combinations are unrepresentable; a child/infant party yields
`partial`; and the adapter **cannot be selected as primary**.

### P2.3 Health, quarantine and fallback `CODE` `OWNER`

**Change:** the health state machine (`HEALTHY → DEGRADED → UNTRUSTED → DISABLED`), the
quarantine triggers, and the fallback routing — **on the owner's answer to D11**, since
whether a fli failure automatically falls back to SerpApi/PFS is a product decision with
cost consequences, not an implementation detail.

**ACCEPT:** with the provider forced to `DISABLED`, a scanner run completes without
failure (admission criterion 9) and reports the degradation — it does not silently
return fewer results.

### P2.4 Alert eligibility for partial coverage `CODE` `OWNER`

**Change:** enforce the owner's answer to D12. The recommendation on the table is **no
alerts on `coverage_mode = partial`** unless the alert states its basis is partial.

**ACCEPT:** a test that a partial-coverage observation cannot generate an alert, or
that it generates one carrying the explicit partial-coverage disclosure — whichever the
owner decides.

### P2.5 Dependency boundary `OWNER`

**Change:** choose among the three options in design D7 — **(A)** depend on `flights`,
**(B)** vendor only the required encoder subset under MIT, **(C)** reimplement the
minimal encoding behind our own contract — with the constraint that applies to all
three: **fli's internal types never appear in our provider contract.**

**ACCEPT:** a recorded decision; if any dependency is taken, the version is pinned and
the **name collision is handled explicitly** — the PyPI distribution is `flights`, while
`fli` on PyPI is an unrelated project (a real dependency-confusion hazard).

---

## 5. Phase P3 — out of scope

Scanner wiring (planner → provider selection → observation store → verification →
alerting) belongs to the scanner repository. This repo's obligation ends at the provider
contract, as with the SerpApi plan.

---

## 6. Observations recorded, not acted on

### O-1 The canary pattern `DEFERRED`
The subject repository runs a scheduled workflow that watches for Google-side breakage.
Our provider contract faces the same problem the moment we own acquisition, and the
provider-health state machine (design §6.5) is the in-process half of it. A scheduled
canary is the other half; deferred until a provider exists to watch.

### O-2 Its own documentation drift `DEFERRED`
Design §4.9: the README's transport section and the API reference disagree, and the date
cap is stated two ways. Nothing to fix in our repo — recorded as evidence *for*
`test/docs-claims.test.mjs`. No action beyond citation.

---

## 7. Owner decisions required

| # | Decision | Blocks | Design ref |
|---|---|---|---|
| F1 | **Run the P0 acceptance protocol** against Google under our IP — including whether to risk the sustained-load probe (§2.7) | All of P0 | §2 here, design §10 |
| F2 | **Is *owned* acquisition acceptable at all** — unhedged, versus SerpApi's Legal Shield from $150/mo? **A business/legal/ownership decision, not a technical one**, and it must be answered **before any request is made to Google**. YES authorizes F1/P0; **NO forbids the probe entirely** — because running it *is* the acquisition being decided | F1, D6, all of P2 | design §6.6, §9, §11 |
| F3 | **The dependency boundary** — depend / vendor a subset / reimplement (D7 A/B/C) | P2.5 | design D7 |
| F4 | ~~Whether to adopt the patterns now~~ — **answered 2026-10-03: yes** | — | design D1/D5 |
| F5 | **Provider health and fallback** (D11): does fli failure automatically permit fallback to SerpApi/PFS, and which classes quarantine versus retry per search? | P2.3 | design §6.5, D11 |
| F6 | **May partial-coverage observations generate alerts** (D12)? Recommendation: no, unless the alert discloses partial coverage | P2.4 | design §6.2, D12 |

---

## 8. Traceability

| Item | Design ref | Depends on | Verification |
|---|---|---|---|
| P0 cohorts A–G | §4.5, §6.3 | F1 | each cohort produces its pass criterion |
| P0.1–P0.14 | §4.8, §4.9, §4.10, §10 | F1 | 14 accept verdicts, each citing raw evidence |
| P0 cap gate | §4.9, D8 | probe 8 | adapter constant derived from measurement |
| P0 cost/capability | §6.4 | probe 7 | no `<measured>` placeholder left in §6.3/§6.4 |
| P0 health classes | §6.5 | probe 13 | every class reproducible and named |
| P1.1–P1.4 | §5.1 P1–P6 | — | **docs done**; guards land with their implementations |
| P2.1 admission | §6.6, D14 | F1, F2 | all ten criteria pass on evidence |
| P2.2 capability + coverage | §6.2, §6.3 | D6 | conformance suite; illegal states unrepresentable; child/infant `partial` |
| P2.3 health + fallback | §6.5, D11 | F5 | provider disabled ⇒ run completes and reports degradation |
| P2.4 partial-coverage alerts | §6.2, D12 | F6 | partial cannot alert (or alerts with disclosure) |
| P2.5 dependency boundary | D7 | F3 | recorded decision; name collision handled |

---

## 9. Resolution log

| Item | Status | Evidence |
|---|---|---|
| Study documents (incl. review #1 revision) | **Done** | This plan and its companion, in the MkDocs nav; `node --test test/docs-claims.test.mjs` and `python -m mkdocs build` green |
| P0 acceptance protocol | **Not started** | Needs F1; **no request has been made to Google** |
| P1.1–P1.4 pattern adoption | **Done (docs) 2026-10-03** | Empty-reason taxonomy in `trvl-study-design.md` §2.1; cost model, two breakers and accounting in scanner §6.1/§6.7; absence and link rules in scanner §8; decision 8 of scanner §12. Guards deliberately not written — no behaviour exists to test |
| P2.1–P2.5 provider / health / dependency | **Not started** | Needs F2, F5, F6 (and F3 for dependency) |
| P3 scanner wiring | **Out of scope** | Other repository |
| O-1, O-2 | **Deferred** | Recorded above with reasons |

---

## 10. Order of work

1. ~~**F4** — adopt the patterns now.~~ **Answered yes, 2026-10-03; landed.**
2. **F2 — the gate, and it comes before every other step in this list.** It is a
   business/legal/ownership decision (are we willing to own the consequences of
   querying Google's undocumented interface?), not a technical one: the technical case
   for probing is already made by the study. **YES → F1/P0. NO → P0 is not run**, and
   the retained value is the contracts, taxonomy, breakers, coverage semantics, cost
   model and admission gate (design §11).
3. **F5/F6** — health/fallback routing, and whether partial coverage may alert. Product
   decisions, needed before P2 but not before P0.
4. **F1 → P0** — only under F2 = YES: run the acceptance protocol from our own network.
   The repository's own history (design §3) is a demonstration of what happens when an
   undocumented interface moves. **Once F2 is yes, do not reopen the study** — go
   straight to the protocol.
5. **P2** only after P0 passes, only behind the existing provider contract, and only
   past the admission gate.
