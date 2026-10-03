/**
 * Envelope tests: the pure classification logic, plus an end-to-end check that
 * the real server attaches the envelope to a real tool result.
 *
 * The end-to-end half points LETSFG_BASE_URL at a local fake and spawns the
 * actual stdio server, so it exercises the shipped code path (transport, JSON-RPC
 * dispatch, readJson, callTool) rather than a copy of it.
 */
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process';
import { createServer, type Server } from 'node:http';
import { once } from 'node:events';
import { fileURLToPath } from 'node:url';
import { join, dirname } from 'node:path';
import {
  envelopeForData,
  envelopeForError,
  firstListLength,
  parseRetryAfterMs,
  withEnvelope,
} from './envelope.js';

const __dir = dirname(fileURLToPath(import.meta.url));
const SERVER_PATH = join(__dir, 'index.ts');

// ── Pure classification ───────────────────────────────────────────────────

describe('envelope — parseRetryAfterMs', () => {
  it('reads delta-seconds', () => {
    assert.equal(parseRetryAfterMs('30'), 30_000);
    assert.equal(parseRetryAfterMs('0'), 0);
  });
  it('reads an HTTP-date', () => {
    const future = new Date(Date.now() + 5_000).toUTCString();
    const ms = parseRetryAfterMs(future);
    assert.ok(ms !== null && ms > 0 && ms <= 5_000, `expected a positive delta, got ${ms}`);
  });
  it('returns null for absent or unparsable values', () => {
    assert.equal(parseRetryAfterMs(null), null);
    assert.equal(parseRetryAfterMs(''), null);
    assert.equal(parseRetryAfterMs('soon'), null);
  });
});

describe('envelope — envelopeForError', () => {
  it('maps 401/403 to auth_required and blocked', () => {
    for (const code of [401, 403]) {
      const env = envelopeForError({ error: true, status_code: code, detail: 'nope' });
      assert.equal(env.status, 'auth_required');
      assert.equal(env.completeness, 'blocked');
      assert.equal(env.fix_hint_code, 'AUTH_INVALID');
    }
  });
  it('maps 429 to rate_limited and carries Retry-After', () => {
    const env = envelopeForError({ error: true, status_code: 429, detail: 'slow down', retry_after_ms: 12_000 });
    assert.equal(env.status, 'rate_limited');
    assert.equal(env.completeness, 'blocked');
    assert.equal(env.fix_hint_code, 'RATE_LIMITED');
    assert.equal(env.retry_after_ms, 12_000);
  });
  it('maps a timeout to timeout, not failed', () => {
    for (const response of [
      { error: true, status_code: 504, detail: 'gateway' },
      { error: true, detail: 'Search timed out after 120s.' },
    ]) {
      const env = envelopeForError(response);
      assert.equal(env.status, 'timeout', `for ${JSON.stringify(response)}`);
      assert.equal(env.completeness, 'blocked');
      assert.equal(env.fix_hint_code, 'SUPPLIER_TIMEOUT');
    }
  });
  it('maps 5xx to failed and a 4xx to invalid input', () => {
    assert.equal(envelopeForError({ status_code: 503, detail: 'x' }).fix_hint_code, 'SERVICE_UNAVAILABLE');
    assert.equal(envelopeForError({ status_code: 422, detail: 'x' }).fix_hint_code, 'INVALID_PARAMETER');
  });
  it('treats an error with no HTTP status as a network failure', () => {
    const env = envelopeForError({ error: true, detail: 'fetch failed' });
    assert.equal(env.status, 'failed');
    assert.equal(env.fix_hint_code, 'NETWORK_ERROR');
  });
});

