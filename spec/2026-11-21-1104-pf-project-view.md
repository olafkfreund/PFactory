---
status: draft
issue: 1104
intent: intent/2026-11-21-1104-pf-project-view.md
---

# Spec: URL-scoped project view in the PFactory frontend

## Design

All frontend changes live under `apps/frontend-web/src` (plus `.github/workflows/factory-ui-drift.yml`).

1. **`lib/project-route.ts` (new, pure) & `shared/types/project.ts`.**
   - Add optional `slug?: string` to `Project` in `shared/types/project.ts:5-15`.
   - `PROJECT_VIEWS`: `['planning', 'pfactory', 'terminals', 'editor', 'context', 'github-issues', 'github-prs', 'changelog', 'insights', 'worktrees', 'agent-tools', 'skills', 'cloud', 'visual-reports'] as const` (matches `SidebarView` in `components/Sidebar.tsx:54`; default view is `'planning'`).
   - `slugify(name, fallback)`: NFKD normalized, lowercase, non-`[a-z0-9]+` collapsed to `-`, trimmed; returns `fallback` when empty.
   - `projectSlugs(projects)`: returns `Map<string, string>` (project id → canonical slug). Backend `project.slug` values are reserved first. Remaining projects are sorted oldest-first (`createdAt`, tie-break `id.localeCompare`) and assigned `base = slugify(p.name, p.id)`, colliding to `${base}-${p.id.slice(0, 8)}`, then `-2`, `-3`, … until unique.
   - `findProjectByRef(projects, ref)`: case-insensitive lookup with precedence: canonical slug (`projectSlugs`) > `project.id` > alias (`p.slug`, plain `slugify(p.name, p.id)`, or `${slugify(p.name, p.id)}-${p.id.slice(0, 8)}`).
   - `parseRoute(pathname)`: matches `/^\/p\/([^/]+)(?:\/([^/]+))?/` (`decodeURIComponent` on `ref`, view validated against `PROJECT_VIEWS` defaulting to `'planning'`), `/^\/projects\/?$/` → `{ kind: 'projects' }`, otherwise `{ kind: 'home' }`.
   - `projectPath(slug, view?)`: returns `/p/${encodeURIComponent(slug)}${view && view !== 'planning' ? `/${view}` : ''}`.

2. **Route mapping table.**

| Old route / state | New `/p/:slug` route | Redirect / resolution behaviour |
| --- | --- | --- |
| `/` with `selectedProject == null` (`WelcomeScreen`) | `/` (`{ kind: 'home' }`) | Renders `WelcomeScreen` ("My projects"); no auto-redirect from `/`. Clicking a project navigates to `/p/:slug`. |
| Project dropdown + delete in `ProjectSelector` | `/projects` (`{ kind: 'projects' }`) | Renders `ProjectsPage` (list, `+ Add project`, and typed-name confirm `Unregister project`). |
| `/*` with `activeView === 'planning'` | `/p/:slug` (or `/p/:slug/planning`) | Default project view; `/p/:slug/planning` normalizes to `/p/:slug` via `projectPath`. |
| `/*` with `activeView === '<view>'` (`pfactory`, `terminals`, `editor`, `context`, `github-issues`, `github-prs`, `changelog`, `insights`, `worktrees`, `agent-tools`, `skills`, `cloud`, `visual-reports`) | `/p/:slug/<view>` | `setActiveView(v)` navigates to `projectPath(slug, v)`. Unknown `<view>` falls back to `'planning'`. |
| `/p/:id[/:view]` or `/p/:alias[/:view]` | `/p/:slug[/:view]` | Resolved via `findProjectByRef`; effect in `AuthenticatedApp` rewrites URL in place (`navigate(..., { replace: true })`, preserving `search`). |
| `/p/:unknown[/:view]` | `/p/:unknown[/:view]` | Once `projectsLoaded` is true, renders not-found notice (`welcome:projectsPage.notFound`) + link to `/`. |
| `/console/:projectId/:specId` | `/p/:slug/console/:specId` | `<LegacyConsoleRedirect />` loads projects via `useEnsureProjects()`, resolves `projectId` → canonical `slug`, and `<Navigate replace />`s. |
| `/console/:projectId` | `/p/:slug/console` | `<LegacyConsoleRedirect />` redirects to `/p/:slug/console` (renders `ConsolePage`). |
| Unauthenticated visit to `/p/...`, `/projects`, `/console/...` | `/login` → original URL | `<RedirectToLogin />` stores `pathname + search` in `sessionStorage['pf-return-to']`; `AuthenticatedApp` consumes it once on `/` after login. |

