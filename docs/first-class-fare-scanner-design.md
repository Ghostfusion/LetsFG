# First-Class Fare Scanner — System Design

**Status:** design only. Nothing in this document is implemented, and none of it
may be implemented as incidental work — see *Scope and working agreement* at the
end.
**Date:** 2026-10-03 (revision 4, after the P2 sequencing decision)
**Purpose:** design a continuously running system that watches a pool of
destinations over flexible dates in First Class, detects genuinely unusual fares,
and alerts on them — instead of merely returning the cheapest fare available at
the moment it was asked.
**Depends on:** [`trvl-study-design.md`](trvl-study-design.md) (MCP contracts,
status/completeness/freshness, client reliability) — the *client* layer this
system is built on top of.

> **What changed in revision 4.** P2 is re-sequenced: it begins with the
> observation store and shadow collection, **not** alert delivery (§11). The
> precision target is an owner *requirement* while the thresholds are
> *uncalibrated hypotheses* (§7.3), and a **P2 alerting gate** now forbids alert
> delivery until the calibration dataset exists and the threshold policy is
> versioned. Calibration sufficiency is defined by sample size **and diversity**,
> not by a count (§9.6), and the selected configuration is the least restrictive
> one that meets ≥80% precision while preserving coverage.

> **What changed in revision 3.** The seven open questions are now **decisions**
> (§12), and they are normative V1 rules: a separate scanner repository, SQLite +
> WAL with append-only observations as the source of truth, a hard account-budget
> reservation that protects interactive use, ECB daily FX with a 24-hour freshness
> rule, verification that is per-candidate logically but batched operationally
> (`N = 5` initially), explicit provider-level `cabin = FIRST` with
> evidence-only product quality, and an ≥80% *objective* alert-precision target
> measured without user behaviour. §9 is new and specifies the four metrics, the
> alert-opportunity record, the 24h/72h observation window, and the
> SHADOW → CALIBRATION → PRODUCTION rollout that calibrates the provisional
> thresholds.

> **What changed in revision 2.** The architecture is frozen as-is; this revision
> fixes contract precision. Review #2's eight pre-implementation items are
> applied: the `price_status` contract is now single-sourced (§5.4), fare identity
> is split into itinerary vs offer identity (§5.2), the baseline methodology is
> specified — cohort, self-exclusion, population separation, comparability,
> currency (§7.1) — `deal_score` and `confidence` are separated (§7.2), the
> evaluator/baseline/policy/identity versions are machine-readable (§5.5), a
> `baseline_status` exists for the months before history accumulates (§5.5), alert
> cooldown is specified (§8), deterministic replay is requirement R10, and the
> Policy Validator, volume estimate and `fresh_enough` are added (§3, §6).

---

## 1. The question this design answers

> How do we turn LetsFG plus the transferable ideas from trvl into a system that
> finds **exceptional** First-Class opportunities rather than the cheapest
> current fare?

"Cheapest current" and "unusually cheap" are different questions, and the second
requires state: history, a notion of normal for *this* route and cabin, a way to
tell a complete answer from a partial one, and a way to tell an indicative price
from a verified one. Everything below exists to make those four things possible.

## 2. What the platform gives us today (and what it does not)

Grounding first, because the design must fit the actual API. Sources:
`docs/api-search.md`, `docs/cli-reference.md`, `AGENTS.md`.

| Capability | Reality today | Consequence for the scanner |
|---|---|---|
| Single search | One origin → **one** destination → `date_from` (+ optional `return_date`) | The unit of work is a *route-date*; a pool × date-window fan-out is ours |
| Cabin | `cabin_class: "F"` / `--cabin F` (First) on both lanes | First Class is a search parameter, not a post-filter |
| Date flexibility | **No departure range, no return range, no nights window anywhere in the API** | Flexible dates mean *many discrete searches* — the dominant cost, and why a Search Planner must exist |
| Multi-destination | `POST /flights/discover` — up to **20 destinations from one origin**, indicative prices, **1 search billed for the batch**, 2–5 s | The cheap first pass: rank destinations before spending |
| Multi-destination (full) | `POST /flights/multi-search` — N destinations in parallel, **1 search per destination** | The expensive second pass, for candidates only |
| Absence reporting | `discover` returns `{"destination":"ORD","price":null,"found":false}` and a `data_note` saying prices are indicative | The server distinguishes "not found" from a price, and warns its prices are not final |
| Billing | Every destination counts as one search, no bundle discount; discover excepted | The scan plan is a **budget**, not just a schedule |
| Rate limits | PFS card: 10/10 min, 30/hour, 100/day. Developer API: 60 req/min, 200 free searches after each booking, then $0.01 each | Cadence is capped by the account, and the limiter's *failure contract* is **unmeasured** — the one P0 blocker for the scheduler (study §6, Q8; §12 here) |
| Offer lifetime | Offers expire ~15 minutes after a search; discover prices are not bookable | Nothing is stored as "current" without an observation time (study §2.1) |
| Fare identity | Offers have an `id` within a search; nothing promises stability across searches. `discover` returns only destination + price | Identity must be **constructed** by us, and graded by how much the API actually tells us (§5.2) |
| Fare conditions | Search offers carry `conditions.refund_before_departure` / `change_before_departure`; fare brand/bucket are **not** documented | Two products can share one itinerary; offer-level identity is best-effort, and must say so (§5.2) |
| Price history / alerts | **No such endpoints** | History, detection and alerting are entirely our layer — which is the point of this document |

**What this tells us:** the platform provides *search*, *cheap ranking* and
*honest absence*. It does not provide continuity. The scanner's job is to be the
memory and the judgement the API deliberately does not have.

## 3. Target architecture

### 3.1 Layers

Seven boxes, each with one job. The boundaries matter more than the boxes:
nothing above the Deal Engine may decide "this is a good fare", and nothing below
it may decide "this is worth telling a human about".

```
┌──────────────────────────────────────────────────────────────┐
│ 1. USER POLICY        origins · destinations · date windows  │
│                       trip length · cabin=FIRST · budget     │
│                       alert thresholds · quiet hours         │
└───────────────────────────┬──────────────────────────────────┘
                            ▼
┌──────────────────────────────────────────────────────────────┐
│ 2. POLICY VALIDATOR   is the policy internally valid?        │
│                       min>max · unsupported cabin ·          │
│                       impossible budget · pool partition     │
└───────────────────────────┬──────────────────────────────────┘
                            ▼
┌──────────────────────────────────────────────────────────────┐
│ 3. SEARCH PLANNER     expand policy → route-date jobs        │
│                       estimate volume · prioritize · budget  │
└───────────────────────────┬──────────────────────────────────┘
                            ▼
┌──────────────────────────────────────────────────────────────┐
│ 4. LETSFG CLIENT      discover · search · verify             │
│                       (study layer: status/completeness/     │
│                        freshness on every call)              │
└───────────────────────────┬──────────────────────────────────┘
                            ▼
┌──────────────────────────────────────────────────────────────┐
│ 5. OBSERVATION STORE  normalize · identity · append-only     │
└───────────────────────────┬──────────────────────────────────┘
             ┌──────────────┴───────────────┐
             ▼                              ▼
┌──────────────────────────┐   ┌──────────────────────────────┐
│ 5a. FareState (derived)  │   │ 5b. Historical Baseline      │
│ current · first/last seen│   │ cohort · window · samples    │
│ lowest seen              │   │ (§7.1)                       │
└───────────┬──────────────┘   └───────────────┬──────────────┘
            └───────────────┬──────────────────┘
                            ▼
┌──────────────────────────────────────────────────────────────┐
│ 6. DEAL ENGINE        price opportunity · travel quality ·   │
│                       confidence · baseline status           │
└───────────────────────────┬──────────────────────────────────┘
                            ▼
┌──────────────────────────────────────────────────────────────┐
│ 7. VERIFY + ALERT     re-search candidates (budget-gated)    │
│                       threshold · dedupe · cooldown          │
└──────────────────────────────────────────────────────────────┘
```

