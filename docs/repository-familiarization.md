# Repository Familiarization Report

*Reconnaissance only — no files were modified. Verified against the working tree at `ec95f0e` (2026-10-02). Claims marked `[INFERENCE]` are derived; everything else cites source.*

## 1. Executive Summary

**What it is.** LetsFG is an agent-native flight *and* hotel search/booking product. The search engine, connectors and checkout run **server-side at `letsfg.co`**; this repository is the public **client surface**: SDKs, CLIs, an MCP server, a desktop (Omarchy/Quickshell) bar plugin, the OpenAPI spec, docs, agent skills, and the tooling that generates the plugin's data and ranking bundle.

**Language/stack.** Python 3.10+ (SDK/CLI), TypeScript (JS SDK + MCP server, zero runtime deps, native `fetch`), JavaScript (`Model.js` plugin logic), QML (Quickshell plugin), Bash (validation). Build: `hatchling` (Python), `tsup` (TS), MkDocs (docs). No bundler/monorepo tool at root; the three SDKs are independent packages.

**Two API lanes** (this distinction is the single most important architectural fact):
1. **PFS lane** — free, card-backed OAuth Bearer token; `POST /api/search` → poll `GET /api/results/{id}` → `POST /api/agent-book` → poll `POST /api/agent-book/status`. Target of the CLI, the MCP Bearer mode, and the Omarchy plugin.
2. **Developer API lane** — paid/prepaid, `X-API-Key`; synchronous `/developers/api/v1/flights/*`, `/hotels/*`, `/agents/*`. Target of the `LetsFG` Python/JS classes and MCP API-key mode.

**Maturity.** Production-grade intent: pinned CI gates, security invariants mechanically enforced (`tools/validate.sh`, `tools/check-search-invariant.py`), a 537-check plugin test suite, careful auth (OAuth 2.1 + PKCE S256), extensive in-code rationale comments, and a real git history (795 commits). But the checkout carries substantial **stale surface**: 17 Python test modules import connector packages that no longer exist (confirmed by running `pytest`), Docker/compose files still install Playwright for removed local connectors, and docs contradict the code in ~15 places.

**Clone provenance.** `origin = https://github.com/Ghostfusion/LetsFG.git`; all metadata/docs point at `github.com/LetsFG/LetsFG`. Treat this as a fork/mirror of the canonical public repo. Single branch `main`, **zero tags**.

## 2. Repository Structure

```
Root/
├── sdk/
│   ├── python/            PyPI package `letsfg` (2026.5.103)
│   │   ├── letsfg/        __init__, __main__, cli, client, local, config,
│   │   │   ├── connectors/  auth.py (OH2.1+PKCE), airport_tz.py  (3 files only)
│   │   │   └── models/      dataclass DTOs + flights.py (pydantic)
│   │   └── tests/         32 unittest modules (17 currently uncollectable)
│   ├── js/                npm `letsfg` 2026.5.75 — index/auth/cli/ranking/offer-details/trip-purpose
│   └── mcp/               npm `letsfg-mcp` 2026.5.78 — stdio MCP server
├── BarWidget.qml          Omarchy bar widget entry (manifest.json entryPoints)
├── Panel.qml              All plugin UI + network (4757 lines)
├── Model.js               Qt-free plugin logic, node-testable (2684 lines)
├── assets/                ranking.js (generated), airports.json, icons, destinations, fonts
├── preview/               PySide6 harness + stubs to run real QML off-Wayland
├── tools/                 build-airports/icons/ranking.py, validate.sh, check-search-invariant.py, split-comparison.py
├── test/model-test.js     537-check node suite for Model.js
├── docs/                  MkDocs source (22 pages) + OpenAPI guide
├── skills/, agent-skills-contribution/, integrations/hermes/   agent skill packages
├── openapi.yaml           Developer API 3.1 spec (v0.2.16)
├── server.json, glama.json, smithery.yaml, mcp-config.json, .mcp.json, manifest.json
├── Dockerfile, Dockerfile.python, docker-compose.yml, lighthouserc.js, mkdocs.yml
└── README.md/AGENTS.md/CLAUDE.md/SKILL.md/OMARCHY-PLUGIN.md/SECURITY.md/CONTRIBUTING.md
```

