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

**Verdict.** `[ADOPT]` — the *ledger*, not necessarily the *action*. Our repo
has known dead weight (`sdk/python/letsfg/config.py`, `system_info.py`, a
`models.py` shadowed by `models/`, Playwright installs in `Dockerfile*` for
removed local connectors) that must be *recorded* rather than silently deleted
under our working agreement. Two cautions: the shared action performs JS/TS
reachability (`knip`) and Rust (`cargo-machete`); **Python support via
`scripts/audit.py` is unverified** and must be checked before depending on it,
and the action is a single-maintainer third-party composite — pin it by SHA as
they do, or vendor an equivalent.

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
the **lane and the SDK**: PFS (`/api/*`, Bearer) vs Developer API
(`/developers/api/v1/*`, `X-API-Key`), and `letsfg` Python / `letsfg` JS /
`letsfg-mcp`. Note the pleasing traceability: the `fix_hint_code` our MCP
envelope now returns (`AUTH_INVALID`, `RATE_LIMITED`, …) is exactly the field
such a template should require.

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
(P2.1, `status`/`completeness`). Their transferable increment is the
**adoption measurement**: count conforming sites, name the outliers, and (their
missing step) fail the build when the count changes unexpectedly. Our envelope
currently wraps 4 of 14 advertised tools; that ratio should be machine-checked
rather than assumed.

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

**Verdict.** `[ADOPT]`. Our verified state is the opposite: `LETSFG_*` is read
at **5 sites in the MCP server** (`sdk/mcp/src/index.ts:35,36,37,84,105`),
**5 in the JS SDK** (`auth.ts:32,96,118`, `cli.ts:34`, `index.ts:354-355`), and
**6+ in the Python SDK** (`cli.py:61,70,634,820,900,933`, `client.py:346,352`,
`local.py:26,55`, `connectors/auth.py:51`). The cost of that duplication is not
hypothetical — the MCP server's Developer-API booking path still posts to the
**retired** `/developers/api/v1/bookings/book` while the JS SDK correctly uses
`/flights/book`. One lane table plus one resolver per SDK, and a test that pins
both, is the fix.

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
statement of it: a "no results" verdict needs a *positive* criterion. It also
generalises: our `no_results` requires a present-and-empty offers list, and any
future "route unsupported" claim needs the same proof.

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

**Verdict.** `[ADOPT-ADAPTED]`. Our equivalent axis is **quoted → held →
ticketed**: `AGENTS.md` already says a hold is not a charge and that only
`completed` with a PNR means booked. What is missing is a machine-readable
per-offer evidence grade. The envelope's `status` is a start; the schema idea is
recorded here and deferred (implementation doc P5).

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

**Verdict.** `[ADOPT-ADAPTED]`. Our Python suite already does the equivalent
with `pytest -m "not live"`, which is the better tool. The transferable part is
the **granularity**: our stale tests were handled by one blunt
`collect_ignore_glob` list in `sdk/python/conftest.py` (18 modules) that
**could not express why each module was parked** at the point of use. (That
list was **cleared 2026-10-03**: all 18 tested implementations removed in
`f91be5b`, so they were deleted and the block removed rather than burnt down
one entry at a time.) The end state
is a per-module marker (or the suffix convention) so a quarantine burns down
one entry at a time. Their counter-example is instructive too: the taxonomy is
**documented nowhere** (verified: no markdown under `docs/`; `CONTRIBUTING.md`
says only *"Test behavior, not implementation details"*), which is what a
`docs-claims`-style test should prevent.

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

**Verdict.** `[ADOPT]`. We already have the vehicle: `test/docs-claims.test.mjs`
(zero-dependency `node --test`, required CI job). Extending it to assert *"every
CI job has a timeout; every workflow declares `permissions`; every `uses:` is
SHA-pinned"* is exactly their intention applied to our own files, and it turns
the P2 fixes into permanent invariants.

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

**Verdict.** `[ADOPT-ADAPTED]`. Our distribution is PyPI + npm + `npx`, so the
concrete analogues are: a **fresh-venv install smoke test** (our README claims
`pip install -U letsfg` "gives you the `letsfg` CLI command" — nothing verifies
that), an **MCP handshake smoke test** over the packaged entry point, the
**tag↔version** binding extended to the manifests our `docs-claims` test already
cross-checks, and a **build identity** in `initialize.serverInfo`. The
`--legacy-installer=<sha>` upgrade test is the single best idea in this section:
test upgrading *from a pinned old release*, not just installing fresh.

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
decision, not queued. The *grandfathering* design is what makes it adoptable at
all; a naive cap would fail on day one.

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

