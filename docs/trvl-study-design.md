# LetsFG × trvl — Design Study

**Status:** Investigation complete; recommendations not yet implemented.
**Date:** 2026-10-03
**Subject:** `https://github.com/MikkoParkkola/trvl` (Go travel MCP server + CLI).
**Scope:** What LetsFG can learn, what it should not copy, and the target design for each adoptable pattern.
**Method:** shallow clone of `trvl` at `D:/Users/vince/_probe/trvl`, read-only inspection of source and docs. Evidence is cited as `file:line`. No LetsFG code was changed. The `OPENROUTER_API_KEY` in `.env` was not needed — the repo itself was the primary source.

Companion document: [`trvl-study-implementation.md`](trvl-study-implementation.md) (phased change plan, acceptance criteria, tests).

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
sources: [{ id, status, results, retry_after_ms?, fix_hint_code?, detail? }]
```
`no_results` and `timeout` are distinct; `fix_hint_code` is a small closed enum. Keep the human text and the machine envelope on separate content blocks (§2.2).

### 2.2 Structured results + audience-annotated content — `[ADOPT]`

**trvl:** `ToolCallResult{Content[], StructuredContent, IsError}` with a per-tool `outputSchema` (`mcp/protocol.go:141-166`), and content blocks annotated `audience:["user"] priority:1.0` (summary) and `audience:["assistant"] priority:0.5` (JSON) (`mcp/tools.go:636-649`). Tool failures are `isError:true` with text — never a JSON-RPC error (`mcp/server.go:549-568`).

**LetsFG:** tools define only `inputSchema`; results are a single `text` block. `structuredContent`, `outputSchema` and annotations are absent.

**Target design:** every tool gets an `outputSchema` and returns `structuredContent` plus the existing text. Split the text content into a user summary and an assistant JSON block. This is additive and client-compatible (MCP clients ignore unknown fields they do not use).

### 2.3 `tools/list` size control and callable-but-unadvertised tools — `[ADOPT, opt-in]`

**trvl:** advertises **one** `travel` router tool and keeps ~66 legacy handlers callable. `tools/list` and `tools/call` are decoupled: `handleToolsList` returns the advertised slice, `handleToolsCall` resolves the handler map regardless of advertisement (`mcp/server.go:480-486` vs `:489+`). A tripwire test pins the counts (`mcp/alias_count_test.go`, `expectedCompatibilityAliasCount = 66`), and `TRVL_MCP_TOOL_MODE=legacy|compat|full` restores the explicit list (`mcp/tools_smart.go:68-74`). Advertised-router claims are ~378 tokens vs ~33,500 for the full list (~98.9%) — but note the token figures are **prose-only and not reproducible** from the repo (no tokenizer/benchmark exists; `[INFERENCE]` measured offline).

**LetsFG:** 14 tools, each with a very long description (the `search_flights` description alone is ~2 KB of prose, `sdk/mcp/src/index.ts:411-443`). The list is a real context cost, and the descriptions carry rules (split-ticket warning, Starlink semantics) that the agent must read.

**Target design:** do **not** collapse to one router today — 14 tools is not 66, and a router adds an indirection an agent can get wrong. Instead:
1. Move long operational guidance out of `tools/list` into the existing `letsfg://guide` resource and shorten descriptions to their contract.
2. If/when the list grows past ~20 tools, add a router mode behind an env flag (`LETSFG_MCP_TOOL_MODE=legacy|router`) with a count tripwire test.

### 2.4 Honest completeness gating — `[ADOPT]`

**trvl:** `Completeness{complete|partial|blocked}` plus `MayClaimExhaustive()` and `IncompleteNote()` (`internal/models/evidence.go:95-160`). Renderers refuse superlatives when a source did not answer: *"Checked X of Y providers; <ids> did not respond — results may be incomplete."* Same principle for prices: a lead-in price is `unverified` and is **never** stamped with a check timestamp (`internal/models/hotel_price_trust.go:9-19`, `internal/models/accommodation.go:398-411`).

**LetsFG gap:** the MCP guide tells the agent to say "no flights found"; nothing stops it asserting "the cheapest" after a partial backend response. Our split-ticket late-merge wait is a partial analogue (we already gate on `split_ticket_pending`/`gf_enrich_pending`).

**Target design:** the §2.1 envelope gates the language. `no_results` may be stated as fact; `partial` must be narrated; `blocked` must not be. If the server exposes per-source counts, carry them; otherwise carry `completeness` from the search status.

### 2.5 Local, redacted, non-blocking health log — `[DEFER]`

**trvl:** append-only JSONL at `~/.trvl/health.jsonl` (`internal/providers/health_log.go:30-71`) with 1 MB rotation, a 256-buffered channel that **drops entries rather than slow the search path**, and `logredact.Text` applied at rest. One merged `StatusRow` view feeds three surfaces: CLI `trvl status`, an MCP `provider_health` tool, and an auto-refreshing loopback `/dashboard` (`internal/providers/status_report.go:66-140`).

