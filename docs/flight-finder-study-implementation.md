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
- **`SPEC`** — the output is a written answer, before any code exists.

**Scope.** The design study separates the **integration surface** (design §0.1:
search semantics, evidence, price representation, provider abstraction, API
contracts, agent behaviour, reliability, booking correctness, observability) from
**repository/process work** found along the way. Only the first is what studying
`flight-finder` is for; the second stands on its own defects. The register's Type
column says which is which, and the integration acceptance bar below is written
against the first group only.

**This study does not itself authorize implementation.** Findings that are
existing LetsFG defects may be fixed under working-agreement rule 3, and are
marked `DEFECT`. Everything else — a resolver, an evidence model, a documentation
contract, a process adoption — needs the owner decision recorded here, per rule 4.

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

**The invariant.** Every **public client surface** — Python SDK, JS SDK, MCP —
resolves its **lane, base URL, API namespace, authentication scheme and
credential** through one canonical resolver. No operation-level code may derive
any of those values itself: it asks the resolver and receives them already
chosen. Precedence stays `explicit argument → environment → saved config`; what
changes is that it is written once per surface instead of re-derived per call
site.

**Scope (Q2, answered 2026-10-04).** The resolver owns **only** the reads that
determine authentication (class A) or lane selection (class B). Class C — runtime
and provider configuration such as a base-URL override, wait-for-split, or the
user agent — is **not** part of D8; class D (tests, docs, fixtures) is excluded
from the runtime resolver entirely. A lane resolver that swallows class C becomes
a settings hub, which is what this boundary prevents.

**Not a request router.** `resolve_lane()` returns lane identity;
`build_request(operation, lane)` decides method and path. Endpoint knowledge stays
with the operation, or the resolver couples to every route in the API.

**Evidence (measured 2026-10-03, counts not line numbers — the lines move).**
`LETSFG_*` is read at **28 sites** with no shared resolver: **5** in the MCP
server, **8** in the JS SDK (10 matches, 2 in doc comments), **15** in the Python
SDK (17 matches, 2 in docstrings). The lane matrix the resolver owns — one row per
surface × lane, with the credential each resolves — is in design P9.

**The drift has already happened once, and it is fixed.** The MCP server's
Developer-API booking path posted to the **retired**
`/developers/api/v1/bookings/book` (410 since 2026-09-08) while the JS SDK used
`/flights/book`. **Fixed 2026-10-03** with route tests — and that is the argument
for this item, not against it: the drift was caught by a test written for *that
route*, not by anything structural, and the next route will not have one.

**Change.**
1. One lane resolver per **public client surface** (Python SDK, JS SDK, MCP)
   implementing the **shared contract**, returning a `ResolvedLane` — `lane`,
   `base_url`, `api_namespace`, `auth_scheme`, `credential` — plus the precedence
   above. The contract is shared; the runtime code is not.
2. Route every authentication/lane read (classes A + B) through it; no operation
   reads those from the environment or hard-codes a lane's path.
3. Enforce **credential/lane compatibility**: a bearer credential on the PFS lane,
   an API key on the Developer lane, and neither offered to the other. This is the
   cross-lane drift that actually happened, so it is asserted, not assumed.
4. Keep the existing lane-route tests (they are the acceptance evidence) and add
   the per-surface **lane-matrix** assertion: for each lane, the literal paths the
   surface issues for search and booking.

**Acceptance.** The read-site table exists — one row per `LETSFG_*` read
(surface · runtime? · class A/B/C/D · resolver?) — and a per-package test asserts
that **no class A or class B read happens outside the resolver**. Class C reads are
permitted, and are *listed* rather than removed; an assertion that every
`LETSFG_*` read disappears would force the settings hub this item exists to avoid.
The lane matrix is complete for all three surfaces (six rows), and each row's
literal paths are asserted in its own package.

**Verify.** `cd sdk/mcp && npx tsc --noEmit && npm test`; `cd sdk/js && npx tsc
--noEmit && npm test`; `cd sdk/python && pytest -m "not live"`.

