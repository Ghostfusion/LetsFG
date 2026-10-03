# LetsFG × trvl — Design Study

**Status:** Investigation complete; selected P0 changes landed; remaining recommendations not yet implemented.
**Implementation state:**
- **Landed:** D1 (status/completeness envelope — `sdk/mcp/src/envelope.ts`), D4 (atomic `0600` config writes), D5 (docs-claims + link test), the P0 documentation/security corrections, and the credential-path unification.
- **Next:** D2 P0 — `outputSchema` + `structuredContent`; then `freshness` in the envelope.
- **Deferred:** D3 router (until measured evidence exists), D7 telemetry sink (interface only, no store), D8 SSRF guard, D9 documentation restructuring.
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
completeness: complete | partial | blocked     # optionally { state, expected, completed, failed }
freshness: live | stale | unknown              # added by owner review 2026-10-03
observed_at: <RFC 3339>                        # earliest trustworthy observation time (definition below)
sources: [{ id, status, results, retry_after_ms?, fix_hint_code?, detail? }]
```
`no_results` and `timeout` are distinct; `fix_hint_code` is a small closed enum. Keep the human text and the machine envelope on separate content blocks (§2.2).

**Contract invariant — three independent axes, never one state machine (review #2 of this study).**

> **`status` describes the execution outcome; `completeness` describes coverage;
> `freshness` describes temporal validity. None is derived by collapsing the others
> into a single state.**

They combine freely, and all three of these are valid and meaningful:

```
status = ok        completeness = partial   freshness = live      # a search that answered, incompletely, just now
status = timeout   completeness = blocked   freshness = unknown   # we learned nothing
status = ok        completeness = complete  freshness = stale     # a retained observation, still valid as evidence
```

This matters because the consumer reasons about the axes independently: the
scanner will gate an alert on completeness *and* freshness *and* status at once,
and a collapsed enum would force it to re-derive the missing dimensions from the
one that survived.

**Empty has a reason, and the reason is data (adopted 2026-10-03, from
[`fli-study-design.md`](fli-study-design.md)).** An empty result set is three
different facts. Collapsing them is how a client tells a user "there are no flights"
when it never loaded a page:

```
empty_reason = provider_empty    # the source answered and its own result set was empty
               not_loaded        # no usable response at all: timeout, limiter refusal,
                                 # consent interstitial, or a 200 whose payload never arrived
               filtered_out      # the source returned rows; our own filters removed all of them
```

The mapping onto the axes is forced, not a matter of taste:

| `empty_reason` | `status` | `completeness` |
|---|---|---|
| `provider_empty` | `no_results` | per the declared coverage contract — **not** automatically `complete` |
| `not_loaded` | `timeout` / `failed` | `blocked` |
| `filtered_out` | `ok` | `partial` — we narrowed it; the source did not report nothing |

Three rules follow, and each has already been paid for somewhere else:

1. **`no_results` may only be claimed for `provider_empty`.** "HTTP 200 with no
   payload" is a documented, routine failure mode of at least one real provider
   (the fli study §3), so it must never be rendered as an empty market.
2. **`filtered_out` is never "no flights".** A client-side filter cannot back-fill a
   server-side one, so an empty set after our own filtering is evidence about our
   filter, not about the route.
3. **A party-dependent empty carries its caveat.** At least one real source thins
   results for children and infants — measured down to zero rows in premium cabins —
   so an empty answer for a party with children is not evidence that the route is
   unserved. Positive evidence is required before any statement about a route's
   existence.

This is §2.4's completeness rule one layer out: never claim more from an absence
than the absence supports.

**Coverage and result state are separate fields** (same origin, 2026-10-03). One
enum cannot express "we asked and got nothing" *and* "we could not ask properly":

```
coverage_mode   complete | partial | unavailable
result_state    results | confirmed_empty | unavailable
```

| `coverage_mode` | `result_state` | Valid | Means |
|---|---|---|---|
| `complete` | `results` / `confirmed_empty` | ✓ | the declared scope was searched |
| `partial` | `results` / `confirmed_empty` | ✓ | narrowed or degraded coverage — an empty here is **not** absence |
| `unavailable` | `unavailable` | ✓ | we learned nothing |
| `unavailable` | `results` / `confirmed_empty` | **✗** | no usable evidence cannot carry a result |
| `complete` | `unavailable` | **✗** | contradictory |

> **`no_results` is prohibited whenever coverage is degraded.** An adapter allowed to
> emit `{no_results, coverage_mode: partial}` will eventually produce "there are no
> flights on this route" when the client simply could not see them. This table is the
> **canonical** statement; provider designs expand it rather than restating it.

**`observed_at` semantics (review #2 of this study).** One canonical definition,
because `freshness` drives alert eligibility:

> `observed_at` is the **earliest trustworthy** timestamp representing when the
> returned data was observed. If the upstream does not supply one, the client
> **MUST NOT manufacture provider observation time**; it may fall back to client
> receipt time **only** under the explicit semantics below, and it must be possible
> to tell the two apart.

The three timestamps the scanner layer will ultimately want are:

```
requested_at   # when the client initiated the request
observed_at    # when the data was observed — provider-stamped if available,
               # otherwise client receipt time, with the basis recorded