**Policy Validator.** Its only job is to answer *"is this policy internally
valid?"* before anything is scheduled: `min_nights > max_nights`, `earliest >
latest`, an unsupported cabin, a budget that cannot fund even one scan, or a
destination pool larger than an endpoint accepts (partition it). It need not be a
separate service; it must be a separate step, so the Planner stays about
scheduling rather than input validation.

**Verification belongs to the alert boundary, not to the search phase.** A
verified price is what an alert is allowed to claim, so verification is the last
gate before Alerting — budgeted there — not a third blanket sweep over every
result (§6.5).

### 3.2 Prohibitions (easier to test than goals)

- The Validator never schedules; the Planner never validates policy.
- The Planner never invents fares and never scores them.
- The Client never decides what is interesting.
- The Store never edits an observation it has already written.
- The Deal Engine never invents data it does not have: missing history means a
  `baseline_status`, not a guess.
- The Alert layer never states a number the Deal Engine did not produce, and
  never alerts on an unverified price.
- **The LLM is not in this diagram** (see §7.4).

## 4. Requirements

| # | Requirement | Acceptance criteria |
|---|---|---|
| **R1** | **First Class is a first-class dimension.** Not `cabin = any` with client-side filtering. | Every scan carries the cabin explicitly (`F`); no stored observation is cabin-ambiguous; a scan that returned mixed cabins is a bug; **a candidate whose provider reports Business or Economy cannot enter the scanner** (§7.2, decision 6 in §12) |
| **R2** | **Destination pool.** Many destinations from one or more origins. | A policy with N destinations produces N route-date plans; `discover` is used for the ranking pass where the lane allows it |
| **R3** | **Origin pool.** Multiple origins as a first-class concept. | Same as R2, per origin; the planner may weight origins independently |
| **R4** | **Departure flexibility.** An earliest/latest window. | A window of D days expands to D discrete search dates; expansion is explicit and auditable |
| **R5** | **Trip-length flexibility.** A nights window (min/max). | Each (departure, nights) pair is one plan unit; the planner can prune by budget |
| **R6** | **Continuous monitoring.** A schedule. | A cadence is declared per policy and honoured under the account's rate limits; a run that cannot execute is recorded as skipped-with-reason, never silently dropped |
| **R7** | **Price history.** Durable observations. | Every observation is append-only with `observed_at`; history is queryable by route, cabin, identity and time |
| **R8** | **Deal detection** distinguishes *cheapest now* from *unusually cheap*. | A fare is only "unusual" relative to a stated baseline (§7.1) with a stated `baseline_status` and confidence |
| **R9** | **Alert threshold, stated as a rule.** e.g. First Class **and** percentile ≤ 15 **and** `completeness == complete` **and** `price_status == verified`. | The threshold is data, not code; every alert can be explained by the rule that fired it |
| **R10** | **Deterministic replay.** Given the same observation set, baseline configuration and algorithm versions, the Deal Engine must produce the same evaluations and the same alert eligibility. | Replaying stored observations with a changed threshold (`percentile <= 15` → `<= 10`) answers *"how many alerts would we have received?"* without re-searching. This is how thresholds get calibrated instead of guessed |

## 5. Contracts

### 5.1 `FareObservation` — what was seen (raw, immutable)

One row per (scan unit, itinerary, offer) as returned. It is a *record*, not an
opinion: every later judgement must be re-derivable from observations alone.

```
FareObservation
  observation_id            # our own key, monotonic
  scan_job_id               # which planned unit produced it
  search_id                 # the API's search, for audit and polling
  source_operation          # discover | search | verify   — provenance, not status
  observed_at               # RFC 3339, UTC — when the source was asked
  freshness                 # live | stale | unknown
  price_status              # indicative | observed | verified | stale | unavailable (§5.4)

  origin · destination      # IATA
  departure_date · return_date
  cabin                     # "F"

  itinerary
    airline · operating_carrier      # may differ — First cabins are often codeshares
    flight_numbers[] · aircraft       # aircraft when known
    stops · total_duration_seconds
    layovers[]                        # { airport, duration }
    departure_time · arrival_time

  price
    amount · currency                 # as returned
    original_amount · original_currency   # immutable copy of `amount`/`currency`
    evaluation_amount · evaluation_currency · fx_rate · fx_source · fx_at   # §7.1
    fx_status                         # fresh | stale | not_required (§7.1, decision 4)
    taxes_included                    # true | false | unknown — never assume
    price_comparability               # comparable | non_comparable | unknown (§7.1)

  availability_status       # available | sold_out | unknown
  raw_payload_hash          # + the payload retained out-of-band for audit
```

**Rules.** Append-only: an observation is never updated to a newer price — a new
observation is written. `indicative` observations (from `discover`) **are
stored** — they are useful for ranking and for a separate indicative series — but
they are never `verified` and they never enter the bookable-fare baseline (§7.1).

### 5.2 Identity: `ItineraryIdentity` and `OfferIdentity`

Two offers can share a flight and differ commercially — same ANA First Class
flight, one refundable at $6,000 and one restrictive at $5,400. Calling
`origin+dates+flights` a "fare identity" would make a refundability downgrade look
like a 10% price drop. So identity is two-level, and the record states which
level it is at:

```
ItineraryIdentity
    origin · destination · departure_date · return_date
  · operating_carriers[] · flight_numbers[] · stops

OfferIdentity
    itinerary_identity
  · cabin
  · fare_conditions          # from conditions.refund_before_departure / change_before_departure
  · fare_brand · booking_class   # only when the API actually returns them
  · identity_granularity     # itinerary | offer  — how much we could distinguish
  · identity_version         # identity construction is versioned; changing it is a migration
```

**Why these fields.** `origin + destination + dates` is not the same fare when the
carrier, the flight numbers or the stop count differ — a "drop" that is really a
different airline is a false alert. **Why not aircraft or departure time:** those
are *churn* — airlines rotate equipment and retime flights, and including them
would make every unchanged fare look new.

**Honesty about granularity.** `fare_brand` and `booking_class` are **not
documented** as present in our API responses (§2). When they are absent, the
record says `identity_granularity = itinerary` — it does **not** silently pretend
two commercially different products are one fare. A price change across a
condition change (refundable → restrictive) is then reported as a *different
offer*, which is the conservative direction: it may cost a duplicate alert, never
a false "40% drop".

**Accepted consequences.** Re-timed flights read as new identities; codeshare
marketing/operating changes may split one fare into two. Both are visible in the
data, and both fail toward *more* alerts rather than toward a wrong claim about a
price fall.

### 5.3 `FareState` — what is currently true (deduplicated)

Derived, never hand-edited:

```
FareState
  offer_identity            # §5.2
  current_observation_id · current_price · evaluation_currency · price_status
  first_seen_at · last_seen_at · last_changed_at
  lowest_seen_price · lowest_seen_at
  observation_count
