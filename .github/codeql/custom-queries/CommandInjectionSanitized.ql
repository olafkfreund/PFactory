/**
 * @name Uncontrolled command line (sanitizer-aware)
 * @description Using externally controlled strings in a command line allows a malicious user to change the meaning of the command.
 * @kind path-problem
 * @problem.severity error
 * @security-severity 9.8
 * @sub-severity high
 * @precision high
 * @id py/command-line-injection-sanitized
 * @tags correctness
 *       security
 *       external/cwe/cwe-078
 *       external/cwe/cwe-088
 */

import python
import semmle.python.dataflow.new.DataFlow
import semmle.python.ApiGraphs
import semmle.python.security.dataflow.CommandInjectionQuery
import semmle.python.security.dataflow.CommandInjectionCustomizations
import CommandInjectionFlow::PathGraph

/**
 * This repo validates every caller-supplied value before it reaches a git
 * argv. Treat the validators as barriers for command injection.
 *
 * Why a custom query is needed at all is the same reason the path-injection
 * variant next door exists, and it is stated in `codeql-config.yml`: stock
 * CodeQL recognises a validator defined AND called in the same module, but
 * does not follow one imported from `services/`. `assert_safe_git_ref` lives
 * in `services/git_utils.py` and every caller imports it, so the two
 * `py/command-line-injection` alerts in issue #505 could not clear however
 * completely the argv was validated.
 *
 * That is worth being explicit about, because "add a barrier" is also what
 * someone would do to silence a real finding. Each name below is a barrier for
 * a stated reason, and the reasons are the whole justification:
 *
 * - `assert_safe_git_ref` is an allowlist, not a denylist: the value must
 *   `fullmatch` `[A-Za-z0-9][A-Za-z0-9_.@/+^~{}-]{0,254}`, so it cannot begin
 *   with `-` and cannot be parsed by git as an option. `git log` accepts
 *   `--output=<file>`, which makes an unvalidated ref a file-write primitive
 *   rather than merely a bad ref, so the leading-character rule is the load
 *   bearing part. It additionally rejects any embedded `..`, because callers
 *   join refs into `a..b` ranges and an embedded separator would let one field
 *   rewrite the range it lands in.
 * - `ref` (routes/changelog.py) is a thin wrapper that calls
 *   `assert_safe_git_ref` and converts its ValueError into a 400. It is listed
 *   because the barrier must be recognised at the call site the taint actually
 *   flows through.
 * - `log_count` bounds a caller-supplied count to 1..N and returns an `int`,
 *   so no string reaches argv at all.
 * - `safe_spec_component` rejects any component that is not a bare name — no
 *   separators, no `.` or `..`, no absolute paths, no null bytes — which also
 *   means it cannot introduce a leading `-` or a shell metacharacter. It is
 *   already a path-injection barrier for the same property.
 *
 * Deliberately NOT listed: `_validate_name`. It is a name check on the
 * worktree, not a ref check, and the module's own comment warns against
 * relying on an incidental property of a different method. The one value whose
 * safety rested on that (`branch`, read back out of the worktree config file)
 * is now asserted at the argv boundary instead, in #505.
 */
class GitRefSanitizer extends CommandInjection::Sanitizer {
  GitRefSanitizer() {
    exists(DataFlow::CallCfgNode call, string name |
      name in [
          "assert_safe_git_ref", "ref", "log_count", "safe_spec_component"
        ] and
      (
        call.getFunction().asExpr().(Name).getId() = name or
        call.getFunction().asExpr().(Attribute).getName() = name
      ) and
      this = call
    )
    or
    // The body of the validator IS the check: barrier its first parameter so
    // the regex/probe inside the helper does not re-fire the alert the barrier
    // exists to clear. Mirrors the containment-helper handling in
    // PathInjectionSanitized.ql.
    exists(Function f |
      f.getName() = "assert_safe_git_ref" and
      this.(DataFlow::ParameterNode).getParameter() = f.getArg(0)
    )
  }
}

from CommandInjectionFlow::PathNode source, CommandInjectionFlow::PathNode sink
where CommandInjectionFlow::flowPath(source, sink)
select sink.getNode(), source, sink, "This command line depends on a $@.", source.getNode(),
  "user-provided value"