3. **URL authority, view gating, and task-race guard (`App.tsx`, `stores/task-store.ts`).**
   - `App()` waits for the initial `checkAuth()` promise (`authChecked` state) before rendering `<Routes>` so cold deep links are not bounced to `/login` before auth state hydrates. Also drop the artificial 2-second `<LoadingScreen>` gate in `AuthenticatedApp` (or gate only when needed) so route tests and deep links mount deterministically, matching AIFactory's `App.tsx`.
   - `AuthenticatedApp` tracks `projectsLoaded` (set in `loadProjects().finally(...)`). `loadProjects()` in `stores/project-store.ts` stays unchanged: its saved `tab-state` and `lastSelectedProjectId` populate `openProjectIds`/`tabOrder`, while a sync effect in `AuthenticatedApp` enforces URL precedence whenever `projectsLoaded && urlProjectId` and `activeProjectId !== urlProjectId || selectedProjectId !== urlProjectId`: it calls `useTaskStore.getState().clearTasks()`, `openProjectTab(urlProjectId)`, and `selectProject(urlProjectId)`.
   - `inSync = !!urlProjectId && activeProjectId === urlProjectId && selectedProjectId === urlProjectId`; `selectedProject = inSync ? urlProject : undefined`. All project views (including `PlanningView` and `PFactoryPortal` when under `/p/:slug`) mount only when `selectedProject` is defined, preventing stale-project renders.
   - `TerminalGrid` mounts whenever `route.kind === 'project' && urlProject` (outside the `selectedProject` store-equality gate, taking `urlProject.path`) so switching between projects or views does not unmount active xterm/PTY instances; it unmounts on `/` and `/projects`.
   - In `stores/task-store.ts:478-497`, guard `loadTasks(projectId)` with a module-level counter (`let loadTasksSeq = 0; const seq = ++loadTasksSeq;`) and ignore responses/errors when `seq !== loadTasksSeq` so a slow fetch from project A never overwrites project B's tasks.
   - Closing the active tab (`onProjectClose`) navigates to the next open tab's `/p/:slug` (or `/` if none) before calling `closeProjectTab(projectId)`, so the URL sync effect does not re-open the closed tab.

4. **Top bar, shared `@factory/ui` components, settings, and console (`ProjectTabBar.tsx`, `AppSettings.tsx`, `ConsolePage.tsx`, `ProjectsPage.tsx`).**
   - Vendor `components/ProjectBreadcrumb.tsx`, and re-vendor `components/PortalSwitcher.tsx` and `components/CommandPalette.tsx` byte-identical from `Factory` `origin/main` (`5f3d231093d30346a584303011412156e8952aad`). Update `.github/workflows/factory-ui-drift.yml` (`HUB_PIN_SHA: "5f3d231093d30346a584303011412156e8952aad"` and add `ProjectBreadcrumb.tsx` to `paths`).
   - `ProjectTabBar.tsx`: replace `ProjectSelector` and the `+` button with `<ProjectBreadcrumb projects={breadcrumbProjects} currentSlug={currentSlug} onSelect={...} onViewAll={onViewAllProjects} />` and pass `currentSlug={currentSlug}` to `<PortalSwitcher current="plan" currentSlug={currentSlug} needsCount={needsCount} />`.
   - `CommandPalette` in `App.tsx`: pass `projects={breadcrumbProjects}` and `onSwitchProject={(slug) => void navigate(projectPath(slug, route.kind === 'project' ? route.view : undefined))}` (enabling the built-in `Switch project...` action in `CommandPalette.tsx`), plus per-project `Switch project: <name>` entries in `paletteCommands`.
   - `components/settings/AppSettings.tsx:51,172-174,312-317`: replace `<ProjectSelector>` with a read-only display of `selectedProject?.name` (and path), drop `handleProjectChange`, and delete `components/settings/ProjectSelector.tsx`.
   - `pages/ProjectsPage.tsx` (new) + `shared/i18n/locales/{en,fr,pt-BR}/welcome.json` (`projectsPage.*` keys, with `"PFactory"` in `unregisterBody`).
   - `pages/ConsolePage.tsx` + `hooks/useEnsureProjects.ts` (new): read `{ slug, specId }` (or `{ projectId, specId }`), call `useEnsureProjects()`, resolve project via `findProjectByRef(projects, slug)`, and link "Back to portal" to `/p/${slugOrRef}`.

## Alternatives rejected

- **Keep view selection purely in React state and sync URL one-way:** leaves two sources of truth and breaks back/forward and multi-tab isolation.
- **Auto-redirect `/` to `lastSelectedProjectId`:** hides the "My projects" landing screen and fights multi-tab navigation.
- **Separate `<Route>` tree per project view:** unmounts `TerminalGrid` on every view switch, killing live xterm/PTY sessions.
- **Keep `HUB_PIN_SHA` at `5477f12a...`:** `Factory#3859` (`5f3d2310...`) is already merged on `Factory` `origin/main` with `ProjectBreadcrumb`, slug-preserving `PortalSwitcher`, and `CommandPalette` project mode; bumping the pin now gives full feature parity and exercises the drift gate immediately.

## Risks

- **Stale tasks during rapid project switch:** mitigated by synchronous `clearTasks()` in the URL sync effect plus the `loadTasksSeq` counter in `task-store.ts`.
- **Closing the active tab while its URL is active:** mitigated by navigating away before `closeProjectTab`.
- **Cold load of `/p/:slug/console/:specId` or `/login` redirect:** mitigated by `authChecked` gate in `App()` and `useEnsureProjects()` in console/redirect routes.

## Verification

- **Vitest (`cd apps/frontend-web && npx vitest run`):** `lib/__tests__/project-route.test.ts`, `components/__tests__/ProjectBreadcrumb.test.tsx`, `pages/__tests__/ProjectsPage.test.tsx`, `__tests__/project-routing.test.tsx` (URL beats saved tab state, id/alias canonical rewrite, unknown slug not-found, back/forward, two projects' tasks never mix + superseded `loadTasks` dropped, `TerminalGrid` stays mounted across project switch, legacy `/console/...` redirect, `pf-return-to` login return), and updated `components/__tests__/ProjectTabBar.test.tsx`.
- **Static & drift gates:** `npm run typecheck`, `npm run lint`, and `python3 /mnt/code/Source-home/GitHub/Factory/scripts/check_factory_ui_drift.py --canonical /mnt/code/Source-home/GitHub/Factory/shared/factory-ui --service pfactory --root .`.
