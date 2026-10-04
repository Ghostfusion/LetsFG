// Docs-claims and documentation-integrity test.
//
// Zero dependencies: plain Node (>=18), `node --test test/docs-claims.test.mjs`.
//
// Why this exists: the repository's prose and machine-readable surfaces drifted
// from the code repeatedly (version tables, tool lists, an OpenAPI server URL
// that double-prefixes its own paths). Nothing failed when they drifted. This
// test pins the claims that a reader or an agent actually relies on, and
// nothing else — it asserts facts, not wording.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync, statSync, readdirSync } from 'node:fs';
import { join, dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const read = (p) => readFileSync(join(ROOT, p), 'utf8');
const json = (p) => JSON.parse(read(p));

// ── Versions ──────────────────────────────────────────────────────────────
// A published package and its registry manifest must agree, or `npx`/`pip`
// resolves one version while the docs describe another.

test('the MCP package version matches the registry manifest, both places', () => {
  const pkg = json('sdk/mcp/package.json').version;
  const server = json('server.json');
  assert.equal(server.version, pkg, 'server.json version must match sdk/mcp/package.json');
  assert.ok(Array.isArray(server.packages) && server.packages.length > 0, 'server.json.packages is required');
  for (const p of server.packages) {
    assert.equal(p.version, pkg, `server.json package ${p.identifier} version must match sdk/mcp/package.json`);
  }
});

// The version the server reports in `initialize.serverInfo` and in its
// User-Agent is a separate literal from the package manifest. It drifted to
// 1.3.1 while the package was 2026.5.78, so an agent could not tell which build
// had answered it.
test('the MCP runtime version matches its package version', () => {
  const pkg = json('sdk/mcp/package.json').version;
  const runtime = /^const VERSION = '([^']+)'/m.exec(read('sdk/mcp/src/index.ts'))?.[1];
  assert.ok(runtime, 'sdk/mcp/src/index.ts must declare VERSION');
  assert.equal(runtime, pkg, 'the version in initialize.serverInfo and the User-Agent must be the package version');
});

test('the Python package version matches its runtime __version__', () => {
  const pyproject = read('sdk/python/pyproject.toml');
  const declared = /^\s*version\s*=\s*"([^"]+)"/m.exec(pyproject)?.[1];
  assert.ok(declared, 'pyproject.toml must declare a version');
  const runtime = /^__version__\s*=\s*"([^"]+)"/m.exec(read('sdk/python/letsfg/__init__.py'))?.[1];
  assert.ok(runtime, "letsfg/__init__.py must declare __version__");
  assert.equal(runtime, declared, 'pyproject.toml version and letsfg.__version__ must agree');
});

// ── MCP tool surface ──────────────────────────────────────────────────────
// `tools/list` is the contract an agent codes against. Every tool the server
// advertises must be documented in the package README, or an agent cannot know
// what it is calling.

