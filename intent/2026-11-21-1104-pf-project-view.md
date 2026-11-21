---
status: draft
issue: 1104
author: olafkfreund
---

# Intent: URL-scoped project view in the PFactory frontend

## Problem

In PFactory's web frontend (`apps/frontend-web`), the current project lives in Zustand (`stores/project-store.ts`), `localStorage` (`lastSelectedProjectId`), and server-persisted `tab-state.json`, rather than in the URL. Every project view in `App.tsx` is selected via local React state (`activeView`) and gated by `selectedProject`, while `planning` and `pfactory` mount unconditionally at `/*`. Only `/console/:projectId/:specId` carries a project identifier in the path. As a result:

- Deep links to project views cannot be shared or bookmarked.
- Two browser tabs cannot reliably view two different projects without `lastSelectedProjectId` and saved tab state fighting each other.
- Browser reload and back/forward navigation do not reflect or restore the project and view from the URL.
- The top bar `ProjectSelector` (`components/settings/ProjectSelector.tsx`) exposes a one-click trash icon that unregisters a project with no confirmation.
- Switching portals via `PortalSwitcher` drops the current project context instead of keeping `/p/<slug>`.
- CommandPalette (`⌘K`) lacks the two-step "Switch project…" picker.

This is the PFactory portion of `olafkfreund/Factory#3857` phase 4 (URL + header rollout), in the handover programme `olafkfreund/Factory#3864`, porting AIFactory PR #2159 (`origin/dev`).

## Proposed outcome

- Every project view lives under `/p/:slug[/:view]` (default view `planning`), and the URL is authoritative. Views do not mount until the store's active/selected project matches the resolved URL project.
- Saved tab state (`tab-state.json` / `lastSelectedProjectId`) restores open tabs only and never overrides the URL. Reload, back/forward, and two tabs on two projects keep their own project and tasks without cross-project task flashes.
- `/` renders "My projects" (`WelcomeScreen`), and `/projects` renders `ProjectsPage` (project list, "Add project", and "Delete" with a typed-name confirmation dialog preserving today's unregister-only semantics).
- Old routes redirect cleanly: `/console/:projectId/:specId` and `/console/:projectId` redirect to `/p/:slug/console/:specId` and `/p/:slug/console`.
- Unauthenticated deep links record the target path in `sessionStorage` (`pf-return-to`) and return there once after login.
- The top bar (`ProjectTabBar.tsx`) replaces `ProjectSelector` and the `+` button with the vendored shared `ProjectBreadcrumb` (searchable switcher, "All projects…" link, no delete button). `ProjectSelector.tsx` is deleted, and `AppSettings.tsx` shows a read-only label for the active project.
- `PortalSwitcher` preserves `currentSlug` (`/p/<slug>`) when moving between Factory portals, and `CommandPalette` (`⌘K`) offers both the interactive `Switch project...` mode (`projects` + `onSwitchProject`) and `Switch project: <name>` commands.

## Affected users and systems

PFactory web UI users; `apps/frontend-web` and `.github/workflows/factory-ui-drift.yml` only. Shared `@factory/ui` components (`ProjectBreadcrumb.tsx`, `PortalSwitcher.tsx`, `CommandPalette.tsx`) are vendored byte-identical from `olafkfreund/Factory` `origin/main` (`5f3d231093d30346a584303011412156e8952aad`).

## Constraints

- Frontend only (`apps/frontend-web` + `.github/workflows/factory-ui-drift.yml`); no backend route changes (P3 enforcement PRs and phase 3 shared registry cutover run in parallel).
- Vendored `factory-ui` files (`CommandPalette.tsx`, `PortalSwitcher.tsx`, `ProjectBreadcrumb.tsx`) must be byte-identical to `Factory` `origin/main` so `check_factory_ui_drift.py` passes.
- All new user-facing strings in `ProjectsPage` and the project-not-found state must use `react-i18next` keys across `en`, `fr`, and `pt-BR`.
- Existing Vitest suites, `npm run typecheck`, and `npm run lint` (`--max-warnings 3004` ratchet) must stay green.

## Open questions

None. Owner pre-approved intent/spec/plan unseen for this programme (2026-11-21); as in AIFactory#2158, `/` renders `WelcomeScreen` ("My projects") without auto-redirecting to the last project.
