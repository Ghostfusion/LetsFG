# First-Class Fare Scanner — System Design

**Status:** design only. Nothing in this document is implemented, and none of it
may be implemented as incidental work — see *Scope and working agreement* at the
end.
**Date:** 2026-10-03
**Purpose:** design a continuously running system that watches a pool of
destinations over flexible dates in First Class, detects genuinely unusual fares,
and alerts on them — instead of merely returning the cheapest fare available at
the moment it was asked.
**Depends on:** [`trvl-study-design.md`](trvl-study-design.md) (MCP contracts,
status/completeness/freshness, client reliability) — the *client* layer this
system is built on top of. That study stays narrow on purpose; this document is
the product architecture it was deliberately kept out of.

---

## 1. The question this design answers

> How do we turn LetsFG plus the transferable ideas from trvl into a system that
> finds **exceptional** First-Class opportunities rather than the cheapest
> current fare?

"Cheapest current" and "unusually cheap" are different questions, and the second
one requires state: history, a notion of normal for *this* route and cabin, a way
to tell a complete answer from a partial one, and a way to tell an indicative
price from a verified one. Everything below exists to make those four things
possible.

## 2. What the platform gives us today (and what it does not)

Grounding first, because the design must fit the actual API rather than an
imagined one. Sources: `docs/api-search.md`, `docs/cli-reference.md`, `AGENTS.md`.

| Capability | Reality today | Consequence for the scanner |
|---|---|---|
| Single search | One origin → **one** destination → `date_from` (+ optional `return_date`) | The unit of work is a *route-date*, and a pool × date-window is a fan-out we own |
| Cabin | `cabin_class: "F"` / `--cabin F` (First) on both lanes | First Class is a search parameter, not a post-filter — good, no client-side re-filtering needed |
| Date flexibility | **No departure range, no return range, no nights/duration window anywhere in the API** | Flexible dates mean *many discrete searches*. This is the dominant cost driver and the reason a Search Planner must exist |
| Multi-destination | `POST /flights/discover` — up to **20 destinations from one origin**, indicative prices, **1 search billed for the batch**, 2–5 s | The cheap first pass: rank destinations before spending |
| Multi-destination (full) | `POST /flights/multi-search` — N destinations in parallel, **1 search per destination** | The expensive second pass, only for candidates |
| Absence reporting | `discover` returns `{"destination":"ORD","price":null,"found":false}` and a `data_note` saying prices are indicative | The server already distinguishes "not found" from a price, and already warns that its prices are not final |
| Billing | Every destination counts as one search, no bundle discount; discover excepted | The scan plan is a **budget**, not just a schedule |
| Rate limits | PFS card: 10/10 min, 30/hour, 100/day. Developer API: 60 req/min, 200 free searches after each booking, then $0.01 each | Cadence is capped by the account, and the limiter's *failure direction* is still unknown (study P1.3) |
| Offer lifetime | Offers expire ~15 minutes after a search; discover prices are not bookable | Nothing may be stored as "current" without an observation time (study §2.1 `freshness`) |
| Fare identity | Offers have an `id` within a search; nothing promises stability across searches. `discover` returns only destination + price | Fare identity must be **constructed** by us (§5.2) |
| Price history / alerts | **No such endpoints** | History, detection and alerting are entirely our layer — which is the point of this document |

**What this tells us:** the platform provides *search*, *cheap ranking* and
*honest absence*. It does not provide continuity. The scanner's job is to be the
memory and the judgement that the API deliberately does not have.

## 3. Target architecture

Six layers, each with one job. The boundaries matter more than the boxes: in
particular, nothing above the Deal Engine may decide "this is a good fare", and
nothing below it may decide "this is worth telling a human about".

