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

/**
 * Empty has a reason, and the reason is data (canonical: `trvl-study-design.md` §2.1).
 *
 *   provider_empty  the source answered and its own result set was empty
 *   not_loaded      no usable response at all: timeout, limiter refusal, auth
 *                   rejection, or a 200 whose payload never arrived
 *   filtered_out    the source returned rows; our own filters removed all of them
 *
 * Only `provider_empty` may ever be reported as `no_results` — `filtered_out` is
 * evidence about our filter, not about the market.
 */
export type EmptyReason = 'provider_empty' | 'not_loaded' | 'filtered_out';

/**
 * Coverage and result state are two fields, and the illegal cells are
 * **unrepresentable here** rather than merely rejected at runtime — the point of
 * writing them as a union. `{coverage_mode: 'unavailable', result_state: 'results'}`
 * asserts a result from evidence we never obtained; `{complete, unavailable}` is
 * contradictory. Canonical table: `trvl-study-design.md` §2.1.
 */
export type LegalCoverage =
  | { coverage_mode: 'complete'; result_state: 'results' | 'confirmed_empty' }
  | { coverage_mode: 'partial'; result_state: 'results' | 'confirmed_empty' }
  | { coverage_mode: 'unavailable'; result_state: 'unavailable' };

export type CoverageMode = LegalCoverage['coverage_mode'];
export type ResultState = LegalCoverage['result_state'];

/**
 * How much to trust `observed_at` (§2.1). `provider` is reserved for a timestamp
 * the upstream stamped for the data itself; a fetch time is `provider_fetch`.
 * Manufacturing either from a client clock is the failure this enum exists to
 * prevent, so this server — whose upstream (letsfg.co) exposes no observation
 * stamp — emits `client_receipt` and never `provider`.
 */
export type ObservedAtBasis = 'provider' | 'provider_fetch' | 'client_receipt';

/**
 * `unknown` is the honest default for anything this server did not itself fetch.
 * The retained-observation staleness classes belong to the scanner's observation
 * store, not to a wire contract whose every tool call fetches live — so the only
 * two values here are "fetched in this call" and "we cannot say".
 */
export type Freshness = 'live' | 'unknown';

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
  /** Why a result set is empty. Present only when emptiness is being asserted. */
  empty_reason?: EmptyReason;
  /** The coverage pair (§2.1), flat on the wire. Construct via `legalCoverage`. */
  coverage_mode?: CoverageMode;
  result_state?: ResultState;
  /** ISO-8601. When the returned data was observed — never a client-fabricated provider time. */
  observed_at?: string;
  observed_at_basis?: ObservedAtBasis;
  freshness?: Freshness;
}

/**
 * The legal coverage cell for a mode/state pair. Throws on the two cells the
 * canonical table forbids, so a dynamic payload cannot smuggle one past the
 * type system and into a caller's reasoning.
 */
export function legalCoverage(mode: CoverageMode, state: ResultState): LegalCoverage {
  if (mode === 'unavailable' && state !== 'unavailable') {
    throw new Error(
      `Illegal coverage: {coverage_mode: 'unavailable', result_state: '${state}'} — `
      + 'no usable evidence cannot carry a result.',
    );
  }
  if (mode !== 'unavailable' && state === 'unavailable') {
    throw new Error(
      `Illegal coverage: {coverage_mode: '${mode}', result_state: 'unavailable'} — contradictory.`,
    );
  }
  return { coverage_mode: mode, result_state: state } as LegalCoverage;
}

/** Non-throwing form, for validating a payload that arrived from somewhere else. */
export function isLegalCoverage(mode: unknown, state: unknown): boolean {
  const modes: unknown[] = ['complete', 'partial', 'unavailable'];
  const states: unknown[] = ['results', 'confirmed_empty', 'unavailable'];
  if (!modes.includes(mode) || !states.includes(state)) return false;
  if (mode === 'unavailable') return state === 'unavailable';
  return state !== 'unavailable';
}

/**
 * `no_results` is justified by exactly one combination: a source-reported empty
 * under complete coverage. Two documented rules meet here — `no_results` may be
 * claimed only for `provider_empty`, and it is prohibited outright while coverage
 * is degraded, because `{no_results, coverage_mode: partial}` asserts an absence
 * the evidence cannot support (§2.1).
 */
export function noResultsJustified(env: Envelope): boolean {
  return env.status === 'no_results'
    && env.empty_reason === 'provider_empty'
    && env.coverage_mode === 'complete';
}

/**
 * Every documented invariant this envelope violates — empty array when it is
 * consistent. Used by the guards, and available for a dev-time assertion on
 * payloads that did not come from this module.
 */