```

**Observation vs state, explicitly:** every scan writes an *observation*; the
*state* is a projection. The same fare seen every three hours for three weeks is
~170 observations and **one** state, and only the state's `last_changed_at`
decides whether anything is worth saying. `first_seen_at` / `last_seen_at` /
`lowest_seen_at` are the four numbers an alert deduper actually needs (§8).

### 5.4 `price_status` — the single authoritative lifecycle

One field, one lifecycle. There is no separate `indicative` boolean: two fields
that can disagree is how "bookable but indicative" gets shipped.

| Status | Meaning | In the bookable baseline? | May it alert? |
|---|---|---|---|
| `indicative` | From `discover`: explicitly not bookable (the API says so in `data_note`) | **No** (separate indicative series, §7.1) | **No** — ranking only |
| `observed` | A real search returned it | **Yes** | Only if policy explicitly allows speculative alerts |
| `verified` | Re-searched at alert time, price held, `verified_at` stamped | **Yes** | **Yes** — the default |
| `stale` | Previously seen, not re-confirmed within the freshness window | Yes (historical) | **No** |
| `unavailable` | Was seen, now `sold_out` or gone | Yes (historical) | No — an event to record, not an alert |

`verified` carries `verified_at` and the `observation_id` that verified it.
Provenance lives in `source_operation` (`discover | search | verify`), so the
*status* stays a single value while the *route it arrived by* remains auditable.

**What `verified` does and does not prove (Q11 decision in the study, §6).**
Re-verification proves the authoritative search path returned that fare again for
that route and date. It does **not** prove the airline checkout will honour that
price: `verified` **≠ guaranteed bookable**. The honest chain is
`indicative → observed → verified → alert eligible`, and an alert must never
promise more than the last step established — which is also why the alert copy in
§8 says "verified 4 min ago" rather than "available".

### 5.5 `FareEvaluation` — what we think about it (derived, versioned)

Written to a different place from observations, so re-scoring history never
rewrites what was observed:

```
FareEvaluation
  offer_identity · evaluated_at
  evaluator_version · baseline_version · policy_version · identity_version

  price
    current_amount · evaluation_currency
    baseline { cohort(§7.1), max_window_days, min_samples, median, p15, p25, low,
               sample_size, excluded_indicative, excluded_non_comparable }

  baseline_status           # established | insufficient_history
                            # | seasonal_insufficient | incomparable   (see below)
  historical_percentile     # null when the baseline is not established
  absolute_discount         # vs median, in currency and percent
  rankable · rankable_reason    # same_currency | fx_fresh | fx_stale
                                # | incomparable | insufficient_history  (§7.1)
  
  price_opportunity_score   # 0–100 — how cheap (§7.2)
  travel_quality_score      # 0–100 — how good the itinerary is (§7.2)
  overall_opportunity       # combination, for ranking only
  confidence                # 0–1 — evidence quality, NOT folded into the scores
  explanation_inputs        # exactly the numbers the LLM may talk about
```

**`baseline_status` is a first-class outcome, not a failure.** For the first
months there is no history, and a system that forces every fare through a score is
a system that invents a normal price. So:

| `baseline_status` | Meaning | Then |
|---|---|---|
| `established` | Enough comparable observations | Score normally |
| `insufficient_history` | Too few observations in the max window | `historical_percentile = null`, no alert on percentile rules; observation still stored |
| `seasonal_insufficient` | Enough overall, too few for the relevant season | Same, plus a lower ceiling on confidence |
| `incomparable` | Baseline exists but is not comparable (currency/FX/tax basis, §7.1) | No percentile-based alert |

## 6. Search Planner

### 6.1 Expansion, volume estimate, and the decide gate

**Expansion.** A policy with O origins, D destinations, a departure window of `dw`
days and a nights window of `nw` values expands to `O × D × dw × nw` units before
pruning. A realistic policy — 3 origins × 20 destinations × 45 dates × 10
durations = **27,000 units** — is not a search plan; it is a bill. So the Planner
computes, **before executing anything**:

```
estimated_units
estimated_billable_searches     # 1 per destination, except a discover batch
estimated_daily_cost            # against the account's allowance and price per excess search
```

and then must choose explicitly: **execute**, **reduce** (drop priorities, narrow
the window), or **defer** (schedule over a longer horizon). A plan that cannot
state its own cost is not allowed to run — this is the rule that keeps R6 from
quietly burning a card's 100 daily searches and then reporting the resulting gaps
as "no deals today".

**Cost means the fan-out, not just the search count** (adopted 2026-10-03, from the
fli study §4.6, which measured a bounded sweep at 42 page fetches / ~135 HTTP
requests after retries / ~4 s / several hundred MB where an unbroken run would have
cost 279 fetches):

```
estimated_units
estimated_billable_searches
estimated_http_requests        # includes the retry multiplier, not just successes
estimated_wall_time
estimated_peak_memory          # a sweep holds pages and parsed results per worker
```

A plan that cannot state **all five** is refused, not run optimistically — a sweep
whose memory or retry cost is unknown is a sweep that can be killed halfway and
report the wreckage as coverage.

**Two-phase scan, because the platform supports it:**

```
Phase 1 — RANK      POST /flights/discover   → up to 20 destinations, 1 search
                    prune: no results · absurd price for the cabin · out of budget
Phase 2 — MEASURE   POST /flights/search     → per destination-date, 1 search each
                    full results, real prices, bookable
Phase 3 — VERIFY    POST /flights/search     → only for alert candidates, budget-gated (§6.5)
```

### 6.2 Default prioritization policy (not architecture)

A first cut, stated as a **default policy** so it can be tuned without pretending
the architecture changed:

1. never searched
2. recently changed price
3. historically volatile route
4. approaching departure while still inside the policy window
5. previously cheap (near the historical low)
6. user favourites

The architecture does not hard-code this ordering. Scheduling is a score:

```
priority_score = freshness_need + volatility + expected_value
               + departure_proximity + user_preference
```

and each job stores the terms that produced it, so a scan log can explain itself.

### 6.3 Skip reasons — "pruned" is not "not eligible"

Every skipped unit carries a reason. Distinguishing a *decision* from a *failure*
is the same discipline as the status contract:

```
budget_exhausted   # the scanner allocation is spent — never the interactive reserve (§6.6)
rate_limit      # the account's limiter refused
policy_pruned   # the user's own filters excluded it
dedupe          # identical to a unit already planned
fresh_enough    # last successful search is newer than the cadence requires
```

`fresh_enough` is deliberately not "budget exhausted": *"we searched this twenty
minutes ago and the cadence is three hours"* is a scheduling decision, and
lumping it with budget starvation is how a planner's logs stop being usable.
`budget_exhausted` is terminal for the run — the Planner stops creating work, and
never borrows (§6.6).

### 6.4 What the Planner must never do

Silently drop a unit. "We did not look" and "we looked and found nothing" are
exactly the two things the study's `status`/`completeness` contract exists to keep
apart, and the same distinction applies here.

### 6.5 Verification is budget-gated

Verification is a second search, so it is ranked and capped, never applied to
every result.

**Logically per candidate, operationally batched** (decision 5, §12). Each
candidate keeps its own verdict, but the planner is free to issue the work in one
batch where the interface allows it:

```
VerificationRequest[]  →  LetsFG batch operation if available  →  VerificationResult[]
```

Where candidates share an origin and differ only by destination,
`POST /flights/multi-search` is that batch operation — and note honestly that it
saves **round trips, not budget**: it bills one search per destination, exactly
like individual searches.

**Ranking**, then verify the top candidates:

```
1. price_opportunity_score    2. confidence
3. completeness               4. expected alert value        5. recency
```

**How many.** `verify_top_n = 5` per scanner run is the V1 default — enough to
surface several exceptional fares without letting verification dominate a 70-search
daily allocation. **5 is a calibration parameter, not a permanent constant**
(§9.6). The long-run rule is dynamic, not fixed:

```
N = min(configured_max, candidates_above_verification_score_threshold, remaining_verification_budget)
```

If only two candidates clear the threshold, `N = 2`: never verify five merely
because five is the maximum.

"100 candidates → 100 verification searches" is forbidden without an explicit
budget gate. If the budget cannot verify a candidate, the candidate is logged as
unverified — not alerted on a stale price (§8).

### 6.6 The account budget is a hard reservation

The scanner and interactive use share one account allowance (100 searches/day on a
PFS card), so the allocation is a **reservation**, not a shared pool (decision 3,
§12):

```yaml
account_budget:
  daily_limit: 100
  interactive_reserved: 30     # never touched by the scanner
  scanner_max: 70