describe('envelope — envelopeForData', () => {
  it('distinguishes an honest empty from a failure', () => {
    const empty = envelopeForData(0);
    assert.equal(empty.status, 'no_results');
    assert.equal(empty.completeness, 'complete');
    assert.equal(empty.fix_hint_code, 'NO_RESULTS');
  });
  it('marks a pending late merge as partial', () => {
    const env = envelopeForData(3, { lateMerge: true });
    assert.equal(env.status, 'ok');
    assert.equal(env.completeness, 'partial');
  });
  it('treats an uncounted payload as partial, never complete', () => {
    assert.equal(envelopeForData(null).completeness, 'partial');
  });
  it('reports a non-empty, settled result as ok/complete', () => {
    const env = envelopeForData(7);
    assert.equal(env.status, 'ok');
    assert.equal(env.completeness, 'complete');
    assert.equal(env.fix_hint_code, undefined);
  });
});

describe('envelope — withEnvelope', () => {
  it('puts the verdict first and explains a non-complete result', () => {
    const out = withEnvelope({ offers: [] }, envelopeForData(0));
    assert.equal(out.status, 'no_results');
    assert.equal(out.completeness, 'complete');
    assert.deepEqual(out.offers, []);
    assert.equal(out.note, undefined, 'a complete verdict needs no caveat');
  });
  it('adds a caveat when the result is blocked', () => {
    const out = withEnvelope({}, envelopeForError({ status_code: 429, detail: 'x' }));
    assert.match(String(out.note), /did not answer/);
    assert.match(String(out.note), /NOT "nothing found"/);
  });
  it('never clobbers a payload status, and keeps the verdict beside it', () => {
    const out = withEnvelope({ status: 'booking_in_progress' }, { status: 'ok', completeness: 'complete' });
    assert.equal(out.status, 'booking_in_progress', "the booking's own status must survive");
    assert.equal(out.envelope_status, 'ok');
  });
});

describe('envelope — firstListLength', () => {
  it('finds the first list under any of the keys', () => {
    assert.equal(firstListLength({ results: [1, 2] }, ['hotels', 'results']), 2);
    assert.equal(firstListLength({ hotels: [] }, ['hotels', 'results']), 0);
  });
  it('returns null when no list is present (unknown, not empty)', () => {
    assert.equal(firstListLength({}, ['hotels']), null);
  });
});

// ── End-to-end through the real server ────────────────────────────────────

/** A minimal stand-in for letsfg.co. Each test installs its own responder. */
async function fakeApi(responder: (req: { method: string; url: string; body: string }) => {
  status: number;
  headers?: Record<string, string>;
  body: unknown;
}): Promise<{ server: Server; baseUrl: string }> {
  const server = createServer((req, res) => {
    let body = '';
    req.on('data', (chunk) => { body += chunk; });
    req.on('end', () => {
      const reply = responder({ method: req.method ?? '', url: req.url ?? '', body });
      res.writeHead(reply.status, { 'Content-Type': 'application/json', ...(reply.headers ?? {}) });
      res.end(typeof reply.body === 'string' ? reply.body : JSON.stringify(reply.body));
    });
  });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  const addr = server.address();
  const port = typeof addr === 'object' && addr ? addr.port : 0;
  return { server, baseUrl: `http://127.0.0.1:${port}` };
}

function spawnServer(env: Record<string, string>): ChildProcessWithoutNullStreams {
  // `process.execPath --import tsx` rather than `npx tsx`: `npx` is a shell
  // script on Windows (ENOENT under spawn), which made every integration test
  // here red for a reason unrelated to the code.
  return spawn(process.execPath, ['--import', 'tsx', SERVER_PATH], {
    stdio: ['pipe', 'pipe', 'pipe'],
    env: {
      ...process.env,
      LETSFG_BEARER_TOKEN: 'test-token',
      LETSFG_WAIT_FOR_SPLIT: '0',
      ...env,
    },
  });
}

function rpc(proc: ChildProcessWithoutNullStreams, msg: Record<string, unknown>): void {
  proc.stdin.write(JSON.stringify(msg) + '\n');
}

