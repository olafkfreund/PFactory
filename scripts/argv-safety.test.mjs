// Run with: npm run test:scripts
//
// Covers scripts/argv-safety.cjs and, more importantly, proves that the call
// sites it guards now run through an argv array rather than a shell string --
// the fix for js/indirect-command-line-injection and
// js/shell-command-injection-from-environment.
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { assertNotOption, assertSafeGitRef } = require('./argv-safety.cjs');

test('assertNotOption passes ordinary operands through', () => {
  assert.equal(assertNotOption('/tmp/browsers'), '/tmp/browsers');
  assert.equal(assertNotOption('patch'), 'patch');
  assert.equal(assertNotOption(''), '');
});

test('assertNotOption rejects anything a CLI would read as a flag', () => {
  for (const bad of ['-v', '--output=/etc/passwd', '-exec']) {
    assert.throws(() => assertNotOption(bad, 'root'), /reads as a command-line option/);
  }
});

test('assertSafeGitRef accepts real revisions', () => {
  assert.equal(assertSafeGitRef('main'), 'main');
  assert.equal(assertSafeGitRef('aifactory/8f2c-4b1a'), 'aifactory/8f2c-4b1a');
  assert.equal(assertSafeGitRef('v1.2.3'), 'v1.2.3');
});

test('assertSafeGitRef rejects options, shell metacharacters and range separators', () => {
  for (const bad of ['--upload-pack=touch /tmp/pwned', 'main; rm -rf /', 'a..b', 'main$(id)', '']) {
    assert.throws(() => assertSafeGitRef(bad, 'spec_id'), /invalid spec_id/);
  }
});

test('execFileSync does not interpret shell metacharacters in an argument', () => {
  // The whole point of the argv-array form. Under a shell string the
  // substitution below would be expanded before the program ever saw it; under
  // an argv array it must come back byte-for-byte as typed.
  //
  // The vulnerable shape is written as a literal string rather than executed,
  // so this test does not itself carry an injectable construct:
  //   const VULNERABLE = "execSync('echo ' + userInput)";
  const payload = '$(touch /tmp/argv-safety-should-not-exist); `id`; rm -rf /';
  const out = execFileSync(
    process.execPath,
    ['-e', 'process.stdout.write(process.argv[1])', payload],
    { encoding: 'utf8' }
  );
  assert.equal(out, payload);
});

test('no script in scripts/ builds a shell command dynamically', () => {
  // The root-cause check behind the js/indirect-command-line-injection and
  // js/shell-command-injection-from-environment fixes: every one of those alerts
  // came from a shell string assembled at run time. Reintroduce that shape
  // anywhere under scripts/ and this test goes red.
  //
  // The rule is deliberately syntactic rather than taint-based: exec/execSync
  // must be called with a quoted string literal, never a template literal and
  // never a variable. That keeps `execSync('command -v ffmpeg')` -- a constant
  // needing a shell builtin -- while rejecting both `execSync(`...${x}...`)`
  // and the `const cmd = ...; execSync(cmd)` spelling of the same thing.
  const DYNAMIC_SHELL_CALL = /(?<![.\w])(?:exec|execSync)\(\s*(?!['"])/;
  // Comments are stripped first: several of these files document the shape they
  // used to have, and the tripwire must not fire on its own explanation.
  const stripComments = (src) =>
    src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/.*$/gm, '$1');
  const dir = path.dirname(fileURLToPath(import.meta.url));
  const offenders = [];
  for (const name of fs.readdirSync(dir)) {
    if (!/\.(js|cjs|mjs)$/.test(name) || name.endsWith('.test.mjs')) continue;
    if (DYNAMIC_SHELL_CALL.test(stripComments(fs.readFileSync(path.join(dir, name), 'utf8')))) offenders.push(name);
  }
  assert.deepEqual(offenders, []);
});
