---
status: approved
issue: 1104
spec: spec/2026-11-21-1104-pf-project-view.md
coders:
  1: codex   # deterministic slugify, collision-safe projectSlugs, findProjectByRef precedence, and parseRoute
  2: agy     # mechanical byte-identical vendoring of @factory/ui components, drift workflow pin bump, and breadcrumb tests
  3: codex   # URL-precedence sync effect, authChecked/inSync view gating, and stale loadTasks request guard
  4: agy     # mechanical route wiring in App.tsx, ConsolePage/useEnsureProjects, ProjectsPage, ProjectTabBar, AppSettings, and i18n
  5: codex   # project-routing integration tests covering URL precedence, task isolation, terminal lifetime, redirects, and login return
reviewers:
  - codex    # independent review of slug precedence, URL/store sync, and test coverage
  - agy      # second vendor review of route wiring, i18n parity, and factory-ui drift compliance
---

# Plan: URL-scoped project view in the PFactory frontend

Self-contained summary of approved decisions:
- Every project view lives under `/p/:slug[/:view]` (default view `planning`, matching `SidebarView`). The URL is authoritative; saved tab state (`tab-state.json`, `lastSelectedProjectId`) only populates open tabs.
- Pure `lib/project-route.ts` provides `slugify`, `projectSlugs` (backend `project.slug` reserved first, derived slugs assigned oldest-first by `createdAt` then `id`, collision suffix `-<id.slice(0,8)>` then `-2`, `-3`…), `findProjectByRef` (canonical slug > `id` > alias: backend slug, plain derived slug, or 8-char collision-suffixed slug), `parseRoute`, and `projectPath`.
- `AuthenticatedApp` syncs store to URL (`clearTasks()`, `openProjectTab`, `selectProject`), gates views on `inSync` (`activeProjectId === urlProject.id && selectedProjectId === urlProject.id`), keeps `TerminalGrid` mounted across `/p/:slug` switches outside the `inSync` gate, rewrites non-canonical project refs via `replace: true`, and consumes `sessionStorage['pf-return-to']` after login.
- `/` renders `WelcomeScreen`; `/projects` renders `ProjectsPage` (add via `AddProjectModal`, unregister with typed-name confirm calling `removeProject(id)`).
- Legacy `/console/:projectId[/:specId]` redirects to `/p/:slug/console[/:specId]` via `useEnsureProjects()`.
- Top bar (`ProjectTabBar.tsx`) replaces `ProjectSelector` and `+` with vendored `ProjectBreadcrumb`, passes `currentSlug` to `PortalSwitcher`, and deletes `ProjectSelector.tsx` (`AppSettings.tsx` renders a read-only project name/path label).
- `ProjectBreadcrumb.tsx`, `PortalSwitcher.tsx`, and `CommandPalette.tsx` are vendored byte-identical from `Factory` `origin/main` (`5f3d231093d30346a584303011412156e8952aad`), and `.github/workflows/factory-ui-drift.yml` bumps `HUB_PIN_SHA` to `5f3d231093d30346a584303011412156e8952aad`.

All frontend paths below are relative to `apps/frontend-web/`.

## Steps