function advertisedToolNames() {
  const src = read('sdk/mcp/src/index.ts');
  // The declaration carries a type annotation so `outputSchema` can be attached
  // to every entry; match the array regardless of whether one is present.
  const match = /const TOOLS(?::\s*[A-Za-z_$][\w$]*\[\])?\s*=\s*\[/.exec(src);
  const start = match ? match.index : -1;
  assert.ok(start >= 0, 'TOOLS array not found in sdk/mcp/src/index.ts');
  const end = src.indexOf('\n];', start);
  assert.ok(end > start, 'TOOLS array is not terminated');
  const block = src.slice(start, end);
  // Top-level tool entries are `    name: 'x',` at four-space indent.
  return [...block.matchAll(/\n {4}name: '([a-z_]+)',/g)].map((m) => m[1]);
}

test('every advertised MCP tool is documented in sdk/mcp/README.md', () => {
  const names = advertisedToolNames();
  assert.ok(names.length >= 14, `expected the full tool surface, found ${names.length}`);
  const readme = read('sdk/mcp/README.md');
  const missing = names.filter((n) => !readme.includes('`' + n + '`'));
  assert.deepEqual(missing, [], `undocumented tools in sdk/mcp/README.md: ${missing.join(', ')}`);
});

test('the same tool set is documented as retired-aware', () => {
  // unlock_flight_offer is delisted from tools/list but still answered, and the
  // README keeps a row for it that says so. Guard the wording so the row cannot
  // be rewritten into an available-tool claim.
  const readme = read('sdk/mcp/README.md');
  assert.ok(/`unlock_flight_offer`[^\n]*RETIRED/.test(readme),
    'the unlock_flight_offer README row must still be marked RETIRED');
  assert.ok(!advertisedToolNames().includes('unlock_flight_offer'),
    'unlock_flight_offer must not be advertised in tools/list');
});

// ── OpenAPI server + path composition ─────────────────────────────────────
// `servers[0].url` and `paths` compose. If both carry `/api/v1`, every generated
// URL is `.../api/v1/api/v1/...` and every doc that quotes the real URL is
// wrong. Assert they compose to the base the docs advertise.

test('openapi.yaml server + paths compose to the documented base URL', () => {
  const spec = read('openapi.yaml');
  // Allow comment/blank lines between `servers:` and the first `- url:` entry.
  const serverUrl = /^servers:\s*\n(?:\s*#[^\n]*\n|\s*\n)*\s*-\s*url:\s*(\S+)/m.exec(spec)?.[1];
  assert.ok(serverUrl, 'openapi.yaml must declare servers[0].url');

  const paths = [...spec.matchAll(/\n {2}(\/[A-Za-z0-9{}._/\\-]+):/g)].map((m) => m[1]);
  assert.ok(paths.length >= 10, `expected the documented paths, found ${paths.length}`);

  const base = new URL(serverUrl).pathname.replace(/\/$/, '');
  // The documented base is https://letsfg.co/developers/api/v1. `servers[0].url`
  // and the path keys compose, so exactly one of them may carry `/api/v1`.
  const occurrences = (s) => s.split('/api/v1').length - 1;
  for (const p of paths) {
    const composed = `${base}${p}`;
    assert.ok(composed.startsWith('/developers/api/v1/'),
      `server (${serverUrl}) + path ${p} composes to "${composed}", outside /developers/api/v1/`);
    assert.equal(occurrences(composed), 1,
      `/api/v1 appears ${occurrences(composed)} times in "${composed}" — the server URL and the ` +
      'path keys both carry it, so every generated URL is double-prefixed');
  }
  // Pin the concrete documented URL as well as the shape.
  const register = paths.find((p) => p.endsWith('/agents/register'));
  assert.ok(register, 'expected an /agents/register path');
  assert.equal(`${base}${register}`, '/developers/api/v1/agents/register',
    'the register path must compose to the URL the docs advertise');
});

// ── Local links ───────────────────────────────────────────────────────────
// A relative link to a file that does not exist is a dead end for a reader and
// for an agent following the docs.

const LINK_ROOTS = [
  'README.md', 'AGENTS.md', 'CLAUDE.md', 'CONTRIBUTING.md', 'SECURITY.md',
  'OMARCHY-PLUGIN.md', 'SKILL.md',
];

function markdownFiles() {
  const out = [...LINK_ROOTS.filter((f) => existsSync(join(ROOT, f)))];
  const docsDir = join(ROOT, 'docs');
  const skip = new Set(['overrides', 'assets']);
  for (const entry of readdirSync(docsDir, { withFileTypes: true })) {
    if (entry.isDirectory()) {
      if (skip.has(entry.name)) continue;
      for (const f of readdirSync(join(docsDir, entry.name))) {
        if (f.endsWith('.md')) out.push(join('docs', entry.name, f));
      }
    } else if (entry.name.endsWith('.md')) {
      out.push(join('docs', entry.name));
    }
  }
  return out;
}

function localLinks(file) {
  const text = read(file);
  const links = [];
  const re = /\[[^\]]*\]\(([^)\s]+)(?:\s+"[^"]*")?\)/g;
  let m;
  while ((m = re.exec(text)) !== null) {
    const target = m[1];
    if (/^(https?:|mailto:|tel:|#|\/\/)/i.test(target)) continue;
    if (target.startsWith('/')) continue; // site-root path, resolved by MkDocs
    const withoutAnchor = target.split('#')[0];
    if (!withoutAnchor) continue;
    links.push(withoutAnchor);
  }
  return links;
}

test('every local markdown link resolves to an existing path', () => {
  const broken = [];
  for (const file of markdownFiles()) {
    for (const link of localLinks(file)) {
      const target = resolve(ROOT, dirname(file), decodeURIComponent(link));
      if (!existsSync(target)) {
        broken.push(`${file} -> ${link}`);
      } else if (statSync(target).isDirectory() && !existsSync(join(target, 'index.md'))) {
        // Directory links only resolve if MkDocs can serve an index for them.
        if (!existsSync(join(target, 'README.md'))) {
          broken.push(`${file} -> ${link} (directory without index.md)`);
        }
      }
    }
  }
  assert.deepEqual(broken, [], `broken local links:\n  ${broken.join('\n  ')}`);
});

// ── Supported versions ────────────────────────────────────────────────────
// SECURITY.md tells a reporter which lines get fixes. A table naming a version
// nobody ships (it said 1.0.x while every package was on 2026.5.x) tells them
// their finding is out of scope when it is not.

test('SECURITY.md supported versions track the shipped version line', () => {
  const supported = read('SECURITY.md');
  const pythonVersion = /^version\s*=\s*"([^"]+)"/m.exec(read('sdk/python/pyproject.toml'))?.[1];
  const rows = [
    ['letsfg (Python)', pythonVersion],
    ['letsfg (npm)', json('sdk/js/package.json').version],
    ['letsfg-mcp (npm)', json('sdk/mcp/package.json').version],
  ];
  const escape = (value) => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

  for (const [name, version] of rows) {
    assert.ok(version, `${name}: could not read a version from its manifest`);
    const line = `${version.split('.').slice(0, 2).join('.')}.x`;
    const row = new RegExp(`\\|\\s*${escape(name)}\\s*\\|\\s*${escape(line)}\\s*\\|`);
    assert.match(supported, row, `SECURITY.md does not list ${name} at ${line} (manifest says ${version})`);
  }
});

// ── Parked Python tests ───────────────────────────────────────────────────
// sdk/python/conftest.py parks modules that cannot run. Two guards, both about
// keeping the quarantine a work queue rather than a resting place:
//
//   1. a dead entry (a parked module that no longer exists) fails;
//   2. the set is pinned to a recorded size, so *adding* a module requires a
//      deliberate edit here — quarantine cannot grow silently.
//
// Quarantine is not the fix: a module should be repaired against the current API
// or deleted. See docs/trvl-study-design.md §2.10.

const PARKED_TEST_MODULES = 19;

test('the parked Python test set is pinned and every entry exists', () => {
  const entries = [...read('sdk/python/conftest.py').matchAll(/"(tests\/[^"]+)"/g)].map((m) => m[1]);
  assert.ok(entries.length > 0, 'expected collect_ignore_glob entries in sdk/python/conftest.py');
  assert.equal(
    entries.length,
    PARKED_TEST_MODULES,
    'the quarantine set changed: repair or delete the module instead of parking it, or update ' +
      'PARKED_TEST_MODULES here deliberately (and the reason in sdk/python/conftest.py)',
  );

  const missing = entries.filter((p) => !existsSync(join(ROOT, 'sdk/python', p)));
  assert.deepEqual(missing, [], 'conftest.py parks modules that no longer exist — delete those entries');
});