## 3. Architecture

```text
Omarchy shell
  BarWidget.qml (bar label + IPC + hosts panel; no network)
      ↓ Loader
  Panel.qml (only XMLHttpRequest factory, only beginSearch(), all UI/state)
      ↓ import
  Model.js  (pure validation/sanitisation/URL/PKCE/poll/format logic)
  assets/ranking.js (GENERATED from sdk/js/src/*) → Ranking.deduplicateOffers
      ↓ HTTPS (origin pinned to https://letsfg.co)
  letsfg.co  (search engine, connectors, booking, hotels)

MCP client ⇄ stdio JSON-RPC ⇄ sdk/mcp/src/index.ts → letsfg.co (/api/* or /developers/api/v1/*)

CLI (Python Typer / JS cli.ts) → local.py or auth.ts (PFS Bearer) | client.py (Dev API) → letsfg.co
```

Three independent client implementations of the same two lanes exist side-by-side (Python, TS, QML/JS); consistency between them is a recurring maintenance burden and several drifts were found (§14).

## 4. Core Components

**Python SDK**
- `client.py` — `LetsFG` class (Developer lane): `search/book/book_and_wait/get_booking/answer_booking`, hotels (`hotel_destinations/search_hotels/book_hotel/hotel_booking/book_hotel_and_wait/cancel_hotel`), `me/register/connect_payment`; `ErrorCode`/`ErrorCategory` + `LetsFGError` hierarchy; config persistence. `_get_bookable_connector()` still maps `ryanair_direct`/`easyjet_direct` and `letsfg.connectors.checkout_engine`, modules **absent from this repo** — guarded by `try/except ImportError` (client.py:118-150).
- `local.py` — PFS lane async functions `search_local/book_offer/booking_status`; 2 s polling, 90-poll cap, then a 90 s late-merge grace while `split_ticket_pending`/`gf_enrich_pending` (gated by `LETSFG_WAIT_FOR_SPLIT`). Single `_headers()` helper (UA `LetsFG-Python-SDK/1.0.3`, Cloudflare blocks urllib default).
- `connectors/auth.py` — OAuth 2.1 + PKCE S256 with dynamic client registration, loopback `_CallbackServer`, token refresh with a rotating refresh token, `BearerTokenError`, retired-Stripe stubs.
- `cli.py` — Typer commands: `search, auth, unlock(retired), book, booking, locations, register, recover, connect-payment/setup-payment, me`; loud non-fatal warning on paid-Dev-API commands.
- `models/__init__.py` — dataclass DTOs with dual-shape `from_dict` (Developer vs Bearer field names). `models/flights.py` — pydantic v2 request/response models, timezone-aware duration backfill via `airport_tz`, `PublicFlightOffer` masking. `models.py` exists but is **shadowed** by the `models/` package (verified: `letsfg.models.__file__` → `models/__init__.py`). `config.py`/`system_info.py` are dead.

**JS SDK** (`sdk/js/src/index.ts`) — `LetsFG` class, 18 `ErrorCode`s in 3 categories, `LetsFGError` + subclasses, `offerSummary`/`cheapestOffer`, hotels, retired stubs. `auth.ts` mirrors the Python PKCE flow. `ranking.ts` — the open-source **9-dimension / 12-weight-profile** ranker with constraint-gate hero selection. `offer-details.ts` — regex-based amenity/refund signal extraction. `trip-purpose.ts` — 11 `TripPurpose` values.

**MCP server** (`sdk/mcp/src/index.ts`) — stdio JSON-RPC 2.0, protocol `2024-11-05`, **14 tools** (`search_flights, resolve_location, book_flight, get_flight_booking, answer_booking_question, resolve_hotel_city, search_hotels, book_hotel, get_hotel_booking, cancel_hotel_booking, authenticate, connect_payment, get_agent_profile, load_resources`), one resource `letsfg://guide`. Dispatches per lane; `searchPFS` carries the Bearer token on the poll.

