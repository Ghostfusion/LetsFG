# Study — `affromero/flight-finder` (implementation)

Ordered work derived from `docs/flight-finder-study-design.md` (design pattern
P1–P19, decision register D1–D19). Every item states its **classification** under
the working agreement, because rule 4 forbids changes that are not defect fixes:

- **`DEFECT`** — a documented fact contradicts another documented fact, or a
  control is provably missing. Fix now.
- **`DEFECT-REVIEW`** — a defect whose fix is a visible process change. Fix on
  owner acknowledgement, not silently.
- **`OWNER`** — a policy/behaviour change; requires an explicit decision.
- **`DOCS`** — documentation only.

Verified current state of our repo is quoted throughout; commands are given so
each claim can be re-checked before and after.

## Phase P0 — CI hygiene defects (fix now, no owner decision)

### P0.1 Job timeouts, least privilege, credential persistence, concurrency `DEFECT`

**Evidence of the defect.** Verified with
`grep -n 'timeout-minutes' .github/workflows/*.yml` → **no matches**: not one of
our jobs has a wall-clock bound, so a hung job burns the default 6-hour budget.
`sdk-tests.yml` declares **no `permissions:`** (the other three do), so its
token is read/write by default. `persist-credentials: false` appears only in
`plugin-scanner.yml`.

**Change.**
1. Add `timeout-minutes` to **every** job: `test.yml` (TS packages 15, docs
   claims 5, coverage gate 5, Python deterministic 15 — adjust to the observed
   run times), `sdk-tests.yml` 10, `docs.yml` 10, `plugin-scanner.yml` 10.
2. `sdk-tests.yml`: add top-level `permissions: contents: read`.
3. `persist-credentials: false` on every checkout **except** `docs.yml`'s
   deploy job, whose `mkdocs gh-deploy --force` pushes with the checkout
   credential — setting it there would break the deploy. Note this exemption in
   a comment so a later sweep does not "fix" it.
4. `concurrency: {group: <workflow>-${{ github.ref }}, cancel-in-progress: true}`
   on `sdk-tests.yml`, `docs.yml` (deploy is a push-to-main job; keep
   `cancel-in-progress: false` for it, matching the publication rule in design
   P2) and `plugin-scanner.yml`. `test.yml` already has it.

**Acceptance.** A workflow-hygiene test fails before the change and passes after,
and asserts: every workflow declares `permissions:`; every job declares
`timeout-minutes`; every `uses:` matches `@[0-9a-f]{40}\s+#\s+v`; every checkout
outside `docs.yml` sets `persist-credentials: false`; every push/PR workflow
declares `concurrency`.

**Test.** New `test/workflow-hygiene.test.mjs` (zero dependencies, `node --test`)
— same shape as `test/docs-claims.test.mjs`, line-based parsing, one assertion
per rule, and an explicit exemption list (`docs.yml` deploy checkout) so an
exemption is a visible, reviewed line rather than a silent gap. Wire it into the
existing `docs-claims` CI job (or a sibling job in `test.yml`).

**Risk.** Line-based YAML parsing can mis-read a reformatted file. Mitigation:
keep the assertions about *presence within a job block*, mirror the existing
`docs-claims` style, and let the test fail loudly on a file it cannot parse.

**Verify.** `node --test test/workflow-hygiene.test.mjs` (must fail on the
current tree, pass after), plus `node --test test/docs-claims.test.mjs`,
`sdk/mcp npm test`, `pytest -m "not live"`.

---

### P0.2 `CHANGELOG.md` with provenance and an `[Unreleased]` section `DOCS`

Design P16/D12. This also completes the deferred half of the `trvl` plan's P1.3
(version↔changelog), which was blocked because the file did not exist.

**Change.** Create `CHANGELOG.md` following Keep a Changelog 1.1.0 with a
**deliberate divergence** from the probe: keep an `[Unreleased]` section, so the
version check has a stable anchor. Rules to state at the top of the file:
- one section per released version, `## [X.Y.Z] - YYYY-MM-DD`;
- `### Added` / `### Changed` / `### Fixed` / `### Upgrade notes`;
- **every bullet ends with provenance** — a commit SHA, a PR link, or both;
- entries state scope limits, not only wins;
- the top released version must equal the version in `sdk/mcp/package.json`,
  `sdk/js/package.json`, `sdk/python/pyproject.toml` and
  `sdk/python/letsfg/__init__.py`.