```

```
scanner_available = daily_limit - interactive_reserved - scanner_consumed
```

Three rules make this a guarantee rather than a hope:

1. **The scanner may never consume the interactive reserve** — not even if it left
   its own allocation unused earlier in the day. The safety property is: *the
   scanner can never starve interactive use.*
2. **Discovery, search and verification all consume the scanner allocation.** The
   budget is one pool for all three, which is why verification is ranked and capped
   (§6.5) instead of applied to every result.
3. **Exhaustion is terminal for the run** (`budget_exhausted`, §6.3): the Planner
   stops creating work and does not borrow from the reserve.

### 6.7 Emptiness, failure, and the sweep breaker

Three facts must never be collapsed when a sweep reports nothing. The taxonomy is
normative in [`trvl-study-design.md`](trvl-study-design.md) §2.1:

```
provider_empty    the lane answered and its own result set was empty
not_loaded        no usable response: timeout, limiter refusal, auth failure, or a
                  200 whose payload never arrived
filtered_out      the lane returned rows; our own filters removed all of them
```

**Only `provider_empty` may be reported as "no results".** `not_loaded` is a failed
unit, `filtered_out` is a narrowed one. A scan log that says "no deals today" while
half its units never loaded is lying — this is §6.4's rule applied to outcomes
rather than to plans.

**And `no_results` is prohibited outright whenever coverage is degraded** (canonical
statement: [`trvl-study-design.md`](trvl-study-design.md) §2.1): `{no_results,
coverage_mode: partial}` is a contradiction, because it asserts an absence the
evidence cannot support. A degraded source's emptiness is a statement about our
visibility, not about the market.

**Attempt states, not a failure count** (adopted 2026-10-03, from the fli study). A
sweep reports epistemic states, because they are not interchangeable:

```
ATTEMPTED  ├── loaded_with_results    evidence: rows exist
           ├── loaded_empty           evidence: the source returned nothing here
           ├── rejected               evidence: the source refused (policy/limiter)
           ├── timeout                NO evidence about this unit
           ├── transport_error        NO evidence: DNS, TLS, reset
           └── parse_error            NO evidence: we do not know what came back
```

A 93-date sweep that reports "42 failures" has thrown away the only information that
matters. The planner reasons over the breakdown, and only the two evidence-bearing
classes may support a conclusion about the market (§6.4).

**Two breakers, because they answer different questions** — and neither is another
project's arithmetic ([`fli-study-design.md`](fli-study-design.md) §6.4/§6.5, D13):

```
search coverage breaker   "should I keep trying units?"   counts loaded_empty and
                                                          provider-confirmed rejection only
provider health breaker   "is this source trustworthy?"   counts contract-level anomalies
                                                          (e.g. a parse failure on a 200)
```

- The coverage breaker's bound is proportional to concurrency
  (`breaker_bound = (grace + worker_count) × attempts_per_unit`) — **our** formula,
  **our** constants.
- **Only evidence-bearing outcomes count against it.** A timeout says nothing about
  the units not yet tried, so it must not consume their share.
- It **disarms on the first successful load**, even an empty one — which is exactly why
  it cannot catch a sweep that is mostly timeouts around one lucky success. So a
  **second, independent condition** is required: raise when nothing priced **and** at
  least half the attempted units never loaded.
- A **parse error must not feed the coverage breaker at all**: it is evidence about the
  *provider*, not about the route. It feeds the health breaker, which can move a source
  to `DEGRADED` and take it out of the run without failing it (design §6.5, admission
  criterion 9).

**Unattempted work is never charged as failed work.** The budget accounting counts
units we actually sent; a breaker that abandons a sweep records the rest as
`unattempted` — never `failed`, never billable. This keeps §6.3's distinction intact
at the accounting layer. (The rule first appeared in the SerpApi lane's ledger design
§10 — a lane declined and adopted the same day — which is exactly why it is restated here
as a scanner invariant rather than left as a citation: it holds whether or not that lane
is in service.)

## 7. Deal engine

### 7.1 Baseline methodology

The baseline is the part of this system most able to produce confident nonsense,
so it is specified rather than implied.

**Cohort — what "normal for this route" compares against.** V1 cohort:

```
origin · destination · cabin · trip_type · approximate trip length · departure season (month)
```

A $6,000 First-Class fare can be normal in February and extraordinary in July;
comparing across seasons would announce seasonal price moves as deals. Later
additions, deliberately not in V1: carrier, nonstop-vs-connection, weekday
pattern, booking lead time.

**Self-exclusion.** The current observation **must not participate in its own
baseline**:

```
baseline_observations =
      observations in the same cohort
    · observed_before evaluated_at
    · excluding the current observation_id
```

Otherwise a genuinely low price raises the very percentile it is being judged
against — the evaluation gets quieter exactly when it should get louder.

**Population separation.** `indicative` (discover) observations are stored but
live in a **separate series**. The bookable baseline uses only:

```
price_status ∈ { observed, verified, stale, unavailable }
```

Mixing them would make the baseline *"historical advertised price"* rather than
*"historical observed bookable fare"* — a subtle and systematic source of false
deals, since discover prices are cheaper by construction and not bookable.

**Provider price context is not a baseline.** An external provider may supply its
own price context — Google Flights returns `typical_price_range`, `price_level` and a
price history per search, as measured by the SerpApi lane
([`serpapi-provider-design.md`](serpapi-provider-design.md) §9). **That lane was adopted
on 2026-10-03**, so a provider in the portfolio does supply context now — under the rule
below, and never as a baseline. The baseline itself is still built from our own
observations alone; the rule below is what stops a future contributor from becoming one
by accident. That
context is a
**`ProviderPriceContext`**: it carries the *provider's* cohort and window, which
are not ours and are unmeasured. It is stored as a provider claim with provenance,
kept **separable** in the evaluation record — so a replay can be scored against our
baseline alone, provider context alone, and both — and it is **not rankable or
alertable** until its semantics are established. It never enters the cohort it is
being compared against, and it is never folded into the baseline silently.

**Comparability.** `taxes_included` and `price_comparability` are recorded per
observation, and the baseline excludes anything not comparable:

```
price_comparability = comparable | non_comparable | unknown
```

An all-in $5,900 must never be compared against a $6,400 base fare plus $600 of
taxes. `unknown` is excluded from the baseline and lowers `confidence` — missing
data reduces confidence, it does not license an assumption.

**Currency normalization (decision 4, §12).** Ranking across currencies is
meaningless without a declared policy:

```
display_currency = evaluation_currency = the user policy's currency (USD in V1)
original_amount · original_currency      # preserved verbatim, always
fx_rate · fx_source · fx_at              # ECB daily reference rate + snapshot time
fx_status                                # fresh | stale | not_required
```

**Source and freshness:** ECB reference rates, daily snapshot, **24-hour freshness
tolerance** (a V1 policy parameter, not a scientific truth). Daily is sufficient
because this system detects anomalies over days, weeks and months — a 30-minute FX
move is not what decides whether a First-Class fare is exceptional.

**Stale FX does not get used silently.** The rule is explicit, including the
same-currency exception that would otherwise punish a user for a feed outage:

```
if fare.currency == evaluation_currency:      rankable = true    # no conversion needed
elif fx_age <= 24h:                           rankable = true    # fx_status = fresh
else:                                         rankable = false   # fx_status = stale
                                              confidence = insufficient
                                              alert_eligible = false
