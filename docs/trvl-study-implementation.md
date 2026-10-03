# LetsFG × trvl — Implementation Plan

**Status:** Proposed; nothing implemented. Awaiting owner approval per item.
**Date:** 2026-10-03
**Companion:** [`trvl-study-design.md`](trvl-study-design.md) (design rationale, decision register D1–D9).
**Source of lessons:** `github.com/MikkoParkkola/trvl`, inspected read-only at `D:/Users/vince/_probe/trvl`.

This document turns the design decisions into ordered, independently reviewable changes. Each item lists the files, the observable acceptance criterion, the test that proves it, and the risk. Nothing here is a feature request; items marked **defect** are correctness fixes.

---

## 0. Ground rules for executing this plan

- Follow `docs/working-agreement.md`: defect-only change surface (rule 4), fix defects on the spot (rule 3), docs in sync (rule 5), commit + push (rule 2), no secrets (rule 1).
- No trvl code is copied. Interfaces and ideas only; trvl is PolyForm Noncommercial.
- Every item ships with the test named in its Acceptance line. No green test = not done.
- Do not batch phases. P0 unblocks CI; P1 is defect-only; P2+ are additive.

---

## 1. Phase 0 — unblock CI and add the missing release spine

### P0.1 — **Defect:** 17 Python test modules cannot be collected — **DECISION NEEDED BEFORE CODING**

**Evidence.** `cd sdk/python && python -m pytest -m "not live" -q` → `17 errors during collection`, `121 tests collected`; exit non-zero. Every failing module imports a connector removed by `f91be5b` ("remove local connectors, route all search through PFS cloud API", 2026-06): `letsfg.connectors.{wizzair,vueling,emirates,skyscanner,tripcom,checkout_engine,…}`. `.github/workflows/test.yml` runs exactly this command in the required `python-deterministic` job, so the gate cannot be green.

**Why it needs a decision.** The connector code is gone from this history (`git log --all -- 'sdk/python/letsfg/connectors/checkout_engine.py'` is empty), so the tests cannot be made to pass by restoring code. Options:

| Option | Change | Trade-off |
|---|---|---|
| A. Delete the 17 modules | `git rm` each uncollectable file | Removes dead tests; loses nothing runnable, but destroys history of intent |
| B. Quarantine | add `collect_ignore_glob` in a new `sdk/python/conftest.py` listing them, with a comment + tracking issue | CI green immediately, files kept for reference; markers can rot |
| C. Restore connectors | re-import from the private engine | Out of scope — connectors are server-side by design |

**Recommendation: B**, because it is reversible, keeps the intent visible, and does not silently delete tests an owner may want to re-home. A is acceptable if the owner prefers a smaller tree.

**Acceptance:** `cd sdk/python && python -m pytest -m "not live" -q` exits 0 with the surviving suite, and no module errors during collection.
**Test:** the CI command itself; plus a new assertion that `collect_ignore_glob` (if B) covers exactly 17 files and that each is cited in a comment.
**Risk:** low; this is the single change that unblocks the required gate.

### P0.2 — Add `CHANGELOG.md`

**Files:** new `CHANGELOG.md`.
**Change:** Keep a Changelog 1.1.0 + SemVer, an `## [Unreleased]` section, and retroactive sections for the current package versions (`letsfg` 2026.5.103, `letsfg-js` 2026.5.75, `letsfg-mcp` 2026.5.78). Reference the trvl precedent (`CHANGELOG.md:3-5`).
**Acceptance:** file exists; the latest documented version equals `sdk/python/pyproject.toml` `version`.
**Test:** folded into the docs-claims test (P1.3).
**Risk:** none.

### P0.3 — Add `ROADMAP.md`

**Files:** new `ROADMAP.md`.
**Change:** `Shipped` bullets linking CHANGELOG entries only; `Next` stating "new work starts as a scoped issue with acceptance criteria; the issue tracker is the source of truth"; no wishlist.
**Acceptance:** links resolve (P1.3 link test).
**Risk:** none.

### P0.4 — Record the MCP protocol-version decision as a design record

