# Changelog

All notable, user-visible changes to the LetsFG SDKs, the `letsfg` CLI and the
`letsfg-mcp` server.

The format follows [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/).
This file keeps an `## [Unreleased]` section, a deliberate divergence from the
reference format: that section is the anchor `test/docs-claims.test.mjs` uses to
compare the newest released version against the package manifests.

Three packages share this repository and version independently — `letsfg` on
PyPI, `letsfg` on npm, and `letsfg-mcp` on npm — so these rules say which stream
a heading carries:

1. One section per release: `## [X.Y.Z] - YYYY-MM-DD`, where `X.Y.Z` is the
   **Python** version. A `Published:` line under the heading names the npm
   versions that shipped with it, or that they were unchanged.
2. Sections use `### Added`, `### Changed`, `### Fixed`, `### Removed` and
   `### Upgrade notes`.
3. Every bullet ends with provenance — a commit SHA resolvable here with
   `git show <sha>`, and the upstream PR number where the commit carries one.
4. Bullets state scope limits, not only wins.

## [Unreleased]

### Added

- MCP: every tool result carries a typed envelope (`status`, `completeness`,
  `retry_after_ms`, `fix_hint_code`) plus an `outputSchema` and
  `structuredContent`, and a valid empty result is never reported as a timeout.
  Result blocks are annotated `audience: ["assistant"]`, so a client does not
  render the JSON payload to the traveller. (2ca9223, 5d2eeda, 733c1dd)
- A provider contract and a SerpApi Google Flights adapter behind it, with a
  credential-gated registry, a client-side budget ledger and a six-criteria
  conformance suite. Nothing user-facing is wired to it yet: no new MCP tool and
  no new REST route. (9a71cf4, 3f1f95e, e626e27)
- Two repository guards, wired into CI: `test/docs-claims.test.mjs` (manifest
  versions, tool documentation, OpenAPI URL composition, local links) and
  `test/workflow-hygiene.test.mjs` (action pinning, job timeouts, permissions).
  (5d2eeda, 6ead61d)
- CI: a pull request that reads as a fix — title or any commit subject saying
  `fix`/`hotfix`/`bugfix` — must change a test in every package whose
  `sdk/js/src` or `sdk/mcp/src` it changed. The `skip-regression-test` label is
  the documented bypass. (04da615)

### Changed

- MCP: the reference material moved out of `tools/list` into the `letsfg://guide`
  resource — the Starlink and split-ticket rules, the booking state machines,
  hotel pricing, the hotel result fields and the paused-booking shapes — while
  the facts that change what an agent does stay inline in the description that
  owns them. The 14 descriptions went from 9,115 to 6,183 bytes. (733c1dd)
- MCP keeps answering `protocolVersion: 2024-11-05`. The decision and its
  reversal trigger are recorded in
  [docs/design/mcp-protocol-version.md](docs/design/mcp-protocol-version.md), and
  the literal dates from the original server commit (d9c2c9e).

### Fixed

- Python: the SerpApi adapter labelled every First-class segment `economy`. A
  response segment's `travel_class` reads `"First Class"` — measured against the
  live provider — while `_CABIN_CLASSES` held only `"first"`, so the lookup missed
  and fell through to the economy default on the `FlightOffer` a consumer reads.
  The measured spelling is now mapped, asserted at the segment. **Scope limit:
  only measured spellings are added (design §3.5) — a value the provider spells
  some other way still lands on the economy default, because there is no evidence
  for any other spelling yet.** (791180e)

- Python + JS: `MISSING_PARAMETER` — documented as "Required field missing" — was
  assigned to nothing. Python raised a bare `ValueError` when `search_id` was
  missing, outside the taxonomy its own documentation promises, so a caller
  catching `LetsFGError` never saw the refusal at all; the JS `searchId` checks
  and the `bookHotel` `expectedCost` check threw with no code. All four now carry
  `MISSING_PARAMETER`/`validation`. **The Python refusal's type changes from
  `ValueError` to `ValidationError` — catch `LetsFGError` for it.** (82b889c)
- Python + JS: three error paths still dropped the documented `error_code` —
  Python's free-lane `BearerTokenError` had no such field at all, and both
  `register` implementations raised without inferring one. `BearerTokenError`
  now carries `AUTH_INVALID`/`business` (without subclassing `LetsFGError`, so
  the connectors layer stays free of the Developer-API client), and both
  `register` handlers reuse the same status → code inference the main HTTP seam
  uses, keeping a code the server sent over the inferred one. (646bd61)
- Python: `AuthenticationError` left `error_code` empty on the paths that never
  saw an HTTP response — `_require_api_key`, the first one an agent hits — so a
  caller branching on the documented field read `""`. It now defaults to
  `AUTH_INVALID`, as the JS SDK's already did, and still keeps the server's own
  code when one was returned. (174da9d)
- JS: the PFS poll timeout was thrown with no `errorCode`, so it was classified
  `business` with `isRetryable: false` — a caller that trusts the flag would
  refuse to retry a timeout — and its message told the reader to poll
  `/api/results/<id>`, a literal placeholder that never named the search. It now
  carries `errorCode: SUPPLIER_TIMEOUT` (transient, retryable) and the real
  `search_id`, matching the Python SDK. (2b7ab79)
- Python: `search_local` returned `{"offers": [], "total_results": 0}` when the
  search never reached a terminal status, so a ~3-minute timeout was
  indistinguishable from a genuine "nothing flies this route". `letsfg search`
  therefore printed *"No flights found for GDN → BCN on …"* — a claim about the
  market the server never made — and exited 0. It now raises
  `LetsFGError(status_code=504, error_code=SUPPLIER_TIMEOUT)` carrying the
  `search_id`, so a caller can poll `/api/results/<id>` itself. The JS SDK
  already threw on this path; Python was the outlier. (e2f6f31)