```
┌──────────────────────────────────────────────────────────────┐
│ 1. USER POLICY        origins · destinations · date windows  │
│                       trip length · cabin=FIRST · budget     │
│                       alert thresholds · quiet hours         │
└───────────────────────────┬──────────────────────────────────┘
                            ▼
┌──────────────────────────────────────────────────────────────┐
│ 2. SEARCH PLANNER     expand policy → route-date jobs        │
│                       prioritize · budget · avoid waste      │
└───────────────────────────┬──────────────────────────────────┘
                            ▼
┌──────────────────────────────────────────────────────────────┐
│ 3. LETSFG CLIENT      search · discover · re-verify · book   │
│                       (study layer: status/completeness/     │
│                        freshness on every call)              │
└───────────────────────────┬──────────────────────────────────┘
                            ▼
┌──────────────────────────────────────────────────────────────┐
│ 4. TRAVEL DATA LAYER  normalize · identity · dedupe · history│
│                       append observations, never overwrite   │
└───────────────────────────┬──────────────────────────────────┘
                            ▼
┌──────────────────────────────────────────────────────────────┐
│ 5. DEAL ENGINE        percentile · discount · quality ·      │
│                       completeness · confidence · score      │
└───────────────────────────┬──────────────────────────────────┘
                            ▼
┌──────────────────────────────────────────────────────────────┐
│ 6. ALERTS             threshold · dedupe · one message format│
└──────────────────────────────────────────────────────────────┘
```

**Layer responsibilities, stated as prohibitions** (easier to test than goals):

- The Planner never invents fares and never scores them.
- The Client never decides what is interesting.
- The Data Layer never edits an observation it has already written.
- The Deal Engine never invents data it does not have: missing history means low
  confidence, not a guess.
- The Alert layer never states a number the Deal Engine did not produce, and
  never alerts on an unverified price.
- **The LLM is not in this diagram.** It explains a decision the Deal Engine
  already made (see §7).

## 4. Requirements

Numbered as the review framed them, with acceptance criteria attached so each is
testable rather than aspirational.

| # | Requirement | Acceptance criteria |
|---|---|---|
| **R1** | **First Class is a first-class dimension.** Not `cabin = any` with client-side filtering. | Every scan carries the cabin explicitly (`F`); no stored observation is cabin-ambiguous; a scan that returned mixed cabins is a bug |
| **R2** | **Destination pool.** Many destinations from one or more origins. | A policy with N destinations produces N route-date plans; `discover` is used for the ranking pass where the lane allows it |
| **R3** | **Origin pool.** Multiple origins as a first-class concept. | Same as R2, per origin; the planner may weight origins independently |
| **R4** | **Departure flexibility.** An earliest/latest window. | A window of D days expands to D discrete search dates; expansion is explicit and auditable, never implicit |
| **R5** | **Trip-length flexibility.** A nights window (min/max). | Each (departure, nights) pair is one plan unit; the planner can prune by budget |
| **R6** | **Continuous monitoring.** A schedule. | A cadence is declared per policy and honoured under the account's rate limits; a run that cannot execute is recorded as skipped-with-reason, never silently dropped |
| **R7** | **Price history.** Durable observations. | Every observation is append-only with `observed_at`; history is queryable by route, cabin, identity and time |
| **R8** | **Deal detection** distinguishes *cheapest now* from *unusually cheap*. | A fare is only "unusual" relative to a stated baseline (median/percentile over a stated window) with a stated confidence |
| **R9** | **Alert threshold, stated as a rule.** e.g. First Class **and** percentile ≤ 15 **and** `completeness == complete` **and** `price_status == verified`. | The threshold is data, not code; every alert can be explained by the rule that fired it |

## 5. Contracts

### 5.1 `FareObservation` — what was seen (raw, immutable)

One row per (scan unit, itinerary) as returned. It is a *record*, not an opinion:
it must be possible to re-derive every later judgement from observations alone.

```
FareObservation
  observation_id            # our own key, monotonic
  scan_job_id               # which planned unit produced it
  search_id                 # the API's search, for audit and polling
  observed_at               # RFC 3339, UTC — when the source was asked
  freshness                 # live | stale | unknown
  price_status              # observed | verified | stale | unavailable  (§5.4)

  origin                    # IATA
  destination               # IATA
  departure_date            # date
  return_date               # date | null
  cabin                     # "F"

  itinerary
    airline                 # marketing carrier
    operating_carrier       # may differ — First Class cabins are often codeshares
    flight_numbers[]
    aircraft                # when known
    stops                   # 0..n
    total_duration_seconds
    layovers[]              # { airport, duration }
    departure_time, arrival_time

  price
    amount, currency
    taxes_included          # true | false | unknown  — never assume
    indicative              # true for discover results; they are not bookable

  availability_status       # available | sold_out | unknown
  booking_source            # which lane/provider answered
  raw_payload_hash          # + the payload retained out-of-band for audit
```