**Files:** new `docs/design/mcp-protocol-version.md`.
**Change:** trvl's `docs/design/2026-09-25-mcp-two-revisions.md` shape: `Status:`, `Date:`, Problem, measured constraints (with the MCP spec URLs), Decision, acceptance signal, and a **"What would make this wrong"** reversal trigger. Decision content: LetsFG stays on `2024-11-05` for now (`sdk/mcp/src/index.ts:1208`), with the trigger "when the two revisions trvl tracks (`2025-11-25`, `2026-07-28`) become the common client baseline, or when a client requires `server/discover`".
**Acceptance:** doc exists with all sections; `Status:` line present.
**Risk:** none.

---

## 2. Phase 1 — process gates (no product code)

### P1.1 — Regression-test merge gate

**Files:** new `.github/workflows/regression-test-gate.yml`.
**Change:** port trvl's gate. On PRs whose title or any commit subject matches `^(fix|hotfix|bugfix)(\(|:| )`, require that every changed package directory under `sdk/js/src` and `sdk/mcp/src` also contains a changed `*.test.ts`. Bypass only via an explicit `skip-regression-test` label.
**Acceptance:** a synthetic PR touching `sdk/js/src/index.ts` without a test change fails the job; adding `index.test.ts` changes passes it.
**Test:** the workflow run itself on a scratch branch.
**Risk:** medium false-positive rate for pure-comment/docs PRs → mitigate with the label escape hatch, same as trvl.

### P1.2 — SHA-pin GitHub Actions + a hygiene guard

**Files:** `.github/workflows/*.yml`, new `.github/scripts/check-workflow-hygiene.sh`.
**Change:** pin every `uses:` to a 40-hex SHA with a trailing version comment; add a guard step that fails on any unpinned `uses:` (trvl: `.github/scripts/check-workflow-hygiene.sh`).
**Acceptance:** guard fails when reverted to `@v4`; passes on `main`.
**Risk:** low; trvl's own guard is the reference.

### P1.3 — Docs-claims and link test

**Files:** new `test/docs-claims.test.mjs` (node, no deps — matches the existing root `test/model-test.js` harness style) or `sdk/python/tests/test_docs_claims.py`. Choose one; node avoids adding a Python test to an already-broken suite, so **node is recommended**.
**Change:** assert:
1. `sdk/js/package.json.version` == `sdk/mcp/package.json.version` == `server.json.packages[].version` == the top `CHANGELOG.md` released version.
2. `sdk/python/pyproject.toml` `version` == `letsfg/__init__.py` `__version__`.
3. The MCP tool names in `sdk/mcp/src/index.ts` equal the tool list documented in `docs/mcp.md`.
4. Every local markdown link under `README.md`, `AGENTS.md`, `CLAUDE.md`, `docs/**` resolves to an existing file.
5. `openapi.yaml` `servers[0].url` + a sample path composes to the URL used in `docs/openapi.md` (this forces the currently flagged double-prefix to be resolved one way or the other).
**Acceptance:** test fails when any version or tool name is drifted; passes on `main` after P0.2.
**Test:** the new test file, run in `test.yml` (add a step).
**Risk:** medium — item 5 will likely require fixing `openapi.yaml` or the docs; that fix is a **defect** and belongs in the same change.

### P1.4 — Nightly live probe (`live-probes.yml`) — **defer until a live test exists**

**Files:** new `.github/workflows/live-probes.yml`; one opt-in live test in `sdk/python/tests/` marked `live` (the marker already exists in `pyproject.toml`).
**Change:** daily cron runs `pytest -m live`; on schedule failure, open/comment a single labelled issue ("Live API contract drift"). trvl precedent: `live-probes.yml`.
**Acceptance:** the job skips cleanly with no `LETSFG_BEARER_TOKEN` secret; documents the env it needs.
**Risk:** low, but requires a token secret in CI — flag for owner before adding.

---

## 3. Phase 2 — MCP result envelope (D1, D2)

### P2.1 — Typed status + completeness in tool results

