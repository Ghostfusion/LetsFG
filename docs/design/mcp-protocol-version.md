# Design record — the MCP protocol version the server answers

**Status:** Decided — stay on `2024-11-05` for now
**Date:** 2026-10-03
**Surface:** `sdk/mcp/src/index.ts`, the `initialize` handler

## Problem

The MCP specification is a dated sequence of revisions. A server answers
`initialize` with the `protocolVersion` it speaks, and a client either accepts it
or disconnects. LetsFG answers

```
protocolVersion: '2024-11-05'
```

from the original server commit (d9c2c9e). Since then the specification has moved
forward, so the question is not "can we upgrade" but "what forces the upgrade",
and the cost of being wrong in either direction is different:

- Answer too old, and a client built against a newer revision may refuse the
  connection, or silently lose features it expects.
- Answer too new, and a client that only implements the older revision is told it
  has capabilities we do not actually implement.

## Measured constraints

- The revision we answer, [`2024-11-05`](https://modelcontextprotocol.io/specification/2024-11-05),
  describes **stateful connections** with server and client capability
  negotiation over a persistent session. Our stdio server keeps that shape.
- [`2025-11-25`](https://modelcontextprotocol.io/specification/2025-11-25) adds
  client features — sampling, roots, elicitation — on the same stateful base.
- [`2026-07-28`](https://modelcontextprotocol.io/specification/2026-07-28), the
  current revision, describes **stateless, self-contained requests** with
  **per-request capability negotiation**, and moves optional behaviour into
  negotiated extensions (tasks, skills, apps). That is a different transport
  model, not an additive change.
- Our server advertises `capabilities: { tools: {}, resources: {} }` and nothing
  else: no sampling, roots, elicitation, prompts, logging or extensions. The
  features the two newer revisions add are the ones we do not implement.

Every URL above was fetched on 2026-10-03 and resolves.

## Decision

**Stay on `2024-11-05`.** The version we answer is a claim about what we
implement. Our surface is tools and resources over a stateful stdio session —
exactly `2024-11-05` — and the newer revisions' additions are features we would
be promising without implementing. Answering a newer revision is therefore not a
version-string edit: for `2026-07-28` in particular it would mean moving off the
stateful session model.

## Acceptance signal

- `initialize` answers `2024-11-05`, and the existing spawn tests in
  `sdk/mcp/src/index.test.ts` and `sdk/mcp/src/envelope.test.ts` pass against a
  real client handshake.
- Clients on the current revision connect and call tools today; nothing in the
  transport we use is negotiable at connect time.

## What would make this wrong

Reopen on either trigger:

1. **A client we care about requires a newer revision**, or the tracked revisions
   (`2025-11-25`, `2026-07-28`) become the common client baseline — at which
   point our answer is the thing that disconnects us.
2. **We implement a feature that only exists in a newer revision** — for example
   a negotiated extension, or per-request capability negotiation. Then the
   version string is stale relative to the code and must move with it, not after
   it.
3. **A client requires protocol-level discovery** we cannot serve on the current
   revision.

Until one of those holds, the honest answer is the older revision, and a newer
string would be a claim we cannot back with behaviour.