Initial content: backfill the quarantine, the OpenAPI double-prefix fix, the
typed MCP envelope, and the docs-claims test from this session plus the
user-visible history already in `docs/trvl-study-implementation.md`.

**Acceptance.** `CHANGELOG.md` exists with an `[Unreleased]` section; the
docs-claims test asserts version↔changelog equality for the newest *released*
section (and skips that assertion while only `[Unreleased]` exists, so the test
is green from day one).

**Blocked part.** Design D13 (tag↔version binding) has **no prerequisite yet**:
`git tag | wc -l` → **0**. There is no tagging convention in this repo, so a
release-tag assertion cannot be written. Record the convention as an owner
decision (Phase P3) rather than inventing one.

---

### P0.3 `sdk-tests.yml` is a narrower duplicate of a `test.yml` job `DEFECT-REVIEW`

**Evidence.** `.github/workflows/sdk-tests.yml` installs
`pip install pydantic pytest && pip install -e . --no-deps` and runs a single
module: `python3 -m pytest tests/test_public_offer_masking.py -v`. Its comments
justify the narrow selection by *"Connector tests require playwright browser
binaries and curl_cffi C extensions that are not available in standard GitHub
Actions runners"* — **stale**: the connector modules were removed in `f91be5b`,
the 19 stale modules are now parked in `sdk/python/conftest.py`, and
`test.yml`'s **"Python deterministic tests (Tier-1, required)"** job already
runs the full `pytest -m "not live"` (99 passed, verified locally).

**Change.** Either fold the masking run into `test.yml` (it is a subset of the
job that already runs) and delete `sdk-tests.yml`, or narrow `sdk-tests.yml` to
a real purpose and correct its comments. Recommendation: **delete**, because a
required job that asserts less than another required job implies coverage that
does not exist — the same failure mode as the probe's never-run live tests.

**Acceptance.** Exactly one workflow runs the Python suite; its comments match
reality; the masking module still runs in CI.

**Why `DEFECT-REVIEW`.** Removing a workflow changes the checks list on every
open PR. Owner acknowledgement, then it is a one-file deletion.

---

## Phase P1 — defect-class code work

### P1.1 One credential/lane resolver per SDK, pinned by a lane-matrix test `DEFECT`

Design P9/D8.

**Evidence.** `LETSFG_*` is read at **16+ sites** with no shared resolver: MCP
`sdk/mcp/src/index.ts:35,36,37,84,105`; JS `sdk/js/src/auth.ts:32,96,118`,
`cli.ts:34`, `index.ts:354-355`; Python `cli.py:61,70,634,820,900,933`,
`client.py:346,352`, `local.py:26,55`, `connectors/auth.py:51`. The cost is
already realised: the MCP server's Developer-API booking path posts to the
**retired** `/developers/api/v1/bookings/book` while the JS SDK uses
`/flights/book` (`AGENTS.md` states the retired route answers `410 Gone`).

**Change.**
1. One `resolveCredentials()` per SDK (Python, JS, MCP) implementing the
   documented precedence **flag → env → saved config**, with the lane choice
   (`bearer` vs `api_key`) derived in exactly one place.
2. Fix the MCP Developer-API booking path to `/developers/api/v1/flights/book`.
3. Add a **lane-matrix regression test in each SDK** asserting the literal
   request paths per lane: PFS search `/api/search`, PFS booking
   `/api/agent-book`, Dev API search `/developers/api/v1/flights/search`,
   Dev API booking `/developers/api/v1/flights/book`. Each SDK tests its own
   table (cross-language sharing is not worth the plumbing).

**Acceptance.** The regression test fails on the current MCP build and passes
after; no SDK reads `LETSFG_*` outside its resolver (assert with a grep-based
test per package, like the existing "dead-route guards" in
`sdk/mcp/src/index.test.ts`).

**Verify.** `cd sdk/mcp && npm test`; `cd sdk/js && npm test` (needs
`npm ci`); `pytest -m "not live"`.

---