**Status.** The functional defect is fixed; the structural half is **not done**
and needs an owner nod (rule 4 — it is a refactor, not a defect fix).

---

### P1.2 Envelope conformance across the advertised tools `DEFECT` — **done 2026-10-03**

Design P7, D9-adjacent.

**This item used to say "we wrap 4 of 14 advertised MCP tools". That was stale.**
The envelope now wraps **11 of the 14**; the other three (`connect_payment`,
`get_agent_profile`, `load_resources`) are exempt because they answer from local
data, where an API-outcome status would be a lie.

**Change (landed).** `sdk/mcp/src/envelope.test.ts` enumerates `tools/list` and
asserts:
- every advertised tool is classified as enveloped or exempt — a new tool fails
  until someone decides which it is;
- every classified name is still advertised — deleting a tool fails until the
  entry goes with it;
- each enveloped tool returns `status` + `completeness` from the vocabulary, and
  `structuredContent` deep-equal to the text block;
- each exempt tool answers locally and carries **no** API-outcome status.

**Acceptance.** Met. The metric is deliberately structural, not the measured
adoption ratio the design first proposed: a ratio goes stale with every new tool,
whereas "every advertised tool is accounted for" cannot.

**Verify.** `cd sdk/mcp && npx tsc --noEmit && npm test` → 67/67.

---

### P1.3 Determine the limiter failure contract `OWNER` → **decided: measure, do not guess**

Design P8/D10. **Resolved as a decision on 2026-10-03 (trvl study §6, Q8):** this
is a **measurement task**, not a documentation tidy-up, and it is the **P0 blocker**
for the fare scanner's scheduler — "the Search Scheduler cannot be finalised until
the rate-limit failure contract is established by experiment."

**Evidence.** Our docs publish quotas (10/10 min, 30/hour, 100/day) but never say
what happens when they are crossed; the probe makes failure direction an explicit,
per-endpoint decision (fail-closed on `community/register`, fail-open on `parse`).

**Change — the six fields D10 requires, per limiter.** Run a controlled probe
against the live API and record, for each limiter:

1. **scope/key** — per bearer token? per API key? per IP? per user? per route?
   global?
2. **operation** — which calls it governs;
3. **counting event** — arrival, success, failure, provider call, or only
   expensive operations. (A rejected request may or may not spend quota, and this
   is the field most often assumed rather than measured);
4. **limit/window** — the number, and whether the window is fixed or sliding;
5. **failure direction** — what happens when the counter store is unavailable:
   allow or deny. **Do not assume one policy across all limiters**;
6. **retry/response** — status, error code, `Retry-After`, and any reset
   information.

Plus the surrounding behaviour: whether concurrency counts separately, behaviour
after repeated violations, consistency across `/flights/search`,
`/flights/discover` and `/flights/multi-search`, and whether polling
`/api/results/{id}` draws on the search quota or a separate request quota. Then
document the measured contract in `docs/api-*.md` + `AGENTS.md`. Until it is
measured, `rate_limit_failure_behavior = UNKNOWN` and no scheduler logic may
assume one.

**Acceptance.** Each rate-limited operation in the docs names its measured failure
behaviour, and the scanner's scheduler encodes only measured behaviour. Full
question list and probe recipe: trvl study §6 (Q8).

---

### P1.4 The offer evidence ladder, on the existing contract `SPEC` `OWNER`

Design P11/D11. **Promoted by the 2026-10-03 review from "deferred schema idea" to
a Tier-1 integration requirement**, because it is the one finding in this study
that changes what a price *means*.

**The invariant.** Three axes that are never read for each other:

```
search outcome     results | partial | no_results | timeout | rate_limited | failed
price evidence     indicative | observed | verified | stale | unavailable
booking state      search_result | quoted | held | ticketed | confirmed
```