// A response-timeout guard, not a delay: it only fires when the server never
// answers, so the test fails with a named cause instead of hanging. The
// executor form is required because this package supports Node >=18 and CI runs
// Node 20; `Promise.withResolvers` is Node 22+ and would throw at runtime there.
function nextMessage(proc: ChildProcessWithoutNullStreams, timeoutMs = 10_000): Promise<Record<string, unknown>> {
  return new Promise((resolve, reject) => {
    let buf = '';
    const signal = AbortSignal.timeout(timeoutMs);
    const onAbort = () => {
      proc.stdout.off('data', onData);
      reject(new Error('MCP response timeout'));
    };
    const onData = (chunk: Buffer) => {
      buf += chunk.toString();
      const nl = buf.indexOf('\n');
      if (nl === -1) return;
      signal.removeEventListener('abort', onAbort);
      proc.stdout.off('data', onData);
      try {
        resolve(JSON.parse(buf.slice(0, nl)));
      } catch {
        reject(new Error(`Invalid JSON: ${buf.slice(0, nl)}`));
      }
    };
    signal.addEventListener('abort', onAbort, { once: true });
    proc.stdout.on('data', onData);
    proc.on('error', (err) => { signal.removeEventListener('abort', onAbort); reject(err); });
  });
}

async function callSearchFlights(responder: Parameters<typeof fakeApi>[0]) {
  const { server, baseUrl } = await fakeApi(responder);
  const proc = spawnServer({ LETSFG_BASE_URL: baseUrl });
  try {
    rpc(proc, { jsonrpc: '2.0', id: 1, method: 'initialize', params: {
      protocolVersion: '2024-11-05', capabilities: {}, clientInfo: { name: 'envelope-test', version: '1' },
    }});
    await nextMessage(proc);
    rpc(proc, { jsonrpc: '2.0', id: 2, method: 'tools/call', params: {
      name: 'search_flights', arguments: { origin: 'LON', destination: 'BCN', date_from: '2026-06-15' },
    }});
    const response = await nextMessage(proc);
    const result = response.result as { content: Array<{ text: string }> };
    return JSON.parse(result.content[0].text) as Record<string, unknown>;
  } finally {
    proc.kill();
    server.close();
  }
}

describe('MCP server — search_flights result envelope (end-to-end)', () => {
  it('a settled search with offers is ok / complete', async () => {
    const payload = await callSearchFlights(({ url }) =>
      url.startsWith('/api/search')
        ? { status: 200, body: { search_id: 's1' } }
        : { status: 200, body: { status: 'completed', offers: [{ id: 'o1', price: 90, currency: 'EUR' }] } });
    assert.equal(payload.status, 'ok');
    assert.equal(payload.completeness, 'complete');
    assert.equal(payload.fix_hint_code, undefined);
    assert.equal((payload.offers as unknown[]).length, 1);
  });

  it('a settled search with no offers is no_results / complete, not a failure', async () => {
    const payload = await callSearchFlights(({ url }) =>
      url.startsWith('/api/search')
        ? { status: 200, body: { search_id: 's2' } }
        : { status: 200, body: { status: 'completed', offers: [] } });
    assert.equal(payload.status, 'no_results');
    assert.equal(payload.completeness, 'complete');
    assert.equal(payload.fix_hint_code, 'NO_RESULTS');
  });

  it('a 429 is rate_limited with the server back-off, and never no_results', async () => {
    const payload = await callSearchFlights(() => ({
      status: 429,
      headers: { 'Retry-After': '45' },
      body: { detail: 'too many searches' },
    }));
    assert.equal(payload.status, 'rate_limited');
    assert.equal(payload.completeness, 'blocked');
    assert.equal(payload.retry_after_ms, 45_000);
    assert.notEqual(payload.status, 'no_results');
  });

  it('a 401 is auth_required', async () => {
    const payload = await callSearchFlights(() => ({ status: 401, body: { detail: 'no session' } }));
    assert.equal(payload.status, 'auth_required');
    assert.equal(payload.completeness, 'blocked');
  });

  it('a 500 is failed / blocked with a service hint', async () => {
    const payload = await callSearchFlights(() => ({ status: 500, body: { detail: 'boom' } }));
    assert.equal(payload.status, 'failed');
    assert.equal(payload.completeness, 'blocked');
    assert.equal(payload.fix_hint_code, 'SERVICE_UNAVAILABLE');
  });
});
