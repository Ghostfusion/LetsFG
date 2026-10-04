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
  envelopeViolations,
  firstListLength,
  isLegalCoverage,
  legalCoverage,
  noResultsJustified,
  parseRetryAfterMs,
  withEnvelope,
  type Envelope,
  type LegalCoverage,
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

// ── Coverage pair, emptiness, and the observation stamp (trvl-study-design §2.1) ──

describe('envelope — legalCoverage', () => {
  it('returns every legal cell', () => {
    assert.deepEqual(legalCoverage('complete', 'results'), { coverage_mode: 'complete', result_state: 'results' });
    assert.deepEqual(legalCoverage('partial', 'confirmed_empty'), { coverage_mode: 'partial', result_state: 'confirmed_empty' });
    assert.deepEqual(legalCoverage('unavailable', 'unavailable'), { coverage_mode: 'unavailable', result_state: 'unavailable' });
  });

  it('rejects both illegal cells at runtime, for dynamic payloads', () => {
    for (const state of ['results', 'confirmed_empty'] as const) {
      assert.throws(() => legalCoverage('unavailable', state), /no usable evidence cannot carry a result/);
    }
    for (const mode of ['complete', 'partial'] as const) {
      assert.throws(() => legalCoverage(mode, 'unavailable'), /contradictory/);
    }
  });

  it('leaves the illegal cells unrepresentable to the type checker', () => {
    // `npx tsc --noEmit` runs in CI. If either literal below ever compiles, the
    // build fails — that is the guard, and the reason the pair is a union.
    // @ts-expect-error no usable evidence cannot carry a result
    const noEvidenceWithResult: LegalCoverage = { coverage_mode: 'unavailable', result_state: 'results' };
    // @ts-expect-error a searched scope cannot be unavailable
    const searchedButUnavailable: LegalCoverage = { coverage_mode: 'complete', result_state: 'unavailable' };

    // The runtime validator catches what the compiler refuses to name, so a
    // payload arriving from elsewhere cannot smuggle either cell through.
    const asEnvelope = (cell: unknown): Envelope => ({
      status: 'ok', completeness: 'partial', ...(cell as Record<string, unknown>),
    });
    assert.deepEqual(envelopeViolations(asEnvelope(noEvidenceWithResult)), ['illegal coverage cell {unavailable, results}']);
    assert.deepEqual(envelopeViolations(asEnvelope(searchedButUnavailable)), ['illegal coverage cell {complete, unavailable}']);
  });

  it('agrees with isLegalCoverage across the whole 3x3 grid', () => {
    for (const mode of ['complete', 'partial', 'unavailable'] as const) {
      for (const state of ['results', 'confirmed_empty', 'unavailable'] as const) {
        const env: Envelope = { status: 'ok', completeness: 'partial', coverage_mode: mode, result_state: state };
        const coverageViolations = envelopeViolations(env).filter((v) => v.startsWith('illegal coverage cell'));
        assert.equal(coverageViolations.length, isLegalCoverage(mode, state) ? 0 : 1, `{${mode}, ${state}}`);
      }
    }
  });
});

describe('envelope — no_results is justified by exactly one combination', () => {
  it('accepts provider_empty under complete coverage', () => {
    assert.equal(noResultsJustified(envelopeForData(0)), true);
    assert.deepEqual(envelopeViolations(envelopeForData(0)), []);
  });

  it('rejects an empty claimed while coverage is degraded', () => {
    const env: Envelope = { ...envelopeForData(0), coverage_mode: 'partial' };
    assert.equal(noResultsJustified(env), false, '{no_results, coverage_mode: partial} asserts an absence we cannot support');
    assert.deepEqual(envelopeViolations(env), [
      'no_results claimed without provider_empty under complete coverage (empty_reason=provider_empty, coverage_mode=partial)',
    ]);
  });

  it('rejects a bare no_results with no reason attached', () => {
    const env: Envelope = { status: 'no_results', completeness: 'complete' };
    assert.equal(noResultsJustified(env), false);
    assert.equal(envelopeViolations(env).length, 1, 'an unexplained no_results is itself a violation');
  });
});

