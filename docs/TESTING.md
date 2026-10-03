# Testing Guide

LetsFG runs a three-tier test strategy. **Only Tier-1 lives in this repository**
— the public SDKs, MCP server and docs. Tiers 2 and 3 exercise the hosted engine
and the private product repo and cannot be run from here; they are described at
the end so the strategy is legible, not so you can run them.

## Tier-1 (this repository) — deterministic, offline, a hard merge gate

Everything here is offline: no network, no browser, no live airline API. It runs
in `.github/workflows/test.yml`.

| Job | What it runs | Blocks merge |
|-----|--------------|--------------|
| `typescript-packages` | `npx tsc --noEmit` + `npm test` in `sdk/js` and `sdk/mcp` | **Yes** (required) |
| `python-deterministic` | `pytest -m "not live"` in `sdk/python` | **Yes** (required) |
| `docs-claims` | `node --test test/docs-claims.test.mjs test/workflow-hygiene.test.mjs` | **Yes** (required) |
| `test-coverage-gate` | new source files must have a sibling test | **Yes** on PRs |

```bash
# Python SDK
cd sdk/python && pip install -e ".[dev]" && pytest -m "not live"

# JS SDK / MCP server
cd sdk/js  && npm ci && npx tsc --noEmit && npm test
cd sdk/mcp && npm ci && npx tsc --noEmit && npm test

# Repository guards (zero dependencies, by design — they must run even when an
# install is broken)
node --test test/docs-claims.test.mjs test/workflow-hygiene.test.mjs

# Plugin (Omarchy) logic — not wired into CI yet
node test/model-test.js
```

### The two repository guards

- **`test/docs-claims.test.mjs`** pins the claims that would otherwise rot
  silently: manifest versions against each other, the advertised MCP tool list
  against `sdk/mcp/README.md`, the retired `unlock_flight_offer` route, the
  OpenAPI server+path composition, and every local markdown link.
- **`test/workflow-hygiene.test.mjs`** pins the CI rules: every job has a
  `timeout-minutes`, every workflow declares `permissions`, every action is
  pinned to a commit SHA with a version comment, checkouts do not persist
  credentials unless the job publishes with them, and push/PR workflows declare a
  concurrency group with the right cancellation policy.

Adding a rule means adding an assertion to one of those files; a policy with no
test is a policy that decays.

### Parked tests (`sdk/python/conftest.py`)

Nineteen modules are listed in `collect_ignore_glob` because they import
`letsfg.connectors.*` (removed with the local connectors in `f91be5b`) or call
removed local code. The suite is green *because* they are parked, so treat that
list as a work queue, not a resting place: each entry must end up deleted or
rewritten against the server-side engine, and then removed from the list.

### Live tests

`pytest -m live` tests exist but are excluded from Tier-1 and are **not
scheduled** — there is no nightly workflow yet (it needs a `LETSFG_BEARER_TOKEN`
secret). Until one exists, treat the live markers as on-demand tooling. A live
test that never runs advertises coverage that does not exist.

## Definition of Done (TDD mandate)

Every PR that adds or modifies behaviour **must include tests reviewed alongside
the code**. No exceptions. CI enforces this structurally via `test-coverage-gate`.

- **New source file** → a sibling `<file>.test.ts` / `<file>.test.tsx` or
  `__tests__/<file>.test.ts` in `sdk/js/src` or `sdk/mcp/src`. Fails if missing.
- **Bug fix** → a regression test that fails before the fix and passes after. If
  it cannot be written, say so in the PR and explain why.
- **Lane-sensitive change** (PFS Bearer vs Developer API `X-API-Key`) → assert
  the request route and field names for each lane, because the two lanes name
  the same concepts differently. `sdk/mcp/src/envelope.test.ts` has the pattern:
  it spawns the real server against a fake API and records the paths called.
- **Documented claim change** (version, tool list, endpoint) → update the
  relevant guard file above.
- **No green Tier-1 = no merge.**

### Red-Green-Refactor