**Rules.** Append-only. An observation is never updated to a newer price — a new
observation is written. `indicative: true` observations (discover) are recorded —
they are useful for ranking and for history *signals* — but they can never become
`verified`.

### 5.2 `FareIdentity` — what "the same fare" means

The API does not give us a stable identity (§2), so we construct one, and we say
what it is:

```
FareIdentity =
    origin · destination · departure_date · return_date
  · cabin
  · operating_carriers[] · flight_numbers[]
  · stops
```

**Why these fields and not fewer.** `origin + destination + dates` is not the
same fare when the carrier, the flight numbers or the stop count differ — a
"price drop" that is really a different airline is a false alert. **Why not
more:** aircraft and departure time are *churn* — airlines rotate equipment and
retime flights, and including them would make every unchanged fare look new.

**Consequences we accept now.** Re-timed flights will read as new identities;
codeshare marketing/operating changes may split one fare into two. Both are
tolerable (they cost a duplicate alert at worst) and both are visible in the
data, whereas the opposite error — merging two different products under one
identity — produces the false "it dropped 40%" alert that destroys trust in the
system. Identity is a versioned derivation: changing it is a migration, not an
edit.

### 5.3 `FareState` — what is currently true (deduplicated)

Derived, never hand-edited:

```
FareState
  identity                  # §5.2
  current_observation_id · current_price · currency · price_status
  first_seen_at · last_seen_at · last_changed_at
  lowest_seen_price · lowest_seen_at
  observation_count
  history_window_days       # the window over which `normal` was computed
```

**Observation vs state, explicitly:** every scan writes an *observation*; the
*state* is a projection. This is the deduplication the scanner needs — the same
fare seen every three hours for three weeks is ~170 observations and **one**
state, and only the state's `last_changed_at` decides whether anything is worth
saying.

### 5.4 `price_status` — the verified/indicative lifecycle

| Status | Meaning | May it alert? |
|---|---|---|
| `indicative` | From `discover`: explicitly not bookable (the API says so in `data_note`) | **No** — ranking only |
| `observed` | A real search returned it | Only if the policy explicitly allows speculative alerts |
| `verified` | Re-searched just before alerting, price held, `verified_at` stamped | **Yes** — the default |
| `stale` | Previously seen, not re-confirmed within the freshness window | **No** |
| `unavailable` | Was seen, now `sold_out` or gone | No — this is an event worth recording, not an alert |

This exists because of the failure everyone in this domain ships at least once:
a "$5,200 First Class!" alert that becomes "$11,400" at checkout. Verification is
a second search on the specific route-date, and its cost is budgeted (§6).

### 5.5 `FareEvaluation` — what we think about it (separate, derived)

Deliberately a different object from the observation, written to a different
place:

```
FareEvaluation
  identity · evaluated_at
  current_price · currency
  baseline            # { window_days, median, p15, p25, low, sample_size }
  historical_percentile
  absolute_discount   # vs median, in currency and percent
  deal_score          # 0–100, deterministic (§7)
  confidence          # 0–1, from data quality (§7.2)
  completeness        # of the scan that produced it
  explanation_inputs  # the numbers the LLM may talk about
```

**Never contaminate the observation with the evaluation.** Re-scoring history
(after a baseline change) must not require rewriting what was observed.

## 6. Search Planner

The Planner is what keeps this from being a cron loop.

**Input:** the user policy (R1–R6) plus the current `FareState` table and the
account's remaining budget.

**Output:** ordered, budgeted scan jobs, each of which is one *unit* of billed
work.

**Expansion.** A policy with O origins, D destinations, a departure window of `dw`
days and a nights window of `nw` values expands to `O × D × dw × nw` units before
pruning — and the planner's first duty is to *not* run most of them.