**Files:** `sdk/mcp/src/index.ts` (`callTool`, `searchPFS`, the `TOOLS` array), `sdk/mcp/src/index.test.ts`.
**Change:** introduce a shared result envelope and populate it in `search_flights`, `search_hotels`, `get_flight_booking`, `get_hotel_booking`:
```ts
type SourceStatus = 'ok' | 'no_results' | 'timeout' | 'rate_limited'
                  | 'auth_required' | 'failed';
type Completeness = 'complete' | 'partial' | 'blocked';
interface ToolEnvelope<T> {
  status: SourceStatus;
  completeness: Completeness;
  retry_after_ms?: number;
  fix_hint_code?: string;   // closed enum; document in letsfg://guide
  data: T;
}
```
`no_results` (valid empty) and `timeout` (unknown) are never conflated. Map HTTP 429 → `rate_limited` with `retry_after_ms` from the response, 401/403 → `auth_required`, 5xx/network → `failed`. The text content must not say "no flights found" when `completeness !== 'complete'`; emit the incomplete note instead (trvl `evidence.go:95-160`).
**Acceptance:** a mocked 429 search returns `status:"rate_limited"` with a positive `retry_after_ms`; a mocked 200 with zero offers returns `status:"no_results"`, `completeness:"complete"`; a timeout returns `status:"timeout"` and never claims no results.
**Test:** `sdk/mcp/src/index.test.ts` — three new cases against `callTool` with a stubbed `fetch`.
**Risk:** medium — changing the text content shape affects agents; keep the existing text block and add the envelope as an additional `content` entry so old behaviour is preserved.

### P2.2 — `outputSchema`, `structuredContent`, audience annotations

**Files:** `sdk/mcp/src/index.ts`.
**Change:** add an `outputSchema` to each of the 14 tools; return `structuredContent` alongside `content`; annotate the user summary `audience:["user"]` and the JSON block `audience:["assistant"]`. Leave `isError` for genuine tool failures (trvl `server.go:549-568`).
**Acceptance:** `tools/list` includes an `outputSchema` for each tool; `tools/call` returns a non-empty `structuredContent`; the existing spawn test `initialize`/`tools/list` still passes.
**Test:** extend `sdk/mcp/src/index.test.ts` (every tool has `outputSchema`; a call returns `structuredContent`).
**Risk:** medium — verify against one real MCP client (Claude/Cursor) before publishing; the spawn tests cannot prove client rendering.

### P2.3 — Move long guidance out of `tools/list` into the guide resource

**Files:** `sdk/mcp/src/index.ts` (`description` fields, `GUIDE_TEXT`).
**Change:** shorten each tool description to its contract (name, required args, one line of semantics) and move Starlink/split-ticket/booking-safety prose into `letsfg://guide`, which already exists. Keep the two facts that change agent behaviour inline: split-ticket shares are unprotected; `confirmed_*` is fact, `likely_*` is not.
**Acceptance:** `tools/list` payload size drops measurably (record before/after bytes); every moved rule is present verbatim in the guide.
**Test:** a test asserts the guide contains the split-ticket and Starlink sentences, and that tool descriptions no longer duplicate them.
**Risk:** low-medium — an agent that only reads `tools/list` loses context; hence keep the two load-bearing facts.

---

## 4. Phase 3 — state integrity (D4, D5)

### P3.1 — **Defect:** atomic, `0600`-from-creation config writes

**Files:** `sdk/js/src/auth.ts` (`saveConfig`), `sdk/python/letsfg/connectors/auth.py` (`_save_config`), `sdk/python/letsfg/client.py` (`_save_config`), and the QML writer path in `Panel.qml`/`Model.js` if it writes config.
**Change:** one helper per language: create temp sibling with `O_CREAT|O_EXCL|0o600` → write → `fsync` → `rename` over target (Windows: remove-then-rename). Never `write` then `chmod`. Keep the existing JSON shape and key-merge behaviour unchanged.
**Acceptance:** a write interrupted by a simulated failure leaves the prior `config.json` intact (no truncation); the file is never readable by group/other between creation and rename.
**Test:** js `auth.test.ts` — write, then attempt a second write with the temp creation forced to fail, assert original content survives; assert temp mode is `0600` on POSIX.
**Risk:** low; reference trvl `internal/atomicjson/atomicjson.go:29-93`.