describe('envelope — a degraded empty is never "no flights"', () => {
  it('reports zero rows under partial coverage as ok / partial / confirmed_empty', () => {
    const env = envelopeForData(0, { coverage: 'partial' });
    assert.equal(env.status, 'ok', 'never no_results while coverage is degraded');
    assert.equal(env.completeness, 'partial');
    assert.equal(env.coverage_mode, 'partial');
    assert.equal(env.result_state, 'confirmed_empty');
    assert.equal(env.empty_reason, 'provider_empty');
    assert.deepEqual(envelopeViolations(env), []);
  });

  it('says so in the note, because this is the false-"no flights" failure mode', () => {
    const out = withEnvelope({ offers: [] }, envelopeForData(0, { coverage: 'partial' }));
    assert.match(String(out.note), /NOT evidence/);
    assert.match(String(out.note), /unserved/);
  });

  it('keeps the complete-coverage path unchanged', () => {
    const env = envelopeForData(0);
    assert.equal(env.status, 'no_results');
    assert.equal(env.completeness, 'complete');
    assert.equal(env.result_state, 'confirmed_empty');
  });
});

describe('envelope — a failure never carries a result', () => {
  it('stamps every error unavailable / not_loaded', () => {
    for (const response of [
      { status_code: 401, detail: 'no session' },
      { status_code: 429, detail: 'slow down' },
      { status_code: 504, detail: 'gateway' },
      { status_code: 503, detail: 'down' },
      { error: true, detail: 'fetch failed' },
    ]) {
      const env = envelopeForError(response);
      const label = JSON.stringify(response);
      assert.equal(env.completeness, 'blocked', label);
      assert.equal(env.coverage_mode, 'unavailable', label);
      assert.equal(env.result_state, 'unavailable', label);
      assert.equal(env.empty_reason, 'not_loaded', label);
      assert.deepEqual(envelopeViolations(env), [], label);
    }
  });

  it('never reaches no_results on the error path', () => {
    assert.notEqual(envelopeForError({ status_code: 429, detail: 'x' }).status, 'no_results');
    assert.notEqual(envelopeForError({ status_code: 403, detail: 'x' }).status, 'no_results');
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

  it('stamps receipt time honestly, never a fabricated provider time', async () => {
    const payload = await callSearchFlights(({ url }) =>
      url.startsWith('/api/search')
        ? { status: 200, body: { search_id: 's1' } }
        : { status: 200, body: { status: 'completed', offers: [{ id: 'o1', price: 90 }] } });
    // letsfg.co exposes no observation stamp, so `provider` would be a claim we
    // cannot support — §2.1 names this the failure the basis exists to prevent.
    assert.equal(payload.observed_at_basis, 'client_receipt');
    assert.notEqual(payload.observed_at_basis, 'provider');
    assert.equal(payload.freshness, 'live');
    assert.match(String(payload.observed_at), /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/);
  });

  it('carries the coverage pair on a settled search', async () => {
    const withOffers = await callSearchFlights(({ url }) =>
      url.startsWith('/api/search')
        ? { status: 200, body: { search_id: 's1' } }
        : { status: 200, body: { status: 'completed', offers: [{ id: 'o1', price: 90 }] } });
    assert.equal(withOffers.coverage_mode, 'complete');
    assert.equal(withOffers.result_state, 'results');
    assert.deepEqual(envelopeViolations(withOffers as unknown as Envelope), []);

    const empty = await callSearchFlights(({ url }) =>
      url.startsWith('/api/search')
        ? { status: 200, body: { search_id: 's2' } }
        : { status: 200, body: { status: 'completed', offers: [] } });
    assert.equal(empty.empty_reason, 'provider_empty', 'a real empty states its reason');
    assert.equal(empty.result_state, 'confirmed_empty');
    assert.equal(empty.coverage_mode, 'complete');
    assert.deepEqual(envelopeViolations(empty as unknown as Envelope), []);
  });

  it('carries unavailable/unavailable on a failure, never a result state', async () => {
    const payload = await callSearchFlights(() => ({ status: 429, headers: { 'Retry-After': '5' }, body: { detail: 'slow' } }));
    assert.equal(payload.coverage_mode, 'unavailable');
    assert.equal(payload.result_state, 'unavailable');
    assert.equal(payload.empty_reason, 'not_loaded');
    assert.deepEqual(envelopeViolations(payload as unknown as Envelope), []);
  });
});

// ── Per-lane routes ───────────────────────────────────────────────────────

describe('MCP server — per-lane request routes', () => {
  it('the Developer API lane books and polls on /flights/*, never the retired /bookings/book', async () => {
    const seen: string[] = [];
    const { server, baseUrl } = await fakeApi(({ method, url }) => {
      seen.push(`${method} ${url}`);
      return { status: 200, body: { booking_id: 'b1', status: 'booked' } };
    });
    // No bearer token: the API key selects the Developer API lane.
    const proc = spawnServer({ LETSFG_BASE_URL: baseUrl, LETSFG_BEARER_TOKEN: '', LETSFG_API_KEY: 'letsfg_test' });
    try {
      rpc(proc, { jsonrpc: '2.0', id: 1, method: 'initialize', params: {
        protocolVersion: '2024-11-05', capabilities: {}, clientInfo: { name: 'envelope-test', version: '1' },
      }});
      await nextMessage(proc);

      rpc(proc, { jsonrpc: '2.0', id: 2, method: 'tools/call', params: {
        name: 'book_flight',
        arguments: {
          offer_id: 'o1', passengers: [{ given_name: 'Ada', family_name: 'Lovelace' }],
          contact_email: 'a@example.com',
        },
      }});
      await nextMessage(proc);

      rpc(proc, { jsonrpc: '2.0', id: 3, method: 'tools/call', params: {
        name: 'get_flight_booking', arguments: { booking_ref: 'b1' },
      }});
      await nextMessage(proc);

      assert.ok(
        seen.includes('POST /developers/api/v1/flights/book'),
        `expected the Developer API booking route, saw: ${seen.join(', ')}`,
      );
      assert.ok(
        seen.includes('GET /developers/api/v1/flights/bookings/b1'),
        `expected the Developer API booking-status route, saw: ${seen.join(', ')}`,
      );
      assert.ok(
        !seen.some((entry) => entry.includes('/bookings/book')),
        '/developers/api/v1/bookings/book was retired on 2026-09-08 and answers 410',
      );
    } finally {
      proc.kill();
      server.close();
    }
  });
});

// ── Coverage across the advertised tool list ──────────────────────────────

const PERMISSIVE_BODY: Record<string, unknown> = {
  status: 'completed',
  offers: [],
  hotels: [],
  results: [],
  destinations: [],
  cities: [],
  locations: [],
};

const STATUSES: Record<string, true> = {
  ok: true, no_results: true, timeout: true, rate_limited: true, auth_required: true, failed: true,
};
const COMPLETENESS: Record<string, true> = { complete: true, partial: true, blocked: true };

/** Tools that reach the API with well-formed arguments, and arguments that get them there. */
const ENVELOPED_TOOLS: Array<[string, Record<string, unknown>]> = [
  ['search_flights', { origin: 'LON', destination: 'BCN', date_from: '2026-06-15' }],
  ['resolve_location', { query: 'LON' }],
  ['book_flight', {
    search_id: 's1', offer_id: 'o1',
    passengers: [{ given_name: 'Ada', family_name: 'Lovelace' }], contact_email: 'a@example.com',
  }],
  ['get_flight_booking', { booking_ref: 'ref1' }],
  ['answer_booking_question', { booking_ref: 'ref1', kind: 'seat', seats: [{ d: '12A' }], round: 1 }],
  ['resolve_hotel_city', { text: 'Barcelona' }],
  ['search_hotels', { city_id: 1, check_in: '2026-06-15', check_out: '2026-06-18' }],
  ['book_hotel', {
    expected_cost: 100, expected_price: 100, session_id: 'sess', hotel_code: 'H1',
    combination_id_v2: 'c1', city_id: 1, check_in: '2026-06-15', check_out: '2026-06-18',
    email: 'a@example.com', guests: [{ first_name: 'Ada', last_name: 'Lovelace' }],
  }],
  ['get_hotel_booking', { booking_job_id: 'job1' }],
  ['cancel_hotel_booking', { confirmation: 'conf1' }],
  ['authenticate', {}],
];

/**
 * Tools that answer from local data: they never reach the API, so an API-outcome
 * envelope would be a lie ("the source did not answer" when nothing was asked).
 * Each is asserted below, so adding a tool here is a visible decision.
 */
const EXEMPT_TOOL_NAMES = ['connect_payment', 'get_agent_profile', 'load_resources'];

async function callTool(
  name: string,
  args: Record<string, unknown>,
  env: Record<string, string> = {},
): Promise<{
  payload: Record<string, unknown> | null;
  text: string;
  structured: Record<string, unknown> | undefined;
  audience: string[] | undefined;
}> {
  const { server, baseUrl } = await fakeApi(() => ({ status: 200, body: PERMISSIVE_BODY }));
  const proc = spawnServer({ LETSFG_BASE_URL: baseUrl, ...env });
  try {
    rpc(proc, { jsonrpc: '2.0', id: 1, method: 'initialize', params: {
      protocolVersion: '2024-11-05', capabilities: {}, clientInfo: { name: 'envelope-test', version: '1' },
    }});
    await nextMessage(proc);
    rpc(proc, { jsonrpc: '2.0', id: 2, method: 'tools/call', params: { name, arguments: args } });
    const response = await nextMessage(proc);
    const result = response.result as {
      content: Array<{ text: string; annotations?: { audience?: string[] } }>;
      structuredContent?: Record<string, unknown>;
    };
    const text = result.content[0].text;
    const audience = result.content[0].annotations?.audience;
    try {
      return {
        payload: JSON.parse(text) as Record<string, unknown>,
        text,
        structured: result.structuredContent,
        audience,
      };
    } catch {
      return { payload: null, text, structured: result.structuredContent, audience };
    }
  } finally {
    proc.kill();
    server.close();
  }
}

async function advertisedTools(): Promise<Array<{ name: string; description: string; outputSchema?: Record<string, unknown> }>> {
  const { server, baseUrl } = await fakeApi(() => ({ status: 200, body: PERMISSIVE_BODY }));
  const proc = spawnServer({ LETSFG_BASE_URL: baseUrl });
  try {
    rpc(proc, { jsonrpc: '2.0', id: 1, method: 'initialize', params: {
      protocolVersion: '2024-11-05', capabilities: {}, clientInfo: { name: 'envelope-test', version: '1' },
    }});
    await nextMessage(proc);
    rpc(proc, { jsonrpc: '2.0', id: 2, method: 'tools/list', params: {} });
    const response = await nextMessage(proc);
    const result = response.result as { tools: Array<{ name: string; description: string; outputSchema?: Record<string, unknown> }> };
    return result.tools;
  } finally {
    proc.kill();
    server.close();
  }
}

async function advertisedToolNames(): Promise<string[]> {
  return (await advertisedTools()).map((tool) => tool.name).sort();
}

describe('MCP server — envelope coverage across the advertised tool list', () => {
  it('classifies every advertised tool as enveloped or exempt', async () => {
    const names = await advertisedToolNames();
    const classified = [...ENVELOPED_TOOLS.map(([name]) => name), ...EXEMPT_TOOL_NAMES];

    const unclassified = names.filter((name) => !classified.includes(name));
    assert.deepEqual(
      unclassified,
      [],
      'a new tool must be added to ENVELOPED_TOOLS (with its envelope) or to EXEMPT_TOOL_NAMES (with a reason)',
    );
    const stale = classified.filter((name) => !names.includes(name)).sort();
    assert.deepEqual(stale, [], 'these names are classified but no longer advertised — delete the entry');
  });

  it('every tool that reaches the API returns status + completeness', async () => {
    for (const [name, args] of ENVELOPED_TOOLS) {
      const { payload } = await callTool(name, args);
      assert.ok(payload, `${name}: expected a JSON payload`);
      // A payload may own `status` (a booking's own state). The envelope keeps
      // its verdict as `envelope_status` in exactly that case — documented in
      // withEnvelope, and the reason the booking's status is never clobbered.
      const verdict = payload?.envelope_status ?? payload?.status;
      assert.equal(
        STATUSES[String(verdict)],
        true,
        `${name}: envelope verdict ${JSON.stringify(verdict)} is outside the vocabulary ` +
          `(status=${JSON.stringify(payload?.status)}, envelope_status=${JSON.stringify(payload?.envelope_status)})`,
      );
      if (payload?.envelope_status !== undefined) {
        assert.notEqual(payload.status, undefined, `${name}: envelope_status without a payload status is ambiguous`);
      }
      assert.equal(
        COMPLETENESS[String(payload?.completeness)],
        true,
        `${name}: completeness ${JSON.stringify(payload?.completeness)} is not complete/partial/blocked`,
      );
    }
  });

  it('exempt tools answer locally and never claim an API outcome', async () => {
    const connect = await callTool('connect_payment', {});
    assert.equal(connect.payload?.error, 'wrong_tool');
    assert.equal(connect.payload?.status, undefined, 'a local refusal must not carry an API outcome status');

    const profile = await callTool('get_agent_profile', {});
    assert.equal(profile.payload?.not_applicable, true);
    assert.equal(profile.payload?.status, undefined, 'not_applicable is not an API outcome');

    const guide = await callTool('load_resources', {});
    assert.equal(guide.payload, null, 'load_resources returns the guide text, not JSON');
    assert.match(guide.text, /^# LetsFG/);
    assert.equal(guide.structured, undefined, 'the guide is text, so there is nothing to structure');
  });

  it('returns structuredContent identical to the text payload', async () => {
    for (const [name, args] of ENVELOPED_TOOLS) {
      const { payload, structured } = await callTool(name, args);
      assert.ok(structured, `${name}: an enveloped tool must return structuredContent`);
      assert.deepEqual(structured, payload, `${name}: structuredContent must not disagree with the text block`);
      assert.equal(
        structured?.observed_at_basis,
        'client_receipt',
        `${name}: every response here is fetched live, so the basis is receipt — never provider`,
      );
      assert.equal(structured?.freshness, 'live', name);
    }
  });

  it('advertises an outputSchema on every tool, carrying the envelope vocabulary', async () => {
    const tools = await advertisedTools();
    assert.ok(tools.length > 0, 'tools/list returned nothing to check');
    for (const tool of tools) {
      const schema = tool.outputSchema;
      assert.ok(schema, `${tool.name} advertises no outputSchema`);
      assert.deepEqual(schema.required, ['status', 'completeness'], `${tool.name}: the envelope is required`);
      const props = schema.properties as Record<string, { enum?: string[] }>;
      assert.deepEqual(props.status.enum, ['ok', 'no_results', 'timeout', 'rate_limited', 'auth_required', 'failed'], tool.name);
      assert.deepEqual(props.completeness.enum, ['complete', 'partial', 'blocked'], tool.name);
      assert.deepEqual(props.empty_reason.enum, ['provider_empty', 'not_loaded', 'filtered_out'], tool.name);
      assert.deepEqual(props.coverage_mode.enum, ['complete', 'partial', 'unavailable'], tool.name);
      assert.deepEqual(props.result_state.enum, ['results', 'confirmed_empty', 'unavailable'], tool.name);
      assert.deepEqual(props.observed_at_basis.enum, ['provider', 'provider_fetch', 'client_receipt'], tool.name);
      assert.deepEqual(props.freshness.enum, ['live', 'unknown'], tool.name);
      assert.equal(schema.additionalProperties, true, `${tool.name}: the payload half stays open`);
    }
  });
});

// ── Guidance lives in the guide, not in tools/list ────────────────────────
// `tools/list` is what every client receives on connect, and for a client that
// never fetches a resource it is the whole of what the server knows. The
// reference material — how to read a Starlink field, what a split ticket means,
// the booking state machine, hotel pricing, the paused-booking shapes — belongs
// in `letsfg://guide`, fetched on demand. A description is a contract, not an
// essay.
//
// Measured 2026-10-03: the 14 descriptions were 9,115 bytes before the split and
// 6,183 after (the whole tools/list payload, which the input schemas dominate,
// went 41,220 -> 38,231). The budget below is the after-state with headroom:
// raising it re-bloats tools/list for every client, so it is a deliberate edit.

const DESCRIPTION_BUDGET = 6800;
const MAX_DESCRIPTION_BYTES = 800;

/** Rules moved out of the tool descriptions into the guide, verbatim. */
const GUIDE_ONLY_RULES = [
  'Anything ending in "_some" has at least one leg WITHOUT it.',
  'An absent field means no information, NOT an absence of Wi-Fi.',
  'because no one seller offers the combination as a single ticket.',
  'the tickets are not linked, so if the first flight is late and the connection is missed, the second airline owes nothing — no rebooking, no refund.',
  'the money is HELD, not taken, until the airline confirms',
  "the supplier's cost plus `markup_rate` (6.4% for Revolut Pay or an EEA card, 8.3% for a card issued outside the EEA)",
];

/**
 * Facts that stay inline because they change what an agent DOES with the answer,
 * not merely how it explains it. Losing one of these is the failure mode the
 * split risks, so each is asserted in the description that owns it.
 */
const RETAINED_FACTS: Array<[tool: string, fact: string]> = [
  ['search_flights', 'SPLIT TICKET'],
  ['search_flights', '"confirmed_*"` is a fact'],
  ['answer_booking_question', 'NOTHING PROGRESSES UNTIL YOU ANSWER, and the cart expires'],
  ['book_flight', '"booking_url"'],
  ['book_hotel', 'Do NOT call book_hotel again'],
];

describe('guidance — the guide carries the rules, tools/list carries the contract', () => {
  it('the guide holds every rule moved out of the tool descriptions', async () => {
    const guide = await callTool('load_resources', {});
    for (const rule of GUIDE_ONLY_RULES) {
      assert.ok(guide.text.includes(rule), `letsfg://guide is missing a moved rule: ${rule}`);
    }
    // Field lists that were dropped from descriptions must be documented here, or
    // moving them would have deleted the information rather than relocated it.
    for (const field of ['total_price', 'supplier_paid', 'free_cancellation_until', 'cancellation_ladder']) {
      assert.ok(guide.text.includes(field), `letsfg://guide must document the ${field} field`);
    }
    for (const [, fact] of RETAINED_FACTS) {
      assert.ok(!GUIDE_ONLY_RULES.some((rule) => fact.includes(rule)),
        `a retained fact must not also be a guide-only rule: ${fact}`);
    }
  });

  it('no tool description repeats a moved rule', async () => {
    const tools = await advertisedTools();
    for (const tool of tools) {
      for (const rule of GUIDE_ONLY_RULES) {
        assert.ok(!tool.description.includes(rule), `${tool.name} duplicates guide prose: ${rule}`);
      }
    }
  });

  it('keeps the facts that change what an agent tells the user', async () => {
    const tools = await advertisedTools();
    for (const [name, fact] of RETAINED_FACTS) {
      const tool = tools.find((candidate) => candidate.name === name);
      assert.ok(tool, `${name} must be advertised`);
      assert.ok(tool.description.includes(fact),
        `${name} must keep "${fact}" inline — it changes what the agent does, so it cannot live only in letsfg://guide`);
    }
  });

  it('descriptions stay inside the budget the split set', async () => {
    const tools = await advertisedTools();
    let total = 0;
    for (const tool of tools) {
      const bytes = Buffer.byteLength(tool.description, 'utf8');
      total += bytes;
      assert.ok(bytes <= MAX_DESCRIPTION_BYTES,
        `${tool.name} description is ${bytes} bytes (max ${MAX_DESCRIPTION_BYTES}) — reference material belongs in letsfg://guide`);
    }
    assert.ok(total <= DESCRIPTION_BUDGET,
      `the ${tools.length} descriptions total ${total} bytes (budget ${DESCRIPTION_BUDGET})`);
  });

  it('marks the result block as assistant-facing, not user prose', async () => {
    // The block is the JSON payload the model acts on. Annotating the audience
    // keeps a client from rendering it verbatim to the traveller, which matters
    // most for the payloads that carry an envelope and raw offer data.
    for (const [name, args] of ENVELOPED_TOOLS) {
      const { audience } = await callTool(name, args);
      assert.deepEqual(audience, ['assistant'], `${name}: the result block must be audience ["assistant"]`);
    }
    const guide = await callTool('load_resources', {});
    assert.deepEqual(guide.audience, ['assistant'], 'the guide block is model input too');
  });
});