### P1.2 Envelope conformance across the advertised tools `DEFECT`

Design P7/D3-adjacent. We wrap 4 of 14 advertised MCP tools
(`search_flights`, `search_hotels`, `get_flight_booking`, `get_hotel_booking`).

**Change.** Extend the MCP tool tests to assert that every tool returning
search/booking *data* carries `status` + `completeness`, with an explicit,
reviewed exempt list for tools that genuinely return no data payload
(`authenticate`, `load_resources`, `get_agent_profile`, `resolve_location`,
`resolve_hotel_city`). Then wrap whatever the test proves is missing
(`book_flight`, `book_hotel`, `cancel_hotel_booking`).

**Acceptance.** The conformance test enumerates the 14 advertised tools, asserts
each is either wrapped or on the exempt list, and fails if a new tool is added
without a decision. This is the "measured adoption ratio" of design P7, applied
to us: their 71/75 is the model, our 4/14 is the gap.

**Verify.** `cd sdk/mcp && npx tsc --noEmit && npm test`.

---

### P1.3 Determine the limiter failure contract `OWNER` → **decided: measure, do not guess**

Design P8/D10. **Resolved as a decision on 2026-10-03 (trvl study §6, Q8):** this
is a **measurement task**, not a documentation tidy-up, and it is the **P0 blocker**
for the fare scanner's scheduler — "the Search Scheduler cannot be finalised until
the rate-limit failure contract is established by experiment."

**Evidence.** Our docs publish quotas (10/10 min, 30/hour, 100/day) but never say
what happens when they are crossed; the probe makes failure direction an explicit,
per-endpoint decision (fail-closed on `community/register`, fail-open on `parse`).

**Change.** Run a controlled probe against the live API and record: the status
returned on crossing the limit; whether `Retry-After` is supplied; **whether a
rejected request still consumes quota**; the limit's scope (key/account/IP/endpoint);
whether concurrency counts separately; behaviour after repeated violations;
consistency across `/flights/search`, `/flights/discover`, `/flights/multi-search`;
and whether polling `/api/results/{id}` draws on the search quota or a separate
request quota. Then document the measured contract in `docs/api-*.md` +
`AGENTS.md`, and state the intended direction per operation. Until it is measured,
`rate_limit_failure_behavior = UNKNOWN` and no scheduler logic may assume one.

**Acceptance.** Each rate-limited operation in the docs names its measured failure
behaviour, and the scanner's scheduler encodes only measured behaviour. Full
question list and probe recipe: trvl study §6 (Q8).

---

## Phase P2 — process adoption (owner decisions)

### P2.1 PR and issue templates `OWNER`

Design P5/P6, D6/D7. Our repo has **neither** (verified absent:
`.github/PULL_REQUEST_TEMPLATE.md`, `.github/ISSUE_TEMPLATE/`).

**Proposed content, adapted:**
- **Verification** section demanding pasted commands **and observed results**,
  the exact suites run, and any check not run with the reason — this is our
  working agreement's evidence rule at the merge surface, and it is exactly what
  `docs/working-agreement.md` rule 3 already requires informally.
