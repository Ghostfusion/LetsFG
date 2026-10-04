# Study — `affromero/flight-finder` (design)

What a peer flight-pricing project does well, what it does badly, and what is
worth taking. Companion to `docs/flight-finder-study-implementation.md`, which
turns the `[ADOPT]` items below into ordered work with acceptance criteria.

> **Provenance.** Probed read-only from `github.com/affromero/flight-finder` at
> commit `aa37742` (2026-09-28), shallow clone. **MIT licensed**, so unlike the
> `trvl` study there is no licence obstacle to adopting code — the constraint on
> us is our own working agreement (rule 4: no changes that are not defect fixes),
> not copyright. No code has been copied into this repository; this document
> records ideas, interfaces and policies only.
>
> Every claim below cites a path in the probed tree. Line numbers are from the
> probe checkout. Findings marked **[VERIFIED]** were read directly; the few
> marked **[INFERENCE]** say so.

## 0. What the project is

A **self-hosted price tracker**, not an agent toolkit: Next.js 16 web app + API
back end, Prisma 7 over PostgreSQL 16, Redis for caching/rate limiting,
Playwright for scraping, an Ink/React CLI, and a Tauri desktop launcher
(`AGENTS.md` "Stack"). It tracks flights, hotels and car rentals, compares
prices across VPN exit countries, and notifies on price drops. It is
single-tenant-ish, browser-scraping first, and human-facing.

That difference matters for every verdict below: we are an **agent-first,
server-side search and booking** product with Python/JS SDKs, an MCP server and
a QML panel. Where their machinery assumes a web app with a database, we take
the *policy* and leave the *implementation*.

Scale for calibration: 1092 tracked files, 232 test files, 8 workflows,
907-line hand-written `API.md`, 6 version points kept in lockstep
(`AGENTS.md` "Monorepo layout"; verified all at `0.15.0`).

## 0.1 Scope — the integration surface vs incidental repository findings

This study answers one question: **what should LetsFG take from
`affromero/flight-finder` to improve its flight search, price evidence and
booking architecture?** The probe found more than that, and the difference
matters, because a finding that is not on the integration surface is not an
argument for anything outside this study.

**Integration-relevant** — a pattern that changes how LetsFG *searches, prices,
verifies, books or describes results*, or how an agent consumes them:

| Pattern | Changes |
|---|---|
| P7 response envelope, measured | API contract |
| P8 named limiter failure direction | reliability |
| P9 single credential/lane resolver | provider abstraction, correctness |
| P10 positive evidence for "no results" | search semantics |
| P11 observation → estimate → verified fact | price representation, evidence model |
| P14 publication integrity | distribution, release correctness |
| P17 operational semantics for agents | API contract, agent behaviour |

**Repository/process-derived** — patterns that improve CI, release engineering,
security, dependency management, documentation or contributor workflow. They
were *discovered* during a flight-pricing study, but they are not flights:

| Pattern | Changes |
|---|---|
| P1 dead-code ledger | repository hygiene |
| P2 workflow hardening | CI |
| P3 secret scanning | security |
| P4 Dependabot | dependency management |
| P5 PR template | contributor workflow |
| P6 issue templates | triage |
| P12 test execution classes | test architecture |
| P13 process verification | CI |
| P15, P16, P18, P19 | architecture hooks, changelog, Makefile, screenshots |

**The second category is not part of the flight-finder integration surface.**
Those are independent repository improvements that happened to be found while
reading a flight-pricing project. They stand on their own evidence — a missing
timeout is a defect whatever motivated the reading — but nobody should cite this
study as the reason LetsFG has a PR template. The register in §3 carries a
**Type** column so the two never blur again.

### The integration boundary, capability by capability

| Flight-finder capability | LetsFG decision |
|---|---|
| Next.js UI (`apps/web`) | **Reject** — we have no human-facing app to serve |
| Prisma/Postgres schema | **Reject** — our storage is the user's `config.json`, and server-side state is theirs |
| Redis caching/rate limiting | **Reject** — our limiter is server-side; only its *failure direction* is ours to document (P8) |
| Playwright scraping | **Reject** — connectors are server-side by design (`f91be5b`); revisiting is an owner decision |
| LLM extraction pipeline | **Reject** — no extraction happens client-side |
| Price evidence model (observation vs attempt vs estimate) | **Adopt-adapt** — the one idea with direct architectural value (P11) |
| Search-attempt semantics (`success`/`partial`/`failed` + `snapshotsCount`) | **Adopt** — as the outcome axis of our envelope (P10) |
| Search-result validity semantics (positive-evidence capability flag) | **Adopt** — `no_results` needs proof *and* complete coverage (P10) |
| Credential resolver with written precedence | **Adopt** — one lane-resolution *contract*, one implementation per SDK (P9) |
| Response-envelope adoption measurement | **Adopt-adapt** — we already assert it structurally; the lesson is *every advertised surface is classified* (P7) |
| Agent-facing operational semantics in the API reference | **Adopt** — with assertions (P17) |
| Install/upgrade/publication verification | **Adopt-adapt** — fresh installs, packaged entry points, build identity (P14) |
| CI hardening, Dependabot, templates, changelog, screenshot archive, Makefile | **Repository work** — independent of this integration; see the table above |

### This study authorizes nothing

**Findings marked as existing LetsFG defects may be fixed under working-agreement
rule 3** (fix defects on the spot). Everything else — a new integration surface, a
new credential resolver, a new documentation contract — requires the owner
decision recorded in `flight-finder-study-implementation.md`, per rule 4. This
document records what a peer project does well; it does not queue work.

## 1. Pattern catalogue

Each entry: pattern → evidence → why it exists → verdict for us. Verdicts are
`[ADOPT]`, `[ADOPT-ADAPTED]`, `[DEFER]`, `[REJECT]`.

---

### P1. Dead-code gate backed by a reviewed-exception ledger `[ADOPT]`

**Evidence.**
- `.github/workflows/dead-code.yml` — runs on `pull_request`, `push: main`,
  weekly `cron: '17 9 * * 2'`, and `workflow_dispatch`; `permissions: contents:
  read`; `concurrency: group dead-code-${{ github.ref }}, cancel-in-progress:
  true`; SHA-pinned `actions/checkout` **with `persist-credentials: false`**;
  then `affromero/repo-maintenance@bf4eb3b…  # v1.0.2` with
  `rust-paths: 'apps/desktop/src-tauri'` and
  `prepare: 'npx prisma generate --schema=apps/web/prisma/schema.prisma'`.
- The action (`action.yml` at the pinned SHA) installs Node 22 + `uv`, runs
  `npm ci` when a lockfile exists, runs the `prepare` command, optionally
  `cargo machete` at a version pinned in `tool-versions.json`, then
  `python3 "$GITHUB_ACTION_PATH/scripts/audit.py" --root "$GITHUB_WORKSPACE"`,
  and always uploads `.maintenance-reports/` as an artifact.
- `knip.json` — per-workspace `entry` lists, with `!` suffixes to *keep* runtime
  entry points that are not statically reachable.
- `.maintenance-exceptions.json` — a versioned array of findings, each with an
  **exact identity** (`tool`, `mode`, `file`, `kind`, `name`) **and a review
  reason**, e.g. `ffmpeg` for `scripts/capture-demo-light.ts`; `codex` for
  `apps/web/src/lib/scraper/cli-models.ts`.