**Two-phase scan, because the platform supports it:**

```
Phase 1 — RANK      POST /flights/discover   → up to 20 destinations, 1 search
                    prune: no results · absurd price for the cabin · out of budget
Phase 2 — MEASURE   POST /flights/search     → per destination-date, 1 search each
                    (full results, real prices, bookable)
Phase 3 — VERIFY    POST /flights/search     → only for candidates about to alert
```

**Prioritization order** (highest first; each rule is a *reason* a unit is
scheduled, and the reason is stored with the job so a scan log can explain
itself):

1. never searched,
2. recently changed price,
3. historically volatile route,
4. approaching departure while still inside the policy window,
5. previously cheap (near the historical low),
6. user favourites.

**Budget accounting.** Every job records its expected cost in searches before it
runs and its actual billing after. A planner that cannot account for R7's
requirement — "the scan plan is a budget" — will quietly exhaust a card's daily
100 searches and then misreport the resulting failures as "no deals today".

**What the Planner must never do:** silently drop a unit. Skipped work carries a
reason (`budget`, `rate_limit`, `policy_pruned`, `dedupe`), because "we did not
look" and "we looked and found nothing" are exactly the two things the study's
`status`/`completeness` contract exists to keep apart.

## 7. Deal engine

### 7.1 Deterministic score

The score is arithmetic over stored numbers, so it can be recomputed, back-tested
and argued with:

```
deal_score =
      w1 · percentile_component      # where in the historical distribution
    + w2 · absolute_discount        # currency/percent below median, capped
    + w3 · route_quality            # nonstop, sensible duration, decent arrival window
    + w4 · cabin_quality            # First Class with a real First product, not a recliner
    + w5 · itinerary_quality        # short layovers, few stops, no self-transfer
    + w6 · data_completeness        # from the scan envelope (complete > partial)
```

Bands: `0–49` normal · `50–69` interesting · `70–84` good · `85–94` very good ·
`95–100` exceptional.

Weights are configuration with a documented default, and changing them is a
versioned decision — a score whose weights drift silently is a score nobody can
trust.

### 7.2 Completeness and confidence feed the score (not just the message)

This is the connection that makes the design worth building:

```
Fare A: $5,900   completeness = complete   percentile = 8
Fare B: $5,500   completeness = partial    percentile = 5
```

A naive "cheapest wins" ranks B first. **A is the better alert**: B's percentile
was computed from a scan that did not finish, so both its price and its baseline
are less trustworthy. The score therefore weights `data_completeness`, and
`confidence` is derived from three inputs — scan completeness, baseline sample
size (`sample_size` from §5.5), and `price_status` (verified > observed >
indicative). An alert whose confidence is below the policy floor is not sent; it
is logged for the operator.

### 7.3 The LLM explains, it does not decide

Allowed: *"ANA First Class ORD–HND, 38% below its 90-day median, nonstop,
verified 4 minutes ago."*
Not allowed: deciding that a fare is a deal, inventing a baseline, or contradicting
the score. The explanation is generated from `explanation_inputs` only.

## 8. Alerts

- **Threshold as data** (R9): `cabin == F AND percentile <= 15 AND completeness ==
  complete AND price_status == verified AND confidence >= 0.7`.
- **Dedupe on `FareIdentity` + state change**, not on time: one alert per fare
  state transition (first appearance, new low, price increase after a fall if the
  policy cares). Re-alerting the same unchanged fare every cadence is the fastest
  way to have alerts muted.
- **One message format** for v1:

```
🔥 ORD → NRT   ANA First
$5,940 round trip   ·   −42% vs 90-day median
Mar 12 → Mar 22   ·   nonstop   ·   verified 4 min ago
[View]
```

- **Quiet hours and rate caps** belong to the policy, and the alert layer is
  where they are enforced — the Deal Engine has no idea what time it is.

## 9. Deliberate non-goals