```

The distinction that matters: **stored ≠ rankable ≠ alertable.**

```
observation = valid (stored, with its original currency)
evaluation  = deferred (rankable = false, reason = fx_stale)
alert       = prohibited
```

A JPY 850,000 fare with a stale rate is stored as `raw_price = 850000 JPY`; the
system does not pretend to know its USD equivalent well enough to rank it — and
ranks it again the moment the feed refreshes.

**Window and sample size.** 90 days is an **illustration, not a V1 default**:

```
max_window_days = 180      # provisional
min_samples     = 30       # provisional — below this: baseline_status = insufficient_history
```

Too few comparable observations for the season yields
`seasonal_insufficient` and no percentile alert (§5.5) — not an invented normal.

### 7.2 Two scores and a confidence — not one number

"How cheap is this?" and "how good is this?" are different questions. A $5,000
First-Class ticket that is historically exceptional but has two stops, twenty
hours of travel and a 5 a.m. departure is a price opportunity with poor travel
quality, and folding both into one number hides exactly the thing the user needs
to decide.

```
price_opportunity_score = w1·percentile_component      # where in the historical distribution
                        + w2·absolute_discount         # vs median, capped
                        + w3·verified_status           # verified > observed > indicative(never alerts)

travel_quality_score    = w4·route_quality             # nonstop, sensible duration, decent arrival window
                        + w5·itinerary_quality         # short layovers, few stops, no self-transfer
                        + w6·cabin_product_quality     # see below — neutral in V1

overall_opportunity     = combine(price_opportunity_score, travel_quality_score)   # ranking only
```

Reported, not merged:

```
Price opportunity: 96
Travel quality:    81
Confidence:        0.92
Overall:           91
```

**`cabin_product_quality` — three levels, and neutral when unknown** (decision 6,
§12). The hard part is not detecting "First Class"; it is not lying about *which*
First Class.

| Level | What it requires | Effect |
|---|---|---|
| 1 — cabin verification | The provider explicitly returns `cabin = FIRST` | **Required to qualify at all.** A result that says Business or Economy cannot enter the scanner, however cheap |
| 2 — product evidence | Retain `aircraft`, `flight_number`, `operating_carrier`, `fare_brand`, `booking_class`, `cabin_description`, seat/product description when present | Evidence, stored for later use — not a judgement |
| 3 — product quality | Enough explicit product information to classify `known_high` / `known_standard` / `known_regional` | Contributes to `travel_quality_score` **only** at this level |

**`unknown` is neutral in both directions.** It does not lower the score and it does
not raise it; it reduces `confidence` and nothing else. That means:

```
aircraft = 777   →  premium First        REJECTED (heuristic)
airline  = ANA   →  good First           REJECTED (heuristic)
product_quality = unknown                ACCEPTED — and the component stays neutral
```

A verified-cabin ANA First on a 777 with no suite information is a perfectly valid
candidate: `cabin = verified First`, `product_quality = unknown`. We simply do not
claim to know whether it is ANA's best First product.

**Confidence is evidence quality, never a multiplier on the scores.**

```
deal:      price_opportunity_score = 97, confidence = 0.52
           → potentially exceptional, insufficient evidence
deal:      price_opportunity_score = 91, confidence = 0.96
           → very good, highly trustworthy

alert eligibility = scores + confidence + policy
```

Confidence is derived from: scan `completeness` (study §2.1/§2.4), baseline
`sample_size` and `baseline_status`, `price_status`, and
`price_comparability`. Note the deliberate change from revision 1: completeness no
longer *penalises the score*. A cheap fare found by a partial scan is still a cheap
fare; what it lacks is *evidence*, and that is what `confidence` says. A
low-confidence candidate is logged for the operator rather than alerted.

### 7.3 Thresholds are provisional

**Thresholds are hypotheses. The precision target is a requirement.** These are
two different things and the document must not conflate them:

```
owner requirement (fixed):     alert precision >= 80% objective
candidate thresholds (uncalibrated):
    percentile <= 15
    confidence >= 0.70
status:                        provisional / uncalibrated  — hypotheses to test
```

So it is **not** correct to say "we target 80% precision, therefore use
`percentile <= 15` and `confidence >= 0.70`". The two numbers on the bottom line
are *candidates* to be tested against the calibration dataset (§9.6); the number on
the top line is what a selected configuration must achieve.

Every number in §7–§8 is an **initial configuration value, not a discovered
truth**: `percentile <= 15`, `confidence >= 0.7`, the score bands (`0–49` normal
… `95–100` exceptional), `max_window_days = 180`, `min_samples = 30`,
`verify_top_n = 5`, `fx freshness = 24h`, the ≥80% precision target. They require
calibration against accumulated observations before anyone treats them as
production defaults — which is exactly what R10 (deterministic replay) and the
shadow-mode rollout in §9.5–§9.6 exist to make possible. Until calibration has run,
they are policy defaults, and the system should say so when it explains an alert.

### 7.4 The LLM explains, it does not decide

Allowed: *"ANA First Class ORD–HND, 38% below its 90-day median for the season,
nonstop, verified 4 minutes ago."*
Not allowed: deciding that a fare is a deal, inventing a baseline, or contradicting
the scores. The explanation is generated from `explanation_inputs` only (§5.5).

## 8. Alerts

- **Threshold as data** (R9): `cabin == F AND percentile <= 15 AND
  completeness == complete AND price_status == verified AND confidence >= 0.7`.
  Provisional values (§7.3).
- **Dedupe on identity + state change, not on time:** one alert per fare-state
  transition — first appearance, new low, and (only if the policy cares) a
  re-rise after a fall.
- **Cooldown, because state transitions alone are not enough.** An unstable fare
  can oscillate (`5900 → 6200 → 5900 → …`) and generate an alert per cycle. So a
  repeat alert requires a *meaningful* change, not merely a change:

```
same offer_identity AND same overall_opportunity band
  → suppress unless the new low improves by >= X (provisional: 250 evaluation-currency units)
    or the percentile improves by >= Y points (provisional: 5)
