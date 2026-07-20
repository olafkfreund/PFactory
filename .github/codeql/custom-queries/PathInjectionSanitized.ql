/**
 * @name Uncontrolled data used in path expression (sanitizer-aware)
 * @description Accessing paths influenced by users can allow an attacker to access unexpected resources.
 * @kind path-problem
 * @problem.severity error
 * @security-severity 7.5
 * @sub-severity high
 * @precision high
 * @id py/path-injection-sanitized
 * @tags correctness
 *       security
 *       external/cwe/cwe-022
 *       external/cwe/cwe-023
 *       external/cwe/cwe-036
 *       external/cwe/cwe-073
 *       external/cwe/cwe-099
 */

import python
import semmle.python.dataflow.new.DataFlow
import semmle.python.ApiGraphs
import semmle.python.security.dataflow.PathInjectionQuery
import semmle.python.security.dataflow.PathInjectionCustomizations
import PathInjectionFlow::PathGraph

/**
 * The result of this repo's path sanitizers is a component that cannot escape
 * the root it is joined to. Treat it as a barrier for path injection.
 *
 * Why a custom query is needed at all: stock CodeQL recognises a validator
 * defined AND called in the same module -- which is why
 * `routes/pfactory_tasks.py` has always reported clean -- but it does not
 * follow one imported from `services/`. Every fix in issue #335 routes through
 * the shared `safe_spec_component`, so without this the alert count cannot
 * move however much of the traversal is actually closed. That was measured,
 * not assumed: after phase 1 shipped, alerts below a guard still fired.
 *
 * The names below are barriers for different reasons, and the distinction
 * matters if this list is ever extended:
 *
 * - `safe_spec_component` rejects any component that is not a bare name --
 *   no separators, no `.` or `..`, no absolute paths, no null bytes.
 * - `split_task_id` returns the spec half of a task id having passed it
 *   through `safe_spec_component`, raising a 400 otherwise.
 * - `get_next_spec_id` is server-generated: a numeric counter plus a slug
 *   built by `re.sub(r"[^a-z0-9]+", "-", title)`, so a hostile title cannot
 *   survive it ("../../etc/passwd" becomes "001-etc-passwd").
 * - `_safe_launch_path` does not sanitize a component; it CONFINES a whole
 *   path, requiring it to resolve inside a registered project root. It is a
 *   barrier because containment is checked after `resolve()`, so `..` and
 *   symlinks cannot escape it.
 */
class SpecPathSanitizer extends PathInjection::Sanitizer {
  SpecPathSanitizer() {
    this = API::moduleImport("os").getMember("path").getMember("basename").getACall()
    or
    exists(DataFlow::CallCfgNode call, string name |
      name in [
          "safe_spec_component", "split_task_id", "get_next_spec_id",
          "_safe_launch_path"
        ] and
      (
        call.getFunction().asExpr().(Name).getId() = name or
        call.getFunction().asExpr().(Attribute).getName() = name
      ) and
      this = call
    )
    or
    // The body of _safe_launch_path is the containment check itself: barrier
    // its parameter so the resolve()/is_dir() probes inside the helper do not
    // re-fire the very alert the barrier exists to clear.
    exists(Function f |
      f.getName() = "_safe_launch_path" and
      this.(DataFlow::ParameterNode).getParameter() = f.getArg(0)
    )
  }
}

from PathInjectionFlow::PathNode source, PathInjectionFlow::PathNode sink
where PathInjectionFlow::flowPath(source, sink)
select sink.getNode(), source, sink, "This path depends on a $@.", source.getNode(),
  "user-provided value"