**LetsFG:** no client-side health record. This only becomes valuable once LetsFG owns multiple sources (it does not — the engine is server-side), or if we add client-side health for *our own* transport (auth refresh failures, rate-limit hits, late-merge duration).

**Target design (deferred, scoped):** a minimal `~/.letsfg/health.jsonl` for *client* events only — search latency, terminal status, late-merge wait, auth-refresh outcome. Do not build a dashboard. Revisit if we add a second lane or per-endpoint drift.

### 2.6 Atomic state writes with restrictive permissions — `[ADOPT]`

**trvl:** `internal/atomicjson` is the single temp+fsync+rename implementation for every state file; the temp file is opened `O_CREATE|O_EXCL` at `0600` **before** writing (closing a umask TOCTOU window), fsync'd, then renamed, with a Windows remove-then-rename fallback (`internal/atomicjson/atomicjson.go:29-93`). Orphaned temps are reported by a dry-run-by-default `trvl tempfiles` command (`cmd/trvl/tempfiles.go:34-73`).

**LetsFG gap (verified):** `sdk/js/src/auth.ts:67-76` does `writeFileSync(...)` then `chmodSync(0o600)` — a crash mid-write can truncate `config.json` (holding the bearer **and rotating refresh token**), and the file is briefly world-readable under a permissive umask. The Python writers (`connectors/auth.py:_save_config`, `client.py:_save_config`) are also non-atomic.

**Target design:** one shared atomic-write helper per language (temp `0600 O_EXCL` → fsync → rename), used by every writer of `~/.letsfg/config.json`. Rotating refresh tokens make a torn write a re-auth event, so this is a correctness fix, not cosmetics.

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

**Target design:** a small docs-claims test (pytest or node) that asserts: package versions match between `package.json`/`pyproject.toml`/`server.json`/`README` minimums; the MCP tool list in `docs/mcp.md` equals the registered tool names; `openapi.yaml`'s `servers` + path prefixes compose to the documented URLs; every local markdown link resolves. This is cheap and catches the entire class.

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
| One-router tool with 66 hidden aliases | We have 14 tools, not 66; indirection costs agent accuracy. Revisit past ~20. |
| ML-DSA/cosign release signing | Disproportionate for a client SDK on npm/PyPI. |
| PolyForm Noncommercial licence terms | Our repo is MIT; do not import trvl code. |
| `capabilities/*.yaml` fulcrum manifests | Not loaded even by trvl; an external-gateway artifact we have no gateway for. |
| A local `/dashboard` | No local server surface in LetsFG's client; not worth the attack surface. |

---

## 4. Decision register

| # | Decision | Rationale | Reversal trigger |
|---|---|---|---|
| D1 | Adopt typed status + completeness envelope in MCP results | Highest-value gap: empty-vs-failed is currently indistinguishable | If the hosted API exposes richer status itself, carry it through instead |
| D2 | Adopt `outputSchema` + `structuredContent` + audience annotations | Additive, client-compatible, improves agent reliability | If the MCP client matrix rejects unknown fields (test before rollout) |
| D3 | Do **not** adopt a single-router tool now | 14 tools is manageable; agent accuracy beats token savings | Tool count exceeds ~20, or a measured token budget is exceeded |
| D4 | Adopt atomic `0600` writes for `~/.letsfg/config.json` | Torn write = lost rotating refresh token = forced re-auth | None — this is a defect fix |
| D5 | Adopt docs-claims + link test | ~15 known doc/code contradictions, currently unguarded | None |
| D6 | Adopt regression-test gate for TS packages | Mirrors our own `test-coverage-gate`, cheap to add | If false-positive rate on doc-only PRs is high |
| D7 | Defer client-side health log / dashboard | Only one client-owned source; low value today | A second lane, or repeated transport drift |
| D8 | Defer SSRF guard | No user-supplied fetch target exists | `LETSFG_BASE_URL` honoured in a server context, or a callback added |
| D9 | Defer AGENTS/CLAUDE audience split to a staged pass | Large doc rewrite; do after P0/P1 | — |

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

- Does the hosted `letsfg.co` search response expose per-source counts/statuses, or must the client synthesise `completeness` from the search `status`? Resolve by inspecting a live `/api/results/{id}` payload.
- Will MCP clients in our matrix (Claude, Cursor, Windsurf, ChatGPT) accept `structuredContent` + annotations without degrading? Resolve with the existing `sdk/mcp` spawn-based test harness plus one manual client check.
- Is the `openapi.yaml` double-prefix a spec error or a server quirk? The familiarization report flags it; a docs-claims test would force the answer.
- Do we want a `DESIGN.md` at root when `docs/architecture-guide.md` already exists? Prefer extending the existing doc and linking, to avoid a third architecture surface. `[INFERENCE]`