**Verdict.** `[ADOPT]`. We have the raw material in `AGENTS.md` (polling does
not consume quota; offers expire ~15 minutes; a hold is not a charge;
`needs_attention` means do not rebook) but it lives in the *agent* file, not the
*API reference*, and **nothing asserts it stays true**.

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

| # | Decision | Verdict | Rationale / conflict |
|---|---|---|---|
| D1 | Adopt a dead-code ledger with exact identities + reasons | `[ADOPT]` | Records the dead weight our rules forbid us to delete silently. Verify Python support in `scripts/audit.py` before depending on the action; prefer a vendored check. |
| D2 | Harden CI: per-job timeouts, top-level `permissions`, `persist-credentials: false`, concurrency | `[ADOPT]` | Pure defect fix; we have zero timeouts today. |
| D3 | Enforce D2 with a workflow-hygiene test | `[ADOPT]` | Their drift is the evidence that unenforced rules decay; extends `test/docs-claims.test.mjs`. |
| D4 | Adopt secret scanning | `[ADOPT]` | Enforces working-agreement rule 1; needs placeholder hygiene in `AGENTS.md`/`docs/**` first. (The dangling `scripts/strip-adapter-env.bash` clause in rule 1 was removed by owner decision, 2026-10-03 — nothing left to resolve there.) |
| D5 | Extend Dependabot to npm + pip with grouping and cooldown | `[ADOPT]` | Our `sdk/js`, `sdk/mcp` and Python deps are currently unmanaged. |
| D6 | Adopt a PR template with a mandatory verification section and consent block | `[ADOPT]` | Codifies rules 3/4 at the merge surface. Their issue-link requirement does **not** conflict with our commit-subject rule (PR body ≠ commit subject) — do not "fix" that later. |
| D7 | Adopt an issue template requiring lane + SDK + error/envelope code | `[ADOPT]` | Our diagnosis correlate is the lane; `fix_hint_code` is a first-class field. |
| D8 | Single credential/lane resolver per SDK + lane-matrix test | `[ADOPT]` | 16+ scattered `LETSFG_*` read sites; one drift already realised (MCP dev-lane booking posts to the retired route). |
| D9 | Positive-evidence rule for "no results" and capability flags | `[ADOPT]` | Same principle as P2.1; their issue-65 postmortem is the proof that absence-of-error is not evidence. |
| D10 | Publish the failure direction of every limiter | `[ADOPT]` | Fail-open vs fail-closed must be a decision, not an accident. |
| D11 | Document "semantics agents get wrong" in the API reference, with assertions | `[ADOPT]` | Move the knowledge from `AGENTS.md` into the reference docs and pin it. |
| D12 | CHANGELOG with provenance links and an `[Unreleased]` section | `[ADOPT]` | Diverges from theirs deliberately: `[Unreleased]` makes the version check possible. |
| D13 | Tag↔version binding and required-checks-for-this-SHA at release | `[ADOPT]` | Our docs-claims test covers manifest↔manifest only; the tag is unchecked. |
| D14 | Fresh-install + packaged-entry-point smoke tests; build identity | `[ADOPT-ADAPTED]` | `pip install` and `npx letsfg-mcp` are unverified claims in our README. |
| D15 | Per-module test markers replacing the blunt quarantine list | `[ADOPT-ADAPTED]` | Moot for the 18 parked modules: all tested code removed in `f91be5b` and were **deleted 2026-10-03**. The marker taxonomy is still the end state if parking is ever needed again; `pytest -m` is already better than `skipIf` flags. |
| D16 | 1000-line file cap + ≤10 files/dir pre-commit hooks | `[DEFER]` | Would require refactors forbidden by rule 4; owner decision. Grandfathering design noted. |
| D17 | Committed screenshot archive under `docs/` | `[REJECT]` | Zero inbound references; undiscoverable. Keep visual evidence in PR bodies. |
| D18 | Makefile | `[DEFER]` | Ergonomics only. |
| D19 | Depend on the third-party maintenance action | `[DEFER]` | Pin-by-SHA if adopted; verify Python coverage first; consider a vendored script. |

## 4. Open questions

1. Does `affromero/repo-maintenance`'s `scripts/audit.py` cover Python, or only
   JS/TS + Rust? Determines whether D1 is adoptable directly.
2. Which of our `LETSFG_*` read sites are load-bearing versus incidental? Needed
   to size D8 without breaking documented precedence (flag → env → saved config).
3. Does our quota limiter (10/10 min, 30/hour, 100/day) fail open or closed when
   the limiter is unavailable? D10 cannot be documented until that is known.
4. Should a "live" probe workflow (trvl plan P1.4) be scheduled with a
   `LETSFG_BEARER_TOKEN` secret, or should the live tests be deleted? Their
   inaction is the argument for deciding rather than drifting.
