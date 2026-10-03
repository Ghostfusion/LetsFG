/**
 * Result envelope for MCP tool responses: a typed status and a completeness
 * verdict, kept separate from the payload.
 *
 * Why this exists: a tool result used to be either a JSON payload or
 * `Error: <e>`, with nothing in between. An agent therefore could not tell
 * "the source answered and there is genuinely nothing" from "the source never
 * answered" — and an empty result was routinely narrated to the user as "no
 * flights found" when a 429 or a timeout was the real story. Those are
 * different facts and only one of them is safe to state as fact.
 *
 * The `fix_hint_code` values reuse the error codes already published by the
 * Python and JS SDKs (RATE_LIMITED, SUPPLIER_TIMEOUT, SERVICE_UNAVAILABLE,
 * AUTH_INVALID, INVALID_PARAMETER, NETWORK_ERROR) so an agent sees one
 * taxonomy across every LetsFG surface rather than a second one invented here.
 *
 * Pure functions only — no I/O, no environment — so they are unit-testable.
 */

export type ToolStatus =
  | 'ok'
  | 'no_results'
  | 'timeout'
  | 'rate_limited'
  | 'auth_required'
  | 'failed';

export type Completeness = 'complete' | 'partial' | 'blocked';

export interface Envelope {
  status: ToolStatus;
  /** complete = every source answered; partial = some did not; blocked = none did. */
  completeness: Completeness;
  /** Present unless the call succeeded cleanly. Closed enum — see file header. */
  fix_hint_code?: string;
  /** Only for `rate_limited`, when the server sent Retry-After. */
  retry_after_ms?: number;
  /** Human/agent-facing sentence explaining a non-complete verdict. */
  note?: string;
}

/** Parse an HTTP `Retry-After` (delta-seconds or HTTP-date) into milliseconds. */
export function parseRetryAfterMs(value: string | null | undefined): number | null {
  if (!value) return null;
  const trimmed = value.trim();
  if (trimmed === '') return null;
  const seconds = Number(trimmed);
  if (Number.isFinite(seconds) && seconds >= 0) return Math.round(seconds * 1000);
  const when = Date.parse(trimmed);
  if (Number.isFinite(when)) {
    const delta = when - Date.now();
    return delta > 0 ? delta : 0;
  }
  return null;
}

/**
 * Classify an error envelope produced by `readJson` (or a locally-built one).
 *
 * Every error means the source did NOT answer — so completeness is `blocked`,
 * never a claim that the answer was empty. The status narrows down why, and
 * `rate_limited`/`timeout` are deliberately distinct from `failed`: both are
 * retryable and neither justifies "no results".
 */
export function envelopeForError(response: Record<string, unknown>): Envelope {
  const status = Number(response.status_code);
  const detail = String(response.detail ?? response.error ?? '').toLowerCase();

  if (status === 401 || status === 403) {
    return { status: 'auth_required', completeness: 'blocked', fix_hint_code: 'AUTH_INVALID' };
  }
  if (status === 429) {
    const env: Envelope = { status: 'rate_limited', completeness: 'blocked', fix_hint_code: 'RATE_LIMITED' };
    const retry = Number(response.retry_after_ms);
    if (Number.isFinite(retry) && retry > 0) env.retry_after_ms = retry;
    return env;
  }
  if (status === 408 || status === 504 || /timed out|timeout|deadline exceeded/.test(detail)) {
    return { status: 'timeout', completeness: 'blocked', fix_hint_code: 'SUPPLIER_TIMEOUT' };
  }
  if (status >= 500) {
    return { status: 'failed', completeness: 'blocked', fix_hint_code: 'SERVICE_UNAVAILABLE' };
  }
  if (status >= 400) {
    return { status: 'failed', completeness: 'blocked', fix_hint_code: 'INVALID_PARAMETER' };
  }
  // No HTTP status at all: the request never got a response.
  return { status: 'failed', completeness: 'blocked', fix_hint_code: 'NETWORK_ERROR' };
}

/**
 * Classify a successful response by the number of results it carries.
 *
 * `count === 0` is `no_results`/`complete` — a genuine, statable empty. A null
 * count means the payload's list could not be found, which is NOT the same as
 * empty: treat it as at least `partial`.
 *
 * `lateMerge` records that the search returned while its late merge was still
 * pending, so the result set may still grow (the caller may not claim to have
 * seen everything).
 */
export function envelopeForData(
  count: number | null,
  opts: { lateMerge?: boolean } = {},
): Envelope {
  if (count === 0) {
    return { status: 'no_results', completeness: 'complete', fix_hint_code: 'NO_RESULTS' };
  }
  if (opts.lateMerge || count === null) {
    return { status: 'ok', completeness: 'partial' };
  }
  return { status: 'ok', completeness: 'complete' };
}

/** Length of the first array found under `keys`, or null when none is present. */
export function firstListLength(
  record: Record<string, unknown>,
  keys: string[],
): number | null {
  for (const key of keys) {
    const value = record[key];
    if (Array.isArray(value)) return value.length;
  }
  return null;
}

function noteFor(completeness: Completeness): string | undefined {
  if (completeness === 'blocked') {
    return 'The source did not answer, so this is NOT "nothing found". Do not tell the user '
      + 'there are no options; report the failure and retry if the status says it is retryable.';
  }
  if (completeness === 'partial') {
    return 'Not every source finished. Results may be incomplete — say so before calling '
      + 'anything cheapest, exhaustive, or final.';
  }
  return undefined;
}

/**
 * Put the envelope first, then the payload. Payload fields win on a name
 * collision, so a booking response's own `status` (e.g. `booking_in_progress`)
 * is never clobbered — that is the booking's state and an agent acts on it.
 * When a collision happens the envelope verdict is kept beside it as
 * `envelope_status`, so it is never silently lost.
 */
export function withEnvelope(
  payload: Record<string, unknown>,
  env: Envelope,
): Record<string, unknown> {
  const out: Record<string, unknown> = {
    status: env.status,
    completeness: env.completeness,
  };
  if (env.fix_hint_code) out.fix_hint_code = env.fix_hint_code;
  if (env.retry_after_ms !== undefined) out.retry_after_ms = env.retry_after_ms;
  const note = noteFor(env.completeness);
  if (note) out.note = note;

  const collides = Object.prototype.hasOwnProperty.call(payload, 'status');
  const merged = Object.assign(out, payload);
  if (collides) merged.envelope_status = env.status;
  return merged;
}
