# KV Gateway

## User Story

As a service consumer of the Key-Value Gateway, I want to call its HTTP
endpoints and get correct status codes and JSON bodies, so that I can rely on
the gateway for health checks, storing values, listing keys, and getting a
clear 404 when a key does not exist.

## Acceptance Criteria

> **Lane:** API only — these are **httpx** tests against the running service.
> **No browser / Playwright.** Drive the service over HTTP at the base URL from
> the `gateway` target (read it from the `PFACTORY_TARGET_URL` environment
> variable). Do **not** import the application module; treat it as a black box.

### AC#1 — Health endpoint

`GET /health` must return HTTP 200 with a JSON body where `status == "ok"` and
`service == "kv-gateway"`.

### AC#2 — Set then get round-trips the value

`PUT /api/keys/{key}` with body `{"value": <v>}` must return 200, and a
subsequent `GET /api/keys/{key}` must return 200 with `value == <v>` (verify
with a string value such as `"hello"` and a numeric value such as `42`).

### AC#3 — Listing reflects stored keys

After setting two keys, `GET /api/keys` must return 200 with a `keys` array
containing both key names and a `count` of at least 2.

### AC#4 — Missing key returns 404

`GET /api/keys/<a key that was never set>` must return HTTP 404. (A gateway
that returns 200 with a null value for a missing key is a contract bug — this
test must catch it.)

## Out of Scope

- Authentication / API keys
- Persistence across restarts (the store is in-memory)
- Rate limiting / pagination

## Technical Notes

- Target: `gateway` (http) — base URL provided via `PFACTORY_TARGET_URL`.
- Framework: pytest + httpx (`import httpx`); assert on `response.status_code`
  and `response.json()`.
- Each test should be independent — use a unique key name per test (e.g. a
  suffix) so ordering doesn't matter against the shared in-memory store.