1. **Red** — write a failing test that describes the expected behaviour.
2. **Green** — write the minimum code to make it pass.
3. **Refactor** — clean up without breaking green.

Reviewers check that the test is atomic with the implementation, not appended
afterwards.

### Skipping the coverage gate

For rare exceptions (emergency hotfixes, config-only changes), label the PR
`skip-test-gate`. This bypasses the automated check but is visible in the PR
history.

## Test file locations in this repository

| What | Where |
|------|-------|
| Python SDK tests (including the parked list) | `sdk/python/tests/`, `sdk/python/conftest.py` |
| JS SDK tests | `sdk/js/src/**/*.test.ts` (e.g. `auth.test.ts`, `index.test.ts`) |
| MCP server tests | `sdk/mcp/src/index.test.ts` (protocol + contract guards), `sdk/mcp/src/envelope.test.ts` (status/completeness envelope, lane routes) |
| Repository guards | `test/docs-claims.test.mjs`, `test/workflow-hygiene.test.mjs` |
| Plugin (Omarchy) logic | `test/model-test.js` |
| CI gate | `.github/workflows/test.yml` |

## Experiments coexist via feature flags

When a feature is experimental (A/B test), write tests for both branches:

- The test for the **live behaviour** documents the current default.
- The test for the **experiment** asserts behaviour *under the feature flag* — it
  does not contradict the live tests.
- Both suites run in CI.
- When an experiment graduates, the old live test is removed and the experiment
  test becomes canonical.

Never overwrite live behaviour tests with experiment tests — use feature flags.

## Strategic decision registry

These decisions are recorded in the **private product repository**, where the
tests that pin them live (`website/tests/`, `growth-ops/src/services/__tests__/`).
They are listed here so an SDK change does not contradict one by accident.

| Decision | Test (private repo) | Date |
|----------|---------------------|------|
| Results show 3 ranked deals: best/cheapest/fastest | `website/tests/recommendation-quality.test.ts` | 2026-06-01 |
| Quality scoring weights: results 30 / price 40 / diversity 20 / speed 10 | `website/tests/recommendation-quality.test.ts` | 2026-06-01 |
| Google comparison shown on results page; zero/negative baselines are invalid | `website/tests/google-comparison.test.ts` | 2026-06-01 |
| Search is free; the checkout-unlock experiment routes traffic to the payment-only path | `website/tests/checkout-unlock-experiment.test.ts` | 2026-06-01 |
| Growth funnel measured via L1–L7 | `growth-ops/src/services/__tests__/` | 2026-06-01 |
| Quality measured via Q1–Q3 composite score | `website/tests/recommendation-quality.test.ts` | 2026-06-01 |
| Experiments run behind feature flags with tests for both branches; the winner replaces the default without a backwards shim | `website/tests/checkout-unlock-experiment.test.ts` | 2026-06-01 |

------

## Tiers 2 and 3 (private product repository — not runnable here)

Recorded for context. Nothing in this section can be run from this repository:
the paths belong to the private product repo, or to the local-connector harness
that was removed with the connectors.

| Tier | What it is | Where it lives now |
|------|-----------|--------------------|
| **2 — Targeted live smoke** | Real searches for changed providers only, report-only | Server-side provider checks in the private repo. The old `connectors/tests/smoke_harness.py` and `connectors/test_routes.py` went away with the local connectors. |
| **3 — Production synthetic** | Full-funnel probe users against production, tagged `is_test_search=true`, alerting on failure | `LetsFG-private/.github/workflows/quality-probes.yml`, `growth-ops/src/crons/quality-probes.cron.ts` |

Tier-3 secrets (private repo settings and Cloud Run env): `LETSFG_INTERNAL_PROBE_SECRET`,
`GROWTH_OPS_TELEGRAM_BOT_TOKEN`, `GROWTH_OPS_TELEGRAM_CHAT_ID`. The probe gateway
is `website/app/api/internal/probe/route.ts` and accepts requests only when
`x-probe-secret` matches, proxying through `LETSFG_WEBSITE_API_KEY`.

If you are working in the public SDKs, Tier 1 above is the whole story.
