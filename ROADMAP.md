# Roadmap

What this repository has shipped, and where new work comes from.

## Shipped

- [LetsFG 2026.5.103](CHANGELOG.md#20265103---2026-10-02) — the Bearer lane keeps
  the offers the API returns, and enforces `limit`.
- [LetsFG 2026.5.102](CHANGELOG.md#20265102---2026-10-02) — Hermes Agent plugin,
  BRL, and three one-way-round-trip fixes across the lanes.
- [LetsFG 2026.5.101](CHANGELOG.md#20265101---2026-09-14) — `connect_payment()`,
  the unlock lane retired, and auth/scopes fixes.
- [Unreleased](CHANGELOG.md#unreleased) — the MCP result envelope, the provider
  contract and its first adapter, and the repository's docs and workflow guards.

## Next

New work starts as a scoped issue with acceptance criteria: the expected
behaviour, the acceptance signal, and the test that proves it. The issue tracker
is the source of truth for what is in progress and what is planned; this file is
not, and holds no wishlist.

Two constraints on what can be proposed:

- Hosted-behaviour changes are server-side and are not made from this
  repository. What lands here is the client contract that consumes them.
- A change with no defect or acceptance criterion behind it is not started. The
  working agreement in [docs/working-agreement.md](docs/working-agreement.md)
  governs the change surface.