```

- **Unverified candidates are never alerted.** If verification could not run
  inside the budget, the candidate is logged (§6.5) — silence is the correct
  output when the evidence is missing.
- **Never claim absence.** No alert, and no scan summary, may say "no flights on
  this route" from a result that was `not_loaded` or `filtered_out`; and a party
  containing children or infants is `partial`, never `no_results`, when a provider
  thins results client-side (§6.7,
  [`trvl-study-design.md`](trvl-study-design.md) §2.1). "We could not check" is a
  legitimate output; "there is nothing there" is not, without positive evidence.
- **The link is built locally, never fetched** (adopted from the fli study §5.1 P5,
  where booking links are deterministic tokens built offline from airports, dates
  and flight numbers). `[View]` must be constructed from the itinerary's own
  identity with no provider call at alert time, so the alerting path cannot fail
  because a source is down. **Deferred in practice:** there is no alert path yet to
  carry it, and the builder belongs with provider work (§11) — recorded so the
  requirement is not lost.
- **One message format** for v1:

```
🔥 ORD → NRT   ANA First
$5,940 round trip   ·   −42% vs 90-day seasonal median
Mar 12 → Mar 22   ·   nonstop   ·   verified 4 min ago
Price opportunity 96 · Travel quality 81 · Confidence 0.92
[View]
```

- **Quiet hours and rate caps** belong to the policy and are enforced here; the
  Deal Engine has no idea what time it is.

## 9. Alert quality: metrics, calibration, rollout

The scanner's success criterion is not "did it find cheap fares" but "did it find
genuinely exceptional fares **and avoid crying wolf**". That needs metrics before
it needs thresholds.

### 9.1 Four metrics, reported separately

| Metric | Definition | Answers |
|---|---|---|
| **Objective alert precision** | `TP / (TP + FP)` | "When you alert me, how often is it genuinely good?" |
| **Alert rate** | alerts per day | "How noisy is the system?" |
| **Coverage (recall)** | `TP / (TP + FN)` | "How many opportunities are we missing?" |
| **User utility** | optional feedback signal | "Did the user personally find it useful?" |

These are deliberately not merged. `objective precision = 91%` with
`user utility = 68%` is a perfectly possible, non-contradictory state: a fifth of
the genuinely excellent fares may have been to destinations the user could not
visit. That is not the algorithm being wrong.

### 9.2 The evaluation unit is an alert opportunity

Every candidate gets an outcome label, whether or not it became an alert:

```
alert_id · offer_identity · alerted_at
price_opportunity_score · confidence · price · historical_percentile
verification result · subsequent price movement
user_judgment? · objective_outcome
```

**True positive** — an alert that satisfies the predefined opportunity criteria
(verified, First Class, `completeness == complete`, percentile within the policy
bound) **and** remains valid after verification.

**False positive** — an emitted alert that failed those criteria. The two canonical
shapes:

```
initial price $5,700  →  verification $8,900      # the price did not hold
percentile 8%         →  corrected baseline 48%   # the baseline was wrong
```

**Two rules that stop the system learning the wrong lesson:**

> **An ignored alert is neither a true positive nor a false positive.**
> **A booked alert is not automatically a true positive.**

Clicks and bookings measure user behaviour, not alert correctness: an excellent
fare can arrive when a traveller simply cannot go, and a mediocre fare can be
booked by someone who urgently needed to travel. Objective precision is measured
against the criteria above; user behaviour is captured separately as utility.

Optional feedback is therefore recorded as a distinct, non-authoritative signal:

```
user_feedback: useful | not_useful | too_expensive | wrong_product
             | wrong_dates | unable_to_travel | booked | ignored
```

### 9.3 V1 targets

```
Primary     objective alert precision >= 80%     (eventually 85–90% if coverage stays useful)
Secondary   <= 2 high-priority alerts per day
Tertiary    maintain useful opportunity coverage
```

Why the rate cap matters as much as precision: 20 alerts a day is annoying even at
90% precision, while **one excellent alert every few days may be worth more than
all of them**. The objective is not "find everything" — it is "tell me when
something unusually good appears". A 95% precision target is deliberately *not* the
starting point: it would make the scanner so conservative that it rarely speaks.

### 9.4 Measuring alerts nobody acted on

Every alert gets a fixed observation window:

```
alerted_at  →  24h verification window  →  72h outcome window
```

(Window lengths are policy parameters, set by how long the observed fare stays
actionable.) The question asked at the end of the window is objective:

> Was the opportunity real and attractive, regardless of whether anyone clicked?

```
alert:   ORD → NRT  ANA First  $5,900  percentile 8%
verify:  $5,950          (the price held)
48h:     $6,800          (the opportunity closed)
```

That is a **successful alert** even though the user did nothing: the system
correctly identified a genuinely cheap fare.

### 9.5 Rollout: SHADOW → CALIBRATION → PRODUCTION

```
SHADOW        observe-only. No user alerts. Collect candidates, scores,
              verification results and subsequent price movement.
   ↓
CALIBRATION   limited alerts. Measure precision, alert rate, coverage.
   ↓
PRODUCTION    thresholds frozen into a version. Any change requires a new
              evaluator_version / policy_version (§5.5) and a replay (R10).
```

Shadow mode is the reason the versioning and replay requirements exist: it produces
the labelled history that makes calibration possible at all.

### 9.6 How the thresholds actually get calibrated

Do **not** tune `percentile <= 15` and `confidence >= 0.70` directly. First collect
the shadow dataset, then evaluate a grid:

```
percentile ∈ {5, 10, 15, 20}   ×   confidence ∈ {0.60, 0.70, 0.80}
        → precision / coverage curve per configuration
