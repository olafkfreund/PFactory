// Argv-safety helpers for the Node scripts.
//
// These are the JS half of the dialect the Python services already speak
// (apps/web-server/server/services/git_utils.py::assert_safe_git_ref and
// PFactory's equivalent). Same names, same rules, so there is one convention in
// the fleet rather than one per language.
//
// The primary defence against command injection is never building a shell
// string: pass an argv array to execFileSync/spawnSync so nothing is word-split
// or metacharacter-expanded. These helpers are the second layer -- an argv array
// still lets a value that starts with '-' be read as an option by the tool being
// run, which is how "a path" turns into "a flag".
//
// CommonJS on purpose: the .js scripts require() it and the .mjs scripts import
// named bindings from it (Node resolves those through cjs-module-lexer).

const { execFileSync } = require('child_process');

/**
 * Return `value` as a string if no CLI would mistake it for an option.
 *
 * Use together with a `--` separator wherever the tool supports one; `--` alone
 * is not enough for tools that take the value before the separator (e.g. the
 * program name itself, or `find`'s start path).
 *
 * @throws {Error} if the value could be parsed as an option.
 */
function assertNotOption(value, field = 'value') {
  const text = String(value);
  if (text.startsWith('-')) {
    throw new Error(`invalid ${field}: reads as a command-line option: ${text.slice(0, 80)}`);
  }
  return text;
}

// Mirrors _GIT_REF_RE in the Python helper, character for character.
const GIT_REF_RE = /^[A-Za-z0-9][A-Za-z0-9_.@/+^~{}-]{0,254}$/;

/**
 * Return `value` as a string if it is safe to hand git as a revision.
 *
 * Rejects anything that could be parsed as an option, and anything carrying its
 * own `..` -- callers join refs into `a..b` ranges, so an embedded range
 * separator would let one field rewrite the range it lands in.
 *
 * @throws {Error} if the value is not a usable revision.
 */
function assertSafeGitRef(value, field = 'ref') {
  const text = String(value);
  if (!GIT_REF_RE.test(text) || text.includes('..')) {
    throw new Error(`invalid ${field}: ${text.slice(0, 80)}`);
  }
  return text;
}

/**
 * `find -L <root> -maxdepth <depth> -type f -name <namePattern>`, first hit.
 *
 * Lives here because it is the argv-array replacement for the
 * `find -L ${root} ... 2>/dev/null | head -1` shell pipelines the capture
 * scripts used to run: `root` is $PLAYWRIGHT_BROWSERS_PATH, so the shell form
 * was js/shell-command-injection-from-environment. The pipeline was only there
 * for `| head -1` and `2>/dev/null`, both of which are done here instead.
 *
 * @returns {string} the first matching path, or '' if there is none.
 */
function findFilesUnder(root, namePattern, depth = 3) {
  if (!root) return '';
  const out = execFileSync(
    'find',
    ['-L', assertNotOption(root, 'search root'), '-maxdepth', String(depth), '-type', 'f', '-name', namePattern],
    { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }
  );
  return out.split('\n')[0].trim();
}

module.exports = { assertNotOption, assertSafeGitRef, findFilesUnder };
