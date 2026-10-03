// Enforces the CI hygiene rules that were adopted after the flight-finder
// study (docs/flight-finder-study-design.md P2/P13). The study's evidence is
// that an unenforced process rule decays: only one of the probed repo's eight
// workflows kept the hardening it documented.
//
// Zero dependencies on purpose — like test/docs-claims.test.mjs, this must run
// even when a package install breaks, so the workflow files are parsed as text.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

const WF_DIR = '.github/workflows';

// Checkouts whose job publishes with the checkout credential: docs.yml runs
// `mkdocs gh-deploy --force`, which pushes. Those must keep the credential.
const PERSIST_CREDENTIALS_ALLOWED = new Set(['docs.yml']);

// Jobs that publish must never be cancelled mid-flight (a cancelled deploy can
// leave a half-published site); every other workflow must cancel superseded runs.
const PUBLISHING = new Set(['docs.yml']);

const SHA_PIN = /@[0-9a-f]{40}$/;
const files = readdirSync(WF_DIR)
  .filter((f) => f.endsWith('.yml') || f.endsWith('.yaml'))
  .sort();

function read(file) {
  return readFileSync(join(WF_DIR, file), 'utf8');
}

/** Top-level keys (column 0) of a workflow file. */
function topLevelKeys(src) {
  return src
    .split('\n')
    .filter((line) => /^[A-Za-z0-9_"'-]+:/.test(line))
    .map((line) => line.split(':')[0].replace(/["']/g, ''));
}

/** The `on:` block as text, so triggers can be inspected without a YAML parser. */
function triggerBlock(src) {
  const lines = src.split('\n');
  const start = lines.findIndex((line) => /^on:/.test(line));
  if (start === -1) return '';
  const rest = lines.slice(start + 1);
  const end = rest.findIndex((line) => /^[A-Za-z0-9_"'-]+:/.test(line));
  return (end === -1 ? rest : rest.slice(0, end)).join('\n');
}

/** `{ name, block }` for every job under `jobs:`. */
function jobBlocks(src) {
  const lines = src.split('\n');
  const start = lines.findIndex((line) => /^jobs:\s*$/.test(line));
  assert.notEqual(start, -1, 'workflow must declare jobs:');

  const jobs = [];
  let current = null;
  for (const line of lines.slice(start + 1)) {
    const header = /^  ([A-Za-z0-9_.-]+):\s*$/.exec(line);
    if (header) {
      if (current) jobs.push(current);
      current = { name: header[1], block: '' };
      continue;
    }
    if (current === null) continue;
    if (/^[A-Za-z0-9_"'-]+:/.test(line)) break; // back to a top-level key
    current.block += `${line}\n`;
  }
  if (current) jobs.push(current);
  assert.ok(jobs.length > 0, 'workflow must declare at least one job');
  return jobs;
}

/** Every `uses:` reference in the file, as the raw line. */
function usesLines(src) {
  return src.split('\n').filter((line) => /^\s*(-\s*)?uses:/.test(line));
}

/**
 * `uses:` lines for actions/checkout whose step does not set
 * `persist-credentials: false`. The step's extent is taken as the lines between
 * this list item and the next list item at the same indentation.
 */
function checkoutsKeepingCredentials(src) {
  const lines = src.split('\n');
  const offenders = [];

  lines.forEach((line, index) => {
    if (!/uses:\s*actions\/checkout@/.test(line)) return;
    const indent = /^(\s*)/.exec(line)[1];
    let step = '';
    for (let i = index + 1; i < lines.length; i += 1) {
      const next = lines[i];
      if (new RegExp(`^${indent}- `).test(next)) break;
      if (next.trim() !== '' && !next.startsWith(`${indent} `)) break;
      step += `${next}\n`;
    }
    if (!/persist-credentials:\s*false/.test(step)) offenders.push(line.trim());
  });

  return offenders;
}

test('every workflow declares least-privilege permissions', () => {
  for (const file of files) {
    assert.ok(
      topLevelKeys(read(file)).includes('permissions'),
      `${file}: missing top-level permissions: (defaults to a read/write token)`,
    );
  }
});

test('every job is bounded by timeout-minutes', () => {
  for (const file of files) {
    for (const job of jobBlocks(read(file))) {
      assert.match(
        job.block,
        /^\s+timeout-minutes:\s*\d+\s*$/m,
        `${file}: job ${job.name} has no timeout-minutes (a hung job burns the 6h default)`,
      );
    }
  }
});

test('every action is pinned to a commit SHA with a version comment', () => {
  for (const file of files) {
    for (const line of usesLines(read(file))) {
      const ref = line.split('uses:')[1].split('#')[0].trim();
      assert.match(ref, SHA_PIN, `${file}: ${line.trim()} is not pinned to a 40-char SHA`);
      assert.match(
        line,
        /#\s*v\d/,
        `${file}: ${line.trim()} has no trailing "# vX.Y.Z" comment (Dependabot needs it to bump the pin)`,
      );
    }
  }
});

test('checkouts do not persist credentials unless the job publishes with them', () => {
  for (const file of files) {
    const offenders = checkoutsKeepingCredentials(read(file));
    if (PERSIST_CREDENTIALS_ALLOWED.has(file)) {
      assert.equal(offenders.length, 1, `${file}: expected exactly one publishing checkout`);
      continue;
    }
    assert.deepEqual(
      offenders,
      [],
      `${file}: checkout step keeps the token in .git/config — add "persist-credentials: false"`,
    );
  }
});

test('every push/PR workflow sets a concurrency group with the right cancellation policy', () => {
  for (const file of files) {
    const triggers = triggerBlock(read(file));
    if (!/\bpush:|\bpull_request:/.test(triggers)) continue;

    const src = read(file);
    assert.ok(
      topLevelKeys(src).includes('concurrency'),
      `${file}: runs on push/PR without concurrency (stale runs keep running)`,
    );
    const group = /^\s+group:\s*\S+/m.test(src);
    assert.ok(group, `${file}: concurrency block has no group`);

    const publish = PUBLISHING.has(file);
    const cancel = /cancel-in-progress:\s*(\S+)/.exec(src);
    assert.ok(cancel, `${file}: concurrency block has no cancel-in-progress`);
    assert.equal(
      cancel[1],
      publish ? 'false' : 'true',
      `${file}: cancel-in-progress must be ${publish ? 'false (publishing job)' : 'true (checks)'}`,
    );
  }
});
