# LetsFG × trvl — Design Study

**Status:** Investigation complete; recommendations not yet implemented.
**Date:** 2026-10-03
**Subject:** `https://github.com/MikkoParkkola/trvl` (Go travel MCP server + CLI).
**Scope:** What LetsFG can learn, what it should not copy, and the target design for each adoptable pattern.
**Method:** shallow clone of `trvl` at `D:/Users/vince/_probe/trvl`, read-only inspection of source and docs. Evidence is cited as `file:line`. No LetsFG code was changed. The `OPENROUTER_API_KEY` in `.env` was not needed — the repo itself was the primary source.

Companion document: [`trvl-study-implementation.md`](trvl-study-implementation.md) (phased change plan, acceptance criteria, tests).

**Architectural principle (owner review, 2026-10-03).** *Borrow client-side contracts and engineering patterns from trvl; do not reproduce trvl's provider/search-engine architecture inside LetsFG unless LetsFG explicitly assumes ownership of provider acquisition.* trvl is instructive because it owns its providers end to end; we do not. A feature that only makes sense if you own acquisition (adapters, anti-bot, provider health, destination guards) is out of scope here, however good it looks.

**Scope boundary.** This study stays narrow: it is about what a *client* should learn from a *peer client*. The product it serves — a continuously running First-Class fare scanner over destination pools and flexible dates — is designed separately in [`first-class-fare-scanner-design.md`](first-class-fare-scanner-design.md), which depends on the contract work here (status, completeness, freshness, verified-vs-indicative price) but adds its own layer. Nothing about the scanner belongs in this document.

---

## 1. What trvl is, and why it is comparable

trvl is a single static Go binary that acts as a **local, keyless travel search engine**: an MCP server + CLI over flights, hotels, ground transport (22 providers), cars, weather and destination intel. It reverse-engineers public endpoints (Google `batchexecute`, Kiwi REST, Booking/Airbnb SSR caches) and merges results itself (`DESIGN.md` §2-4).

LetsFG is the **opposite split** — the engine runs server-side at `letsfg.co`; this repo is the client surface (Python/TS/QML + MCP). So the comparable surface is narrow but real, and it is exactly the part under our control:

