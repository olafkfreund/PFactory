# SaaS connector targets — ServiceNow · Salesforce · SAP · MuleSoft (#111)

> Status: the connector **target type**, the **platform registry**, and the
> per-platform **check templates** are landed (schema-validated + unit-tested).
> End-to-end against a real instance needs a live tenant + a stored credential,
> and the Planner/Gen-Functional routing that prefers a platform's library
> template is the next increment. Epic
> [#111](https://github.com/olafkfreund/PFactory/issues/111) · Horizon 3.

Big platforms can technically be driven through the browser/api lanes, but
naive Playwright against an SSO-gated, dynamic-DOM SaaS is brittle. A
**connector target** makes a managed platform first-class: name the `platform`
and PFactory knows its API style, which `library/` check template to use, and
the guidance to inject — and tests drive the platform's **REST/OData API** (the
api lane), which is far more stable than UI automation.

## Declare a connector in `.pfactory.yml`

```yaml
version: 1
egress:
  enabled: true                 # required — SaaS APIs are off-network
targets:
  - name: snow
    type: connector
    platform: servicenow        # servicenow | salesforce | sap | mulesoft
    base_url: https://acme.service-now.com
    entities: [incident, change_request]   # tables / objects / OData sets (hints)
    auth:
      type: ref                 # resolve an OAuth/SSO token from the vault
      ref: snow-svc
test_credentials:
  snow-svc:
    ref: env:SNOW_TOKEN         # or vault:… — see guides/test-target-auth.md
    as_secret: TEST_PASSWORD    # injected to the test as a bearer token
```

Auth + `base_url` reuse the existing HTTP + credential-vault plumbing — a
`auth: { type: ref }` connector resolves its token exactly like an `http`
target, and the same `test_credentials` validator applies (`auth.ref` must name
a declared credential, and declaring `test_credentials` requires `egress`).

The generated api-lane test reads `PFACTORY_TARGET_URL` (the instance) +
`TEST_PASSWORD` (the resolved token) — never a hard-coded secret. See the
shipped templates: `frameworks/pytest/library/servicenow-table-api.py.tmpl`,
`salesforce-rest-query.py.tmpl`, `mulesoft-api.py.tmpl`.

## The platform registry

`CONNECTOR_PLATFORMS` in `apps/backend/pfactory_yml/schema.py` maps each
platform to its API style, its `library/` check template, and Gen-Functional
guidance:

| Platform | API style | Library template |
|----------|-----------|------------------|
| `servicenow` | REST (Table API) | `servicenow-table-api.py.tmpl` |
| `salesforce` | REST + SOQL | `salesforce-rest-query.py.tmpl` |
| `mulesoft` | REST | `mulesoft-api.py.tmpl` |
| `sap` | OData (Gateway / S/4HANA) | _TBD_ |

## Adding the next platform (the pattern)

1. **Registry** — add an entry to `CONNECTOR_PLATFORMS` (`schema.py`): the
   `api_style`, the `library_template` filename, and a one-paragraph
   `guidance` block (prefer API over UI; which endpoint; how auth flows).
2. **Type** — add the platform string to `ConnectorTarget.platform`'s
   `Literal[...]` so it validates.
3. **Template** — add `frameworks/pytest/library/<template>.py.tmpl`: a
   parameterised api-lane check that reads `PFACTORY_TARGET_URL` +
   `TEST_PASSWORD` and asserts on the platform's API response. Front-matter
   declares `requires_target: true`, `requires_auth: true`, and the `vars`.

`tests/test_connector_target.py::test_every_platform_template_exists` enforces
that every registered platform points at a real template file.

## Why API-first

- **Stable.** A REST/OData contract doesn't churn like a Lightning/SAP-GUI DOM.
- **Auth-friendly.** Service-account OAuth tokens beat scripting an SSO/SAML
  browser dance.
- **Fast + headless.** No browser runtime needed for the api lane.

Browser-lane SaaS automation (login via `auth.setup.ts` storageState, then
assert protected pages) remains available for genuinely UI-only flows — see
`guides/test-target-auth.md`.
