/**
 * @name Partial server-side request forgery (sanitizer-aware)
 * @description Making a network request with partially user-controlled data in the URL allows for request forgery attacks.
 * @kind path-problem
 * @problem.severity error
 * @security-severity 9.1
 * @precision medium
 * @id py/partial-ssrf-sanitized
 * @tags correctness
 *       security
 *       external/cwe/cwe-918
 */

import python
import semmle.python.dataflow.new.DataFlow
import semmle.python.ApiGraphs
import semmle.python.security.dataflow.ServerSideRequestForgeryQuery
import semmle.python.security.dataflow.ServerSideRequestForgeryCustomizations
import PartialServerSideRequestForgeryFlow::PathGraph

/**
 * The fourth query in this pack, for the fourth variant of one recognition gap:
 * stock CodeQL does not model a validator that establishes safety by RAISING.
 *
 * `py/full-ssrf` was already swapped for a sanitizer-aware copy
 * (FullSsrfSanitized.ql). `py/partial-ssrf` is a DIFFERENT rule id, so that
 * swap never covered it — which is why ten criticals stayed open on the LLM
 * provider probes. Returning the checked value does not clear stock either;
 * the function returns its argument, so taint flows straight through. That was
 * MEASURED for `assert_safe_probe_url` (see .github/codeql/codeql-config.yml),
 * not assumed, and re-measuring it is not worth another cycle.
 *
 * What `assert_safe_outbound_url` (apps/web-server/factory_common/url_safety.py)
 * actually enforces, since barriering it is a claim that it is sound:
 *
 * - Scheme allowlist: http/https only. `urllib` will happily open `file://`,
 *   which turns a model-list call into an arbitrary local-file read.
 * - The host is RESOLVED and EVERY returned address is checked, so a public
 *   hostname that resolves into a blocked range cannot slip past. This is the
 *   part most SSRF guards get wrong by checking the literal string.
 * - Both postures block the cloud metadata addresses: 169.254.0.0/16,
 *   fe80::/10 and fd00:ec2::254. That set is refused even when
 *   `allow_private=True`, which is the whole reason the permissive posture is
 *   not simply "no check".
 * - The default (`allow_private=False`) additionally requires a PUBLIC address.
 * - Fails closed: an unresolvable host is refused, not fetched.
 * - It rejects by RAISING `InputRejectedError` (a `ValueError` subclass), and
 *   returns the checked URL otherwise. Every call site catches `ValueError`
 *   or lets it reach a broad handler, so the registration below is on the
 *   call, not on any exception type.
 * - Callers pair it with `build_no_redirect_opener()`, so a permitted URL that
 *   302s to the metadata address is refused at the hop rather than followed.
 *   (`httpx` callers need nothing: it does not follow redirects by default.)
 *
 * `allow_private=True` is used at every LLM-provider probe on purpose: a
 * self-hosted Ollama, a LAN vLLM and a local proxy are supported configurations
 * here, and a guard that blocked RFC-1918 would break the product, get
 * reverted, and leave nothing. That is a deliberate scope decision, not an
 * oversight, and it is why the blocked set is narrow.
 *
 * KNOWN LIMITATION, recorded so this barrier is not read as a stronger claim
 * than it is: the guard resolves the host, and the transport then resolves it
 * again. Between those two lookups DNS can change, so a caller who controls the
 * authoritative response can return a permitted address to the check and a
 * blocked one to the fetch (DNS rebinding). Closing that requires connecting to
 * the address that was actually validated rather than re-resolving the name.
 * Tracked in PFactory#517; the barrier reflects what the validator checks, not
 * a claim that rebinding is handled.
 */
class OutboundUrlSanitizer extends ServerSideRequestForgery::Sanitizer {
  OutboundUrlSanitizer() {
    exists(DataFlow::CallCfgNode call, string name |
      // `assert_safe_probe_url` is the MCP health probe's adapter; it delegates
      // to `assert_safe_outbound_url` and re-raises as its own error type.
      //
      // PFactory#612 moved `assert_safe_outbound_url` from the forked
      // `server/services/url_safety.py` to the vendored hub canonical at
      // `factory_common/url_safety.py`. Registration is BY NAME and the name did
      // not change, so both clauses below still match; only the file the
      // definition lives in moved, and that file was already in scope (nothing
      // in codeql-config.yml's paths-ignore covers factory_common/).
      name in ["assert_safe_outbound_url", "assert_safe_probe_url"] and
      (
        call.getFunction().asExpr().(Name).getId() = name or
        call.getFunction().asExpr().(Attribute).getName() = name
      ) and
      this = call
    )
    or
    // The body of the validator IS the check: barrier its parameter so the
    // urlparse/getaddrinfo probes inside it do not re-fire the alert the
    // barrier exists to clear. Mirrors FullSsrfSanitized.ql.
    exists(Function f |
      f.getName() in ["assert_safe_outbound_url", "assert_safe_probe_url"] and
      this.(DataFlow::ParameterNode).getParameter() = f.getArg(0)
    )
  }
}

from
  PartialServerSideRequestForgeryFlow::PathNode source,
  PartialServerSideRequestForgeryFlow::PathNode sink, Http::Client::Request request
where
  request = sink.getNode().(Sink).getRequest() and
  PartialServerSideRequestForgeryFlow::flowPath(source, sink) and
  not fullyControlledRequest(request)
select request, source, sink, "Part of the URL of this request depends on a $@.",
  source.getNode(), "user-provided value"