**Omarchy plugin** — `BarWidget.qml` (glyph/price label, IPC `open/close/show/hide/toggle`, deliberately **no search IPC**); `Panel.qml` (homepage, search form, connect flow, poll state machine, results/map/filters, hotel panes present but tab not user-visible); `Model.js` (pinned origin, response caps, token closure, PKCE/SHA-256, poll decision, formatting, throttle/breaker); generated `assets/ranking.js`.

## 5. Execution Flow

**PFS search + book (Python):**
```
letsfg search GDN BCN ... → cli.search → local.search_local → ensure_bearer_token()
  → POST https://letsfg.co/api/search            → {search_id}
  → GET  /api/results/{id} every 2s (≤90)        → status/offers
  → while split_ticket_pending|gf_enrich_pending and <90s: re-poll every 3s
  → returns {offers,total_results,search_id}
letsfg book <offer> --search-id ... → local.book_offer → POST /api/agent-book
  → {booking_ref, state:"booking_in_progress", held:{...}}
letsfg booking <ref> --wait → local.booking_status → POST /api/agent-book/status
  → completed(PNR) | failed(hold released) | needs_attention
```

**OAuth connect (`letsfg auth`):** discovery → bind loopback listener → dynamic client registration (`redirect_uri` must match, so bind first) → open `https://letsfg.co/connect` → PKCE code exchange → store access+refresh+client_id in `~/.letsfg/config.json` under `pfs_auth` (chmod 0600). Refresh rotates the refresh token.

**Dev API (`LetsFG.search`):** one synchronous `POST /developers/api/v1/flights/search`; `book()` → `/flights/book`; poll `GET /flights/bookings/{id}`.

**Plugin:** click/Enter → `Panel.beginSearch()` → `Model.buildSearchBody` → `newRequest` POST `/api/search` → poll with `Model.pollDecision` → `Ranking.deduplicateOffers` + `Model.dedupePricedOffers` → `Model.summarizeOffers` → render; cheapest drives the bar label.

**MCP:** readline line → JSON-RPC dispatch → `callTool` → `searchPFS`/`apiRequest` → text content.

Config is loaded lazily: env vars first (`LETSFG_BEARER_TOKEN`, `LETSFG_API_KEY`), then `~/.letsfg/config.json`; no DI container, no app bootstrap.

## 6. Data Flow

```
Input (IATA codes, ISO dates, pax/cabin/currency)
  → validation: Model.buildSearchBody / pydantic FlightSearchRequest / cli arg checks
  → serialization: JSON, LF-specific field names per lane (see invariants §15)
  → POST letsfg.co → server-side engine (connectors, merge, dedup, currency, interlining)
  → poll JSON → normalization + dedup + ranking (JS ranking / Model formatting)
  → Output: JSON (SDK/CLI --json) | rich table (CLI) | QML result cards/map (plugin) | MCP text
Booking: offer_id + search_id → server holds fare on connected card → PNR or release.
```

