/**
 * @name Full server-side request forgery (sanitizer-aware)
 * @description Making a network request with user-controlled data in the URL allows for request forgery attacks.
 * @kind path-problem
 * @problem.severity error
 * @security-severity 9.1
 * @precision high
 * @id py/full-ssrf-sanitized
 * @tags correctness
 *       security
 *       external/cwe/cwe-918
 */

import python
import semmle.python.dataflow.new.DataFlow
import semmle.python.ApiGraphs
import semmle.python.security.dataflow.ServerSideRequestForgeryQuery
import semmle.python.security.dataflow.ServerSideRequestForgeryCustomizations
import FullServerSideRequestForgeryFlow::PathGraph

/**
 * `assert_safe_probe_url` (routes/git.py) resolves a probe URL and refuses it
 * unless every address it resolves to is safe to fetch. Treat it as a barrier.
 *
 * The third custom query in this pack, for the third variant of the same
 * recognition gap: stock CodeQL does not model a validator that establishes
 * safety by RAISING. Returning the checked value does not help either -- the
 * function returns its argument, so the taint flows straight through it, which
 * was measured (the alert survived that change) rather than assumed.
 *
 * What the validator actually enforces, since barriering it is a claim that it
 * is sound:
 *
 * - Scheme allowlist: http/https only. `urllib` will happily open `file://`,
 *   which turns a health check into an arbitrary local-file read.
 * - The host is RESOLVED and EVERY returned address is checked, so a public
 *   hostname that resolves into a blocked range cannot slip past. This is the
 *   part most SSRF guards get wrong by checking the literal string.
 * - Blocks link-local (169.254.169.254 is the cloud-metadata endpoint),
 *   reserved, multicast and unspecified addresses.
 * - Fails closed: an unresolvable host is refused, not probed.
 * - Loopback and RFC-1918 are ALLOWED on purpose -- MCP servers in this fleet
 *   run in-cluster or on the operator's machine, so blocking them would break
 *   the feature's main use. That is a deliberate scope decision, not an
 *   oversight, and it is why the blocked set is narrow.
 *
 * KNOWN LIMITATION, recorded so this barrier is not read as a stronger claim
 * than it is: the guard resolves the host, and `urlopen` then resolves it
 * again. Between those two lookups DNS can change, so a caller who controls
 * the authoritative response can return a permitted address to the check and a
 * blocked one to the fetch (DNS rebinding). Closing that requires connecting
 * to the address that was actually validated rather than re-resolving the
 * name, which is a larger change to how the probe is issued. Tracked in
 * PFactory#517; the barrier reflects what the validator checks, not a claim
 * that rebinding is handled.
 */
class ProbeUrlSanitizer extends ServerSideRequestForgery::Sanitizer {
  ProbeUrlSanitizer() {
    exists(DataFlow::CallCfgNode call |
      (
        call.getFunction().asExpr().(Name).getId() = "assert_safe_probe_url" or
        call.getFunction().asExpr().(Attribute).getName() = "assert_safe_probe_url"
      ) and
      this = call
    )
    or
    // The body of the validator IS the check: barrier its parameter so the
    // urlsplit/getaddrinfo probes inside it do not re-fire the alert the
    // barrier exists to clear. Mirrors the containment helpers in
    // PathInjectionSanitized.ql.
    exists(Function f |
      f.getName() = "assert_safe_probe_url" and
      this.(DataFlow::ParameterNode).getParameter() = f.getArg(0)
    )
  }
}

from
  FullServerSideRequestForgeryFlow::PathNode source,
  FullServerSideRequestForgeryFlow::PathNode sink, Http::Client::Request request
where
  request = sink.getNode().(Sink).getRequest() and
  FullServerSideRequestForgeryFlow::flowPath(source, sink) and
  fullyControlledRequest(request)
select request, source, sink, "The full URL of this request depends on a $@.", source.getNode(),
  "user-provided value"