received_at    # when the client finished reading the response
```

The MCP envelope carries `observed_at` plus `observed_at_basis` rather than all
three: the wire contract needs one number and a way to know how much to trust it.
**Three** bases, because an upstream *fetch* is a real thing that is still not an
*observation*:

```
observed_at_basis = provider         the upstream stamped when the data was observed
observed_at_basis = provider_fetch   an upstream fetch happened at a known time, but the
                                     upstream exposes no observation time of its own
observed_at_basis = client_receipt   no upstream time at all; we received it now
```

Manufacturing a provider-stamped time from a client clock is the failure this rule
exists to prevent — it would make a stale fare look freshly observed, and the
scanner's whole purpose is to act on prices at the right moment. Symmetrically,
`provider_fetch` must never be *reported* as `provider`: a "no cache" fetch is not
a provider observation timestamp. (Third value added 2026-10-03 from provider
evidence — see [`serpapi-provider-design.md`](serpapi-provider-design.md) §7. That
lane has since been declined, and the value outlives it: the rule is about *any*
upstream that exposes no observation time of its own.)

**Why `freshness` (review).** A retained observation is dangerous without it: `current_price = 6200` means nothing unless you also know *when* it was seen and whether it was verified. A scanner that keeps observations must never present a stale price as current, so the two fields are part of the contract from the start rather than a later retrofit. `unknown` is the honest default for anything the client did not fetch itself.

**Completeness must be able to grow quantitatively (review #1) — optionally (review #2).** The enum stays the MCP contract, but the shape should not *prevent* the future form:
```
completeness: { state: complete|partial|blocked, expected: 8, completed: 7, failed: 1 }
```
`expected`/`completed`/`failed` are **optional extensions, not a required field on every tool**: a single-origin search reporting `expected = 1, completed = 1, failed = 0` is accounting that adds nothing. The invariant that actually matters is what `complete` is allowed to mean:

> **`complete` must mean the server knows the intended search scope was satisfied —
> not merely that a response arrived.**

A one-source $5,500 result and a seven-source $5,500 result are not the same claim; the quantitative form is how a caller will eventually tell them apart, and `MayClaimExhaustive` is derived from it rather than asserted.

### 2.2 Structured results + audience-annotated content — `[ADOPT]`

**trvl:** `ToolCallResult{Content[], StructuredContent, IsError}` with a per-tool `outputSchema` (`mcp/protocol.go:141-166`), and content blocks annotated `audience:["user"] priority:1.0` (summary) and `audience:["assistant"] priority:0.5` (JSON) (`mcp/tools.go:636-649`). Tool failures are `isError:true` with text — never a JSON-RPC error (`mcp/server.go:549-568`).

**LetsFG:** tools define only `inputSchema`; results are a single `text` block. `structuredContent`, `outputSchema` and annotations are absent.

**Target design:** every tool gets an `outputSchema` and returns `structuredContent` plus the existing text. Split the text content into a user summary and an assistant JSON block. This is additive and client-compatible (MCP clients ignore unknown fields they do not use).

**Staged, per owner review (2026-10-03)** — do not ship the elegant part before the load-bearing part:
1. **P0 — `outputSchema` + `structuredContent`.** The contract an agent codes against.
2. **P1 — content separation** (summary for the user, JSON for the assistant).
3. **P2 — audience/priority annotations**, only after the client matrix (Claude, Cursor, Windsurf, ChatGPT) is validated against a real payload. Annotations are presentation, and the Q2 answer in §6 admits they are untested here; an unvalidated annotation is a compatibility risk with no upside yet.

**`structuredContent` is canonical; text is a rendering (review #2 of this study).**

> The assistant-readable structured payload is **authoritative**. Human-readable
> text is **derived** from it and must not contain information absent from the
> structured contract, unless that text is explicitly marked as explanatory prose.

Otherwise the two representations drift, and the drift is invisible: a summary that
says "cheapest is $412" while the structured payload shows that $412 came from a
`partial` search is exactly how a client ends up asserting something its own
contract denies. The invariant also decides arguments in advance — when the two
disagree, the structured payload is right and the text is a bug.

**Acceptance criterion for the annotations stage (Q2 decision, §6): client
incompatibility must not break the basic tool result.** If a client mishandles an
annotation, the search result must still be usable — annotations are presentation,
so an unverified one is a risk with no upside yet. Protocol behaviour is covered by
the spawn-based MCP harness; presentation needs one manual check per client, which a
harness cannot do.

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

**And what the client may claim without evidence (Q1 decision, §6):** a successful
response is not evidence that every backend was queried, so the client must not
invent provider-level failures — and must not claim `complete` on the strength of a
200 alone. A payload whose list is present and empty may be reported as
`no_results`/`complete`; an uncountable one is `partial`. That is already the shipped
behaviour (`envelopeForData(null)` → `ok`/`partial` in `sdk/mcp/src/envelope.ts`),
so the decision records the implementation rather than proposing a change to it.

### 2.5 Local, redacted, non-blocking health log — `[DEFER]`

**trvl:** append-only JSONL at `~/.trvl/health.jsonl` (`internal/providers/health_log.go:30-71`) with 1 MB rotation, a 256-buffered channel that **drops entries rather than slow the search path**, and `logredact.Text` applied at rest. One merged `StatusRow` view feeds three surfaces: CLI `trvl status`, an MCP `provider_health` tool, and an auto-refreshing loopback `/dashboard` (`internal/providers/status_report.go:66-140`).

**LetsFG:** no client-side health record. This only becomes valuable once LetsFG owns multiple sources (it does not — the engine is server-side), or if we add client-side health for *our own* transport (auth refresh failures, rate-limit hits, late-merge duration).

**Target design (deferred — narrowed by owner review 2026-10-03):** do **not** create another local state subsystem. Define the interface only:

```
ClientTelemetryEvent   # { kind, at, duration_ms?, status?, lane?, detail? }
```

…and initially send it nowhere (a no-op sink). A JSONL file, an OpenTelemetry exporter or a debug logger can implement the same interface later, when there is a question to answer. Writing `~/.letsfg/health.jsonl` now would add a file, a rotation rule and a redaction rule for data nobody reads.

**Important boundary (review).** Client operational telemetry — auth-refresh failure, HTTP failure, latency, late-merge wait — is **not** the same thing as travel observation data (fare observations, price changes, availability, price history). The second is **domain data**, owned by the scanner layer, and mixing the two would put fare history behind a "health log" nobody thinks of as a system of record. See [`first-class-fare-scanner-design.md`](first-class-fare-scanner-design.md) §5 (Contracts).

The two pipelines, side by side — they share no store, because they are different
kinds of fact:

```
client operation  →  ClientTelemetryEvent  →  sink            (no-op today; §2.5, D7)
search + parse    →  FareObservation       →  observation store (scanner, D11)

