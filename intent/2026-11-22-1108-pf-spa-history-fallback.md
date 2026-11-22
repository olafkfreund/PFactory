---
status: draft
issue: 1108
author: olafkfreund
---

# Intent: serve the SPA shell for client-side routes

## Problem

PFactory 0.9.0 answers a direct GET or reload of a client route (`/p/<slug>`, `/settings`) with FastAPI's JSON 404. In-app navigation works. Since #3857 phase 4 the URL is the source of truth for the project, so a project link cannot be shared, bookmarked or reloaded.

## Proposed outcome

A direct GET of any client route returns the SPA shell (`index.html`, 200) and React Router renders it. API, websocket, asset and schema paths keep their real 404.

## Affected users and systems

PFactory web-server (`apps/web-server/server/main.py`), every PFactory portal user.

## Constraints

- Same behaviour as TFactory #878 and AIFactory: one fallback rule across the portals.
- Missing hashed assets must stay 404 (a deploy that serves HTML for a missing bundle is hard to spot).
- Keep the existing cache policy (shell no-cache, assets immutable).

## Open questions

None.