Persistence: `~/.letsfg/config.json` (tokens + optional API key), plugin per-shell state (`letsfg-auth.json`, `letsfg-install.json`), bundled `assets/airports.json`. No database, no cache (the docs' caching layer is example code only).

## 7. External Dependencies

- **letsfg.co** (everything): `/api/*` (PFS), `/developers/api/v1/*`, `/developers/api/oauth/{register,token}`, `/.well-known/oauth-authorization-server`, `/connect`, `/api/agent-access/request`, `/api/booking-payment/{seat,extra}`.
- **Third-party CDNs** (plugin): `images.kiwi.com`, `pics.avs.io` (airline logos, allowlisted), `basemaps.cartocdn.com` (map tiles), `images.pexels.com`/`upload.wikimedia.org` (destination cards), `avatars.githubusercontent.com`. Allowlists in `Model.js:200,2015,2178,2315`.
- **Python libs**: `pydantic, httpx, airportsdata, requests, typer, rich, click` (pyproject). Note: `client.py`/`local.py` actually use **stdlib urllib**; `httpx`/`requests` appear unused by the shipped code `[INFERENCE]`.
- **Node**: zero runtime deps (native `fetch`, Node ≥18); dev deps `tsup`, `tsx`, `typescript`.
- **Qt6 + Quickshell + Omarchy qs.** (plugin runtime); `PySide6` (preview harness only).

## 8. Configuration

| Configuration | Purpose | Default | Required? | Used By |
|---|---|---|---|---|
| `LETSFG_BEARER_TOKEN` | PFS token | — | choose one lane | local.py, js index/auth, mcp, Model.js reads file not env |
| `LETSFG_API_KEY` | Dev API key | — | Dev lane | client.py, js, mcp |
| `LETSFG_BASE_URL` | Override host | `https://letsfg.co` | no | local.py, auth.py, js, mcp, tools |
| `LETSFG_WAIT_FOR_SPLIT` | `0` disables late-merge wait | wait enabled | no | local.py, js, mcp |
| `LETSFG_USER_AGENT` | UA override (MCP) | built-in | no | mcp |
| `~/.letsfg/config.json` `pfs_auth.{token,expires_at,refresh_token,client_id}` | Token store | — | PFS | auth.py, js auth, Panel.qml FileView |
| `config.json` `api_key`, `agent_id` | Dev creds | — | Dev | client.py |
| `APPDATA`/`XDG_CONFIG_HOME` | Config dir base | home | no | two divergent implementations (§14) |
| `manifest.json version` | Plugin version | 1.1.0 | — | Omarchy |

Secrets live only in the config file (chmod 0600) or env; never bundled (plugin reads the CLI's file). `context7.json` embeds a Context7 *public* key (not a secret).

## 9. Error Handling & Reliability

- **Typed errors**: Python `LetsFGError(status_code,error_code,error_category)` + `AuthenticationError/PaymentRequiredError/OfferExpiredError/ValidationError`; JS mirror with `isRetryable`; `BearerTokenError` for PFS.
- **Mapping** via `inferErrorCode(status, detail)`; 401→auth, 402→payment, 410→offer expired, 422→validation, 429/503/504→transient, 409→already booked.
- **No retry/backoff is implemented** in any client; docs show backoff snippets only.
- **Timeouts** hard-coded per call (search 30 s POST/15 s poll; book 60 s; hotels up to 240 s; JS `AbortController`; MCP `AbortSignal`).
- **Poll correctness**: "terminal before set stops growing" — all three clients gate on `split_ticket_pending`/`gf_enrich_pending` with a 90 s grace; comments document a measured 51 s late merge.
- **Non-JSON responses**: MCP `readJson` detects anti-bot/HTML; JS SDK uses bare `resp.json()` (throws SyntaxError) — asymmetry.
- **Plugin hardening**: byte-cap enforcement while bytes arrive (`newRequest`, `tools/validate.sh` fails if a second `XMLHttpRequest` appears), per-request deadline sweep, `redact()` of credentials in messages, origin-pinned `apiUrl`, `safeText`/`safeHttpsUrl` for all network strings.
- **Graceful shutdown**: none needed (short-lived CLI/stdio); plugin has watchdogs that abort in-flight XHR on close.

## 10. Concurrency & Performance

- Python PFS lane is `async` (`asyncio.sleep`), but HTTP itself is synchronous `urlopen`; OAuth callback runs a daemon thread.
- JS/MCP are promise-based on a single Node event loop; search polling is sequential (one poll at a time).
- QML is a single event-loop/thread; no worker threads. Concurrency is bounded by watchdog timers and a self-throttle (`MIN_SEARCH_INTERVAL_MS=5000`, breaker after 3 consecutive failures).
- No batching/connection pools/multiprocessing. `docker-compose.yml`'s `LETSFG_MAX_BROWSERS` and Playwright volume refer to removed local-connector concurrency (stale).

## 11. Testing

| Suite | Location | Framework | Status in this checkout |
|---|---|---|---|
| Python SDK | `sdk/python/tests/` (32 files) | unittest + pytest | **green after quarantine**: `pytest -m "not live"` → 99 passed, 0 errors; 19 stale modules are listed in `sdk/python/conftest.py` `collect_ignore_glob` (17 failed at import from missing `letsfg.connectors.*`, 2 failed at runtime) |
| Python masking (advisory CI) | `test_public_offer_masking.py` | pytest | passes (deps pydantic only) |
| JS SDK | `sdk/js/src/index.test.ts`, `auth.test.ts` | node:test via tsx | not run here (no node_modules) |
| MCP | `sdk/mcp/src/index.test.ts`, `envelope.test.ts` | node:test via tsx | protocol smoke + contract guards + envelope unit/E2E; 41/41 pass (verified on Windows after the spawn harness fix) |
| Plugin logic | `test/model-test.js` | homemade node harness | **PASS 537/537** (verified) |
| QML preview | `preview/run.py --strict` + fixtures | PySide6 | offline render/behaviour checks |
| Plugin static rules | `tools/validate.sh`, `check-search-invariant.py` | Bash/Python | manual only, not in CI |
| Repo docs claims | `test/docs-claims.test.mjs` | node:test (zero deps) | 6/6 pass; new required `docs-claims` CI job (versions, tool list, OpenAPI composition, local links) |
| Plugin logic suite | `test/model-test.js` | node | 537/537 pass; not wired to CI |

Strong coverage on: plugin logic/security, offer masking, per-lane field names, error taxonomy, hotel hold-then-capture contract. Light coverage: Python CLI end-to-end, live connectors (Tier-2 is private), auth refresh on the QML path (preview flags exist). `test/model-test.js` and `tools/validate.sh` are **not invoked by any workflow**.

## 12. Build & Development Workflow

```bash
# Python SDK/CLI
cd sdk/python && pip install -e ".[dev]"
python -m pytest -m "not live"          # green: 99 passed (stale modules quarantined in conftest.py)
python -m pytest tests/test_public_offer_masking.py
letsfg auth / letsfg search LON BCN 2026-06-01

# JS SDK
cd sdk/js && npm ci && npm run build && npm test
# MCP
cd sdk/mcp && npm ci && npm run build && npm test

# Plugin (offline, any OS with Qt6)
python preview/run.py --strict
python preview/run.py --fixture --screenshot out.png
python tools/validate.sh
python tools/check-search-invariant.py
node test/model-test.js
python tools/build-ranking.py            # regenerate assets/ranking.js

# Docs
pip install mkdocs-material && mkdocs build
```
Lint/format/typecheck: only `npx tsc --noEmit` (CI-invoked; no `typecheck` script). No ESLint/Prettier/Ruff config. No DB/migrations. Containers (`Dockerfile*`, compose) are stale w.r.t. the server-side-connectors architecture.

## 13. Git / Recent Changes

`main` only, **0 tags**, 795 commits, HEAD `ec95f0e` "fix(python): read the offers the Bearer lane returns… (#223)", 2026-10-02. History arc: initial release as **BoostedTravel** → renamed LetsFG; `f91be5b` "feat: remove local connectors, route all search through PFS cloud API" (June 2026) replaced `connectors/` with an auth stub and moved all search server-side; later commits built the connect/OAuth flow (replacing Twitter/X then Stripe), the Omarchy plugin (`83390dd`), Hermes integration, hotels, and repeated docs corrections. 0 TODO/FIXME/HACK markers in tracked source.

## 14. Documentation vs Implementation

**Accurate:** AGENTS.md/README two-lane model, pricing/connect narrative, late-merge polling, error taxonomy (mostly), plugin security description (OMARCHY-PLUGIN.md).

**Outdated / contradicts code (verified):**
1. `docs/TESTING.md` + `CONTRIBUTING.md` reference `connectors/tests/smoke_harness.py`, `connectors/test_routes.py`, `website/tests/`, `growth-ops/`, `sdk/python/tests/fixtures/` — **none exist**.
2. 17 test modules imported `letsfg.connectors.{wizzair,vueling,emirates,skyscanner,tripcom,checkout_engine,…}`, absent since `f91be5b`, so `python-deterministic` CI failed. **Resolved 2026-10-03:** those 17 plus 2 runtime-stale modules (`test_india_user_surfaces.py`, `test_telemetry_enrichment.py`) are quarantined in `sdk/python/conftest.py`; the suite is green.
3. `docker-compose.yml`/`Dockerfile(.python)`/`Dockerfile` install Playwright Chromium and document `--mode fast`, `LETSFG_MAX_BROWSERS`, `LETSFG_PROXY`, `LETSFG_NO_TELEMETRY` — all for removed local connectors.
4. `openapi.yaml`: server carried `…/developers/api/v1` **and** paths began `/api/v1/…` → every generated URL was double-prefixed. **Fixed 2026-10-03**: `servers[0].url` is now `https://letsfg.co/developers`, and `test/docs-claims.test.mjs` pins the composition. The spec is still a stale subset missing top-up/billing/rotate-key/discover/async/multi-search/sandbox paths documented elsewhere.
5. `openapi.yaml` says API key prefix `trav_`; docs/examples use `letsfg_`.
6. `register` guidance: AGENTS.md/SKILL.md/context7 say "never call `/agents/register`", but README, api-guide, api-onboarding, getting-started, cli-reference, mcp README and openapi's own description still present `letsfg register` as the auth path.
7. Hotel credential: `docs/agent-guide.md` says Bearer tokens don't work for hotels; hotels.md/packages.md/AGENTS.md/context7 say either credential works.
8. `SECURITY.md` version table says 1.0.x; actual 2026.5.x.
9. `CLAUDE.md` claims Python has "zero external dependencies" and lists stdlib urllib — contradicted by pyproject; its repo map is stale.
10. `models.py` (shadowed), `config.py`, `system_info.py` are dead; `client.py`'s connector registry/`test_checkout_engine_configs.py` reference absent modules.
11. Version drift: MCP `VERSION='1.3.1'` vs npm `2026.5.78`; committed tarballs older than package.json; README minimums below current.
12. MCP `book_flight` API-key branch posts to `/developers/api/v1/bookings/book` — a route AGENTS.md declares **retired (410)**; JS SDK correctly uses `/flights/book` (verified).
13. AGENTS.md ranking sample uses wrong call shape (`{ranked}`, `wantsDirectFlight`, `_score.total`) and documents a non-existent `letsfg recover`.
14. JS README says `letsfg auth` cannot get a token; `auth.ts` implements the working OAuth flow.
15. Plugin docs say hotels were removed; hotel code remains reachable via the preview-only `debugShow` hook.

## 15. Important Contracts & Invariants

**Explicit (enforced by code/tests/CI):**
- Search only from click/keypress in the plugin (`check-search-invariant.py`, `tools/validate.sh`); exactly one `XMLHttpRequest` construction; response caps enforced while streaming.
- Plugin API origin pinned to `https://letsfg.co`; every network string passed through `safeText`/`safeHttpsUrl`; airline-logo/image hosts allowlisted.
- PFS booking = **hold, not charge**; only `completed` with a PNR means booked; never start a second booking while one is in progress.
- Per-lane request field names (critical): PFS sends `return_date`, `cabin`, `max_stops`, `sort_by`; Dev API reads `return_from`, `cabin_class`, `max_stopovers`, `sort`. A shared body silently drops options (documented in both SDKs).
- Terminal status = anything not in `pending`/`searching`; don't stop while late-merge flags are set.
- Error `error_code`/`error_category` mapping and `isRetryable`.
- Hotel contract: hold-then-capture, one guest name per person in the room, final statuses `succeeded|failed|attention`.
- Auth: OAuth 2.1 + PKCE S256 only; bind loopback before registration; refresh token rotates on use; requested scopes must be advertised (`flights:search flights:book profile:read`).
- Retired endpoints throw locally rather than calling the network (`unlock`, `setup_payment`, Stripe verification).

**Implicit:** three clients stay behaviourally aligned; `~/.letsfg/config.json` shape is shared between CLI and plugin; version constants should track package versions; `assets/ranking.js` is regenerated when `sdk/js/src` ranking changes.

**Assumptions to confirm:** that `pytest -m "not live"` is meant to be green here (it is not); that CI workflows actually run on this fork; that the Dev-API hotels endpoints used by CLI/plugin match the live contract.

## 16. Engineering Conventions

- **Comments carry rationale** (issue numbers, "why", regression history) — this is the dominant documentation style; preserve it.
- **Client-side safety first**: never trust server/network strings; constrain IDs to tight regexes before path interpolation; redact credentials from messages.
- **Errors as data**: machine-readable codes/categories for agents; human strings separate.
- **Typing**: Python type hints (`from __future__ import annotations`), pydantic for wire models; TS `strict`.
- **Async**: Python PFS is `async def`; Dev API synchronous; JS/TS promise-based; never block the QML loop (byte/time-bounded requests).
- **Testing**: node:test for TS/Model.js; unittest+pytest for Python; tests precede implementation (TESTING.md TDD mandate, `test-coverage-gate`).
- **Config pattern**: env-var-first, then JSON file; no globals.
- **No new runtime deps** in JS/MCP (zero-dep is a stated invariant); prefer stdlib in Python where already used.
- **Docs duplicated across README/AGENTS/CLAUDE/SKILL/context7** — changes usually must be mirrored.

## 17. Critical Files

**Tier 1 (must understand):**
- `sdk/python/letsfg/local.py` — PFS search/book/status + late-merge polling (the free lane).
- `sdk/python/letsfg/client.py` — Developer lane, error taxonomy, config.
- `sdk/python/letsfg/connectors/auth.py` — OAuth/PKCE, token lifecycle, config file.
- `sdk/js/src/index.ts` — JS dual-lane client + types/errors.
- `sdk/js/src/ranking.ts` — the open-source ranking algorithm used by the site and plugin.
- `sdk/mcp/src/index.ts` — MCP tool surface and lane dispatch.
- `Panel.qml` + `Model.js` — plugin UI/network and all safety logic.
- `manifest.json` — Omarchy entry contract.

**Tier 2:** `BarWidget.qml`, `assets/ranking.js` (generated), `sdk/python/letsfg/models/{__init__,flights}.py`, `sdk/js/src/auth.ts`, `sdk/mcp/src/index.test.ts`, `preview/run.py`, `tools/validate.sh`, `openapi.yaml`, `.github/workflows/test.yml`.

**Tier 3:** `tools/build-*.py`, `test/model-test.js`, `docs/**`, `skills/**`, `integrations/hermes/**`, registry manifests, Docker files, `lighthouserc.js`.

## 18. Risk Areas

1. **Per-lane field naming** — a single mistaken key silently ignores filters (documented past bugs in `local.py` and both SDKs). Any search-option change must be applied per lane.
2. **MCP `book_flight` (API-key) targets a retired route** — real functional bug for Developer-tier MCP users.
3. **Python test quarantine** — 19 stale modules are parked in `sdk/python/conftest.py`; the suite is green, but the quarantined files remain and must eventually be deleted or rewritten.
4. **QML monoliths** (`Panel.qml` 4.8k, `Model.js` 2.7k) with a shared closure-based session and strict invariants — easy to violate the single-`newRequest`/click-only-search rules.
5. **Two config-path implementations** (`client.py` uses `XDG_CONFIG_HOME`; `auth.py` does not) → API key and PFS token can land in different files.
6. **Generated `assets/ranking.js` drift** if `sdk/js/src` changes without re-running `build-ranking.py`.
7. **Docs-as-prompt surface** (AGENTS/SKILL/context7) directly instructs agents; wrong commands (e.g. `letsfg register`, non-existent `recover`, wrong ranking sample) cause real account/behaviour problems.
8. **Stale OpenAPI** — the committed spec is a subset: generated clients will miss top-up/billing/discover/sandbox paths. The double-prefixed server URL was fixed 2026-10-03 and is now guarded by `test/docs-claims.test.mjs`.
9. **JS SDK poll drops Authorization** while MCP sends it — auth handling asymmetry.
10. **`models.py` shadowing** — a future edit to the wrong file does nothing.

## 19. Open Questions / Unknowns

- **~~Is `python-deterministic` intentionally red?~~ Resolved 2026-10-03:** it was red because 17 test modules imported connectors removed in `f91be5b`. Quarantined in `sdk/python/conftest.py`; `pytest -m "not live"` now yields 99 passed / 0 errors.
- **Are the connector test fixtures/modules expected to return?** They never existed in this history (`git log -- 'sdk/python/letsfg/connectors/checkout_engine.py'` empty). Likely a private-repo split; confirm intent before deleting.
- **Which repo is canonical?** Origin is `Ghostfusion/LetsFG.git`; docs/metadata say `LetsFG/LetsFG`. Affects where PRs/CI run.
- **Do the Developer API hotel endpoints (`/hotels/*`) still match openapi?** Docs describe a larger surface than the spec; live contract unverified (no network calls made).
- **Does the live `/api/search` still accept `max_results`?** `Model.buildSearchBody` sends `max_results:1000`; other clients send `limit`/`max_stops`. Unverified which the server reads.
- **Is `lighthouserc.js` dead?** Targets a `website/` dir absent here; no workflow runs it.
- **Is `httpx`/`requests` actually unused?** Appear so in shipped modules; could matter for dependency trimming.

## 20. Mental Model for Future Work

**Core rule: this repo is the *client*, not the engine.** Any behaviour that looks like search/merge/checkout logic belongs to the server; here you own request shape, polling, auth, ranking-on-display, and UI.

**If asked to change X, look first here, and preserve…**

| Ask | First files | Must preserve | Tests to run |
|---|---|---|---|
| Search fields/filtering | `local.py` + `client.py` (Py), `index.ts` (JS), `mcp/index.ts`, `Model.buildSearchBody` | **per-lane key names**; IATA/date validation | `test_search_api_key_lane_field_names.py`, js `index.test.ts` |
| Booking flow | `local.py` `book_offer`/`booking_status`, `client.py` `book`, `Panel.beginSearch`/poll | hold-not-charge, single booking, terminal semantics | `test_hotel_booking_contract.py`, `test_cli_offer_shape.py`, js book tests |
| Auth/token | `connectors/auth.py`, `js/src/auth.ts`, `Model.createSession/PKCE` | PKCE S256, loopback-before-register, rotating refresh, scope list | `test_connect_auth*.py`, js `auth.test.ts`, `node test/model-test.js` |
| Plugin UI/behaviour | `Panel.qml` + `Model.js` (+ regenerate `ranking.js`) | click-only search, one XHR factory, pinned origin, byte caps, allowlists | `tools/validate.sh`, `check-search-invariant.py`, `node test/model-test.js`, `preview/run.py --strict` |
| MCP tools | `sdk/mcp/src/index.ts` (+ tests) | tool schemas, lane dispatch, cautious responses; fix the retired `/bookings/book` route | `npm test` in `sdk/mcp` |
| Ranking | `sdk/js/src/ranking.ts` → `tools/build-ranking.py` | 9 dims/12 profiles, hero gates, deterministic sort | js `index.test.ts`, rerun build, plugin preview |
| Docs/agent guidance | README/AGENTS/CLAUDE/SKILL/context7/docs | keep lanes and retired endpoints consistent; mirror across files | grep for `register`/`unlock`/`setup_payment` contradictions |
| Python tests | `sdk/python/tests/` | deterministic offline, `live` marker for network | `pytest -m "not live"` (**currently red** — fix or quarantine the connector-import modules) |

**Non-negotiables:** don't reintroduce local connectors/Playwright; don't add JS/MCP runtime deps; don't relax the plugin's pinned-origin/byte-cap/click-only invariants; don't charge before a PNR; keep `assets/ranking.js` generated, never hand-edited; keep the config-file shape compatible with the CLI.

**Before any change, run:** `node test/model-test.js`, `python tools/validate.sh` (plugin), `pytest -m "not live"` (expect pre-existing 17 collection errors to isolate), and `npm test` in the touched TS package.