Search → Observation → Identity → History → Evaluation → Alert      (domain)
ClientOp → ClientTelemetryEvent → sink                              (operations)
```

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

**LetsFG:** `.github/workflows/test.yml` has a `test-coverage-gate` for **new TS files only**. The Python gate (`pytest -m "not live"`) **failed collection** — 17 test modules imported `letsfg.connectors.*` packages removed in June 2026 (`f91be5b`) — and there were no doc-claims or link tests. **Both fixed 2026-10-03:** the modules are parked with recorded reasons in `sdk/python/conftest.py`, the suite collects and runs (`106 passed`), and `test/docs-claims.test.mjs` + `test/workflow-hygiene.test.mjs` now exist and are required checks.

**Target design:** fix/quarantine the broken modules first (they block the gate outright), then add (a) a regression-test gate for TS packages, (b) a docs-claims test in CI, (c) SHA-pinning of workflow actions. **All three landed 2026-10-03**, and the suite collects again.

**Contract invariant (review #2 of this study):**

> **CI must not report a green test gate when the Python suite cannot collect a
> module.** A quarantined module is a known, recorded piece of debt — not a pass.

So quarantine is never the fix; it is the holding pen, and the plan distinguishes
the two explicitly:

```
repair        # the module is rewritten against the current API   ← preferred
quarantine    # parked, with a recorded owner/reason, and a plan to delete or repair
```

Two guards keep the pen from becoming permanent:

1. **Dead entries fail.** Every path in `sdk/python/conftest.py`'s
   `collect_ignore_glob` must exist on disk (`test/docs-claims.test.mjs`) — so a
   parked module that gets deleted cannot linger in the list.
2. **Growth needs a visible decision.** The parked set is pinned to a recorded
   size, so adding a module requires editing the expectation in the same change —
   the same "no silent drift" discipline the workflow-hygiene rules use.

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
| D10 | **Architectural principle (permanent invariant):** borrow trvl's client-side contracts and engineering patterns; never reproduce its provider/search-engine architecture unless LetsFG assumes ownership of provider acquisition | Keeps the client a client. Prevents "trvl has a nice feature, let's port it" from silently importing an engine we do not maintain. The ownership model in §7 is its concrete form | LetsFG explicitly takes ownership of provider acquisition |
| D11 | **Boundary:** travel observation data (fares, price history, availability) is *domain data*, not telemetry — it belongs to the scanner layer's store, never to a health log | A system of record must be named as one; fare history behind `health.jsonl` is undiscoverable and unqueryable | — |
| D12 | Carry **verified-vs-indicative** price semantics in the contract, but put the *policy* (what may alert) in the scanner, not in the client | The client can only report what it observed; "is this good enough to act on" is a product decision with a different owner | — |
| D13 | **One architecture surface:** extend `docs/architecture-guide.md` and link the studies from it; do **not** add a root `DESIGN.md` | A second architecture document makes "which one is authoritative?" a per-reader judgement, which is exactly the ambiguity the docs-claims test exists to prevent | An architecture-guide.md rewrite too large to review in one change |

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

## 6. Decisions on the client/API questions (Q1–Q11)

These began as open questions. The owner decided all eleven on 2026-10-03 from the
evidence already in this study — the document should not keep calling something a
"work queue" once the evidence supports a decision. **Q8 is the one exception and
stays OPEN**, because it needs a live experiment rather than a judgement.

**Q1 — Per-source status in the search response. DECIDED.**
Carry `completeness` in the contract and **synthesise it client-side** when the
hosted API does not expose source-level evidence. This is D1, plus a rule about
what the client may claim:

```
completeness: { state: complete | partial | blocked, expected: null, completed: null, failed: null }
sources: []
```

> A successful response is **not** evidence that every backend was queried. The
> client must not invent provider-level failures the API did not report, **and it
> must not claim `complete` on the strength of a 200 alone**.

A documented empty result may be stated as fact; an uncountable one may not. This is
already how the shipped envelope behaves — `envelopeForData(null)` in
`sdk/mcp/src/envelope.ts` returns `ok`/`partial`, never `complete` — so the decision
matches the implementation rather than proposing a change to it.

**Q2 — MCP client compatibility. DECIDED (with a test still to run).**
Adopt `outputSchema` and `structuredContent`; treat `structuredContent` as
canonical and the text as a rendering (§2.2). **Content audience/priority
annotations stay conditional** on a real client check. Incompatibility must never
break the basic tool result: if a client mishandles an annotation, the search result
must still be usable. Protocol behaviour is covered by the spawn-based MCP harness;
presentation needs one manual check per client, which a harness cannot do.

| Feature | Decision |
|---|---|
| `outputSchema` | Adopt (P0) |
| `structuredContent` | Adopt (P0) |
| Separate human/JSON content | Adopt (P1) |
| Audience/priority annotations | Test first (P2) |

**Q3 — OpenAPI double-prefix. CLOSED.**
It was a **specification error**, not a server quirk: `servers[0].url` and every path
key both carried `/api/v1`, composing to `…/api/v1/api/v1/…`. Fixed and pinned by
`test/docs-claims.test.mjs` on 2026-10-03. No further design work.

**Q4 — A root `DESIGN.md`? DECIDED: no.**
Keep `docs/architecture-guide.md` as the single stable architecture surface and link
the specific studies from it. A root `DESIGN.md` would create a second place where a
reader (or an agent) has to work out which architecture document is authoritative.

```
README.md                → what LetsFG is, quick start
docs/architecture-guide.md → stable architecture and principles   ← extended, not duplicated
docs/**                  → subsystem and API reference
docs/*-study-*.md        → specific investigations/proposals
```

**Q5 — Search dimensions the scanner needs. DECIDED → the Search Planner is
first-class.** Supported: origin, one destination, `date_from` (+ `return_date`),
`cabin_class: "F"`, `max_stops`, passengers, currency, `limit`, `sort`,
departure-time window (`docs/api-search.md:20-45`, `docs/cli-reference.md:55`).
**Absent everywhere: departure range, return range, trip-duration/nights range.**
So `search(origin, destination, date_range)` does not exist and cannot be emulated
by one call; flexible dates mean generating individual searches, each consuming
capacity.

> Build the Search Planner as a first-class component. **Do not implement
> flexible-date scanning as a nested Cartesian-product loop.**

**Q6 — Multiple destinations in one operation. DECIDED (P2).**
Two primitives with different meanings, and the difference is the point:
`POST /flights/discover` takes up to **20 destinations from one origin, billed as
one search**, 2–5 s, **indicative** prices, destination-level absence
(`{"destination":"ORD","price":null,"found":false}`) and a `data_note` saying so
(`docs/api-search.md:290-340`); `POST /flights/multi-search` runs N destinations in
parallel and bills **one search per destination**.

> `discover` is an optimisation for **candidate generation**, not the scanner's
> authoritative fare observation. Indicative prices can never become verified.

```
Candidate generation (discover) → ranking → budget/rate-limit check
    → targeted search (authoritative) → verification
```

**Q7 — Practical search volume. DECIDED (P2): it is a budget problem, not a
maximum.** PFS: 10/10 min, 30/hour, 100/day. Developer API: 60 req/min, 200 free
searches per booking, then $0.01 each; **every destination counts as one search**, no
bundle discount, `discover` excepted.

```
candidate search space → candidate search count → cost estimate
    → rate-limit feasibility → ranking → execution schedule
```

A 10 × 50 × 90 × 10 expansion is not an execution plan — it is a candidate space.
The optimisation algorithm belongs in the scanner design (§6 there).

**Q8 — Limiter failure behaviour. OPEN, and a P0 blocker.**

> **The Search Scheduler cannot be finalised until the rate-limit failure contract
> is established by experiment.** Until it is measured, treat
> `rate_limit_failure_behavior = UNKNOWN` and encode no speculative behaviour.

The published limits describe **capacity**; they say nothing about **failure**.
Before a continuously running scheduler exists, all of this must be measured:

1. What status is returned on crossing the limit — `429` or something else?
2. Is `Retry-After` supplied, and in which form?
3. **Does a rejected request still consume quota?**
4. What is the limit scope — API key, account, IP, endpoint, or a combination?
5. Does concurrency count separately from request rate?
6. What happens after repeated violations — cooldown, temporary block, suspension?
7. Is it enforced the same way on `/flights/search`, `/flights/discover`,
   `/flights/multi-search`?
8. **Does polling `/api/results/{id}` consume the search quota or a separate
   request quota?**

The reason to insist on measuring: a scheduler holding a 100-search plan that
reaches the limit at search 37 must know whether to stop, wait, retry, reschedule,
discard or deprioritise — and none of those is inferable from a published rate.
Do it with a controlled probe (approach the threshold, cross it deliberately, record
status/headers/`Retry-After`, immediately retry once, observe recovery and whether
quota was consumed), without generating unnecessary production traffic.

**Q9 — Stable fare/itinerary identity. DECIDED (P1): construct it client-side.**
An offer carries an `id` within one search; nothing documents that it survives
across searches, and `discover` returns no fare identity at all.

> The API's offer id is an **observation-local identifier**, not a durable scanner
> identity. Never inherit it as one.

The durable forms are the scanner's `ItineraryIdentity` / `OfferIdentity`
(scanner design §5.2), with canonicalisation defined there.

**Q10 — Price expiry. DECIDED (P1): separate offer validity from observation
freshness.** The documented ~15-minute offer lifetime is an **offer-validity hint**,
not the scanner's freshness policy. Three distinct things are persisted:
`observed_at`, `freshness`, `price_status` (scanner design §5.1/§5.4). A stored
observation stays useful historically after the underlying fare has expired; the
scanner's vocabulary covers both cases — a fare past its validity window is `stale`
as an observation and `unavailable` once an authoritative search no longer returns
it. `discover` prices are `indicative` and must not inherit the semantics of a
search offer.

**Q11 — Re-verification before alerting. DECIDED (P1): a verified-before-alert
gate.** Re-running a targeted `/flights/search` for that origin/destination/date
pair is the correct pre-alert check:

```
indicative → observed → verified → alert eligible
```

**With the semantic limit stated explicitly:** re-verification proves the
authoritative search path returned the fare again. It does **not** prove the airline
checkout will honour that price.

> `verified` ≠ guaranteed bookable.

**Summary.**

| Q | Decision | Priority | Status |
|---|---|---|---|
| Q1 | Carry `completeness`; synthesise it when source-level evidence is not exposed; never claim `complete` from a 200 alone | P1 | **Decided** |
| Q2 | Adopt `structuredContent`; annotations only after a client check | P1 MCP | **Decided; compatibility test remains** |
| Q3 | OpenAPI was wrong; fixed and pinned | Done | **Closed** |
| Q4 | Extend `docs/architecture-guide.md`; no root `DESIGN.md` | Low | **Closed** |
| Q5 | Missing flexible-date dimensions require a first-class Search Planner | P1 | **Decided** |
| Q6 | `discover` for candidate generation; `multi-search` for real multi-destination search | P2 | **Decided** |
| Q7 | Search volume is a budget/rate-limit planning problem | P2 | **Decided** |
| Q8 | Must measure limiter failure semantics before the scheduler | **P0** | **OPEN — the only blocker** |
| Q9 | Construct durable identity client-side; the API id is observation-local | P1 | **Decided** |
| Q10 | Separate offer expiry from observation freshness; persist `observed_at` | P1 | **Decided** |
| Q11 | Targeted re-search before alert; `verified` ≠ guaranteed bookable | P1 | **Decided** |

**The dependency chain these decisions imply:**

```
                    LetsFG API capabilities
                            │
                 ┌──────────┴──────────┐
              Q5 / Q6                Q7 / Q8
           dimensions &            budget &
           primitives              failure contract   ← Q8 blocks here
                 └──────────┬──────────┘
                            ▼
                     Search Planner
                            ▼
                    Search Scheduler
                            ▼
                    Raw observations
                    ┌───────┴────────┐
                 Q9 identity     Q10 freshness
                    └───────┬────────┘
                            ▼
                    Candidate ranking
                            ▼
                    Q11 verification
                            ▼
                      Alert gate
```

Q1 and Q2 need validation but do not block the architecture; Q5, Q9, Q10 and Q11
establish the core scanner contracts; **Q8 is the only true blocker**.

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

**Ownership model (review #2 of this study) — the D10 invariant, made concrete.**

| Concern | LetsFG client | Hosted engine | Scanner |
|---|---|---|---|
| MCP protocol · client auth · client transport | **Yes** | — | — |
| Provider acquisition · search execution | **No** | **Yes** | — |
| Source of fare observations | — | **Yes** | — |
| Fare history · identity · search planning · alert policy | — | — | **Yes** |
| Client telemetry | **Yes** | — | — |

D10 is a **permanent architectural invariant**, not a position taken in this study:
LetsFG does not become a travel search engine. The failure it prevents is
predictable — see a useful trvl feature, notice we lack it, port it, and end up
maintaining two overlapping travel engines.

**And one rule that keeps this document from growing back into the scanner:**

> **No scanner behaviour may be introduced into this study solely because it
> consumes a contract defined here.**

The contract belongs here; the policy belongs in the scanner (D12). "The scanner
needs freshness, so let's add scanner freshness logic here" is the exact move this
sentence forbids.

**Review provenance.** This document was reviewed three times by the owner on 2026-10-03.

- *Review #1* — architecture and scope: accepted and applied as the architectural
  principle (D10), `freshness` in the envelope, the quantitative path for
  completeness, staged `structuredContent` work, the corrected router trigger, the
  health-log narrowing plus the telemetry/domain-data boundary (D11), the staged
  docs plan, the P0 promotion for atomic writes, and the scanner-layer open
  questions Q5–Q11. Its central recommendation — *keep this study narrow and put
  the product architecture in its own document* — is what §6/§7 and the scanner
  design implement.
- *Review #2* — contract precision and record-keeping, applied: the **three-axis
  invariant** (`status` / `completeness` / `freshness` are independent, never one
  state machine) and the **`observed_at` semantics** with
  `requested_at`/`observed_at`/`received_at` and `observed_at_basis` (§2.1);
  quantitative completeness marked **optional** with the "`complete` means the
  intended scope was satisfied" invariant (§2.1); `structuredContent` declared
  **canonical** and text a derived rendering (§2.2); the two pipelines written out
  side by side (§2.5); the CI invariant *a green gate is not a pass when the suite
  cannot collect*, with repair vs quarantine and the two anti-growth guards
  (§2.10); the question priority table with **Q8 promoted to P0 scanner
  prerequisite** (§6); the ownership model table and the "no scanner behaviour
  enters this study because it consumes a contract defined here" rule (§7); and
  the status/implementation-state split at the top of this document.
- *Approval record (review #2):* architecture **approved**, scope boundary
  **approved**, decision register **approved**, implementation direction
  **approved**; remaining contract work Q1/Q2; scanner dependency questions **Q8
  first**, then Q1/Q9–Q11; documentation restructuring deferred; router deferred
  until measured evidence; health store **not built**; provider architecture
  explicitly out of scope.
- *Review #3 — Q1–Q11 decided*, applied: §6 is now a **decisions** section rather
  than a work queue. Q1 carry-and-synthesise `completeness`, never claiming
  `complete` from a 200 alone (§2.4); Q2 adopt `structuredContent` with annotations
  gated on a client check, and the criterion that incompatibility must not break the
  basic tool result (§2.2); Q3 closed (spec error, fixed and pinned); Q4 no root
  `DESIGN.md` — one architecture surface (D13); Q5 the Search Planner is
  first-class and flexible dates are not a nested Cartesian loop; Q6 `discover` is
  candidate generation only, never authoritative observation; Q7 search volume is a
  budget/rate-limit planning problem; **Q8 stays OPEN as the single P0 blocker**
  with the eight behaviours a probe must establish, and the rule that no speculative
  limiter behaviour may be encoded; Q9 identity is constructed client-side and the
  API offer id is observation-local; Q10 offer validity is separated from
  observation freshness; Q11 the verified-before-alert gate, with **`verified` ≠
  guaranteed bookable** stated in both documents. A summary table and the implied
  dependency chain close §6.