**`price evidence` is our existing `price_status`, not a superset** (Q5, answered
2026-10-04). No `price_evidence` / `evidence_grade` / `quote_status` field is
introduced: the peer semantics map onto the field we already have, and the
decidable question is whether `price_status` + the verification outcome fully
express them. If they do, **this item adds no schema at all** — only the
no-promotion policy below.

with the rules: **a degraded or partial retrieval cannot *promote* an offer** (a
partial search may legitimately carry a genuine `observed` price — what it may not
do is imply complete coverage or raise a grade merely because something came
back); a price that was merely advertised cannot be presented as verified;
`freshness=live` requires a live fetch; `price_status=verified` requires a
verification outcome; and **a booking state may never be read back as evidence
about a price** — a hold is not a better quote, and a ticket is not a better
observation. An earlier draft folded `held`/`ticketed` (and then `quoted`) into
the price ladder, which is exactly the conflation this item exists to prevent.

**The constraint that decides the design.** This **extends the offer we already
return; it does not model a second price**. No new field may duplicate
`price_status`, and nothing may reach `verified` without a verification result.
The envelope's existing `status`/`completeness`/`coverage_mode`/`empty_reason`/
`freshness` and the provider contract's `price_status`/`VerificationResult` are
the vocabulary; this item adds the per-offer grade on top, it does not add an
enum.

**Change.**
1. Answer design §4 **Q5** first — which existing offer field carries the grade,
   and which it must not duplicate — and record the answer here before writing
   code. This is a specification step, not a coding step.
2. Add the grade to the offer in `sdk/python/letsfg/models/flights.py` and the JS
   equivalent, populated from what the lane actually observed.
3. Assert the two prohibitions: no promotion without a verification result, and no
   `freshness=live` without a live fetch.

**Acceptance.** A search result cannot be represented as a quoted price, a quoted
price cannot be represented as verified, and the tests fail if either promotion
becomes possible. `docs/api-search.md` names the grade and what it does not mean
(design P17).

**Risk.** This touches the public offer shape on all three surfaces. It is the
item most likely to accidentally create the parallel price model the study
forbids, which is why step 1 is a written answer rather than an implementation.

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

**Prerequisite — resolved 2026-10-04 (design Q1).** `affromero/repo-maintenance`'s
`scripts/audit.py` **does** cover Python: it runs `vulture` (exact pin, via
`uvx`) at `--min-confidence 80` over every tracked `*.py`, resolving each hit to
its deepest enclosing scope with `ast`, and it needs an installed `knip` plus a
tracked `knip.json` for the JS/TS half. Both halves engage for us. So the
prerequisite no longer decides *format* — it decides **whether to take on the
toolchain**, which is the owner's call (rule 4). If the toolchain is declined,
copy the ledger *format* (exact identity + review reason + stale-rejection) into
a repo-local script over `vulture` + `knip`; that format is the valuable part.
Either way the ledger is ours, and entries must fail the build once they go stale.

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
| Depending on the third-party maintenance action | P1 / D19 | Pin-by-SHA if adopted; Python coverage **verified** 2026-10-04 (design Q1) — the remaining gate is the owner's, not the evidence's. |
| Their monorepo scale (Postgres/Redis/Tauri, 232 test files) | §2.6 | Different product shape; nothing to copy. |

## Integration acceptance bar

The review of 2026-10-03 asked for an explicit bar rather than "implement the
adopted patterns". This is the bar for the **integration surface** (design §0.1) —
the repository/process items are their own defects and are judged by their own
tests. Nothing below is claimed as done unless its line says so.

**1. Lane resolution** (D8, P1.1) — *partly done*
- [x] no operation posts a retired route; the drift that happened is fixed and
      pinned by route tests (`envelope.test.ts`)
- [ ] no operation reads `LETSFG_*` outside one resolver per SDK
- [ ] PFS and Developer lanes are tested on all three surfaces, against the
      six-row lane matrix