### P3.2 — One config-path resolver per language + credential list

**Files:** `sdk/python/letsfg/client.py` (drops the `XDG_CONFIG_HOME` branch or aligns), `sdk/python/letsfg/config.py` (dead — either delete or make canonical), `sdk/js/src/auth.ts`, `docs/working-agreement.md` (reference fix).
**Change:** all writers/readers of `~/.letsfg/config.json` resolve the same directory on every OS. Create the canonical credential/env list that working-agreement rule 1 currently points at as a missing `scripts/strip-adapter-env.bash` — either add that file or reword rule 1 to name the real location.
**Acceptance:** a test asserts the Python and JS resolvers return the same path for a given `HOME`/`APPDATA`.
**Test:** `sdk/python/tests/test_config_path.py` (new) + js `auth.test.ts`.
**Risk:** low-medium — changing a path could orphan an existing user's token; migrate rather than move (read old path if new is absent).

---

## 5. Phase 4 — deferred items (explicitly not now)

| Item | Design ref | Trigger to revisit |
|---|---|---|
| Client health log (`~/.letsfg/health.jsonl`) | D7 | A second client-owned data source, or repeated transport drift |
| SSRF/destination guard | D8 | `LETSFG_BASE_URL` honoured by a long-lived server, or a user-supplied fetch target |
| Single-router MCP tool + count tripwire | D3 | Tool count >~20 or a measured `tools/list` token budget exceeded |
| AGENTS/CLAUDE audience split | D9 | After P0/P1 land; staged consolidation of duplicated claims |
| Release signing (cosign/ML-DSA) | Rejected (§3 of design) | Only if distributing binaries outside npm/PyPI |

---

## 6. Traceability matrix

| trvl artifact | LetsFG item |
|---|---|
| `internal/models/evidence.go:18-31` status enum, `fixhint.go:14-23` | P2.1 |
| `mcp/protocol.go:141-166`, `mcp/tools.go:636-649` | P2.2 |
| `mcp/tools_smart.go:68-74`, `mcp/alias_count_test.go` | P2.3 / D3 |
| `internal/models/evidence.go:95-160` completeness gate | P2.1 |
| `internal/atomicjson/atomicjson.go:29-93` | P3.1 |
| `internal/consent/consent.go:10-20` | P3.2 |
| `cmd/trvl/docs_freshness_test.go`, `public_claims_test.go` | P0.2, P1.3 |
| `regression-test-gate.yml`, `check-workflow-hygiene.sh` | P1.1, P1.2 |
| `CHANGELOG.md:3-5`, `ROADMAP.md`, `docs/design/2026-09-25-mcp-two-revisions.md` | P0.2–P0.4 |
| `live-probes.yml` | P1.4 |
| `docs/TESTING.md` tier model | P0.1 (make the deterministic tier actually green) |

---

## 7. Verification commands (run before claiming any item done)

```bash
# Python (after P0.1)
cd sdk/python && python -m pytest -m "not live" -q

# JS + MCP
cd sdk/js  && npm ci && npm test
cd sdk/mcp && npm ci && npm test

# Root plugin logic (unchanged, must stay green)
node test/model-test.js

# Docs claims (new, P1.3)
node test/docs-claims.test.mjs
```

---

## 8. Open questions the implementation depends on

- **P0.1 option A vs B** — owner decision required (delete vs quarantine the 17 modules). This is the only blocking decision.
- **P2.1 status mapping** — need one real `/api/results/{id}` payload to learn whether the hosted API already reports per-source status (then we only pass it through) or whether the client must infer it from HTTP + search `status`.
- **P1.4 live probe** — requires a `LETSFG_BEARER_TOKEN` (or key) secret in CI; owner decision on whether to spend one search/day.
- **P2.2 client compatibility** — `structuredContent`/annotations must be checked against one real MCP client before rollout; the spawn harness cannot prove rendering.
