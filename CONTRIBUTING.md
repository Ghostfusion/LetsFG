# Contributing to LetsFG

Thanks for your interest in contributing! 🚀

## Quick Links

- **GitHub:** https://github.com/LetsFG/LetsFG
- **API Docs:** https://letsfg.co/developers/api/docs
- **npm (JS SDK):** https://www.npmjs.com/package/letsfg
- **npm (MCP):** https://www.npmjs.com/package/letsfg-mcp
- **PyPI:** https://pypi.org/project/letsfg/

## How to Contribute

1. **Bugs & small fixes** → Open a PR directly
2. **New features / architecture changes** → Open a [GitHub Issue](https://github.com/LetsFG/LetsFG/issues) first to discuss
3. **Questions** → Open a [GitHub Discussion](https://github.com/LetsFG/LetsFG/discussions)

## Before You PR

- Test locally and run the relevant SDK tests (see [docs/TESTING.md](docs/TESTING.md) for the full testing guide)
- Keep PRs focused — one thing per PR
- Describe **what** you changed and **why**

## Testing & Definition of Done

**Every PR that adds or modifies behavior must include tests.** No exceptions.

See **[docs/TESTING.md](docs/TESTING.md)** for the complete guide, including:
- The three-tier test taxonomy (only Tier 1 runs in this repository)
- How to add tests for a lane-sensitive change
- The Red-Green-Refactor mandate and the coverage gate

### Quick reference

```bash
# Tier-1 — must be green before merge
cd sdk/python && pytest -m "not live"              # Python SDK
cd sdk/js  && npx tsc --noEmit && npm test         # JS SDK
cd sdk/mcp && npx tsc --noEmit && npm test         # MCP server
node --test test/docs-claims.test.mjs test/workflow-hygiene.test.mjs

# Live checks are on demand only (network + a card-backed token)
cd sdk/python && LETSFG_BEARER_TOKEN=... pytest -m live
```

## Development Setup

### Python SDK

```bash
cd sdk/python
pip install -e ".[dev]"
python -m pytest
```

### JS/TS SDK

```bash
cd sdk/js
npm install
npm run build
npm test
```

### MCP Server

```bash
cd sdk/mcp
npm install
npm run build
```

## Repository Structure

```
sdk/
├── python/    # Python SDK (PyPI: letsfg)
├── js/        # JavaScript/TypeScript SDK (npm: letsfg)
└── mcp/       # MCP Server (npm: letsfg-mcp)
```

The backend API is in a separate private repository. This repo contains the public SDKs, MCP server, and documentation only.

## Code Style

### Python
- Type hints everywhere
- `httpx` for HTTP requests
- `pydantic` for data models
- Follow existing patterns in `client.py`

### TypeScript
- Strict mode enabled
- Native `fetch` (no axios/got)
- Export types from `types.ts`
- Rebuild dist after changes: `npm run build`

## Commit Messages

Use concise, action-oriented messages:

```
fix: handle timeout in Python search client
feat: add returnUrl option to JS unlock method
docs: update MCP server README with new tool descriptions
```

> AI agents working in this repository follow
> [docs/working-agreement.md](docs/working-agreement.md) instead, which is why
> their commit subjects carry no `fix:` / `feat:` prefix and no issue number.

## AI-Assisted PRs Welcome! 🤖

Built with Copilot, Claude, Cursor, or other AI tools? Great — just note it in your PR description so I know what to look for when reviewing.

## Important: Keep Messaging Consistent

When editing any agent-facing text (READMEs, SDK docstrings, MCP tool descriptions), please maintain:

1. **Zero price bias** messaging — this is a core differentiator
2. **Real passenger details** warning — critical for bookings
3. **Pricing accuracy** — search is free on MCP/CLI/SDK/PFS once a card is connected at letsfg.co/connect (a 0.00 Revolut setup, nothing charged). Flight booking holds the fare plus LetsFG's markup on that card and captures it only against a real PNR; the markup is inside the price shown, nothing is added at booking. Hotel booking works the same way: the price is held on the card and captured only once the hotel confirms. The Developer API is a separate prepaid-credits product.

## Report a Vulnerability

See [SECURITY.md](SECURITY.md) for our security policy and how to report vulnerabilities.

## License

MIT — see [LICENSE](LICENSE).
