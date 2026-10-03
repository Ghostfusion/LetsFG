# First-Class Fare Scanner — System Design

**Status:** design only. Nothing in this document is implemented, and none of it
may be implemented as incidental work — see *Scope and working agreement* at the
end.
**Date:** 2026-10-03 (revision 2, after owner review #2)
**Purpose:** design a continuously running system that watches a pool of
destinations over flexible dates in First Class, detects genuinely unusual fares,
and alerts on them — instead of merely returning the cheapest fare available at
the moment it was asked.
**Depends on:** [`trvl-study-design.md`](trvl-study-design.md) (MCP contracts,
status/completeness/freshness, client reliability) — the *client* layer this
system is built on top of.

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
| Rate limits | PFS card: 10/10 min, 30/hour, 100/day. Developer API: 60 req/min, 200 free searches after each booking, then $0.01 each | Cadence is capped by the account; the limiter's *failure direction* is still unknown (study P1.3) |
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
| **R1** | **First Class is a first-class dimension.** Not `cabin = any` with client-side filtering. | Every scan carries the cabin explicitly (`F`); no stored observation is cabin-ambiguous; a scan that returned mixed cabins is a bug |
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
budget          # the plan exceeded its allowance
rate_limit      # the account's limiter refused
policy_pruned   # the user's own filters excluded it
dedupe          # identical to a unit already planned
fresh_enough    # last successful search is newer than the cadence requires
```

`fresh_enough` is deliberately not "budget exhausted": *"we searched this twenty
minutes ago and the cadence is three hours"* is a scheduling decision, and
lumping it with budget starvation is how a planner's logs stop being usable.

### 6.4 What the Planner must never do

Silently drop a unit. "We did not look" and "we looked and found nothing" are
exactly the two things the study's `status`/`completeness` contract exists to keep
apart, and the same distinction applies here.

### 6.5 Verification is budget-gated

Verification is a second search, so it is ranked and capped, never applied to
every result:

```
candidates ranked by:  overall_opportunity · confidence · expected alert value
verify top N           where N is constrained by the remaining budget
```

"100 candidates → 100 verification searches" is forbidden without an explicit
budget gate. If the budget cannot verify a candidate, the candidate is logged as
unverified — not alerted on a stale price (§8).

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

**Comparability.** `taxes_included` and `price_comparability` are recorded per
observation, and the baseline excludes anything not comparable:

```
price_comparability = comparable | non_comparable | unknown
```

An all-in $5,900 must never be compared against a $6,400 base fare plus $600 of
taxes. `unknown` is excluded from the baseline and lowers `confidence` — missing
data reduces confidence, it does not license an assumption.

**Currency normalization.** Ranking across currencies is meaningless without a
declared policy:

```
display_currency = evaluation_currency = the user policy's currency
original_amount · original_currency      # preserved verbatim
fx_rate · fx_source · fx_at              # how the conversion was made, and when
```

FX rates are timestamped and the source is recorded, because a stale rate can
turn a 2% fare difference into a fake 5% deal.

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

**`cabin_product_quality` is neutral in V1.** "First Class" does not tell us
whether the product is an international suite or a domestic recliner, and nothing
in our API distinguishes them (§2, open question 5). So the component stays
neutral (`unknown`) and **does not lower the score** — it lowers nothing at all,
it simply does not contribute. Guessing a First product from aircraft type is
explicitly rejected.

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

Every number in §7–§8 is an **initial configuration value, not a discovered
truth**: `percentile <= 15`, `confidence >= 0.7`, the score bands (`0–49` normal
… `95–100` exceptional), `max_window_days = 180`, `min_samples = 30`. They require
calibration against accumulated observations before anyone treats them as
production defaults — which is precisely what R10 (deterministic replay) exists to
make possible.

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

## 9. Deliberate non-goals

| Not building | Why |
|---|---|
| A dashboard or any GUI | First prove the system can answer *"did a genuinely exceptional First-Class fare appear?"* A single good message is the v1 user interface |
| LLM-driven deal decisions | Non-reproducible, unexplainable, cannot be back-tested (§7.4) |
| Any provider/scraper/anti-bot work | Not ours — engine is server-side (study's architectural principle) |
| Speculative alerts on unverified prices | The one failure that destroys trust fastest (§5.4, §8) |
| Guessing First-Class product quality from aircraft type | The data does not support it (§7.2) |
| A local cache of bookable offers | Offers expire in ~15 minutes; a cache of expired offers is a liability |
| Booking automation in v1 | Alerts first; booking is a human decision with real money attached until precision is proven |

## 10. Implementation priority

| Phase | Content | Status |
|---|---|---|
| **P0** correctness/security | atomic config writes · canonical config path · credential ownership · unblock the Python test suite · baseline CI | **Landed 2026-10-03** |
| **P1A** contract correctness | `status` · `completeness` · `freshness` · verified-vs-indicative in the client contract | Status/completeness landed; `freshness` next |
| **P1B** structured MCP output | `outputSchema` · `structuredContent` · content separation | Not started |
| **P2A** scanner data model | `FareObservation` · `ItineraryIdentity`/`OfferIdentity` · persistence (append-only) · `FareState` projection | Not started |
| **P2B** planner | policy validation · volume estimate · budget · fan-out · cadence · `fresh_enough` | Not started |
| **P3** intelligence | cohort baseline · `baseline_status` · two scores + confidence · calibrated thresholds · replay harness (R10) | Not started |
| **P4** user experience | alerts (one format) → then, only if needed, a UI · natural-language policy input · LLM explanation | Not started |
| **P5** optimization | destination prioritization · adaptive scan frequency · budget optimization · anomaly detection | Not started |

**Why P1A is split from P1B, and P2A from P2B** (review #2): the contract fields
are prerequisites for storing anything honestly, while `outputSchema`/
`structuredContent` are presentational and can land in parallel. Likewise the data
model must be validated against real observations before the scheduler is written
on top of it — a scheduler built first would encode assumptions the data model has
not yet earned.

## 11. Open questions

1. **Where does this system live?** *(Recommendation: a separate scanner
   repository/service — `first-class-fare-scanner/` with `policy`, `planner`,
   `storage`, `evaluator`, `scheduler`, `alerts`, depending on LetsFG as a
   library.)* This repository is a *client* repository; giving it a scheduler,
   database and alert delivery would erode the separation the rest of this design
   depends on. Must be decided before P2.
2. **Storage engine?** *(Recommendation: SQLite with WAL, append-only observation
   table, derived state/baseline tables.)* One user, tens of thousands of
   observations, a single scheduler, no concurrent writers — a distributed database
   buys nothing here.
3. **How is the account budget shared** between the scanner and interactive use on
   the same card? The policy must allocate, not compete.
4. **FX policy** (§7.1): which source, what staleness tolerance, and what happens
   when `fx_at` is too old to rank on?
5. **Is verification batched or per-candidate** (§6.5), and what is the default
   `N` before calibration?
6. **How is a "First Class product" verified** (§7.2)? Until it can be, the
   component stays neutral.
7. **What is the alert-precision target** that calibrates §7.3's provisional
   thresholds — and how is precision measured on alerts the user did not act on?

## Scope and working agreement

This document **plans**; it does not authorise changes. Working-agreement rule 4
("never make code changes unless they are defects") applies in full: the scanner
is new scope and needs an explicit owner decision — starting with open question 1
— before any code exists. Until then the only permitted work is the client
contract it depends on (P1A/P1B) and defect fixes.

**Traceability.**

| Study artefact | Used here |
|---|---|
| §2.1 status / completeness / `freshness` / `observed_at` | §5.1, §5.4, §6.3, §7.2 |
| §2.4 completeness gating | §7.2 (confidence, not score) |
| §2.4 verified-vs-unverified pricing | §5.4 (`price_status`), §6.5, §8 |
| §2.5 telemetry vs domain data | §5.1 (observations are domain data) |
| D12 verified-vs-indicative in the contract, policy elsewhere | §5.4, §7.1, §8 |
| `discover` / `multi-search` billing semantics | §6.1 (two-phase scan, volume estimate) |

**Review provenance.** Reviewed twice by the owner on 2026-10-03.

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
  (§6.5); SQLite/WAL and separate-repository recommendations (§11).