1. **(codex) Pure project-route helpers and `Project.slug` type.**
   - `src/shared/types/project.ts:5-15`: add `slug?: string;` to `export interface Project`.
   - `src/lib/project-route.ts` (new, ~85 lines): port from AIFactory `origin/dev:apps/frontend-web/src/lib/project-route.ts`, adapting `PROJECT_VIEWS` to PFactory's `SidebarView` union from `src/components/Sidebar.tsx:54` (`'planning'`, `'pfactory'`, `'terminals'`, `'editor'`, `'context'`, `'github-issues'`, `'github-prs'`, `'changelog'`, `'insights'`, `'worktrees'`, `'agent-tools'`, `'skills'`, `'cloud'`, `'visual-reports'`) with default view `'planning'` in `parseRoute` and `projectPath`.
   - `src/lib/__tests__/project-route.test.ts` (new, ~80 lines): unit tests for `slugify`, `projectSlugs` (oldest keeps plain slug, adversarial collision uniqueness, backend slug reservation), `findProjectByRef` (canonical, id, plain/suffixed alias), `parseRoute` (including `/p/:slug`, `/p/:slug/editor`, unknown view → `'planning'`, `/projects`, `/`), and `projectPath`.
   - → verify by: `(cd apps/frontend-web && npx vitest run src/lib/__tests__/project-route.test.ts && npm run typecheck)`
   - Traps: `PROJECT_VIEWS` default in PFactory is `'planning'` (not AIFactory's `'kanban'`); do not import Zustand stores or router hooks into `project-route.ts`.

2. **(agy) Vendor `@factory/ui` components byte-identical and update drift workflow.**
   - Copy byte-for-byte from `/mnt/code/Source-home/GitHub/Factory` at `5f3d231093d30346a584303011412156e8952aad`:
     - `git -C /mnt/code/Source-home/GitHub/Factory show 5f3d231093d30346a584303011412156e8952aad:shared/factory-ui/ProjectBreadcrumb.tsx` → `src/components/ProjectBreadcrumb.tsx` (new, 152 lines).
     - `git -C /mnt/code/Source-home/GitHub/Factory show 5f3d231093d30346a584303011412156e8952aad:shared/factory-ui/PortalSwitcher.tsx` → `src/components/PortalSwitcher.tsx:1-78` (adds `currentSlug?: string` prop and `/p/${encodeURIComponent(currentSlug)}` link suffix).
     - `git -C /mnt/code/Source-home/GitHub/Factory show 5f3d231093d30346a584303011412156e8952aad:shared/factory-ui/CommandPalette.tsx` → `src/components/CommandPalette.tsx:1-213` (adds `projects?: PaletteProject[]`, `onSwitchProject?: (slug: string) => void`, and two-step `Switch project...` mode).
   - `src/components/__tests__/ProjectBreadcrumb.test.tsx` (new, ~56 lines): port from AIFactory `origin/dev:apps/frontend-web/src/components/__tests__/ProjectBreadcrumb.test.tsx`.
   - `.github/workflows/factory-ui-drift.yml:7-9,22-24,38`: document `ProjectBreadcrumb.tsx` in header comment, add `"apps/frontend-web/src/components/ProjectBreadcrumb.tsx"` to `on.pull_request.paths`, and set `HUB_PIN_SHA: "5f3d231093d30346a584303011412156e8952aad"`.
   - → verify by: `python3 /mnt/code/Source-home/GitHub/Factory/scripts/check_factory_ui_drift.py --canonical /mnt/code/Source-home/GitHub/Factory/shared/factory-ui --service pfactory --root . && (cd apps/frontend-web && npx vitest run src/components/__tests__/ProjectBreadcrumb.test.tsx)`
   - Traps: never format or edit the three vendored files locally — any single-byte difference fails `check_factory_ui_drift.py`.

3. **(codex) Stale task-load guard and URL-precedence / store-sync logic in `App.tsx`.**
   - `src/stores/task-store.ts:478-497`: add a module-level `let loadTasksRequestId = 0;` around `loadTasks(projectId)`. Capture `const requestId = ++loadTasksRequestId;` before `await window.API.getTasks(projectId)` and return early in `try`/`catch`/`finally` if `requestId !== loadTasksRequestId`.
   - `src/hooks/useEnsureProjects.ts` (new, 20 lines): port from AIFactory `origin/dev:apps/frontend-web/src/hooks/useEnsureProjects.ts` for routes outside `AuthenticatedApp`.
   - `src/App.tsx:45-237,545-572`:
     - Add `const RETURN_TO_KEY = 'pf-return-to';`, `<RedirectToLogin />` (saves non-`/` and non-`/login` `pathname + search` to `sessionStorage.setItem(RETURN_TO_KEY, ...)` and renders `<Navigate to="/login" replace />`), and `<LegacyConsoleRedirect />` (uses `useEnsureProjects()`, resolves `projectId` via `findProjectByRef`, redirects to `/p/${encodeURIComponent(slug)}/console${specId ? `/${specId}` : ''}`).
     - In `AuthenticatedApp`: remove the 2-second `<LoadingScreen>` state (`lines 47-51, 339-342`); parse `route = useMemo(() => parseRoute(pathname), [pathname])`; track `projectsLoaded` in `loadProjects().finally(() => setProjectsLoaded(true))` (`lines 182-185`).
     - Compute `slugs = useMemo(() => projectSlugs(projects), [projects])`, `urlProject = route.kind === 'project' ? findProjectByRef(projects, route.ref) : undefined`, `urlSlug = urlProject ? slugs.get(urlProject.id) : undefined`, `activeView: SidebarView = route.kind === 'project' ? route.view : 'planning'`, `urlProjectId = urlProject?.id`, `inSync = !!urlProjectId && activeProjectId === urlProjectId && selectedProjectId === urlProjectId`, `selectedProject = inSync ? urlProject : undefined`.
     - Add the URL→store sync effect (`if (!projectsLoaded || !urlProjectId) return; ... clearTasks(); openProjectTab(urlProjectId); selectProject(urlProjectId);`), the canonical slug rewrite effect (`if (route.kind === 'project' && urlSlug && route.ref !== urlSlug) void navigate(projectPath(urlSlug, route.view) + search, { replace: true });`), and the `RETURN_TO_KEY` consumption effect at `/`.
     - In `App()` (`lines 545-572`): gate `<Routes>` on `authChecked` (set in `checkAuth().finally(...)`), add `<Route path="/p/:slug/console/:specId" ... />`, `<Route path="/p/:slug/console" ... />`, `<Route path="/console/:projectId/:specId" element={isAuthenticated ? <LegacyConsoleRedirect /> : <RedirectToLogin />} />`, `<Route path="/console/:projectId" element={isAuthenticated ? <LegacyConsoleRedirect /> : <RedirectToLogin />} />`, and replace `<Navigate to="/login" replace />` on `/*` with `<RedirectToLogin />`.
   - → verify by: `(cd apps/frontend-web && npm run typecheck)`
   - Traps: do not modify `loadProjects()` in `src/stores/project-store.ts`; keep `/*` as a single `<Route>` so `TerminalGrid` is not remounted on view changes.

4. **(agy) Mechanical view gating, `ProjectsPage`, `ConsolePage`, `ProjectTabBar`, `AppSettings`, and i18n.**
   - `src/App.tsx:124-150,321-542`:
     - Update `paletteCommands` to include `Switch project: ${p.name}` commands and pass `projects={ useMemo(() => projects.map((p) => ({ slug: slugs.get(p.id) ?? p.id, name: p.name, path: p.path })), [projects, slugs]) }` and `onSwitchProject={(slug) => { void navigate(projectPath(slug, route.kind === 'project' ? route.view : undefined)); }}` to `<CommandPalette>` (`lines 485-493`).
     - Update `handleProjectAdded` (`lines 321-324`) to navigate to the new project (`goProject(project.id)`).
     - Update `<ProjectTabBar>` props (`lines 364-377`): `onProjectSelect={(id) => goProject(id, route.kind === 'project' ? route.view : undefined)}`, `onProjectClose` (if `urlProjectId === projectId`, navigate to next open project or `/` before `closeProjectTab(projectId)`), `currentSlug={urlSlug}`, `onViewAllProjects={() => void navigate('/projects')}`.
     - Update `<main>` (`lines 379-458`): render `TerminalGrid` whenever `route.kind === 'project' && urlProject` (using `urlProject.path`); render the not-found block (`welcome:projectsPage.notFound` + `<Link to="/">`) when `route.kind === 'project' && !urlProject && projectsLoaded`; gate all project views (`planning` → `<PlanningView>`, `pfactory` → `<PFactoryPortal>`, `editor`, `worktrees`, `context`, `github-issues`, `github-prs`, `changelog`, `insights`, `agent-tools`, `skills`, `cloud`, `visual-reports`) under `{selectedProject && (...)}`; render `<ProjectsPage>` when `route.kind === 'projects'`; render `<WelcomeScreen>` when `route.kind === 'home'` with `onSelectProject={(id) => goProject(id)}`.
   - `src/pages/ProjectsPage.tsx` (new, ~101 lines) & `src/pages/__tests__/ProjectsPage.test.tsx` (new, ~45 lines): port from AIFactory `origin/dev:apps/frontend-web/src/pages/ProjectsPage.tsx` and its test (typed-name confirmation dialog calling `removeProject(deleting.id)`).
   - `src/shared/i18n/locales/{en,fr,pt-BR}/welcome.json:1-16`: add `projectsPage` keys (`title`, `add`, `empty`, `open`, `delete`, `unregisterTitle`, `unregisterBody` naming `"PFactory"`, `nameLabel`, `cancel`, `confirm`, `notFound`, `backHome`).
   - `src/pages/ConsolePage.tsx:1-70`: read `{ slug, specId } = useParams<{ slug: string; specId?: string }>()`, call `useEnsureProjects()`, resolve project via `findProjectByRef(projects, slug)`, and link "Back to portal" to `project ? `/p/${slugOrRef}` : '/'`.
   - `src/components/ProjectTabBar.tsx:1-45,160-187`: replace `ProjectSelector` and the `+` `<Button>` with `<ProjectBreadcrumb projects={breadcrumbProjects} currentSlug={currentSlug} onSelect={(slug) => { const p = allProjects.find((x) => slugs.get(x.id) === slug); if (p) onProjectSelect(p.id); }} onViewAll={onViewAllProjects} />`, pass `currentSlug={currentSlug}` to `<PortalSwitcher current="plan" currentSlug={currentSlug} needsCount={needsCount} />`, and update `src/components/__tests__/ProjectTabBar.test.tsx` if needed.
   - `src/components/settings/AppSettings.tsx:51,172-174,311-317`: remove `ProjectSelector` import and `handleProjectChange`; render a read-only block showing `selectedProject ? selectedProject.name : t('noProjectSelected', 'No project selected')` (and `selectedProject.path`); delete `src/components/settings/ProjectSelector.tsx`.
   - → verify by: `(cd apps/frontend-web && npx vitest run src/pages/__tests__/ProjectsPage.test.tsx src/components/__tests__/ProjectTabBar.test.tsx && npm run typecheck && npm run lint)`
   - Traps: `eslint` enforces `--max-warnings 3004`; avoid unused imports (`Plus`, `onAddProject`, `onProjectAdded` in `ProjectTabBar.tsx`) and use `void navigate(...)` to satisfy `@typescript-eslint/no-floating-promises`.

5. **(codex) End-to-end routing integration tests (`project-routing.test.tsx`).**
   - `src/__tests__/project-routing.test.tsx` (new, ~195 lines): port from AIFactory `origin/dev:apps/frontend-web/src/__tests__/project-routing.test.tsx`, stubbing PFactory's view children (`PlanningView`, `PFactoryPortal`, `TerminalGrid`, `CloudAssessmentPage`, `VisualReportsPage`, etc.) and testing with `MemoryRouter` + real `useProjectStore` and `useTaskStore`:
     1. `/p/beta` overrides saved `activeProjectId: 'id-alpha'` and `localStorage.lastSelectedProjectId = 'id-alpha'`.
     2. `/p/id-beta/insights` resolves by id and rewrites URL to `/p/beta/insights`.
     3. `/p/no-such-project` shows not-found message and link to `/`.
     4. Back/forward navigation between `/p/alpha` and `/p/beta/insights` updates active project and view, never mixes tasks between projects, drops a slow superseded `getTasks('id-alpha')` response after switching to `/p/beta`, and mounts `TerminalGrid` only once across project switches (unmounting on `/projects`).
     5. Two independent renders at `/p/alpha` and `/p/beta` resolve their own project.
     6. Legacy `/console/id-beta/001-spec` redirects to `/p/beta/console/001-spec`.
     7. Unauthenticated visit to `/p/beta/insights` redirects to `/login` and returns to `/p/beta/insights` once authenticated.
   - → verify by: `(cd apps/frontend-web && npx vitest run src/__tests__/project-routing.test.tsx)`
   - Traps: stub `PlanningView` to read `useTaskStore.getState().tasks` so the cross-project task-isolation assertion tests the real store gating.

## Tests

Run from the worktree root:
```bash
(cd apps/frontend-web && npx vitest run && npm run typecheck && npm run lint) && \
python3 /mnt/code/Source-home/GitHub/Factory/scripts/check_factory_ui_drift.py \
  --canonical /mnt/code/Source-home/GitHub/Factory/shared/factory-ui \
  --service pfactory \
  --root .
```
Expected result: all Vitest suites pass, `tsc --noEmit` exits 0, `eslint` exits 0 within the `--max-warnings 3004` budget, and `check_factory_ui_drift.py` reports byte-exact match for `CommandPalette.tsx`, `PortalSwitcher.tsx`, and `ProjectBreadcrumb.tsx`.

## Rollback

Revert the PR. Because this change is strictly frontend-only (`apps/frontend-web` and `.github/workflows/factory-ui-drift.yml`) and leaves backend endpoints, `projects.json`, and `tab-state.json` untouched, reverting immediately restores `ProjectSelector` and the legacy route handling.