```

`verify_top_n = 5` (§6.5) is calibrated the same way and for the same reason:
measure alert yield per verification search at `N ∈ {3, 5, 10}` and keep the best,
rather than assuming a value.

**Selection rule — least restrictive, not most precise.** Choose the configuration
that achieves **≥80% precision while preserving as much opportunity coverage as
possible**. Maximising precision alone has a trivial degenerate solution:

```
alert almost never  →  precision approaches 100%  →  the scanner is useless
```

so precision is a constraint to satisfy, not an objective to maximise.

**Sufficiency is size *and* diversity, not a count.** "Calibration is complete" is
**not** defined by reaching a fixed number of alerts. 50 evaluated alerts spread
over two routes, one airline and one season are far weaker evidence than 50 spread
over 30 routes, several carriers, several departure months and different trip
lengths. The criterion is:

> The dataset must be large **and representative** enough to distinguish candidate
> threshold configurations with acceptable uncertainty.

50 evaluated alerts is therefore an **initial operational target** to aim at, not a
statistical guarantee under every distribution. The dataset must be reported with
its diversity dimensions (routes, carriers, seasons, trip lengths) whenever a
calibration result is claimed.

## 10. Deliberate non-goals

| Not building | Why |
|---|---|
| A dashboard or any GUI | First prove the system can answer *"did a genuinely exceptional First-Class fare appear?"* A single good message is the v1 user interface |
| LLM-driven deal decisions | Non-reproducible, unexplainable, cannot be back-tested (§7.4) |
| Any provider/scraper/anti-bot work | Not ours — engine is server-side (study's architectural principle) |
| Speculative alerts on unverified prices | The one failure that destroys trust fastest (§5.4, §8) |
| Guessing First-Class product quality from aircraft type or airline | The data does not support it (§7.2, decision 6) |
| A local cache of bookable offers | Offers expire in ~15 minutes; a cache of expired offers is a liability |
| Booking automation in v1 | Alerts first; booking is a human decision with real money attached until precision is proven |
| Learning from clicks and bookings as if they were correctness | They measure user circumstance, not alert quality (§9.2) |

## 11. Implementation priority

**Phase status**

| Phase | Content | Status |
|---|---|---|
| **P0** correctness/security | atomic config writes · canonical config path · credential ownership · unblock the Python test suite · baseline CI | **Landed 2026-10-03** |
| **P1A** contract correctness | `status` · `completeness` · `freshness` · verified-vs-indicative in the client contract | Status/completeness landed; `freshness` next |
| **P1B** structured MCP output | `outputSchema` · `structuredContent` · content separation | Not started |
| **P2.1** observation store | `FareObservation` · `ItineraryIdentity`/`OfferIdentity` · `search_run` · provenance · `price_status` · timestamps · currency/FX metadata — append-only and immutable, everything else derived | Not started |
| **P2.2** shadow collection | the real policy on the real allocated budget, with `alerts_enabled = false`: policy validation · volume estimate · budget reservation (§6.6) · fan-out · cadence · `fresh_enough` · verification | Not started |
| **P2.3** baseline / evaluation | cohort baseline · `baseline_status` · the two scores + confidence · FX normalization · replay harness (R10) | Not started |
| **P2.4** calibration dataset | candidate → initial evaluation → verification → subsequent observed outcome → objective label; **positive and negative examples both required** | Not started |
| **P2.5** threshold calibration | grid over percentile × confidence → select the least restrictive configuration meeting ≥80% precision (§9.6) | Not started |
| **P2.6** alerting | version and freeze the selected policy, **then** enable delivery | Not started |
| **P3** optimization | destination prioritization · adaptive scan frequency · budget optimization · anomaly detection | Not started |
| **P4** user experience | one message format already lands in P2.6 → then, only if needed, a UI · natural-language policy input · LLM explanation | Not started |

**Why P1A is split from P1B** (review #2): the contract fields are prerequisites
for storing anything honestly, while `outputSchema`/`structuredContent` are
presentational and can land in parallel.

**Why P2 begins with observation capture and shadow evaluation, not alerts**
(P2 sequencing decision, revision 4): the ≥80% precision target is an owner
requirement, but **the thresholds cannot be calibrated yet because no calibration
dataset exists** — and §9.5 shadow mode is what creates one. So P2.2 runs the real
policy on the real budget with `alerts_enabled = false`, recording for every
candidate: the observed price, whether it was indicative or verified, the
itinerary, the cabin, the currency, the baseline at the time, what verification
found, and whether the opportunity subsequently persisted, disappeared or changed.
That is the dataset P2.4 assembles and P2.5 calibrates against.

> **P2 alerting gate.** Alert delivery **MUST** remain disabled until the
> observation/shadow pipeline has produced the calibration dataset and the selected
> threshold policy has been versioned (`policy_version` / `evaluator_version` /
> `baseline_version`, §5.5). No exceptions, no "temporary" enabling to see if it
> works: an uncalibrated alert is an unmeasured claim, and the first impression of
> a fare scanner is the only one it gets.

The sequence makes the system become, in order: a reliable **travel-price
observation and evidence engine** → a calibrated **deal detector** → an
**alerting system**. Each stage is useful on its own, and nothing in a later stage
has to be unwound to fix an earlier one.

## 12. Decisions (resolved)

The seven questions this design left open were decided by the owner on 2026-10-03.
They are **normative V1 rules**; where a number is a calibration parameter rather
than a rule, it says so.

**1. Where it lives — a separate repository/service.**
`first-class-fare-scanner/` with `policy/`, `planner/`, `storage/`, `evaluator/`,
`scheduler/`, `alerts/`, `letsfg_client/`, depending on LetsFG as a library.
Decision rule:

> **LetsFG owns flight-search access. The First-Class Fare Scanner owns search
> policy, scheduling, persistence, evaluation and alerting.**

Putting those into the client would turn a search client into a search client plus
a database, a scheduler, historical analytics and alert delivery — violating the
boundary the rest of this design rests on. **P0 architectural decision.**

**2. Storage — SQLite with WAL.**
Tables: `fare_observation` (immutable raw observations), `fare_state`, `baseline`,
`fare_evaluation`, `search_run`, `alert_event` (alert lifecycle / dedupe state).

> **`fare_observation` is the source of truth. Everything else is recomputable.**

`fare_state` is never used as the historical source of truth — that is what makes
R10 replay possible. Postgres is deferred, and the trigger is *not* size:

```
switch when: multiple workers writing concurrently at scale
           · multiple users/accounts
           · remote or shared deployment
           · high-availability requirement
           · external services need direct database access