- `README.md` §"Dead-code checks" documents the rule: *"to reject new findings,
  stale reviewed exceptions, and scanner failures… Any retained finding in
  `.maintenance-exceptions.json` needs an exact identity and a review reason."*

**Why it exists.** Dead code and unused dependencies accumulate silently; a
gate that only rejects *new* findings is adoptable, and an exceptions file that
must carry a reason converts silent debt into reviewed debt — and rejects the
exception once it goes stale.

**Verdict.** `[ADOPT]` the **policy**; `[DEFER]` the **implementation** (D19).
Those are separable, and this study should not need a third-party action to
decide that reviewed debt beats silent debt. Adopt the ledger now; adopt the
action only once Python coverage in `scripts/audit.py` is verified, along with
its maintenance model and false-positive behaviour.

**The list this pattern was written from has since been cleaned — which is the
interesting part.** Measured 2026-10-03: `system_info.py`, the `models.py` that
`models/` shadowed, and the connector-era sweep script are **deleted**; Playwright
is **gone** from all three container files; and `config.py` — the fourth cited
example — is now **load-bearing** (the single atomic config store from trvl
P3.1/P3.2). So the ledger's motivation is no longer "record what we found" — that
work is done — but "stop it accumulating again", which is what a ledger with
reviewed exceptions is for. Two cautions stand: the shared action does JS/TS
reachability (`knip`) and Rust (`cargo-machete`), **Python support is unverified**,
and it is a single-maintainer composite — pin it by SHA as they do, or vendor.

**What the ledger governs, and what it does not.** It applies to *unresolved
findings* — dead code the gate reports that nobody has decided about yet. It does
**not** require retaining dead code that qualifies as an existing defect under
working-agreement rule 3: when the dead weight is observable and removable now
(which is what happened to the four examples above), it is fixed, not parked. The
ledger is for the residue the reviewer has to judge, and the reason it must carry
one is so the judgement is recorded rather than repeated.

---

### P2. Workflow hardening: timeouts, concurrency, least privilege, no persisted credentials `[ADOPT]`

**Evidence.**
- Per-job `timeout-minutes` on every job in `ci.yml` (20/15/15/10/30) and on the
  test matrices in `car-tests.yml` / `hotel-tests.yml`.
- Top-level `concurrency` only in `dead-code.yml:11-13`
  (`cancel-in-progress: true`); **publication** jobs instead take a stable group
  with `cancel-in-progress: false` — `docker.yml` `group:
  flight-finder-image-publication`, `desktop-release.yml` `group:
  desktop-release-publication`. Same idea, opposite setting, chosen per job kind.
- `permissions:` present in 5 of 8 workflows (`car-tests.yml:8`,
  `dead-code.yml:9`, `desktop-ci.yml:9`, `gitleaks.yml:8` — all
  `contents: read`), scoped per job in `docker.yml` (the only `packages: write`
  lives on the build job) and `desktop-release.yml` (`contents: write` only on
  publish).
- SHA pinning: only `dead-code.yml:19,22` pin by commit SHA; every other
  `uses:` is a mutable tag (`ci.yml:14` `actions/checkout@v7.0.1`, etc.).

**Why it exists.** Unbounded jobs hang and burn minutes; superseded PR runs
should be cancelled; a runaway publish must not race a second publish; a
compromised action with a token that has `write` scope can push code.

**Verdict.** `[ADOPT]` — and note the self-inconsistency: the repo that *wrote*
the pattern applies it unevenly (no `permissions:` in `ci.yml`/`hotel-tests.yml`,
no timeouts on the publish jobs). Our own state is the mirror image: **all four
of our workflows SHA-pin every `uses:` with a trailing `# vX.Y.Z` comment** and
`.github/dependabot.yml` explains why (`Dependabot is what keeps them current`),
`test.yml` already has `concurrency` + `permissions`, but we have **zero
`timeout-minutes`**, `sdk-tests.yml` has **no `permissions:`**, and
`persist-credentials: false` appears only in `plugin-scanner.yml`. Those are
plain CI defects, fixable today, and the fix should ship with a test that keeps
it true (P13).

---

### P3. Secret scanning with a full-history PR scan and a *reasoned* ignore file `[ADOPT]`

**Evidence.** `.github/workflows/gitleaks.yml` — `gitleaks/gitleaks-action@v3`
on every push/PR, plus a second step for PRs that validates the base/head SHAs
against `^[0-9a-fA-F]{40}$`, fetches them, and runs
`gitleaks detect --redact --log-opts="$BASE..$HEAD"`. `.gitleaksignore` is a
single entry with an explanatory comment:
`0b3ef53e…:API.md:generic-api-key:429` — *"Synthetic UUID in the historical API
idempotency example, not a credential."*

**Why it exists.** History scans catch what a working-tree diff misses. The
ignore format (`commit:path:rule:line`) is precise — and brittle: it is keyed to
a line number, so it churns whenever that line moves.

**Verdict.** `[ADOPT]`, with a twist that is specific to us. Our documentation is
saturated with credential-*shaped* examples (`eyJ…` bearer tokens, `letsfg_…`
keys) across `AGENTS.md`, `README.md`, `docs/**`. Adopting a scanner therefore
requires either placeholder hygiene (make examples obviously non-secret) or a
line-pinned allowlist that will churn. It also closes a gap in our own working
agreement rule 1: the clause it carried about a canonical adapter-credential list
(`scripts/strip-adapter-env.bash`, a file that never existed here) was removed by
owner decision on 2026-10-03, so rule 1 now states only what can be observed.

**Do this in order — the hierarchy is the decision (D4).**

1. **Make the examples unmistakably fake.** A documented example credential must
   be recognisable as one at a glance.
2. **Use canonical placeholders** rather than realistic-looking strings, so the
   scanner has nothing to match in the first place.
3. **Scan repository history**, not just the working tree — the ignore file's own
   entry is evidence that history is where a stale secret hides.
4. **Only then** add narrow, dated exceptions for material that cannot be
   sanitised, with a reason in the file.

Writing the allowlist first is the failure mode: it teaches the scanner to
tolerate exactly the material that steps 1–3 exist to remove, and it converts a
finding into a footnote.

---

### P4. Dependabot as policy, not just automation `[ADOPT]`

**Evidence.** `.github/dependabot.yml` — five ecosystems (`npm` at `/`, `npm`
at `/apps/desktop`, `cargo`, `docker`, `github-actions`), each `weekly` with
`cooldown: default-days: 7`, `open-pull-requests-limit: 10`, and — for npm —
`groups: {npm: {patterns: ['*']}}` plus an explicit
`ignore: typescript / version-update:semver-major`.

**Why it exists.** Grouping prevents 30 PRs a week; the cooldown stops
same-hour adoption of a fresh (possibly compromised) release; the explicit
major-version ignore encodes a known breaking surface instead of relying on a
reviewer to remember.

**Verdict.** `[ADOPT]` — grouping + cooldown are the transferable parts. Our
`.github/dependabot.yml` currently watches **only `github-actions`**; our two
npm projects (`sdk/js`, `sdk/mcp`) and the Python package are unmanaged.

---

### P5. A PR template that makes evidence and consent explicit `[ADOPT]`