**2. Evidence** (D11, P1.4) — *mapping decided (Q5); implementation not started*
- [ ] the three axes are distinct, and booking state is not folded into price
      evidence
- [ ] `price_status` + the verification outcome are shown to express the
      peer-derived semantics — **or the exact remaining gap is named**
- [ ] no new field is introduced: `price_evidence` / `evidence_grade` /
      `quote_status` are refused
- [ ] a degraded or partial retrieval cannot *promote* an offer (while a partial
      search may still carry a genuine `observed` price)
- [ ] a stale observation cannot masquerade as fresh price evidence
- [ ] the grade is populated from what the lane actually observed, not defaulted

**3. Search outcomes** (D9, P1.2) — *implemented for MCP; vocabulary decided (Q6);
Python/JS audited 2026-10-04*
- [x] `no_results`, `partial`, `timeout`, `failed`, `rate_limited` are distinct,
      and `no_results` requires a present-and-empty list with legal coverage
- [ ] the same discrimination on the Python and JS SDK surfaces
- [ ] the Q6 additions are represented in the **one** vocabulary, not a second
      enum: `unsupported` (the agent can route elsewhere) and `auth_failed`
      (operational, not market information)
- [ ] provider detail rides *alongside* the status (`provider`, `provider_code`,
      `retry_after_seconds`) and is never the machine contract
- [ ] `empty_reason` still explains only an empty result set — it has not grown
      into a second error taxonomy

**Verified 2026-10-04 — every error-construction site in both SDKs was audited,
and five defects were fixed** (`e2f6f31`, `2b7ab79`, `174da9d`, `646bd61`,
`82b889c`):

| Defect | Was | Now |
|---|---|---|
| Python free lane, exhausted poll | returned `{"offers": [], "total_results": 0}` — a 3-minute timeout indistinguishable from a genuine empty, printed as *"No flights found"* with exit 0 | raises `LetsFGError(504, SUPPLIER_TIMEOUT)` carrying the `search_id` |
| JS PFS poll timeout | thrown with no `errorCode` → `business`, `isRetryable: false`; message said poll `/api/results/<id>` | `SUPPLIER_TIMEOUT`, transient, real `search_id` |
| Python `AuthenticationError` (`_require_api_key`) | `error_code == ""` | defaults to `AUTH_INVALID` (JS already did) |
| Python free-lane `BearerTokenError`; both `register` handlers | no field at all / no code | fields added; `register` reuses the seam's inference |
| Missing required argument (4 sites) | Python raised a bare `ValueError` — outside the taxonomy, so `except LetsFGError` never caught it; JS threw code-less | `ValidationError`/`MISSING_PARAMETER`, the code the docs already name for it |

**What that leaves, and precisely why each needs a decision, not engineering:**

1. **`partial` / degraded coverage — absent on both SDKs.** Needs the Q6
   vocabulary (a status *and* a completeness notion), so it is the same decision
   the envelope already made for MCP.
2. **`auth_required` vs `auth_failed`** — the design's own "one name, chosen
   once", spanning three surfaces. MCP ships `auth_required`; the study proposes
   `auth_failed`. Nothing here can pick for you.
3. **`retry_after`** — neither SDK reads response headers at all (they discard
   them); only MCP parses `Retry-After`. Adding it is new behaviour.
4. **Five retired-endpoint refusals** (Python `unlock`/`setup_payment`/
   `start_checkout`, JS `unlock`/`setupPayment`) — **none of the 18 existing codes
   is honest for "this method was retired"**. `_infer_error_code(410, …)` would
   return `OFFER_EXPIRED`, but a retired *route* is not an expired *offer*;
   reusing it would be a lie, and inventing a name is the "chosen once" decision
   above. Python already reports `status_code: 0` for these (a request is never
   made); JS reports `410`, which attributes to an exchange that did not happen —
   the same conflation `withRefusal` avoids in the MCP envelope.