export function envelopeViolations(env: Envelope): string[] {
  const bad: string[] = [];

  if (env.coverage_mode !== undefined || env.result_state !== undefined) {
    if (!isLegalCoverage(env.coverage_mode, env.result_state)) {
      bad.push(`illegal coverage cell {${env.coverage_mode}, ${env.result_state}}`);
    }
  }

  if (env.status === 'no_results' && !noResultsJustified(env)) {
    bad.push(
      `no_results claimed without provider_empty under complete coverage `
      + `(empty_reason=${env.empty_reason ?? 'absent'}, coverage_mode=${env.coverage_mode ?? 'absent'})`,
    );
  }

  if (env.empty_reason === 'provider_empty' && env.result_state !== undefined
      && env.result_state !== 'confirmed_empty') {
    bad.push('provider_empty with a non-empty result_state');
  }
  if (env.empty_reason === 'not_loaded' && env.completeness !== 'blocked') {
    bad.push('not_loaded must be blocked — nothing loaded');
  }
  if (env.empty_reason === 'filtered_out'
      && (env.status !== 'ok' || env.completeness !== 'partial')) {
    bad.push('filtered_out is ok/partial — we narrowed it, the source did not report nothing');
  }
  if (env.observed_at_basis === 'provider' && env.observed_at === undefined) {
    bad.push('provider basis with no observed_at');
  }

  return bad;
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
 * Every error means the source did NOT answer — so completeness is `blocked`
 * and coverage is `unavailable`, never a claim that the answer was empty. The
 * status narrows down why, and `rate_limited`/`timeout` are deliberately distinct
 * from `failed`: both are retryable and neither justifies "no results".
 *
 * All of them carry `empty_reason: 'not_loaded'`. §2.1 defines that reason as "no
 * usable response at all: timeout, limiter refusal, consent interstitial, or a 200
 * whose payload never arrived" — so a limiter refusal and an auth rejection are
 * members of the class by the definition itself, not by extension.
 */
export function envelopeForError(response: Record<string, unknown>): Envelope {
  const status = Number(response.status_code);
  const detail = String(response.detail ?? response.error ?? '').toLowerCase();

  const notLoaded: Envelope = {
    status: 'failed',
    completeness: 'blocked',
    empty_reason: 'not_loaded',
    coverage_mode: 'unavailable',
    result_state: 'unavailable',
  };

  if (status === 401 || status === 403) {
    return { ...notLoaded, status: 'auth_required', fix_hint_code: 'AUTH_INVALID' };
  }
  if (status === 429) {
    const env: Envelope = { ...notLoaded, status: 'rate_limited', fix_hint_code: 'RATE_LIMITED' };
    const retry = Number(response.retry_after_ms);
    if (Number.isFinite(retry) && retry > 0) env.retry_after_ms = retry;
    return env;
  }
  if (status === 408 || status === 504 || /timed out|timeout|deadline exceeded/.test(detail)) {
    return { ...notLoaded, status: 'timeout', fix_hint_code: 'SUPPLIER_TIMEOUT' };
  }
  if (status >= 500) {
    return { ...notLoaded, status: 'failed', fix_hint_code: 'SERVICE_UNAVAILABLE' };
  }
  if (status >= 400) {
    return { ...notLoaded, status: 'failed', fix_hint_code: 'INVALID_PARAMETER' };
  }
  // No HTTP status at all: the request never got a response.
  return { ...notLoaded, status: 'failed', fix_hint_code: 'NETWORK_ERROR' };
}

/**
 * Classify a successful response by the number of results it carries.
 *
 * `count === 0` is `no_results`/`complete` — a genuine, statable empty — **but
 * only while coverage is complete**. Under narrowed coverage the same zero rows
 * are `confirmed_empty`, and reporting them as `no_results` is the prohibited
 * combination of §2.1: it would assert an absence the evidence cannot support.
 *
 * A null count means the payload's list could not be found, which is NOT the same
 * as empty: treat it as at least `partial`. `lateMerge` records that the search
 * returned while its late merge was still pending, so the result set may still
 * grow — the caller may not claim to have seen everything.
 */
export function envelopeForData(
  count: number | null,
  opts: { lateMerge?: boolean; coverage?: Exclude<CoverageMode, 'unavailable'> } = {},
): Envelope {
  const coverage = opts.coverage ?? 'complete';

  if (count === 0) {
    if (coverage === 'complete') {
      return {
        status: 'no_results',
        completeness: 'complete',
        fix_hint_code: 'NO_RESULTS',
        empty_reason: 'provider_empty',
        ...legalCoverage('complete', 'confirmed_empty'),
      };
    }
    return {
      status: 'ok',
      completeness: 'partial',
      empty_reason: 'provider_empty',
      ...legalCoverage('partial', 'confirmed_empty'),
    };
  }

  const degraded = opts.lateMerge === true || count === null;
  return {
    status: 'ok',
    completeness: degraded ? 'partial' : 'complete',
    ...legalCoverage(degraded ? 'partial' : coverage, 'results'),
  };
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

function noteFor(env: Envelope): string | undefined {
  if (env.completeness === 'blocked') {
    return 'The source did not answer, so this is NOT "nothing found". Do not tell the user '
      + 'there are no options; report the failure and retry if the status says it is retryable.';
  }
  // The false-"no flights" failure mode: an empty under narrowed coverage says
  // something about our visibility, not about the market.
  if (env.result_state === 'confirmed_empty' && env.coverage_mode !== 'complete') {
    return 'The source returned nothing, but our coverage was narrowed — this is NOT evidence '
      + 'that the route is unserved. Do not say there are no flights on this basis.';
  }
  if (env.completeness === 'partial') {
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
  if (env.empty_reason) out.empty_reason = env.empty_reason;
  if (env.coverage_mode) out.coverage_mode = env.coverage_mode;
  if (env.result_state) out.result_state = env.result_state;
  if (env.observed_at) out.observed_at = env.observed_at;
  if (env.observed_at_basis) out.observed_at_basis = env.observed_at_basis;
  if (env.freshness) out.freshness = env.freshness;
  const note = noteFor(env);
  if (note) out.note = note;

  const collides = Object.prototype.hasOwnProperty.call(payload, 'status');
  const merged = Object.assign(out, payload);
  if (collides) merged.envelope_status = env.status;
  return merged;
}