**Evidence.** `.github/PULL_REQUEST_TEMPLATE.md` requires a tracking issue
(*"Required for every PR, including documentation and dependency updates… Use
`Closes #123` only if this PR fully resolves it"*), a type checkbox set, and —
the important part — a **Verification** section: *"Paste exact commands and
results. List failed or skipped checks and explain why any check was not run.
For bug fixes, identify the regression test and whether it fails before the fix
and passes afterward."* It also has **Scope and remaining work** (*"Keep
incomplete PRs in draft"*), **Compatibility and deployment**, and a
**Review assistance** block offering three options with the closing rule:
*"If the choice is blank or conflicting, maintainers will ask before preparing
changes. This choice does not approve merging the PR."*

`AGENTS.md` reinforces the last point: *"An inspection-only request does not
authorize creating an issue or PR."*

**Why it exists.** It converts "be careful" into checkboxes that fail visibly,
and it makes AI-assisted follow-up an explicit, recorded consent rather than an
inference.

**Verdict.** `[ADOPT]` — the strongest governance artifact in the probe. It is
the same principle as our working agreement rules 3 and 4 (fix on the spot; no
unrelated changes) and our "evidence, not claims" rule, but enforced at the
merge surface. **Conflict to record:** their template *requires* an issue link
while our rule 2 forbids issue/PR numbers in the **commit subject**. Those are
different surfaces (PR body vs commit subject) and do not collide — say so in
the decision register so nobody "fixes" it later.

---

### P6. Issue templates that demand the facts that change the diagnosis `[ADOPT-ADAPTED]`

**Evidence.** `.github/ISSUE_TEMPLATE/bug_report.yml` opens with *why* the
fields exist (*"Flight Finder extracts prices with an LLM, so the version,
provider, and model fields below matter a lot"*), then makes `version`,
`deployment` and `provider` **required** dropdowns, asks for the exact model ID,
and requests logs with `render: shell` naming the log prefixes to grep
(`[extract]`, `[scrape]`, `[navigate]`).

**Why it exists.** The single fastest triage input is the field that correlates
with the failure class; asking for it up front removes a round trip.

**Verdict.** `[ADOPT-ADAPTED]`. Our triage correlate is not the LLM model but
the **lane and the surface**, and the two are **orthogonal** — a single dropdown
("Python SDK") cannot express "Python SDK on the Developer lane", which is a
different code path with a different credential, base path and failure mode:

```
surface:    python-sdk | js-sdk | mcp | qml
lane:       pfs | developer
operation:  search | price | hold | booking | polling | hotels
```

Every one of those combinations is reachable, so they are three fields, not one.
The pleasing traceability holds: the `fix_hint_code` our MCP envelope returns
(`AUTH_INVALID`, `RATE_LIMITED`, …) is exactly the last field such a template
should require — it is the one value that already names the failure class.

---

### P7. One response envelope, measured `[ADOPT-ADAPTED]`

**Evidence.** `apps/web/src/lib/api-response.ts` is the whole mechanism:
`ApiResponse<T> = {ok:true;data:T} | {ok:false;error:string}` with
`apiSuccess`/`apiError`. Verified adoption: **71 of 75 `route.ts` files** import
it; the remainder hand-build the same shape, so the contract holds by
convention, not by type. `API.md:6-9` documents it — and then admits *"Some
older flight examples below omit the `ok` field for brevity."*

**Why it exists.** A single success/failure discriminator lets a client branch
once instead of per endpoint.

**Verdict.** `[ADOPT-ADAPTED]`. We built the same idea for MCP tool results
(P2.1, `status`/`completeness`). Their transferable increment is the **adoption
measurement**: account for every call site, name the outliers, and fail the build
when the accounting changes unexpectedly.

**Corrected 2026-10-03 — this paragraph used to say "4 of 14", and that was
stale.** The envelope now wraps **11 of the 14 advertised tools**; the other
three (`connect_payment`, `get_agent_profile`, `load_resources`) answer from
local data and are exempt *because* an API-outcome status would be a lie for
them. The metric is deliberately not a ratio: `sdk/mcp/src/envelope.test.ts`
asserts that **every advertised tool is classified as enveloped or exempt, and
every classified name is still advertised**, so adding a tool fails until someone
decides which it is, and removing one fails until the entry goes with it. That
structural invariant — not a percentage — is what this pattern is actually about,
and it landed with the envelope.

---

### P8. Name the failure direction of every limiter `[ADOPT]`

**Evidence.** Rate limiting is Redis `incr`/`expire` counters in five routes.
Registration and ingest **fail closed** (a Redis outage returns 503 —
`app/api/community/register/route.ts`); `parse` and
`community/routes/[route]` **fail open** (`if (!redis) return …not limited`).
`API.md:385-392` publishes the resulting policy per operation (*"Scrape trigger:
One at a time (subsequent calls queue)… Price reads: Cached for 2 minutes"*).

**Why it exists.** "Rate limited" has two failure modes — letting traffic
through, or dropping it — and the right answer differs per endpoint. Writing it
down stops an outage from silently disabling a safety control.

**Verdict.** `[ADOPT]`. Our docs publish numeric quotas (10/10 min, 30/hour,
100/day) but do not state what happens when the limiter itself is unavailable,
nor which side of the tradeoff each endpoint takes.

**What "the limiter" has to say, per limiter.** A quota is not a policy on its
own — *what* is being counted and *what happens* when the counter is not there
are separate decisions, and flight-finder's five routes split them three ways:

```
scope:              what the counter is attached to (API key, IP, provider, route, session)
operation:          which call it governs (search, poll, booking, price read)
window + limit:     the numbers, and whether a successful-but-empty call spends one
failure direction:  when the store holding the counter is unavailable — fail open or closed
response:           what the caller sees (429 + Retry-After? a queue? a silent no-op?)
```

Our own case is worse than a documentation gap: for the PFS lane we know the
numbers but **not the scope** (per card? per token? per IP?) nor the failure
direction, and this study cannot settle it from the outside — it needs the
controlled probe in the trvl study (§6 Q8), which is still open.

---

### P9. A single credential resolver, with precedence written down `[ADOPT]`

**Evidence.** `apps/web/src/lib/scraper/ai-registry.ts` resolves provider keys
through `resolveApiKey()`, where DB-stored encrypted keys take precedence over
environment variables; `AGENTS.md` §Secrets states the rule flatly: *"Do not
read `process.env.<PROVIDER>_API_KEY` directly; go through the registry."*
[INFERENCE] the encrypted-store implementation lives in the un-vendored
`thesidedoor-core` package; the resolver and the rule are in-tree.

**Why it exists.** One precedence order, in one place, means a user cannot end
up with two different credentials active depending on which code path runs.

**Verdict.** `[ADOPT]`. Our verified state is the opposite, measured 2026-10-03:
`LETSFG_*` is read at **5 sites in the MCP server**, **8 in the JS SDK** (10
matches, 2 of them in doc comments) and **15 in the Python SDK** (17 matches, 2
in docstring examples). Those are counts, not line numbers, because the lines
move and the count is the claim.

**The drift this is supposed to prevent has already happened once.** The MCP
server's Developer-API booking path posted to the **retired**
`/developers/api/v1/bookings/book` (410 since 2026-09-08) while the JS SDK
correctly used `/flights/book` — two surfaces, two ideas of one lane.
**Corrected 2026-10-03: that specific defect is fixed**, and fixed the only way
available without a resolver — with a route-specific test that asserts the MCP
books and polls on `/flights/*` and never on `/bookings/book`
(`envelope.test.ts`). That is the point: the drift was caught by a test written
*for that route*, not by anything structural. The next route will not have one.

**The invariant, stated properly.** There is **one canonical lane-resolution
contract**, and **one implementation of it per SDK** — the contract is shared, the
runtime code is not. Python, JS and MCP implement the same matrix and the same
precedence; they do not share an artifact, and nothing here requires them to.

> **The resolver is authoritative for lane, base path, authentication mechanism
> and credential selection. Operation code may consume the resolved
> configuration but may not independently derive any of those values.**

"Consume the resolved configuration" is a shape, not a promise, so it is named:
the resolver returns a **`ResolvedLane`** carrying **`lane`, `base_path`,
`auth_scheme`, `credential`**. That is what makes the invariant testable — a
`resolveLane()` that returns only `"pfs"` while URL and auth selection stay
scattered across call sites does **not** satisfy it, and the test can say so.

The matrix that contract defines, one row per surface × lane:

| Surface | Lane | Auth | Base path | Credential |
|---|---|---|---|---|
| Python SDK | PFS | Bearer | `/api/*` | `LETSFG_BEARER_TOKEN` → saved `pfs_auth` |
| Python SDK | Developer | API key | `/developers/api/v1/*` | `LETSFG_API_KEY` → saved `api_key` |
| JS SDK | PFS | Bearer | `/api/*` | `LETSFG_BEARER_TOKEN` → saved bearer |
| JS SDK | Developer | API key | `/developers/api/v1/*` | `LETSFG_API_KEY` → saved key |
| MCP | PFS | Bearer | `/api/*` | `LETSFG_BEARER_TOKEN` |
| MCP | Developer | API key | `/developers/api/v1/*` | `LETSFG_API_KEY` |

The precedence is `explicit argument → environment → saved config`, and it stays
as it is today; what changes is that it is written **once per SDK** instead of
re-derived at every call site.

**One more invariant, because this is the cross-lane drift that actually
happened:** the credential must be **compatible with the resolved lane**. A PFS
lane takes a bearer credential; a Developer lane takes an API key. So an explicit
API key must not be paired with a bearer lane, and a saved `pfs_auth` token must
never be offered as a Developer credential. Stating the precedence without this
leaves open exactly the hole the resolver exists to close.

**Transferable principle:** lane and authentication configuration must have one
authoritative source, and operation code may not re-derive it.

---

### P10. A health flag must require positive evidence, not the absence of an error `[ADOPT]`

**Evidence.** `apps/web/src/lib/scraper/navigate.ts` carries the postmortem
inline: the old loose "currency symbol + digit" gate *accepted a Turkish stub
page (1964 chars, "EUR" appearing in marketing copy), extraction returned zero
prices, and the cron silently saved nothing every cycle* (issue 65). The
replacement is a **two-criterion** signal — currency mentioned at least
`MIN_CURRENCY_MENTIONS = 3` times **and** at least one price-shaped token, with
lookarounds to avoid `TRY` inside `INDUSTRY`. `NavigationResult` carries
`resultsFound: boolean` as an explicit capability flag.

The same discipline appears in the provider status union:
`ProviderAvailability = 'configured' | 'ready' | 'no_key' |
'invalid_credentials' | 'not_installed' | 'not_authenticated' | 'unreachable'`,
with the comment *"API configuration checks do not claim that a remote server
has authenticated the key."*

**Why it exists.** "Found nothing" and "never looked properly" are different
facts, and collapsing them produces a silent false negative that runs forever.

**Verdict.** `[ADOPT]`. This is the same principle as our `no_results` vs
`partial`/`timeout` envelope split, and their phrasing is the best one-line
statement of it: a "no results" verdict needs a *positive* criterion.

**The invariant, stated so it can be asserted.**

> **LetsFG must never convert an unsuccessful search attempt into `no_results`
> merely because the normalised offer list came back empty. `no_results` requires
> both successful execution *and* declared-complete coverage for the requested
> search domain.**

Both halves are load-bearing, and the second is the one that gets forgotten:
`status = completed` with `coverage_mode = partial` and an empty offer list is
**not** a `no_results` — it is a partial read of a market we did not finish
looking at. That is the same rule as "`no_results` is prohibited whenever coverage
is degraded" (`trvl-study-design.md` §2.1), restated as the positive requirement
rather than the prohibition, because a positive requirement is what a test can
assert directly.

An empty list can arise from at least seven different causes, and only the first
is a result:

| What happened | What the caller must be able to see |
|---|---|
| the provider searched and found nothing | `no_results`, `coverage_mode: complete` |
| it returned offers and all failed validation | not a result — `partial` (filtered_out) |
| the itinerary is unsupported (route, date, cabin) | distinct from "nothing found" |
| the provider timed out | `timeout` — never `no_results` |
| the provider returned incomplete data | `partial` |
| the answer came from a stale cache | freshness ≠ live; not a fresh empty |
| the response was malformed | `failed` — a parse error, not an empty market |

Our envelope already carries the pieces (`status`, `completeness`,
`coverage_mode`, `result_state`, `empty_reason ∈ {provider_empty, not_loaded,
filtered_out}`, `freshness`), and the rule "`no_results` is prohibited whenever
coverage is degraded" is canonical in `trvl-study-design.md` §2.1 and enforced in
`envelope.ts` (`noResultsJustified`, `isLegalCoverage`).

**One pushback on the proposed taxonomy.** An earlier review proposed a new
three-way enum — `NO_RESULTS` / `NO_VALID_RESULTS` / `NO_SUPPORTED_RESULTS` — as
well. Three parallel status vocabularies is exactly the mistake this repository
has already refused once: `fix_hint_code` reuses the SDK's error codes rather than
inventing a second taxonomy. The distinctness the review wants is already
expressible as **`status` × `empty_reason` × `coverage_mode`**, and the actionable
half is the *invariant* above, not a longer enum. Where a cause is not yet
represented — "unsupported route" and "malformed response" are the two candidates
— extend `empty_reason`/`status` in the one vocabulary, with a test, rather than
starting a second one.

**Transferable principle:** an empty result is not evidence of no results unless
execution succeeded *and* coverage is complete.

---

### P11. Model the distance between an observation, an estimate, and a verified fact `[ADOPT-ADAPTED]`

**Evidence.** `apps/web/prisma/schema.prisma` separates the *observation*
(`PriceSnapshot`: `travelDate`, `price`, `currency`, `airline`, `stops`,
`flightId` — a documented stable identity `airline-(flightNumber|HHMM)-origin-
dest-date`, `status 'available'|'sold_out'`, `vpnCountry`, `scrapedAt`) from the
*attempt* (`FetchRun`: `status 'success'|'partial'|'failed'`,
`snapshotsCount`, `error`, `extractionCost`). `API.md:855-870` then pins the
semantics: *"`lastCheckedAt` records an attempt and must not be displayed as
fresh price evidence. Failed checks retain earlier verified prices. Historical
observations do not establish current availability or guarantee a booking
price."* The 0.15.0 changelog adds the policy: legacy one-way **estimates** are
*"excluded from flight histories, alert baselines, community prices, and CLI
summaries without deleting stored rows"*, and car results *"distinguish verified
rental totals from advertised prices and estimates… Unverified charges cannot
qualify for price alerts."*

**Why it exists.** A price that was advertised, a price that was quoted, and a
price that was charged are three different claims; conflating them creates
alerts and, in our domain, disputes.

**Verdict.** `[ADOPT-ADAPTED]`, and **promoted: this is an integration
requirement, not a deferred schema idea.**

**Three axes, never collapsed.** An execution outcome, a price fact and a
transactional state are different questions, and any one of them can be strong
while another is weak. An earlier draft of this section folded `held` and
`ticketed` into the price ladder; that was wrong. They are *commercial states*,
not stronger observations of a price, and mixing them is precisely how an agent
ends up presenting a ticket as a well-evidenced fare.

```
search outcome     results | partial | no_results | timeout | rate_limited | failed
price evidence     indicative | observed | quoted | verified | stale | unavailable
booking state      search_result | quote | hold | ticket | confirmed_itinerary
```

The vocabulary is not new: it is our `price_status` with the booking states lifted
out of it. The axes then line up like this, and every row is coherent:

```
search_outcome=partial    price_evidence=observed    booking_state=search_result  the market is real, our read of it was not complete
search_outcome=results    price_evidence=quoted      booking_state=quote          the strongest useful pair today
search_outcome=no_results price_evidence=unavailable booking_state=search_result  a positive empty
search_outcome=timeout    price_evidence=unavailable booking_state=search_result  we do not know
search_outcome=results    price_evidence=verified    booking_state=hold           a verified fare, held on a card
```

`price_evidence` deliberately mirrors `price_status ∈ {indicative, observed,
verified, stale, unavailable}`; **`quoted` is the one addition**, and it is the
point at which a provider answers for a *specific itinerary* rather than
advertising a route. `booking_state` is the axis that must never be read as
evidence: a hold is not a better quote, and a ticket is not a better observation.

**We already have the axes** — this is not a model to invent. The MCP envelope
separates execution (`status`) from coverage (`completeness`, `coverage_mode`)
from temporality (`freshness`, `observed_at_basis`), and the provider contract
already carries `price_status` and a verification outcome. **What is missing is
not another evidence vocabulary. It is the explicit per-offer contract, and the
invariants governing how `price_status`, verification outcome, freshness and
search completeness interact** — the four facts that are currently each true on
their own and never checked against each other:

> A retrieval that did not complete cannot promote a price, and a price that was
> merely advertised cannot be presented as verified. `freshness=live` requires a
> live fetch; `price_status=verified` requires a verification outcome; and a
> booking state may never be read back as evidence about a price.

The booking axis therefore stops being "a third axis noted after the fact": it is
one of the three, stated up top, and the distinction it protects is the one
`AGENTS.md` already carries in prose — a hold is not a charge, and only
`completed` with a PNR means booked. The requirement is that every offer carries
its grade *and* that the reference asserts these distinctions (P17), because an
agent that reads a search result as a quote is the failure this pattern exists to
prevent.

**Deliberate constraint.** The ladder is added to the *existing* offer contract,
not as a parallel one: no field may duplicate `price_status`, and nothing may
promote an offer to `verified` without a verification result. This is the
implementation doc's P11 work, and it is the reason that item is specified as
"extend the offer, do not model a second price".

**Transferable principle:** execution outcome, price evidence and booking state
are independent dimensions, and none may be read as another.

---

### P12. Test taxonomy as filename suffix + `skipIf` flag, hermetic by default `[ADOPT-ADAPTED]`

**Evidence.** Test kinds are encoded in the filename — `*.integration.test.ts`
(20), `*.browser.test.ts` (13), `*.live.test.ts` (4), plain `*.test.ts(x)`
(195) — and every non-hermetic suite opens with
`describe.skipIf(process.env.X !== '1')`, so a bare `vitest run` touches nothing
external. Verified **14 distinct flags**
(`ACCOUNT_INTEGRATION_TESTS`, `CAR_STORE_INTEGRATION_TESTS`,
`TRAVEL_BROWSER_TESTS`, `TRAVEL_LIVE_TESTS`, …). CI then enables exactly one
family per job against dedicted services: `ci.yml` `account-lifecycle` and
`shared-access` each start their own `postgres:16-alpine` on a distinct port
(55442 / 5432) with `ACCOUNT_INTEGRATION_TESTS: '1'` and `REDIS_URL: ''`.
Unit config is minimal — `apps/web/vitest.config.ts` has `environment: 'node'`
and `.test.tsx` files opt into jsdom **per file** via
`/** @vitest-environment jsdom */`.

**Why it exists.** Hermetic-by-default means a developer's plain `npm test` is
fast and can never fail for an environmental reason; the flag names are
self-documenting at the call site.

**Verdict.** `[ADOPT-ADAPTED]`, and the adopted principle is not their naming
scheme:

> **Tests with external dependencies declare their execution class and are
> hermetic by default.**

Our Python suite already does exactly that with `pytest -m "not live"`, and it is
the better tool: the class is a *marker on the test*, not a filename convention
plus an environment flag, so a bare run cannot reach the network by accident and
the class is readable at the definition site. Copying `*.live.test.ts` +
`describe.skipIf(process.env.X !== '1')` would be adopting a mechanism we already
have in a stronger form.

**The transferable part was granularity, and that question is now closed.** The
stale modules used to live in one blunt `collect_ignore_glob` list in
`sdk/python/conftest.py` (18 modules) that could not say *why* each was parked.
**Cleared 2026-10-03:** all 18 tested implementations removed in `f91be5b`, so
they were deleted and the block removed, rather than burnt down one entry at a
time. `test/docs-claims.test.mjs` still pins the parked set, now at
`PARKED_TEST_MODULES = 0`, so re-parking a module takes a deliberate edit.

Their counter-example is still the instructive half: 232 test files, 14 gating
flags, and **no document that explains the scheme** — verified, no markdown under
their `docs/` describes it. A taxonomy nobody writes down is one a new
contributor cannot follow, which is what a `docs-claims`-style assertion is for.
Our own answer is `docs/TESTING.md`, which names the tiers and the required
checks.

---

### P13. Verify the process itself: a hygiene test for the workflow files `[ADOPT]`

**Evidence.** Their `scripts/docker-publication.cjs` is the closest thing they
have to process verification: `requireRelease()` rejects a tag that is not
`v?X.Y.Z`, rejects a **moved tag** (`commit.sha !== sha → 'Release tag moved
after verification'`), and rejects a version mismatch
(`tag !== 'v' + packageJson.version → 'Release tag does not match package
version'`); `requireChecks()` then waits for `REQUIRED_WORKFLOWS = ['ci.yml',
'car-tests.yml', 'hotel-tests.yml', 'desktop-ci.yml']` to report
`conclusion: 'success'` **for that exact SHA on `main`**. Their four other
process rules — SHA pinning, permissions, timeouts, the knip exception ledger —
are enforced by convention only, and the repo has drifted on three of them.

**Why it exists.** A rule nobody checks is a rule that decays; the tag-binding
check is the one place they chose to spend the effort, and it is the one place
that has not drifted.

**Verdict.** `[ADOPT]`. We already have the vehicle — zero-dependency
`node --test` files, run as required CI jobs. Their intention, applied to our
files, is worth keeping **in three classes** rather than one growing policy test,
because a guard nobody can locate is a guard that gets bypassed:

| Class | What it asserts | Where it lives today |
|---|---|---|
| **Repository invariants** | every job has `timeout-minutes`; every workflow declares least-privilege `permissions`; every `uses:` is SHA-pinned with a version comment; checkouts do not persist credentials; push/PR runs set a concurrency group with the right cancellation policy | `test/workflow-hygiene.test.mjs` — **implemented** |
| **Documentation invariants** | manifest versions agree; every advertised MCP tool is documented; the OpenAPI server + paths compose to the documented base; every local markdown link resolves; the changelog's newest release equals the manifests | `test/docs-claims.test.mjs` — **implemented** |
| **Release invariants** | tag ↔ version ↔ commit SHA ↔ required checks for that SHA, and the artifact carrying the same SHA | **split, honestly**: the *version source* is already asserted (`docs-claims` pins manifest ↔ manifest), but a convention test would otherwise assert the **absence** of a convention — there is no release workflow or tagging scheme to check today. So: choosing the convention is the gate (**P3.1**, owner item); the convention test lands with it; the live tag↔SHA assertion activates with the first tag |

The split is also a locator: "why did CI fail" has one answer per class instead of
one 400-line file.

---

### P14. Publication integrity and install verification `[ADOPT-ADAPTED]`

**Evidence.**
- Tag↔version↔checks binding and artifact completeness as in P13; plus
  `desktop-publication.cjs` requires at least one `.dmg` + `.msi` + `.AppImage`
  and rejects duplicate basenames.
- `.github/workflows/ci.yml` `install-smoke` builds
  `scripts/Dockerfile.install-test` — `debian:bookworm-slim`, a **non-root**
  `testuser`, copying only the installer, the CLI shim and the smoke script,
  then `docker run --rm` — and `ci.yml` `integration` builds the **exact
  commit** (`docker build --build-arg COMMIT_SHA="$sha"`), stages it under its
  own compose project (`-p flight-finder-integration-test`), runs browser smoke
  tests against it, and then exercises upgrades in a real browser with
  `node scripts/testing/installer-browser.mjs "$image" --update
  --legacy-installer=8b8bc6e0…` for both web and desktop.
- Build identity is a product feature: 0.1.0 shipped *"`fairtrail version`
  command showing version and git commit SHA"* and *"Commit SHA exposed in
  `/api/version` endpoint for build traceability"*.
- Signing is honestly disclosed as absent: `apps/desktop/README.md` states code
  signing/notarization *"needs certificates added as the Tauri signing
  secrets… before enabling signed release builds."*

**Why it exists.** The published artifact must be the tested artifact, and an
upgrade path is a different code path from a fresh install; testing only the
latter ships confident breakage.

**Verdict.** `[ADOPT-ADAPTED]`, and the adopted principle is the one sentence the
whole pattern rests on:

> **Test the artifact a user actually installs, not the repository checkout.**

Our distribution is PyPI + npm + `npx letsfg-mcp` (+ the published MCP endpoint),
so the concrete matrix is:

| Artifact | Install smoke — **hermetic**: no credential, no provider | Live smoke — **gated** |
|---|---|---|
| `letsfg` (PyPI) | fresh venv → `pip install -U letsfg` → `letsfg --version` → CLI starts | one real search with `LETSFG_BEARER_TOKEN` |
| `letsfg` (npm) | fresh prefix → `npm install letsfg` → import the package | one real search, gated the same way |
| `letsfg-mcp` (npm) | `npx -y letsfg-mcp` → `initialize` → `tools/list` → a call that needs no credential | a call that reaches the API |
| hosted MCP | — | `initialize` → `tools/list` → one call against `letsfg.co/developers/api/mcp` |
| Docker image (if published) | start → `initialize` → version | a representative request |

**Why the split is not cosmetic:** an install test that needs a credential and a
live provider is not an install test, it is an integration test wearing one — it
fails for reasons the package did not cause, and it cannot run on a fork or a
contributor's PR. It is the same distinction D15 draws for the suites, applied to
artifacts.

The README claims `pip install -U letsfg` "gives you the `letsfg` CLI command"
and that `npx letsfg-mcp` works; **nothing verifies either claim today**. The
`--legacy-installer=<sha>` upgrade test is the best idea in this section and the
one we do not have: test upgrading *from a pinned old release*, not just
installing fresh, because an upgrade is a different code path from an install.

**Build identity, defined.** "Identity in `initialize.serverInfo`" is decorative
unless it says *which source revision produced this artifact*. The requirement is
therefore:

```json
{ "name": "letsfg", "version": "2026.x.y", "commit": "<40-hex sha>" }
```

with the invariant: **the reported commit SHA identifies the exact source
revision the packaged artifact was built from.** `version` alone cannot — it is a
hand-maintained literal (our `VERSION` in `sdk/mcp/src/index.ts` is pinned to
`package.json` by `docs-claims.test.mjs`, which keeps it honest but still does not
identify a revision). A build timestamp is optional and adds nothing that the SHA
does not.

---

### P15. Architecture rules enforced by pre-commit, with grandfathering `[DEFER — owner decision]`

**Evidence.** `.pre-commit-config.yaml` registers two local hooks with
`language: system`: `scripts/hooks/check_file_line_count.sh` (default 1000
lines, overridable via `FLIGHT_FINDER_MAX_FILE_LINES`, message *"Split related
code into a focused module"*) and `scripts/hooks/check_dir_file_count.sh`
(≤10 source files per directory, **only for files that are added or untracked**:
`git diff --cached --name-only --diff-filter=A` + `git ls-files --others`, so
existing directories are grandfathered and *adding* a file is what fails).
`AGENTS.md` §Conventions restates both.

**Why it exists.** Fat files and flat directories are the early symptom of
modules losing their boundary; failing the *addition* avoids a big-bang
refactor.

**Verdict.** `[DEFER]`. Our measured state would flag `Panel.qml` (4757 lines),
`Model.js` (2684, plugin), `sdk/js/src/ranking.ts` (1446),
`test/model-test.js` (1294), `sdk/python/letsfg/client.py` (1294),
`sdk/mcp/src/index.ts` (1291), `cli.py` (1103) and `sdk/js/src/index.ts` (1091).
Acting on it would require refactors, which our working agreement rule 4
explicitly forbids without an owner request — so this is recorded as an owner
decision, not queued. The *grandfathering* design is what makes the policy
**incrementally adoptable** — a naive cap would fail on day one.

---

### P16. CHANGELOG as a user-facing contract with per-entry provenance `[ADOPT]`

**Evidence.** `CHANGELOG.md` (712 lines) follows Keep a Changelog: `## [0.15.0]
- 2026-09-10`, a prose lead paragraph, then `### Added` / `### Fixed` /
`### Changed` / `### Upgrade notes`. Every bullet ends with provenance — a PR
link (`([#209](https://github.com/affromero/flight-finder/pull/209))`), a commit
SHA link, or both — bug bullets credit the reporter (*"reported by
@DcryptedCA"*), and entries state **scope limits** rather than only wins
(*"Unsupported Auto Europe extras are identified explicitly and remain
ineligible for tracking as verified combined totals"*). Verified: the file
contains **no `[Unreleased]` section** (grep count 0), so the top entry is
always a shipped release and a version↔changelog check is impossible by
construction.

**Why it exists.** A changelog is the only place a user can learn what changed
*and* how to verify it; the provenance link is what makes each claim auditable.

**Verdict.** `[ADOPT]`, with one deliberate divergence: keep an `[Unreleased]`
section so our `docs-claims` test can assert *"the newest changelog version
equals the package versions"* — the check their format structurally prevents.

---

### P17. Document the operational semantics agents get wrong `[ADOPT]`

**Evidence.** `API.md` spends its most valuable lines on the things a caller
will otherwise get wrong, verified verbatim: *"Validation uses the check's
completion time as `evaluatedAt`… `lastCheckedAt` records an attempt and must
not be displayed as fresh price evidence"*; *"The first eligible price
establishes the low-price baseline… Partial checks can retain eligible prices
without rearming an above-target alert. `notificationsConfigured` reports
channel readiness; saved alert preferences alone do not establish that a
notification was delivered"*; and on delivery: *"Delivery is **at least once**,
not exactly once… The stable `data.eventId` in webhook payloads lets receivers
deduplicate that case."* There is also a *"Typical agent workflow"* section and
an *"Environment variables for agents"* table.

**Why it exists.** For an agent-facing surface, the failure mode is
misinterpreting a status, not malforming a request.

**Verdict.** `[ADOPT]`, and promoted to a documentation **contract** rather than a
prose habit. We have the raw material in `AGENTS.md` (polling does not consume
quota; offers expire ~15 minutes; a hold is not a charge; `needs_attention` means
do not rebook), but it lives in the *agent* file, not the *API reference*, and
**nothing asserts it stays true**.

Every agent-facing operation documents, in the reference:

| Question | Why it changes behaviour |
|---|---|
| what the status means | an agent branches on it |
| **what it does not mean** | `completed` ≠ "the search has stopped growing" |
| freshness semantics | an observation is not fresh evidence |
| retry semantics | a transient failure and a normal outcome look alike |
| idempotency | a retried booking is a second hold, not a no-op |
| whether it consumes quota | polling never does; searching does |
| whether the result is authoritative | advertised vs observed vs verified |
| whether the result is actionable | an offer is bookable only while it is fresh |
| whether repeating it is safe | read-only vs mutating |

And the ladder that belongs in the booking pages specifically, because it is what
a user is actually promised:

```
search result ≠ quote ≠ hold ≠ ticket ≠ confirmed itinerary (PNR)
```

This is more valuable than another endpoint: for an agent-facing product the
failure mode is misreading a status, not malforming a request.

**Transferable principle:** for an agent-facing surface, the most valuable line
in a reference is what a status does *not* mean.

---

### P18. Makefile as a discovery surface `[DEFER]`

**Evidence.** `Makefile` (`setup`, `dev`, `build`, `logs`, `reset`, `clean`)
with `##` doc comments and `.DEFAULT_GOAL := help` rendering them via awk.
`AGENTS.md` §Setup points at `make dev` while giving the raw commands too.

**Verdict.** `[DEFER]` — pure ergonomics, no defect, so rule 4 excludes it.

---

### P19. Committed per-PR screenshot archive `[REJECT]`

**Evidence.** `docs/pr-209/01..12-*.png`, `docs/car-tracking/*.png`,
`docs/screenshots/car-country-validation/*.png`, `docs/flight-pricing/one-way-
estimate.png`, `docs/cli-model-selection/*.png` — and `docs/` contains **no
markdown at all**. Verified: **zero** inbound references to any of these paths
from any `*.md` or `*.yml` file.

**Verdict.** `[REJECT]` as a docs pattern. An evidence archive with no index is
undiscoverable and rots; the useful half is already covered by P5, where the PR
template asks for before/after screenshots in the PR body. We have the stronger
mechanism already (mkdocs nav + the `docs-claims` link checker).

---

## 2. What not to copy

All verified in the probe tree; each is a lesson, not a jab:

1. **A 907-line hand-written API reference with no machine check against 75
   route files.** It has already drifted internally: `API.md` says to configure
   `CRON_SECRET` *"through Doppler"* while `AGENTS.md` forbids secret-manager
   wrappers. Docs rot even in a disciplined repo — which is the entire
   justification for our `docs-claims` test.
2. **Live tests that never run.** Four `*.live.test.ts` gated by
   `TRAVEL_LIVE_TESTS`, and no workflow ever sets it. A test that cannot fail
   advertises coverage that does not exist; either schedule it (our P1.4) or
   delete it.
3. **An undocumented test taxonomy.** 232 test files, 14 gating flags, and no
   file that explains the scheme.
4. **Self-inconsistent hardening.** Only 1 of 8 workflows SHA-pins; 3 of 8 lack
   `permissions:`; publish jobs lack timeouts. Adopt the rules *with* the check
   that keeps them (P13), or they decay.
5. **A line-number-keyed ignore file.** `.gitleaksignore`'s
   `commit:path:rule:line` entry churns when the line moves; prefer placeholder
   hygiene over pinning a line.
6. **Scale as an aspiration.** 1092 files and a Postgres/Redis/Tauri footprint
   for a personal price tracker. Our surface is deliberately smaller.
7. **A single-maintainer third-party composite action as a core gate.** Their
   dead-code gate depends on `affromero/repo-maintenance`; pinning by SHA makes
   it safe but not bus-factor-proof.

## 3. Decision register

The **Type** column is the scope split from §0.1 made checkable: only the rows
typed as integration, semantics, evidence or contract belong to the
flight-finder integration surface. The rest are repository work that this study
happened to surface, and they stand on their own defects.

| # | Decision | Type | Verdict | Rationale / conflict |
|---|---|---|---|---|
| D1 | Adopt a dead-code **ledger**: exact identities + review reasons | Repository hygiene | `[ADOPT]` (policy) | The policy is separable from the tool and worth adopting on its own evidence. Our known dead weight must be *recorded* rather than silently deleted under rule 4. |
| D2 | Harden CI: per-job timeouts, top-level `permissions`, `persist-credentials: false`, concurrency | Repository hygiene | `[ADOPT]` — **done 2026-10-03** | A plain defect; we had zero timeouts. Now enforced by D3. |
| D3 | Enforce D2 with a workflow-hygiene test | Repository hygiene | `[ADOPT]` — **done 2026-10-03** | `test/workflow-hygiene.test.mjs`; their drift is the evidence that unenforced rules decay. |
| D4 | Adopt secret scanning, **after** placeholder hygiene | Security / repository | `[ADOPT]` | Order matters — see P3: make examples unmistakably fake, adopt canonical placeholders, scan history, and only then add narrow, dated exceptions. An allowlist written first teaches the scanner to tolerate the material. |
| D5 | Extend Dependabot to npm + pip with grouping and cooldown | Repository hygiene | `[ADOPT]` | `sdk/js`, `sdk/mcp` and the Python deps are unmanaged; `github-actions` alone is not enough. |
| D6 | PR template with a mandatory verification section and consent block | Contributor workflow | `[ADOPT]` | Codifies rules 3/4 at the merge surface. Their issue-link requirement does **not** conflict with our commit-subject rule (PR body ≠ commit subject) — do not "fix" that later. |
| D7 | Issue template requiring **surface × lane × operation** and the error/envelope code | Triage | `[ADOPT]` | Surface (`python-sdk`/`js-sdk`/`mcp`/`qml`) and lane (`pfs`/`developer`) are **orthogonal** — an enum, not a single dropdown — and `fix_hint_code` is a first-class field. |
| D8 | **One canonical lane-resolution contract** — one implementation per SDK — pinned by a lane-matrix test | **Integration architecture** | `[ADOPT]` | The resolver is authoritative for lane, base path, auth mechanism and credential, and returns them as one `ResolvedLane`; **no operation-level code may derive any of those values itself**, and the credential must be compatible with the resolved lane. 28 scattered `LETSFG_*` reads (5 MCP / 8 JS / 15 Python); the drift it prevents has already happened once (P9). |
| D9 | **Positive evidence required for `no_results`**; retrieval failure is never converted into it | **Flight-search semantics** | `[ADOPT]` | "LetsFG must never convert an unsuccessful search attempt into `no_results` because the offer list is empty." Expressed with the existing `status` × `empty_reason` × `coverage_mode`, **not** a second status enum. |
| D10 | Publish **scope, counter, window, failure direction and response** for every limiter | Reliability | `[ADOPT]` | Fail-open vs fail-closed must be a decision, not an accident. Blocked on the trvl study's limiter probe (Q8) for our own numbers. |
| D11 | **Operational semantics as a documentation contract**, with assertions | **API contract** | `[ADOPT]` | Every agent-facing operation documents the dimensions in P17 **that apply to it** — a read-only profile call has no booking-retry semantic — plus the ladder `search result ≠ quote ≠ hold ≠ ticket ≠ PNR`, moved from `AGENTS.md` into the reference and pinned. |
| D12 | CHANGELOG with provenance links and an `[Unreleased]` section | Documentation | `[ADOPT]` — **done 2026-10-03** | Deliberate divergence from theirs: `[Unreleased]` makes the version check possible, and it is now asserted. |
| D13 | Tag↔version↔commit binding, and required checks for *that* SHA | Release integrity | `[ADOPT]` | One end-to-end chain with D14: **an artifact is releasable only if its version and source SHA correspond to a release tag whose exact commit passed the required checks.** Blocked on a tagging convention — `git tag` is empty (trvl D13). |
| D14 | Fresh-install and packaged-entry-point smoke tests; **build identity = version + immutable commit SHA** | Integration / release | `[ADOPT-ADAPTED]` | The artifact must carry the SHA that D13's tag binds to, or build identity is decoration. Installs are verified hermetically; a real search is a **gated** live test, not part of the install smoke. |
| D15 | **Explicit test execution classes, hermetic by default** | Test architecture | `[ADOPT-ADAPTED]` | Renamed from "per-module markers replacing the quarantine list" — that mechanism is already obsolete here, and `pytest -m "not live"` is stronger than filename suffix + env flag. |
| D16 | 1000-line file cap + ≤10 files/dir pre-commit hooks | Repository architecture | `[DEFER]` | Would require refactors forbidden by rule 4; owner decision. The grandfathering design is what makes the policy incrementally adoptable. |
| D17 | Committed screenshot archive under `docs/` | Docs pattern | `[REJECT]` | Zero inbound references; undiscoverable. Keep visual evidence in PR bodies. |
| D18 | Makefile | Ergonomics | `[DEFER]` | Ergonomics only; rule 4 excludes it. |
| D19 | **Depend on** the third-party maintenance action | Repository hygiene | `[DEFER]` | The implementation, not the policy. Pin by SHA if adopted; verify Python coverage in `scripts/audit.py` first; consider vendoring. |

## 4. Open questions

1. Does `affromero/repo-maintenance`'s `scripts/audit.py` cover Python, or only
   JS/TS + Rust? Determines whether **D19** is adoptable; **D1's policy is
   adoptable either way**, which is the point of separating them.
2. Which of our `LETSFG_*` read sites are load-bearing versus incidental? Needed
   to size D8 without breaking documented precedence (explicit argument → env →
   saved config).
3. What is the limiter's **scope**, and does it fail open or closed when the
   counter store is unavailable? D10 cannot be documented until that is measured;
   the controlled probe is `trvl-study-design.md` §6 **Q8**, still open, and this
   study cannot settle it from the outside.
4. Should a "live" probe workflow (trvl plan P1.4) be scheduled with a
   `LETSFG_BEARER_TOKEN` secret, or should the live tests be deleted? Their
   inaction is the argument for deciding rather than drifting.
5. **Which flight-finder concepts map onto our existing offer contract?** Before
   any of P11 is implemented: where does the evidence grade live on the offer we
   already return, which field carries it, and which existing field it must not
   duplicate? The answer decides whether this is an extension of the current
   contract or the second parallel price model the study explicitly forbids
   (§0.1, P11).
6. **Which provider failures are normalised and which are exposed?** Timeout,
   rate limit, authentication failure, unsupported itinerary, malformed response
   and empty result cannot all become one status — but they also cannot all stay
   provider-specific if the caller is an agent branching on a code. The line
   between "become a LetsFG status" and "keep the provider's detail alongside it"
   is unresolved, and it is the prerequisite for P10's table being complete.

### These are gates, not curiosity

None of the six is a question the study leaves hanging; each is a
**prerequisite** — a decision or an implementation cannot be specified until the
answer exists. Classified:

| # | Kind | Blocks |
|---|---|---|
| Q1 | tooling feasibility | D19 (and only D19 — D1's policy is adoptable either way) |
| Q2 | migration inventory | D8 |
| Q3 | external behaviour, needs a live probe | D10 |
| Q4 | policy choice | trvl P1.4 (the live-probe workflow) |
| Q5 | **contract gate** | P11 — and therefore D11's search half |
| Q6 | **error-taxonomy gate** | D9/P10's table, and the SDK surfaces of P1.2 |

So the dependencies run:

```
Q2 ──→ D8 ──┐
            │
Q6 ──→ D9 ──┼──→ P11 ←── Q5 ──→ D11 ──→ D13/D14
            │
Q3 ──→ D10 ─┘
Q1 ──→ D19
```

Read it as: **nothing downstream of a gate may be implemented before the gate is
answered**, which is why the implementation document's suggested order starts with
a written answer rather than with code.

## 5. Priority ordering

Numerical order is not implementation order. The integration surface first,
because it is the reason for the study; repository work after, because it is
independent.

**Tier 1 — contract foundations (the integration surface)**

Ordered by dependency, not by importance — each item is only implementable once
the one above it has an answer:

| # | Item | Why here |
|---|---|---|
| 1 | **D8** lane/credential resolver | establishes transport and auth identity; everything else is expressed in terms of a lane |
| 2 | **D9 + Q6** search-outcome and error semantics | P11 cannot separate price evidence from execution outcome until the outcomes are settled |
| 3 | **P11 + Q5** the three-axis offer contract | the offer contract must be resolved before anything is written against it |
| 4 | **D11** operational semantics | documentation describes a contract that, by now, exists |
| 5 | **D10 + Q3** limiter scope and failure direction | gated on an external measurement (trvl §6 Q8) that this study cannot perform |
| 6 | **D7** triage metadata (surface × lane × operation) | valuable, but it is an observability contract, whereas D8/D9/P11 define the platform's semantics — so it follows them rather than leading |

**Tier 2 — distribution and API correctness:** D14 (installs, packaged entry
points, build identity), D13 (tag ↔ version ↔ SHA), D3 (already done).

**Tier 3 — security and repository hygiene:** D4 (after placeholder hygiene),
D5, D6, D1 (ledger policy).

**Tier 4 — optional ergonomics:** D15 (already satisfied in substance), D18.

**Rejected or deferred:** D17, D16, D19 until its Python coverage is verified.

This ordering is what the companion implementation document follows; it is also
what the acceptance bar there is written against.