**4. Agent contract** (D11/P17) — *not scheduled*
- [ ] every agent-facing operation documents the dimensions **that apply to it**:
      meaning, what it does **not** mean, freshness, retry, idempotency, quota,
      authority, actionability, repeatability
- [ ] the booking ladder is stated where booking is documented
- [ ] at least the load-bearing ones are asserted, not just written

**5. Distribution** (D14/P3.2) — *not scheduled*
- [ ] **hermetic** install smoke, no credential and no provider: fresh-venv
      `pip install` starts the CLI; fresh `npm install` imports; `npx letsfg-mcp`
      completes `initialize` → `tools/list`
- [ ] **gated** live smoke, `LETSFG_BEARER_TOKEN` present: one real search per
      artifact
- [ ] upgrade from a pinned older release, not just a fresh install

**6. Build identity** (D14, chained to D13) — *not scheduled*
- [ ] the packaged artifact reports its version **and the 40-hex commit it was
      built from**, and the SHA identifies that exact revision
- [ ] that SHA is the one a release tag binds to (D13), so the chain
      tag → version → commit → required checks → artifact is closed end to end

**7. Live canary** (Q4, answered 2026-10-04) — *decided; the test does not exist yet*
- [x] decision: keep the live lane and make it an **opt-in canary**, not a
      deletion and not a dormancy
- [ ] a **LetsFG-contract live test** is written — today the live lane holds only
      the SerpApi provider probe (`test_serpapi_live.py`, 7 tests), so a workflow
      running `pytest -m live` would be a SerpApi quota probe wearing a canary's
      name
- [ ] the workflow is manually dispatchable, optionally scheduled, skips cleanly
      with no `LETSFG_BEARER_TOKEN`, never runs on fork PRs, and never logs the
      secret
- [ ] bounded scope: one representative search, no booking, no mutation, explicit
      timeout

**Not part of this bar:** CI hardening, Dependabot, PR/issue templates, the
changelog, the dead-code ledger, file-size caps, the Makefile or the screenshot
archive. Those are repository work (design §0.1) with their own acceptance.

## Traceability

| Design pattern | Decision | Implementation |
|---|---|---|
| P1 dead-code ledger | D1/D19 | P2.3 |
| P2 workflow hardening | D2/D3 | P0.1 |
| P3 secret scanning | D4 | Placeholder hygiene **verified 2026-10-04** (design P3): one credential-shaped example fixed, none left. The scanner itself is not yet scheduled — an owner decision. |
| P4 Dependabot policy | D5 | P2.2 |
| P5 PR template | D6 | P2.1 |
| P6 issue template | D7 | P2.1 |
| P7 one envelope, measured | — | P1.2 — **done**: the invariant is structural (every advertised tool classified), not a ratio |
| P8 failure direction | D10 | P1.3 |
| P9 credential resolver | D8 | P1.1 (drift fixed; resolver outstanding) |
| P10 positive-evidence flags | D9 | P1.2 (envelope rule, done) + acceptance bar §3 (SDK surfaces outstanding) |
| P11 evidence grade for fares | D11 | **P1.4** — promoted to Tier 1 by the 2026-10-03 review; specification first, no parallel price model |
| P12 test taxonomy | D15 | P2.4 / quarantine burn-down (tracked in the `trvl` plan; **cleared 2026-10-03** — the 18 modules were deleted, not rewritten) |
| P13 verify the process | D3/D13 | P0.1 (test), P3.1 (tag binding) |
| P14 publication integrity | D13/D14 | P3.1, P3.2, P3.3; acceptance bar §§5–6 |
| P16 CHANGELOG | D12 | P0.2 — **done 2026-10-03** |
| P17 semantics agents get wrong | D11 | Acceptance bar §4; P1.4 step 3 covers the search half |
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
| P0.2 `CHANGELOG.md` | **Done 2026-10-03** | `CHANGELOG.md` added with an `[Unreleased]` section, provenance on every bullet, and three released sections verified against PyPI and the npm registry. `test/docs-claims.test.mjs` now asserts the newest released section equals `pyproject.toml` and names the current npm versions — the assertion the trvl plan's P1.3 had deferred. |
| Further defects fixed this pass (found while working) | **Done** | `sdk/python/letsfg/models.py` (unreachable behind the `models/` package) and `system_info.py` (stub for a removed architecture) deleted; `config.py` made the live resolver; MCP `VERSION` corrected from `1.3.1` to the package version and now asserted; committed `*.tgz` build artifacts deleted and `*.tgz` ignored; `sdk/python/letsfg/models/flights.py` docstring route corrected; `SECURITY.md` version table + credential model corrected and asserted; `docs/TESTING.md` rewritten around what actually runs here; `CONTRIBUTING.md` commands corrected; Playwright and its system libraries removed from all three container files with the dead knobs. |