| Area | trvl | LetsFG | Comparable? |
|---|---|---|---|
| Search engine | local Go | hosted (`letsfg.co`) | **No** — not ours to change |
| MCP server | `mcp/` (Go) | `sdk/mcp/src/index.ts` (TS) | **Yes** |
| CLI | `cmd/trvl` (cobra) | Python Typer + TS cli.ts | **Yes** |
| Client build/CI/release | GoReleaser + GitHub Actions | hatchling/tsup + GitHub Actions | **Yes** |
| Docs system | README/DESIGN/ARCHITECTURE/ROADMAP/CHANGELOG/AGENTS/CLAUDE | README/docs/** + AGENTS/CLAUDE/SKILL/context7 | **Yes** |
| Provider adapters | 24 embedded definitions | none (server-side) | **No** |
| Anti-bot / TLS fingerprint | core concern | not applicable | **No** |
| Booking | never books | books via hosted service | Different |

**Licence caveat.** trvl is PolyForm Noncommercial 1.0.0. This study borrows *ideas and interfaces*, not code. No trvl source is copied into LetsFG.

---

## 2. Transferable patterns (evidence → what it would mean for LetsFG)

Each pattern states the trvl mechanism, the evidence, and the LetsFG analogue. `[ADOPT]` / `[DEFER]` / `[REJECT]` is the design recommendation; it is carried into the implementation plan.

### 2.1 Typed per-source status taxonomy — `[ADOPT]`

**trvl:** every search returns per-provider rows with a status enum, not a boolean. `internal/models/evidence.go:18-31` defines 13 statuses, including the load-bearing pair `checked_no_hit` (queried, valid empty) vs `timeout` (outcome **unknown**). Comment: *"A timeout must never render as 'no results' — that is a lie by omission."* A `FixHintCode` enum (`internal/providers/fixhint.go:14-23`: `AKAMAI_BLOCK`, `RATE_LIMITED`, `COOKIE_EXPIRED`, …) gives the client LLM an actionable one-line remedy, surfaced per search and aggregated in a stable `StatusRow` wire contract (`internal/providers/status_report.go:12,19-36`).

**LetsFG gap:** our MCP tools return either a JSON payload or `Error: <e>` with `isError: true` (`sdk/mcp/src/index.ts:1246`). There is no per-source status, no empty-vs-failed distinction, and no machine-readable failure code in tool output. An agent cannot tell "no flights exist" from "search backend timed out".

**Target design:** add an envelope to search/hotel tool results:
```
status: ok | no_results | timeout | rate_limited | auth_required | failed
completeness: complete | partial | blocked
freshness: live | stale | unknown        # added by owner review 2026-10-03
observed_at: <RFC 3339>                  # when the source was actually asked
sources: [{ id, status, results, retry_after_ms?, fix_hint_code?, detail? }]
```
`no_results` and `timeout` are distinct; `fix_hint_code` is a small closed enum. Keep the human text and the machine envelope on separate content blocks (§2.2).

**Why `freshness` (review).** A retained observation is dangerous without it: `current_price = 6200` means nothing unless you also know *when* it was seen and whether it was verified. A scanner that keeps observations must never present a stale price as current, so the two fields are part of the contract from the start rather than a later retrofit. `unknown` is the honest default for anything the client did not fetch itself.

**Completeness must be able to grow quantitatively (review).** The enum stays the MCP contract, but the shape should not *prevent* the future form:
```
completeness: { state: complete|partial|blocked, expected: 8, completed: 7, failed: 1 }
```
`MayClaimExhaustive` is then derived, not asserted. This matters for the scanner: a one-source $5,500 result and a seven-source $5,500 result are not the same claim, and only the quantitative form can tell them apart.

### 2.2 Structured results + audience-annotated content — `[ADOPT]`

**trvl:** `ToolCallResult{Content[], StructuredContent, IsError}` with a per-tool `outputSchema` (`mcp/protocol.go:141-166`), and content blocks annotated `audience:["user"] priority:1.0` (summary) and `audience:["assistant"] priority:0.5` (JSON) (`mcp/tools.go:636-649`). Tool failures are `isError:true` with text — never a JSON-RPC error (`mcp/server.go:549-568`).

**LetsFG:** tools define only `inputSchema`; results are a single `text` block. `structuredContent`, `outputSchema` and annotations are absent.

**Target design:** every tool gets an `outputSchema` and returns `structuredContent` plus the existing text. Split the text content into a user summary and an assistant JSON block. This is additive and client-compatible (MCP clients ignore unknown fields they do not use).

**Staged, per owner review (2026-10-03)** — do not ship the elegant part before the load-bearing part:
1. **P0 — `outputSchema` + `structuredContent`.** The contract an agent codes against.
2. **P1 — content separation** (summary for the user, JSON for the assistant).
3. **P2 — audience/priority annotations**, only after the client matrix (Claude, Cursor, Windsurf, ChatGPT) is validated against a real payload. Annotations are presentation, and our own open question admits they are untested here; an unvalidated annotation is a compatibility risk with no upside yet.

### 2.3 `tools/list` size control and callable-but-unadvertised tools — `[ADOPT, opt-in]`

**trvl:** advertises **one** `travel` router tool and keeps ~66 legacy handlers callable. `tools/list` and `tools/call` are decoupled: `handleToolsList` returns the advertised slice, `handleToolsCall` resolves the handler map regardless of advertisement (`mcp/server.go:480-486` vs `:489+`). A tripwire test pins the counts (`mcp/alias_count_test.go`, `expectedCompatibilityAliasCount = 66`), and `TRVL_MCP_TOOL_MODE=legacy|compat|full` restores the explicit list (`mcp/tools_smart.go:68-74`). Advertised-router claims are ~378 tokens vs ~33,500 for the full list (~98.9%) — but note the token figures are **prose-only and not reproducible** from the repo (no tokenizer/benchmark exists; `[INFERENCE]` measured offline).

**LetsFG:** 14 tools, each with a very long description (the `search_flights` description alone is ~2 KB of prose, `sdk/mcp/src/index.ts:411-443`). The list is a real context cost, and the descriptions carry rules (split-ticket warning, Starlink semantics) that the agent must read.

**Target design:** do **not** collapse to one router today — 14 tools is not 66, and a router adds an indirection an agent can get wrong. Instead:
1. Move long operational guidance out of `tools/list` into the existing `letsfg://guide` resource and shorten descriptions to their contract.
2. Revisit a router mode behind an env flag (`LETSFG_MCP_TOOL_MODE=legacy|router`) with a count tripwire test when **a measured cost or a measured accuracy problem** appears — not at a tool count.

**Trigger, corrected by owner review (2026-10-03).** "Past ~20 tools" is a warning threshold, not an architectural law: 14 tools at 2 KB each (~28 KB) is a worse context cost than 25 tools at 250 bytes (~6 KB). The real trigger is the combination — advertised description size, measured tool-selection accuracy, and count. Since the description budget is the part we control, shrinking descriptions (step 1) is the first response, and the router is the fallback.

### 2.4 Honest completeness gating — `[ADOPT]`

**trvl:** `Completeness{complete|partial|blocked}` plus `MayClaimExhaustive()` and `IncompleteNote()` (`internal/models/evidence.go:95-160`). Renderers refuse superlatives when a source did not answer: *"Checked X of Y providers; <ids> did not respond — results may be incomplete."* Same principle for prices: a lead-in price is `unverified` and is **never** stamped with a check timestamp (`internal/models/hotel_price_trust.go:9-19`, `internal/models/accommodation.go:398-411`).

**LetsFG gap:** the MCP guide tells the agent to say "no flights found"; nothing stops it asserting "the cheapest" after a partial backend response. Our split-ticket late-merge wait is a partial analogue (we already gate on `split_ticket_pending`/`gf_enrich_pending`).

**Target design:** the §2.1 envelope gates the language. `no_results` may be stated as fact; `partial` must be narrated; `blocked` must not be. If the server exposes per-source counts, carry them; otherwise carry `completeness` from the search status.

### 2.5 Local, redacted, non-blocking health log — `[DEFER]`

**trvl:** append-only JSONL at `~/.trvl/health.jsonl` (`internal/providers/health_log.go:30-71`) with 1 MB rotation, a 256-buffered channel that **drops entries rather than slow the search path**, and `logredact.Text` applied at rest. One merged `StatusRow` view feeds three surfaces: CLI `trvl status`, an MCP `provider_health` tool, and an auto-refreshing loopback `/dashboard` (`internal/providers/status_report.go:66-140`).

**LetsFG:** no client-side health record. This only becomes valuable once LetsFG owns multiple sources (it does not — the engine is server-side), or if we add client-side health for *our own* transport (auth refresh failures, rate-limit hits, late-merge duration).

**Target design (deferred — narrowed by owner review 2026-10-03):** do **not** create another local state subsystem. Define the interface only:

```
ClientTelemetryEvent   # { kind, at, duration_ms?, status?, lane?, detail? }
```

…and initially send it nowhere (a no-op sink). A JSONL file, an OpenTelemetry exporter or a debug logger can implement the same interface later, when there is a question to answer. Writing `~/.letsfg/health.jsonl` now would add a file, a rotation rule and a redaction rule for data nobody reads.

**Important boundary (review).** Client operational telemetry — auth-refresh failure, HTTP failure, latency, late-merge wait — is **not** the same thing as travel observation data (fare observations, price changes, availability, price history). The second is **domain data**, owned by the scanner layer, and mixing the two would put fare history behind a "health log" nobody thinks of as a system of record. See [`first-class-fare-scanner-design.md`](first-class-fare-scanner-design.md) §Contracts.

### 2.6 Atomic state writes with restrictive permissions — `[ADOPT]`

**trvl:** `internal/atomicjson` is the single temp+fsync+rename implementation for every state file; the temp file is opened `O_CREATE|O_EXCL` at `0600` **before** writing (closing a umask TOCTOU window), fsync'd, then renamed, with a Windows remove-then-rename fallback (`internal/atomicjson/atomicjson.go:29-93`). Orphaned temps are reported by a dry-run-by-default `trvl tempfiles` command (`cmd/trvl/tempfiles.go:34-73`).

**LetsFG gap (verified, then fixed):** `sdk/js/src/auth.ts:67-76` did `writeFileSync(...)` then `chmodSync(0o600)` — a crash mid-write could truncate `config.json` (holding the bearer **and rotating refresh token**), and the file was briefly world-readable under a permissive umask. The Python writers (`connectors/auth.py:_save_config`, `client.py:_save_config`) were non-atomic too. **Fixed 2026-10-03** (see the implementation plan P3.1/P3.2): `letsfg/config.py` owns one atomic write (temp at `0600` → `fsync` → `os.replace`) and `auth.ts` mirrors it with `renameSync`.

**Target design:** one shared atomic-write helper per language (temp `0600 O_EXCL` → fsync → rename), used by every writer of the config file. Rotating refresh tokens make a torn write a re-auth event, so this is a correctness fix, not cosmetics.

**Priority (owner review, 2026-10-03): P0, not "part of a broader plan".** Correctness/security first, MCP contract second, scanner foundations third — and it is already landed, so the remaining P0 items are the canonical path (done) and credential ownership (done).

### 2.7 Single owner for env/credential rules — `[ADOPT]`

**trvl:** `internal/consent` is stdlib-only specifically so several packages read one answer without an import cycle, and it removed a duplicated rule that was held together by a cross-package test (`internal/consent/consent.go:10-20`). Decline parsing is deliberately liberal (anything but `""`/`0`/`false` declines) and re-checked at the sink, not the entry (`consent.go:103-108`; `cookie_vault.go:86-101`).

**LetsFG gap:** credential resolution was duplicated across `sdk/js/src/auth.ts`, `sdk/python/letsfg/connectors/auth.py`, `sdk/python/letsfg/client.py`, `sdk/mcp/src/index.ts` and `Model.js`, with **two different config directories** (`client.py` honoured `XDG_CONFIG_HOME`; the others did not — see familiarization §14). **Fixed 2026-10-03:** one store at `~/.letsfg/config.json` (`%APPDATA%\.letsfg\config.json` on Windows) resolved by `letsfg/config.py`, with the old Developer-key paths read as migration fallbacks. Working-agreement rule 1's dangling `scripts/strip-adapter-env.bash` clause was removed by owner decision the same day.

**Target design:** document the canonical credential/env list in one place; make all config paths resolve through one function per language; add a test asserting the four writers agree on the path and the JSON shape.

### 2.8 SSRF/destination guard for outbound fetches — `[DEFER, review]`

**trvl:** one policy (`internal/providers/destination.go`) enforced at **dial time** on the resolved IP (`Dialer.Control`), refusing loopback/private/link-local/unspecified, with a **separate** opt-in for a private *proxy* (`TRVL_ALLOW_PRIVATE_PROXY`) so obeying a corporate proxy cannot disable the destination guard (`destination.go:89-110,184-224`; `proxy_split_test.go`). Proxy host and destination are pinned independently.

**LetsFG:** our clients talk to a fixed origin (`letsfg.co`); the plugin pins the origin and allowlists image hosts. So there is no user-supplied fetch target today, and this is **not currently a gap**. It becomes relevant only if we add a configurable base URL or a server-side callback.

**Target design:** defer; if `LETSFG_BASE_URL` is ever honoured in a long-lived server context, adopt a single guarded transport rather than a per-call allowlist.

### 2.9 Docs as a tested interface — `[ADOPT]`

**trvl:** `cmd/trvl/docs_freshness_test.go` asserts exact release-claim strings across `ROADMAP.md`, `CHANGELOG.md`, `.goreleaser.yaml`, `docs/COMPARISON.md`, `docs/POSITIONING.md` and `docs/index.html`, requires version-aware install markers, and fails on broken local markdown links (`docs_freshness_test.go:37-103`). `public_claims_test.go` pins the tool/alias/command/detector counts across seven surfaces (`public_claims_test.go:238-491`).

**LetsFG gap:** the familiarization report found ~15 doc-vs-code contradictions (e.g. `openapi.yaml` server URL double-prefixing `/api/v1`, `SECURITY.md` version table at 1.0.x, `CLAUDE.md` claiming zero Python deps, `docs/TESTING.md` referencing directories absent from this repo). Nothing prevents drift.

**Target design:** a small docs-claims test (pytest or node) that asserts: package versions match between `package.json`/`pyproject.toml`/`server.json`/`README` minimums; the MCP tool list in `docs/mcp.md` equals the registered tool names; `openapi.yaml`'s `servers` + path prefixes compose to the documented URLs; every local markdown link resolves. This is cheap and catches the entire class. **Landed 2026-10-03** as `test/docs-claims.test.mjs` (9 assertions) plus `test/workflow-hygiene.test.mjs`.

**Staged, per owner review (2026-10-03)** — do not turn hygiene into a project sink:
1. **P0 — fix the factual contradictions** that mislead a user or developer (a version table three majors out of date, runnable instructions for directories that do not exist, a stale credential model). *Done.*
2. **P1 — automated checks** so they cannot come back. *Done.*
3. **P2 — audience restructuring** (one audience per document: `AGENTS.md` for agents *using* LetsFG, `CLAUDE.md` for agents *editing* it, reference material into `docs/**`). **Lowest priority:** it is a readability improvement, and reorganising 50 KB of agent-facing prose while the MCP contract is still moving would be work done twice.

### 2.10 Evidence-backed release/merge gates — `[ADOPT, subset]`

**trvl:** a regression-test merge gate: for PRs whose title or commits match `^(fix|hotfix|bugfix)(\(|:| )`, every changed source directory must also contain a changed `*_test.go` (`regression-test-gate.yml`), with an auditable bypass label. Coverage is a hard 80% gate. A single `make dod` means "CI would accept this". Tool versions are SHA-pinned and a `check-workflow-hygiene.sh` guard fails CI on an unpinned `uses:`.

**LetsFG:** `.github/workflows/test.yml` has a `test-coverage-gate` for **new TS files only**. The Python gate (`pytest -m "not live"`) **currently fails collection**: 17 test modules import `letsfg.connectors.*` packages removed in June 2026 (`f91be5b`). No doc-claims or link tests exist.

**Target design:** fix/quarantine the broken modules first (they block the gate outright), then add (a) a regression-test gate for TS packages, (b) a docs-claims test in CI, (c) SHA-pinning of workflow actions.

### 2.11 AGENTS.md as an executable install prompt — `[ADOPT]`

**trvl:** `AGENTS.md` is written for an AI to *execute*: a human one-liner ("Give this URL to your AI assistant and say 'set up trvl'"), numbered steps each with a command and an **expected output literal** (`# Expected: trvl 1.21.0 (or later)`), a scripted "Tell the user: …" sentence, and a symptom→fix troubleshooting table (`AGENTS.md:1-67,612-637`). `CLAUDE.md` is a separate, explicitly divergent brief for an AI *editing* the repo (`CLAUDE.md:57-67`).

**LetsFG:** `AGENTS.md` is 50 KB and mixes product usage, API reference and repo guidance; the same claims are duplicated across README/SKILL/context7. `docs/agent-guide.md` repeats yet more.

**Target design:** pick one audience per document. `AGENTS.md` = agent *using* LetsFG (install prompt with expected outputs). `CLAUDE.md` = agent *editing* LetsFG (conventions, commands, invariants). Move the duplicated reference material into `docs/**` and link. Do not attempt this in one pass; it is a staged consolidation.

### 2.12 Scheduled drift agents — `[DEFER]`

**trvl:** a nightly `live-probes.yml` runs the real-network suite and, on failure, opens/comments a single labelled tracking issue rather than just going red; a `wizzair-version-sentinel.yml` discovers a rotated upstream API version, opens a **test-gated, reviewable PR**, and auto-merges only when it compiles and tests green.

**LetsFG:** our upstream is a hosted API whose contract is documented in `openapi.yaml`. A nightly probe that searches one live route and asserts the response envelope (status names, field names, error codes) would catch server drift before a user does.

**Target design (deferred):** add `live-probes.yml` once we have a stable opt-in live test. Do not build a version sentinel — we do not pin a brittle upstream constant.

---

## 3. What NOT to copy

| trvl thing | Why not |
|---|---|
| Local scraping / anti-bot / TLS+HTTP2 fingerprinting | Our engine is server-side; this is not a client concern. |
| Browser-cookie harvesting | Privacy load we do not need; our auth is OAuth 2.1 + PKCE. |
| One-router tool with 66 hidden aliases | We have 14 tools, not 66; indirection costs agent accuracy. Revisit only on a *measured* context-cost or tool-selection problem, not on a count. |
| trvl's provider-acquisition architecture (adapters, anti-bot, TLS/HTTP2 fingerprinting, cookie harvesting, provider health dashboards, destination guards) | **We do not own acquisition** — the engine is server-side. Adopting any of it would mean building a second, worse engine inside the client. This is the architectural principle at the top of this document, and the reason so many trvl features are `[REJECT]` here on principle rather than on effort. |
| ML-DSA/cosign release signing | Disproportionate for a client SDK on npm/PyPI. |
| PolyForm Noncommercial licence terms | Our repo is MIT; do not import trvl code. |
| `capabilities/*.yaml` fulcrum manifests | Not loaded even by trvl; an external-gateway artifact we have no gateway for. |
| A local `/dashboard` | No local server surface in LetsFG's client; not worth the attack surface. |

---

## 4. Decision register

| # | Decision | Rationale | Reversal trigger |
|---|---|---|---|
| D1 | Adopt typed status + completeness envelope in MCP results | Highest-value gap: empty-vs-failed is currently indistinguishable. **Landed 2026-10-03** (`sdk/mcp/src/envelope.ts`), with `freshness` added to the contract per review | If the hosted API exposes richer status itself, carry it through instead |
| D2 | Adopt `outputSchema` + `structuredContent`; annotations later | Additive, client-compatible, improves agent reliability. **Staged by review:** `outputSchema`/`structuredContent` at P0, content separation at P1, audience annotations at P2 after a client-matrix check | If the MCP client matrix rejects unknown fields (test before rollout) |
| D3 | Do **not** adopt a single-router tool now | 14 tools is manageable; agent accuracy beats token savings — but the trigger is the *description budget plus a measured accuracy problem*, not a count | Advertised description size or tool-selection accuracy measurably degrades |
| D4 | Adopt atomic `0600` writes for the config file | Torn write = lost rotating refresh token = forced re-auth | None — this is a defect fix, and **it has landed** (2026-10-03, P0) |
| D5 | Adopt docs-claims + link test | ~15 known doc/code contradictions, currently unguarded | None — **landed** (`test/docs-claims.test.mjs`, 9 assertions) |
| D6 | Adopt regression-test gate for TS packages | Mirrors our own `test-coverage-gate`, cheap to add | If false-positive rate on doc-only PRs is high |
| D7 | Defer client-side health log / dashboard; **define the interface only** (`ClientTelemetryEvent`, no-op sink) | Only one client-owned source; low value today, and a file plus rotation plus redaction for data nobody reads is a subsystem we would regret | A second lane, or repeated transport drift |
| D8 | Defer SSRF guard | No user-supplied fetch target exists | `LETSFG_BASE_URL` honoured in a server context, or a callback added |
| D9 | Defer AGENTS/CLAUDE audience split to a staged pass | Large doc rewrite; do *after* P0 (facts) and P1 (checks), and only when the MCP contract has stopped moving | — |
| D10 | **Architectural principle:** borrow trvl's client-side contracts and engineering patterns; never reproduce its provider/search-engine architecture unless LetsFG assumes ownership of provider acquisition | Keeps the client a client. Prevents "trvl has a nice feature, let's port it" from silently importing an engine we do not maintain | LetsFG explicitly takes ownership of provider acquisition |
| D11 | **Boundary:** travel observation data (fares, price history, availability) is *domain data*, not telemetry — it belongs to the scanner layer's store, never to a health log | A system of record must be named as one; fare history behind `health.jsonl` is undiscoverable and unqueryable | — |
| D12 | Carry **verified-vs-indicative** price semantics in the contract, but put the *policy* (what may alert) in the scanner, not in the client | The client can only report what it observed; "is this good enough to act on" is a product decision with a different owner | — |

---

## 5. Target design summary (per area)

```
MCP server (sdk/mcp/src/index.ts)
  tools/list        → short contract descriptions; long guidance moved to letsfg://guide
  tools/call        → unchanged dispatch, plus:
                        outputSchema per tool
                        structuredContent = { status, completeness, sources[], data }
                        content = [user summary (audience:user), JSON (audience:assistant)]
  protocolVersion   → stays 2024-11-05 today; version-negotiation decision recorded (ADR)

State (sdk/{python,js,plugin})
  ~/.letsfg/config.json written only through an atomic temp(0600,O_EXCL)→fsync→rename helper
  one config-path resolver per language; one documented credential/env list

Docs
  README/AGENTS/SKILL/context7 → one audience each; shared facts live once and are linked
  CHANGELOG.md (Keep a Changelog + SemVer), ROADMAP.md, DESIGN.md added
  docs-claims + local-link test in CI

CI
  fix/quarantine 17 uncollectable Python test modules
  regression-test gate (TS packages); docs-claims test; SHA-pinned actions
```

---

## 6. Open questions

**Client/contract questions (lower value — mostly resolved or cheap to resolve):**

- Q1 Does the hosted `letsfg.co` search response expose per-source counts/statuses, or must the client synthesise `completeness` from the search `status`? Resolve by inspecting a live `/api/results/{id}` payload.
- Q2 Will MCP clients in our matrix (Claude, Cursor, Windsurf, ChatGPT) accept `structuredContent` + annotations without degrading? Resolve with the existing `sdk/mcp` spawn-based test harness plus one manual client check. This gates D2's P1/P2 stages only.
- Q3 ~~Is the `openapi.yaml` double-prefix a spec error or a server quirk?~~ **Answered and fixed 2026-10-03:** the server URL and the path keys both carried `/api/v1`; the spec was wrong, and `test/docs-claims.test.mjs` now pins the composition.
- Q4 Do we want a `DESIGN.md` at root when `docs/architecture-guide.md` already exists? Prefer extending the existing doc and linking, to avoid a third architecture surface. `[INFERENCE]`

**Scanner-layer questions (owner review: these are the ones that matter to the product).** Each carries what is already known from this repository's own API documentation, so the list is a work queue rather than a blank page.

- **Q5 — Does LetsFG expose the search dimensions the scanner needs?** Partly, and the gaps are structural. Supported today: one origin, **one** destination, a single `date_from` (+ `return_date`), `cabin_class: "F"` for First, `max_stops`, passengers, currency, `limit`, `sort`, and a departure-time window (`docs/api-search.md:20-45`, `docs/cli-reference.md:55`). **Not supported anywhere in the API: a departure *range*, a return range, or a trip-duration/nights window.** A scanner over flexible dates must therefore fan out over dates itself and carry the scheduling cost — which is precisely what makes a Search Planner necessary rather than optional.
- **Q6 — Can it search multiple destinations in one operation?** Yes, two primitives, with very different semantics: `POST /flights/discover` takes **up to 20 destinations from one origin in a single call** and returns **indicative** prices, billed as **1 search** for the whole batch, 2–5 s (`docs/api-search.md:290-340`); `POST /flights/multi-search` fires N destinations in parallel and bills **1 search per destination** (`docs/api-search.md:20-45`). The discover response even reports per-destination absence honestly (`{"destination":"ORD","price":null,"found":false}`) and carries a `data_note` saying the prices are indicative — which is exactly the verified-vs-indicative distinction the scanner needs, arriving from the server.
- **Q7 — What is the maximum practical search volume?** The ceiling is billing, not throughput: **every destination counts as one search** with no bundle discount, except discover. PFS card limits are 10 per 10 min / 30 per hour / 100 per day; the Developer API is 60 req/min with 200 free searches after each booking and $0.01 per excess search (`AGENTS.md` rate-limit table). A 10-origin × 50-destination × 90-day × 10-duration expansion is therefore not a search plan — it is a budget, and the planner must treat it as one.
- **Q8 — What is the actual polling/rate-limit contract?** Published for the happy path (above), but the **failure direction of the limiter is still unknown** — see implementation plan P1.3. That gap is worth closing before a scheduler depends on it.
- **Q9 — Does the API return stable fare/itinerary identifiers?** Within one search an offer has an `id`, but nothing documented promises stability across searches, and `discover` returns no fare identity at all — only a destination and a price. So **fare identity must be constructed client-side** (see the scanner design's `FareIdentity`); it cannot be inherited.
- **Q10 — How quickly does a returned price expire?** Offers expire ~15 minutes after a search (`AGENTS.md`), and discover prices are explicitly indicative and not bookable. Retained observations therefore need `observed_at` + `freshness` from the moment they are stored (§2.1).
- **Q11 — Can a result be re-verified before alerting?** Yes, and it is the only way to avoid the "headline price, checkout price" failure: re-run `/flights/search` for that specific destination and date pair and compare. That re-check is what the scanner's `price_status: observed → verified` transition models (§2.4; scanner design §Contracts).

---

## 7. Scope boundary

This study answers exactly one question: **what should LetsFG learn from trvl?** It deliberately does not design the product the study serves. The scanner's own requirements, contracts and layering live in [`first-class-fare-scanner-design.md`](first-class-fare-scanner-design.md), which treats this document as a dependency:

```
   LetsFG × trvl — Design Study            first-class-fare-scanner-design.md
   ─────────────────────────────           ────────────────────────────────────
   MCP contracts                           destination/origin pools
   status · completeness · freshness       flexible dates · First Class
   client reliability                      search planner · scheduling
   docs/CI/release discipline              fare identity · observations
                                           deal scoring · alerts
```

**Priority order accepted from the review (2026-10-03):** P0 correctness/security → P1 MCP contract → P2 scanner foundations (contracts, planner, scheduler) → P3 intelligence (history, scoring, confidence) → P4 user experience (alerts, then one message format, no dashboard) → P5 optimization (adaptive frequency, budget). The client-side items in P0 and part of P1 are already landed; nothing in P2–P5 may be smuggled in as a "cleanup" under working-agreement rule 4 — it needs its own scope.

**Review provenance.** This document was reviewed by the owner on 2026-10-03. Accepted and applied: the architectural principle (D10), `freshness` in the envelope, the quantitative path for completeness, staged `structuredContent` work, the corrected router trigger, the health-log narrowing plus the telemetry/domain-data boundary (D11), the staged docs plan, the P0 promotion for atomic writes, and the scanner-layer open questions Q5–Q11. The review's central recommendation — *keep this study narrow and put the product architecture in its own document* — is what §6/§7 and the scanner design implement.
