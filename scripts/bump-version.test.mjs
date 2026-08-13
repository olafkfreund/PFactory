// Run with: npm run test:scripts
//
// Covers the two security fixes in bump-version.js:
//   - js/file-system-race, and the ENOENT-vs-unreadable distinction that the
//     existsSync-then-read shape silently erased (readIfPresent)
//   - js/regex-injection + js/incomplete-sanitization (changelogHasVersion)
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { readIfPresent, changelogHasVersion } = require('./bump-version.js');

const tmp = () => fs.mkdtempSync(path.join(os.tmpdir(), 'bumpver-'));

test('readIfPresent returns contents for a file that exists', () => {
  const file = path.join(tmp(), 'here.txt');
  fs.writeFileSync(file, 'contents');
  assert.equal(readIfPresent(file), 'contents');
});

test('readIfPresent returns null for a genuinely missing file', () => {
  assert.equal(readIfPresent(path.join(tmp(), 'nope.txt')), null);
});

test('readIfPresent rethrows a non-ENOENT error instead of reporting "absent"', () => {
  // Reading a directory raises EISDIR, not ENOENT. The old
  // `existsSync(p) && readFileSync(p)` shape collapsed every such failure into
  // "missing", so the bump skipped the file and reported success.
  assert.throws(
    () => readIfPresent(tmp()),
    (err) => err.code !== undefined && err.code !== 'ENOENT'
  );
});

const CHANGELOG = '# Changelog\n\n## 9.9.9 - 2026-08-13\n\n- thing\n\n## 9.9.8\n\n- older\n';

test('changelogHasVersion finds a real header', () => {
  assert.equal(changelogHasVersion(CHANGELOG, '9.9.9'), true);
  assert.equal(changelogHasVersion(CHANGELOG, '9.9.8'), true);
  assert.equal(changelogHasVersion(CHANGELOG, '9.9.7'), false);
});

test('changelogHasVersion does not match a version prefix', () => {
  // "9.9" must not satisfy "## 9.9.9" -- the next char is '.', not \s or '-'.
  assert.equal(changelogHasVersion(CHANGELOG, '9.9'), false);
});

test('regex metacharacters in the version are matched literally', () => {
  assert.equal(changelogHasVersion(CHANGELOG, '.*'), false);
  assert.equal(changelogHasVersion(CHANGELOG, '[0-9].[0-9].[0-9]'), false);
  assert.equal(changelogHasVersion(CHANGELOG, '\\S+'), false);

  // Sanity: the construction this replaced really was injectable. It escaped
  // dots and left every other metacharacter live, so these two "versions"
  // matched a changelog that contains neither of them literally.
  const legacy = (version) =>
    new RegExp(`^## ${version.replace(/\./g, '\\.')}(\\s|-)`, 'm').test(CHANGELOG);
  assert.equal(legacy('\\S+'), true);
  assert.equal(legacy('[0-9].[0-9].[0-9]'), true);
});