- **Scope and remaining work**, with *"keep incomplete PRs in draft"*.
- **Review-assistance consent** (3 options + *"if blank or conflicting, ask
  first"*), matching the probe's wording that an inspection-only request does
  not authorize creating issues/PRs.
- **Bug template fields for our diagnosis correlates**: lane (PFS vs Developer
  API), SDK + version (`letsfg` Python / `letsfg` JS / `letsfg-mcp`), the
  `error_code`/`fix_hint_code` from the result envelope, and the raw response.
- **Stated non-conflict**: the probe's template requires an issue link; our
  working agreement rule 2 forbids issue/PR numbers in the **commit subject**.
  PR body ≠ commit subject; write that into the template so neither rule is
  "corrected" later.

**Acceptance.** Templates exist; a bug report submitted through them contains
lane, SDK version and the envelope code without a follow-up round trip.

### P2.2 Dependabot for npm and pip `OWNER`

Design P4/D5. Verified: `.github/dependabot.yml` currently contains a **single**
`updates:` entry (ecosystem `github-actions`, weekly, `commit-message.prefix:
ci`). `sdk/js/package-lock.json` and `sdk/mcp/package-lock.json` exist, and
`sdk/python` is a published PyPI package — all three are unmanaged.

**Change.** Add ecosystems `npm` (directories `/sdk/js`, `/sdk/mcp`) and `pip`
(`/sdk/python`), each `weekly`, with `groups` (one PR per ecosystem) and
`open-pull-requests-limit`. Copy the probe's `cooldown: default-days: 7` **only
after confirming our Dependabot version honours the key** — if unsupported it is
silently ignored, which is worse than omitting it.

**Acceptance.** A grouped dependency PR appears for each ecosystem; the
SHA-pinned actions keep updating via the existing entry (regression check: the
`github-actions` entry is unchanged apart from any grouping).

### P2.3 Maintenance ledger for known dead weight `OWNER`

Design P1/P2/§2, D1/D19. Verified: our repo has no `knip.json`, no
`.maintenance-exceptions.json`, no dead-code workflow, and no pre-commit config.
Our working agreement forbids silently deleting code we did not write, which
makes a *ledger* the right instrument.

**Change.** Add a ledger at the probe's shape — each entry carrying an exact
identity (`tool`, `mode`, `file`, `kind`, `name`) **and a review reason** — and
populate it from the already-documented findings in
`docs/repository-familiarization.md`: `sdk/python/letsfg/config.py`,
`sdk/python/letsfg/system_info.py`, `sdk/python/letsfg/models.py` (shadowed by
`models/`), and the Playwright installs in `Dockerfile`, `Dockerfile.python`,
`docker-compose.yml` for removed local connectors.

**Prerequisite before choosing the checker.** Determine whether
`affromero/repo-maintenance`'s `scripts/audit.py` covers **Python**; the action
as read performs `knip` (JS/TS) and `cargo-machete` (Rust) and its Python
coverage is unverified. If it does not, use a repo-local script over
`vulture`/`pyflakes` + `knip` and reuse the ledger format. Either way the ledger
is ours and the entries must fail the build once they go stale.

**Acceptance.** The check runs on PR + weekly, rejects new findings and stale
exceptions, and every current entry has a reason a reviewer can accept.

### P2.4 Architecture hooks (file/dir size) `OWNER — recommend defer`

Design P15/D16. Our measured state under the probe's 1000-line rule:
`Panel.qml` 4757, `Model.js` 2684, `sdk/js/src/ranking.ts` 1446,
`test/model-test.js` 1294, `sdk/python/letsfg/client.py` 1294,
`sdk/mcp/src/index.ts` 1291, `sdk/python/letsfg/cli.py` 1103,
`sdk/js/src/index.ts` 1091.

**Recommendation.** Do **not** adopt as a gate now: it would require refactors,
which rule 4 forbids without an owner request. If adopted later, copy the
**grandfathering** design exactly (only *added/untracked* files are counted, so
existing directories keep working) and land it warn-only first.

**Acceptance (if adopted).** The hook fails only on additions that exceed the
cap; no existing file is touched by adoption.

## Phase P3 — release integrity

### P3.1 Tagging convention and tag↔version binding `OWNER`

Design P13/D13. Verified: `git tag | wc -l` → **0**. There are no tags and no
release workflow, so the probe's strongest integrity check
(`Release tag does not match package version`, `Release tag moved after
verification`) has nothing to bind to yet.

**Change.** Decide the convention (`vX.Y.Z`), then extend the docs-claims test:
when `HEAD` is exactly tagged, the tag must equal the manifest versions;
publication must additionally require the required checks to have passed **for
that SHA** (the probe's `requireChecks` shape).

**Acceptance.** An intentionally wrong tag fails the test locally.

**Blocked.** No decision on tagging in this repo → owner input required. Do not
invent a convention.

### P3.2 Install and packaged-entry-point smoke tests `OWNER`

Design P14/D14. Our README asserts `pip install -U letsfg` "gives you the
`letsfg` CLI command" and that `npx letsfg-mcp` runs a local MCP server;
**nothing verifies either**.

**Change.** Two CI jobs:
1. **Python**: build a wheel, install it into a **fresh venv**, and run
   `letsfg --version` + `letsfg locations "New York" --json` (no network needed
   for the latter if it resolves locally; otherwise use
   `LETSFG_BASE_URL` against a stub).
2. **MCP**: spawn the packaged entry point and complete an `initialize` +
   `tools/list` handshake — the same technique as `sdk/mcp/src/envelope.test.ts`,
   which spawns the server and drives it over stdio, but here against the
   *published* artifact rather than `src/`.

**Best idea from the probe to copy here:** verify an **upgrade**, not only a
fresh install (`installer-browser.mjs --update --legacy-installer=<sha>`). Our
equivalent: install a pinned previous version from PyPI, then upgrade to the
built wheel, and assert the config file survives — which also exercises design
P3.1 of the `trvl` study (atomic config writes).

**Acceptance.** Both jobs fail if the CLI entry point or the MCP handshake
breaks in a packaged install.

### P3.3 Build identity in the artifact `OWNER (defer until a publish pipeline exists)`

Design P14. The probe ships the commit SHA in `fairtrail version` and
`/api/version`. Our MCP `initialize` returns a version but no build identity, so
"which build is this?" is unanswerable from an issue report.

**Change.** Inject the commit SHA at publish time and expose it in
`initialize.serverInfo` (+ the Python/JS CLIs' `--version`). **Deferred** while
publishing is manual: a build identity that is always `dev` adds noise.

## Phase P4 — explicitly not doing

| Item | Design ref | Why not |
|---|---|---|
| Committed screenshot archive under `docs/` | P19 / D17 | Zero inbound references in the probe; undiscoverable. Visual evidence belongs in the PR body (P2.1). |
| `Makefile` | P18 / D18 | Ergonomics only; rule 4 excludes it. |
| 1000-line / 10-files-per-dir gate | P15 / D16 | Needs refactors; owner decision recorded in P2.4. |
| Depending on the third-party maintenance action | P1 / D19 | Pin-by-SHA if adopted; verify Python coverage first (P2.3 prerequisite). |
| Their monorepo scale (Postgres/Redis/Tauri, 232 test files) | §2.6 | Different product shape; nothing to copy. |

## Traceability

| Design pattern | Decision | Implementation |
|---|---|---|
| P1 dead-code ledger | D1/D19 | P2.3 |
| P2 workflow hardening | D2/D3 | P0.1 |
| P3 secret scanning | D4 | P2.3 prerequisite discussion; not yet scheduled (needs placeholder hygiene in `AGENTS.md`/`docs/**` — see design P3) |
| P4 Dependabot policy | D5 | P2.2 |
| P5 PR template | D6 | P2.1 |
| P6 issue template | D7 | P2.1 |
| P7 one envelope, measured | — | P1.2 |
| P8 failure direction | D10 | P1.3 |
| P9 credential resolver | D8 | P1.1 |
| P10 positive-evidence flags | D9 | P1.2 (same principle; already implemented for the 4 wrapped tools) |
| P11 evidence grade for fares | — | Deferred; recorded in the design doc, not scheduled |
| P12 test taxonomy | D15 | P2.4 / quarantine burn-down (already tracked in the `trvl` plan) |
| P13 verify the process | D3/D13 | P0.1 (test), P3.1 (tag binding) |
| P14 publication integrity | D13/D14 | P0.2 (blocked part), P3.1, P3.2, P3.3 |
| P16 CHANGELOG | D12 | P0.2 |
| P17 semantics agents get wrong | D11 | P1.3 documentation half + a docs assertion |
| P19 screenshot archive | D17 | Rejected (P4) |

## Resolution log — 2026-10-03

Every `DEFECT` / `DEFECT-REVIEW` item in this document was fixed in one pass,
under working-agreement rule 3 (fix on the spot; record genuine deferrals).

| Item | Status | Evidence |
|---|---|---|
| P0.1 CI hygiene | **Done** | `timeout-minutes` on all 9 jobs across the 3 remaining workflows; `permissions: contents: read` added where missing; `persist-credentials: false` everywhere except `docs.yml`'s publishing checkout (commented); `concurrency` added, `cancel-in-progress: false` for the deploy. Guard: `test/workflow-hygiene.test.mjs` (5 rules; red on 4 before the fix). |
| P0.3 duplicate Python job | **Done** | `.github/workflows/sdk-tests.yml` deleted: it ran one module (`test_public_offer_masking.py`) that `test.yml`'s required `python-deterministic` job already runs as part of `pytest -m "not live"` (32 collected), and its comments about missing connector deps were stale. |
| P1.1 lane routes + credential resolver | **Partly done** | The functional defect is fixed: MCP `book_flight` (API-key lane) now posts to `/developers/api/v1/flights/book`, and `get_flight_booking` polls `/developers/api/v1/flights/bookings/{id}` on that lane instead of the PFS route. Both pinned by a new test that records the paths the server actually calls. The single-resolver refactor of all 16 `LETSFG_*` read sites is **not** done — it is structural, and the drift it prevents is now covered by the route tests instead. |
| P1.2 envelope conformance | **Done** | 11 of 14 advertised tools return `status` + `completeness` on API results; the 3 exceptions (`connect_payment`, `get_agent_profile` on the PFS lane, `load_resources`) are local refusals/static text and are asserted as such. The test enumerates `tools/list`, so a new tool must be classified. |
| P1.3 limiter failure direction | **Decided 2026-10-03 — measure, don't guess** | Owner decision (trvl study §6, Q8): the failure contract must be established by a controlled live probe (status, `Retry-After`, whether a rejected request consumes quota, limit scope, concurrency, repeat violations, per-endpoint consistency, polling quota). It is the **P0 blocker** for the fare scanner's scheduler; until measured, `rate_limit_failure_behavior = UNKNOWN`. |
| P0.2 `CHANGELOG.md` | **Not done** | Not a defect (absence of a file), and rule 4 scopes this pass to defect fixes. It remains the top documentation item, and it unblocks the deferred version↔changelog assertion. |
| Further defects fixed this pass (found while working) | **Done** | `sdk/python/letsfg/models.py` (unreachable behind the `models/` package) and `system_info.py` (stub for a removed architecture) deleted; `config.py` made the live resolver; MCP `VERSION` corrected from `1.3.1` to the package version and now asserted; committed `*.tgz` build artifacts deleted and `*.tgz` ignored; `sdk/python/letsfg/models/flights.py` docstring route corrected; `SECURITY.md` version table + credential model corrected and asserted; `docs/TESTING.md` rewritten around what actually runs here; `CONTRIBUTING.md` commands corrected; Playwright and its system libraries removed from all three container files with the dead knobs. |

**Still open, deliberately:** P2.1–P2.4 and P3.1–P3.3 are `OWNER` items (process
policy, tagging convention, publishing); P3.1's tag binding has no prerequisite
yet (`git tag | wc -l` → 0).

```bash
node --test test/docs-claims.test.mjs test/workflow-hygiene.test.mjs   # 9/9, 5/5
cd sdk/mcp && npx tsc --noEmit && npm test                            # 45/45
cd sdk/js  && npx tsc --noEmit && npm test                            # 39/39
cd sdk/python && pytest -m "not live"                                 # 103 passed, 2 skipped
```


## Suggested order (remaining work)

1. **P0.2** — `CHANGELOG.md` (unblocks the deferred `trvl` P1.3 assertion; the
   format is specified in the design study P16).
2. **P1.1 (rest)** — collapse the 16 `LETSFG_*` read sites into one resolver per
   SDK. Structural, so it needs an owner nod; the drift it would prevent is
   already covered by the lane-route tests.
3. **P1.3** — establish the limiter's failure contract by measurement (the Q8 probe),
   then document it. Nothing downstream may assume it beforehand.
4. Then the `OWNER` items in the order the owner prefers; **P2.1** (PR/issue
   templates) and **P2.2** (Dependabot ecosystems) are the cheapest process wins,
   and **D4** (secret scanning) needs the placeholder-hygiene sweep first.

## Verification for the whole set

```bash
node --test test/docs-claims.test.mjs test/workflow-hygiene.test.mjs
cd sdk/mcp && npx tsc --noEmit && npm test
cd ../js && npm test            # needs npm ci
cd ../../sdk/python && pytest -m "not live"
```

Each phase above lists its own acceptance criteria; a phase is done when its
test fails before the change and passes after, and the four suites above are
green.