```

**3. Account budget — a hard reservation, never a shared pool.**
For a 100-search/day account: `interactive_reserved = 30`, `scanner_max = 70`.
The scanner may not consume the interactive reserve **even if it left its own
allocation unused earlier that day**, so the safety property is absolute: the
scanner can never starve interactive use. Discovery, search and verification all
consume the scanner allocation. Exhaustion is terminal for the run
(`budget_exhausted`, §6.3) with no borrowing. Rates are configurable (§6.6).

**4. FX — ECB daily reference rates, 24h freshness, same-currency exemption.**
Original amount and currency are always preserved; conversion carries
`fx_rate`/`fx_source`/`fx_at`/`fx_status`. A same-currency fare needs no conversion
and stays rankable even during a feed outage. Cross-currency fares with stale FX
are **stored but not rankable and not alert-eligible** until fresh rates arrive:
`observation = valid · evaluation = deferred · alert = prohibited`. 24h is a V1
policy parameter (§7.1).

**5. Verification — logically per candidate, operationally batched.**
V1 verifies the **top 5 eligible candidates per run**, within the remaining scanner
budget, ranked by `price_opportunity_score`, `confidence`, `completeness`, expected
alert value, recency. `N = 5` is a calibration parameter, not a permanent constant;
the long-run rule is
`N = min(configured_max, candidates_above_threshold, remaining_verification_budget)`
(§6.5).

**6. First-Class product verification — explicit cabin, evidence-based quality,
neutral when unknown.**
A candidate must have provider-level `cabin = FIRST` to qualify at all. Product
quality is classified only from explicit itinerary/product evidence
(`known_high` / `known_standard` / `known_regional`); otherwise
`product_quality = unknown`, which is **neutral** — it neither helps nor hurts the
score, and reduces confidence. Inferring a First product from airline or aircraft
is rejected (§7.2).

**7. Alert precision — ≥80% objective, measured without user behaviour.**
Initial target **≥80% objective alert precision** (eventually 85–90% if coverage
remains useful), secondary cap **≤2 high-priority alerts/day**, tertiary
"maintain useful coverage". Precision is measured against the predefined objective
criteria (§9.2), **not** clicks, bookings or any user action. User feedback is a
separate utility signal.

**This is a production target, not evidence that the current thresholds are
calibrated.** The calibration dataset does not initially exist; §9.5 shadow mode is
what produces it. Therefore **P2 begins with observation storage and shadow
collection rather than alerting** (§11): shadow observations, verification results
and subsequent outcomes are retained for calibration, candidate percentile/
confidence thresholds are evaluated against that dataset, and the selected
configuration must achieve ≥80% precision **while preserving useful opportunity
coverage** (the least restrictive qualifying configuration — not the most precise
one). Calibration is complete only when the dataset is large **and representative**
enough to separate candidate configurations with acceptable uncertainty; 50
evaluated alerts is an operational target, not a statistical guarantee (§9.6).
Once that data exists, the selected thresholds are versioned and frozen
(`policy_version` / `evaluator_version` / `baseline_version`, §5.5) **before**
production alerting is enabled.

**One further rule adopted with decision 7:**

> **An ignored alert is neither a true positive nor a false positive.**

**The four metrics are reported separately:** objective precision · alert rate ·
coverage · user utility (§9.1).

---

**Open blocker (not a decision, and not hidden).** One question remains genuinely
unanswered and it gates P2.2's scheduler: the **rate-limit failure contract**.

```
rate_limit_failure_behavior = UNKNOWN
```

Published limits describe *capacity*, not *failure*. Until a controlled probe
establishes what crossing the limit does — status returned, `Retry-After`, **whether
a rejected request still consumes quota**, the limit's scope, whether concurrency
counts separately, behaviour after repeated violations, per-endpoint consistency,
and whether polling `/api/results/{id}` draws on the search quota — **no scheduler
logic may assume a behaviour**. The full question list and probe recipe are in the
study (§6, Q8); the scheduler's recovery policy (§6.3 skip reasons, §6.6 budget)
depends on the answer, so P2.2 lands its planner and observation paths first and its
recovery behaviour last.

> **Owner waiver, 2026-10-04.** The owner elected to proceed without measuring it.
> The contract therefore remains `UNKNOWN`, and P2.2's recovery behaviour is written
> as an assumption rather than from measurement. Recorded here so the waiver stays
> visible: if a limiter interaction is observed in practice it belongs in this
> paragraph, and the recovery policy should be revised against what was seen.

The study's Q5/Q6/Q9/Q10/Q11 decisions (2026-10-03) are the source of several rules
in this document: the planner being first-class (§6.1), `discover` as
candidate-generation only (§6.1, §5.4), client-constructed identity (§5.2),
offer-validity versus observation freshness (§7.1), and the verified-before-alert
gate with `verified` ≠ guaranteed bookable (§5.4, §6.5, §8).

**8. Emptiness is typed, a blocked sweep stops early, and the plan states its full
cost (adopted 2026-10-03, from [`fli-study-design.md`](fli-study-design.md)).**
Four parts, all of them the same discipline the rest of this design already applies
to prices:

- **`empty_reason` is normative** (§6.7, taxonomy in
  [`trvl-study-design.md`](trvl-study-design.md) §2.1): `provider_empty` |
  `not_loaded` | `filtered_out`. `no_results` may only be claimed for the first;
  `not_loaded` is a failure, `filtered_out` is our own narrowing. A party with
  children or infants is `partial`, never `no_results`, when a provider thins
  results client-side.
- **The cost estimate covers the fan-out, not just searches** (§6.1):
  `estimated_units`, `estimated_billable_searches`, `estimated_http_requests`
  (retry multiplier included), `estimated_wall_time`, `estimated_peak_memory`. A
  plan that cannot state all five is refused, not run optimistically.
- **A breaker with our own arithmetic** (§6.7):
  `breaker_bound = (grace + worker_count) × attempts_per_unit`; only
  `provider_empty` counts against it; it disarms on the first successful load; and
  a separate condition raises when nothing priced **and** at least half the
  attempted units never loaded. `grace`, `attempts_per_unit` and the half-threshold
  are ours to calibrate.
- **Unattempted ≠ failed ≠ billable** (§6.7): abandoned units are recorded as
  `unattempted`, never charged as failed work or as searches.
- **Alert links are built locally, never fetched** (§8): `[View]` comes from the
  itinerary's own identity with no provider call at alert time. Deferred in
  practice — there is no alert path yet to carry it, and the builder belongs with
  provider work.

## Scope and working agreement

This document **plans**; it does not authorise changes. Working-agreement rule 4
("never make code changes unless they are defects") applies in full.

Where the scanner lives is now decided (§12, decision 1): a **separate
repository/service**, with LetsFG as a library. That decision keeps rule 4 intact for *this* repository —
the scanner is not built here, so nothing in this design may be landed as
incidental work inside the client. The only permitted work in this repository
remains the client contract the scanner depends on (P1A/P1B) and defect fixes.

Starting the scanner itself still needs an explicit go-ahead from the owner, and it
begins in the other repository, at P2.1 (§11) with the observation store and shadow
collection — not with alert delivery, which the P2 alerting gate blocks.

**Traceability.**

| Study artefact | Used here |
|---|---|
| §2.1 status / completeness / `freshness` / `observed_at` | §5.1, §5.4, §6.3, §7.2 |
| §2.4 completeness gating | §7.2 (confidence, not score) |
| §2.4 verified-vs-unverified pricing | §5.4 (`price_status`), §6.5, §8 |
| §2.5 telemetry vs domain data | §5.1 (observations are domain data) |
| D12 verified-vs-indicative in the contract, policy elsewhere | §5.4, §7.1, §8 |
| `discover` / `multi-search` billing semantics | §6.1 (two-phase scan, volume estimate) |

**Review provenance.** Reviewed three times by the owner on 2026-10-03.

- *Review #1* — architecture: accepted the six-layer split (now seven with the
  Policy Validator), the deterministic-score rule, the LLM-as-explanation rule,
  the no-dashboard stance, and the R1–R9 requirement set.
- *Review #2* — contract precision, all applied: single-sourced `price_status`
  (§5.4); `ItineraryIdentity`/`OfferIdentity` split with explicit
  `identity_granularity` (§5.2); baseline self-exclusion, cohort, population
  separation, comparability and currency (§7.1); `price_opportunity_score` vs
  `travel_quality_score` vs `confidence` (§7.2); machine-readable
  evaluator/baseline/policy/identity versions (§5.5); `baseline_status` (§5.5);
  alert cooldown (§8); deterministic replay (R10); Policy Validator, volume
  estimate and `fresh_enough` (§3, §6); provisional labelling of every threshold
  (§7.3); verification made a budget-gated alert gate rather than a search phase
  (§6.5); SQLite/WAL and separate-repository recommendations (§12).
- *Review #3* — the open questions decided as normative V1 rules (§12): separate
  scanner repository (with the ownership decision rule), SQLite + WAL with
  `fare_observation` as the only source of truth (and explicit Postgres migration
  triggers), the interactive-reserve budget reservation (§6.6), the ECB/24h FX rule
  with the same-currency exemption (§7.1), batched per-candidate verification with
  `N = 5` and a dynamic rule (§6.5), the three-level First-Class product model with
  neutral `unknown` (§7.2), and the ≥80% objective alert-precision target with the
  four-metric evaluation framework, observation window and shadow-mode rollout
  (§9). One further rule adopted: **an ignored alert is neither a true positive nor
  a false positive** (§9.2).
- *Review #4 — P2 sequencing decision*, applied: P2 re-ordered to
  observation store → shadow collection → baseline/evaluation → calibration
  dataset → threshold calibration → alerting (§11); the **P2 alerting gate** added
  as a MUST; the precision target distinguished from the uncalibrated thresholds
  (§7.3); calibration sufficiency defined as size **and** representativeness rather
  than a fixed count, with the least-restrictive-qualifying selection rule (§9.6);
  and decision 7 extended to state that the target is a production requirement, not
  evidence that the thresholds are calibrated (§12).