- MCP: a refusal the server makes itself — a missing credential, a missing
  argument — returned a bare `{error}` with no envelope, so a caller could not
  tell a validation refusal from a transport failure. Every local refusal now
  carries `status`/`completeness`/`fix_hint_code`, and deliberately carries **no**
  `observed_at_basis`/`freshness`, because nothing was received. Found by calling
  the server from a real MCP client: the test suite defaulted a bearer token, so
  the uncredentialed paths were unreachable from it. (44aea31)
- Repository: the OpenAPI `servers[0].url` double-prefixed `/api/v1` on every
  generated URL, two advertised MCP tools were undocumented in the package
  README, and the MCP runtime reported version `1.3.1` while the package was
  `2026.5.78`. (5d2eeda, 6ead61d)
- Config: both SDKs wrote `config.json` non-atomically and left it readable
  between creation and `chmod`, and resolved its path in three places that could
  disagree. There is now one resolver per language and an atomic write that is
  owner-only from creation. (0bc9144, 6ead61d)
- CI: the deterministic Python job could not collect — 17 modules were stale
  against the local connectors removed in `f91be5b`. They were parked behind a
  recorded, size-pinned list so the suite ran while the repair backlog stayed
  visible, and are now deleted (see Removed). (216c82e, 3d1f214)

### Removed

- A bookable-connector registry that returned `None` for every input, the
  shadowed `letsfg/models.py`, the `system_info.py` stub for a removed
  architecture, and the connector-era checkout sweep with its four JSON
  artifacts. (6ead61d, ca11fc2, 74a7a33)
- The 18 parked Python test modules and the `sdk/python/conftest.py` quarantine
  block that hid them. Every one tested an implementation removed in `f91be5b` —
  the local connectors, the source-region and airline-route tables,
  `connectors/currency.py` and the `starlinkflights` source — or a helper deleted
  with them, so none could be rewritten against the current API. The parked set
  stays pinned in `test/docs-claims.test.mjs`, now at 0. (3d1f214)

### Upgrade notes

- **Catch `LetsFGError`, not `ValueError`, for a missing `search_id`.** Python's
  `book()` raised a bare `ValueError` when `search_id` was omitted; it now raises
  `ValidationError` (a `LetsFGError`) carrying `MISSING_PARAMETER`, so the
  documented base class finally catches the most common misuse of the booking
  call. `except ValueError` no longer matches it. (82b889c)
- Nothing in this section has been published yet. The newest release is
  `letsfg` 2026.5.103 (ec95f0e).

## [2026.5.103] - 2026-10-02

**Published:** `letsfg` 2026.5.103 (PyPI) · `letsfg` 2026.5.75 (npm, unchanged)
· `letsfg-mcp` 2026.5.78 (npm, unchanged)

### Fixed

- Python: the Bearer (PFS) lane discarded the offers the API returned, and
  `limit` was not enforced on that lane. (ec95f0e)

## [2026.5.102] - 2026-10-02

**Published:** `letsfg` 2026.5.102 (PyPI) · `letsfg` 2026.5.75 (npm) ·
`letsfg-mcp` 2026.5.78 (npm)

### Added

- A LetsFG plugin for Hermes Agent, under the MIT licence. (561a36d, 3a309f6)
- BRL (Brazilian Real) in the currency picker. (03ffdc2)

### Fixed

- Python: the Developer API-key lane now sends `return_from`, so round trips
  stop running as one-way. (c7b8850)
- JavaScript: search sends the option names each lane actually reads. (9598b4e)
- MCP: round trips on the Bearer lane ran as one-way and dropped the return leg.
  (baac85b)

### Changed

- Documentation: sandbox booking is described as booking; a card that cannot be
  charged in the requested currency is booked in USD rather than refused; the
  price shown is stated as the total paid. (c7be65e, 11a57f4, 4863ff0)

## [2026.5.101] - 2026-09-14

**Published:** `letsfg` 2026.5.101 (PyPI) · `letsfg` 2026.5.74 (npm) ·
`letsfg-mcp` 2026.5.77 (npm)

### Added

- `connect_payment()` in the SDKs, replacing `setup_payment()`. (0a6b769)
- `book()` no longer routes through the retired unlock lane, and
  `unlock_flight_offer` is delisted from `tools/list`. (4bec8d0, 4f1a0ba)

### Fixed

- SDK: `letsfg auth` requests the advertised scopes and a `401` carries the
  server's code, and `letsfg search` refreshes an hour-old token instead of
  asking for `letsfg auth` again. (f19ca44, a0c3ac9)
- CLI: renders the offers the API returns, sends the options the API reads, and
  reports what the booking answered. (7023e68)
- MCP: the published package stops stranding a paused booking. (4f1a0ba)
- Omarchy plugin: every valid token read as expired; sign-in now goes through
  `/connect` and renews silently. (6cb365a)

### Upgrade notes

- Hotel booking needs `letsfg` 2026.5.101 (Python), `letsfg` 2026.5.74 (JS) or
  `letsfg-mcp` 2026.5.77 or later. Earlier releases send the reservation-fee
  fields retired on 2026-09-11, and the API refuses every hotel booking they
  make. (58886e7)

Releases before `2026.5.101` predate this file; their headlines are in their
release commits (`git log --grep='^release:'`).