| Not building | Why |
|---|---|
| A dashboard or any GUI | First prove the system can answer *"did a genuinely exceptional First-Class fare appear?"* A single good message is the v1 user interface |
| LLM-driven deal decisions | Non-reproducible, unexplainable, cannot be back-tested (§7.3) |
| Any provider/scraper/anti-bot work | Not ours — engine is server-side; see the study's architectural principle |
| Speculative alerts on unverified prices | The one failure that destroys trust fastest (§5.4) |
| A local search engine or cache of bookable offers | Offers expire in ~15 minutes; a cache of expired offers is a liability, not a feature |
| Booking automation in v1 | Alerts first; booking is a human decision with real money attached until precision is proven |

## 10. Implementation priority

| Phase | Content | Status |
|---|---|---|
| **P0** correctness/security | atomic config writes · canonical config path · credential ownership · unblock the Python test suite · baseline CI | **Landed 2026-10-03** |
| **P1** MCP contract | `status` · `completeness` · `freshness` · `outputSchema` + `structuredContent` · verified-vs-indicative fields | Status/completeness landed; freshness + `outputSchema`/`structuredContent` next |
| **P2** scanner foundations | `FareObservation` · `FareIdentity` · observation persistence · dedupe → `FareState` · Search Planner · scheduler | Not started |
| **P3** intelligence | historical distribution/baseline · deal scoring · confidence · fare-change detection | Not started |
| **P4** user experience | alerts (one format) → then, only if needed, a UI · natural-language policy input · LLM explanation | Not started |
| **P5** optimization | destination prioritization · adaptive scan frequency · search-budget optimization · anomaly detection | Not started |

Sequencing note: P1's contract work is a prerequisite for P2, not a parallel
stream — an observation whose `status`/`completeness`/`freshness` are guesswork
would poison the history that P3 scores.

## 11. Open questions

Inherited from the study's scanner-layer list (Q5–Q11 there) and unanswered here:

1. **Where does this system live?** This repository is a *client* repository
   (SDKs, MCP server, plugin). The scanner needs persistent storage, a scheduler
   and alerting. Candidates: a new service alongside the client repo, a feature
   inside the QML plugin (local, single-user, no server), or a thin CLI +
   systemd/cron job. **This is the first thing to decide**, because it determines
   the storage engine and whether the Plan's "no dashboard" holds.
2. **What is the storage engine?** SQLite is the obvious default for a
   single-user scanner (append-only observations, a `FareState` view);
   Postgres only if this becomes multi-user.
3. **How is the account budget shared?** A scanner running continuously consumes
   the same 100 searches/day as interactive use on the same card. The policy must
   allocate, not compete.
4. **What is the baseline window** for a given route/cabin (90 days? by
   observation count? by season?), and what happens before it is populated —
   silence, or a lower-confidence provisional alert?
5. **How is a "First Class product" verified?** A First fare on a narrow-body
   regional product is not the same experience as a long-haul suite; R1 says
   cabin is a dimension, but `cabin_quality` needs data we may not have.
6. **How do we verify without paying twice?** Verification is a second search.
   Is it budgeted per alert candidate, or batched (verify the top N candidates
   once per run)?

## Scope and working agreement

This document **plans**; it does not authorise changes. Working-agreement rule 4
("never make code changes unless they are defects") applies in full: the scanner
is new scope, and it needs an explicit owner decision — starting with open
question 1 — before any code exists. Until then, the only permitted work is the
client contract it depends on (P1) and defect fixes.

**Traceability.**

| Study artefact | Used here |
|---|---|
| §2.1 status / completeness / `freshness` / `observed_at` | §5.1, §5.4, §6 (skip reasons), §7.2 |
| §2.4 completeness gating | §7.2 (ranking A above B) |
| §2.4 verified-vs-unverified pricing | §5.4 (`price_status`), §8 (alert gate) |
| §2.5 telemetry vs domain data | §5.1 (observations are domain data, not telemetry) |
| §2.3 tool-description budget | Not applicable here (the scanner is not an MCP tool yet) |
| D12 verified-vs-indicative in the contract, policy elsewhere | §5.4, §8 |
| `discover` / `multi-search` billing semantics | §6 (two-phase scan, budget) |