**Still open, deliberately:** P2.1–P2.4 and P3.1–P3.3 are `OWNER` items (process
policy, tagging convention, publishing); P3.1's tag binding has no prerequisite
yet (`git tag | wc -l` → 0).

```bash
node --test test/docs-claims.test.mjs test/workflow-hygiene.test.mjs   # 19/19
cd sdk/mcp && npx tsc --noEmit && npm test                            # 67/67
cd sdk/js  && npx tsc --noEmit && npm test                            # 39/39
cd sdk/python && python -m pytest -m "not live"                       # 159 passed
node test/model-test.js                                              # 537/537
python -m mkdocs build                                                # exit 0
```


## Suggested order (remaining work)

Design §5 fixes the tiers; numerical order is not implementation order. The
integration surface comes first, because it is the reason for the study.

**Tier 1 — contract foundations (the integration surface)**

Ordered by dependency (design §4: Q2, Q5 and Q6 are answered; Q3 is not):

1. **P1.1** — one lane-resolution contract, one implementation per **public client
   surface**, returning a `ResolvedLane`, scoped to classes A + B, with
   credential/lane compatibility asserted. Structural, so it needs an owner nod
   (rule 4).
2. **P1.2 (rest)** — carry the search-outcome discrimination to the Python and JS
   surfaces, using the Q6 vocabulary rather than a new one. **Audited
   2026-10-04** (acceptance bar §3): five defects fixed; what remains is
   `partial`/coverage, the `auth_required` vs `auth_failed` name, `retry_after`,
   and the retired-endpoint refusals — each a decision, not engineering.
3. **P1.4** — the three-axis contract: confirm whether `price_status` + the
   verification outcome already express the semantics (Q5), and if so add only the
   no-promotion policy. The written answer still comes before code.
4. **D11 / acceptance bar §4** — operational semantics, documenting the contract
   that by now exists.
5. **P1.3** — establish the limiter's six fields by measurement (trvl §6 Q8), then
   document them. Nothing downstream may assume them beforehand.
6. **P2.1** — PR/issue templates with the surface × lane × operation enums.
   Valuable, but an observability contract: it follows the platform semantics,
   not leads them.

Alongside Tier 2, the **live canary** (Q4): write the LetsFG-contract live test
first, then the opt-in workflow.

**Tier 2 — distribution and API correctness**

6. **P3.2** — the install/packaged-entry-point matrix (acceptance bar §5).
7. **P3.1** — tag ↔ version ↔ commit binding; blocked until there is a tagging
   convention (`git tag | wc -l` → 0).
8. **P3.3** — build identity carrying the commit SHA (acceptance bar §6).

**Tier 3 — security and repository hygiene**

9. **D4** — the placeholder-hygiene sweep first, then secret scanning.
10. **P2.2** — Dependabot for npm and pip.
11. **P2.3** — the dead-code ledger *policy* (D1); the tool is adoptable once the
    owner accepts the toolchain (D19, design Q1).

**Tier 4 — optional**

12. **P2.4** — architecture hooks (D16), deferred: it would require refactors that
    rule 4 forbids without an owner request.

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
