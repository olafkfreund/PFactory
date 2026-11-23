# PFactory Shell Inventory (#3876)

Inventory of every action the previous `ProjectTabBar`, `SortableProjectTab`, `Sidebar`, and `App` chrome offered, and where each lives in the adopted `@factory/ui` `AppShell`.

| Old component / area | Old action / element | New home in `AppShell` / routed views |
| --- | --- | --- |
| `ProjectTabBar` / `SortableProjectTab` | Select open project tab | `AppShell` → `ProjectSwitcher` combobox (`onSelectProject`, searchable with recent projects in `localStorage`) |
| `ProjectTabBar` / `SortableProjectTab` | Drag-reorder project tabs | **dropped by design: drag-reorder tabs** (`ProjectSwitcher` recents replace manual tab ordering) |
| `ProjectTabBar` / `SortableProjectTab` | Close project tab (`×` button, `⌘W` / `Ctrl+W`) | `ProjectSwitcher` recents (no open-tab strip to close) |
| `ProjectTabBar` | `⌘1–9` / `Ctrl+1–9` switch to project N | Bound in the `App.tsx` portal keyboard handler (`goProject(projects[index].id)`); N now follows the full project registry order, not the former ordered open tabs or switcher recents |
| `ProjectTabBar` | `⌘Tab` / `⌘Shift+Tab` cycle open tabs | `ProjectSwitcher` combobox arrow keys + recent projects list |
| `ProjectTabBar` / `SortableProjectTab` | Active tab Project Settings icon (`onSettingsClick`) | `AppShell` sidebar bottom-pinned `projectSettings` item → routed `/p/<slug>/settings` (`ProjectSettingsPage` with tabs General, Git, Memory, MCP) |
| `ProjectTabBar` | `PortalSwitcher` (`current="plan"`, `project` slug handoff) | `AppShell` topbar (`portal="plan"`, `portalProject`) |
| `ProjectTabBar` | `ProjectBreadcrumb` ("All projects" link) | `AppShell` → `ProjectSwitcher` footer action "All projects" (`onAllProjects` → `/projects`) and "Add project" (`onAddProject`) |
| `ProjectTabBar` | Provider / CLI status badges (`ClaudeCodeStatusBadge`, `CLIToolStatusBadge`, `OpenAIEndpointsStatusBadge`) | `AppShell` `topbarStatus` slot |
| `ProjectTabBar` | Fleet "Needs you" count | `AppShell` `needsYou` topbar button (`⚑ N` → Cockpit URL) |
| `ProjectTabBar` | Theme toggle button (Light / Dark) | `AppShell` topbar segmented theme switch (`Light`, `Dark`, `System`) |
| `ProjectTabBar` | Global Settings cog button (`onSettingsClick`) | `AppShell` user menu → `Preferences` (`/preferences`, `PreferencesPage`) and sidebar `Project Settings` (`/p/<slug>/settings`, `ProjectSettingsPage`) |
| `ProjectTabBar` | Help & docs button (`https://github.com/olafkfreund/PFactory#readme`) | `AppShell` user menu → `Help & docs` |
| `Sidebar` | Brand logo & `PFactory` wordmark | `AppShell` topbar `.f-brand` (`labels.brand`) |
| `Sidebar` | Project section header (`Project — <name>`) and project filesystem path | `AppShell` → `ProjectSwitcher` (`currentProject` name and `projectSubtitle={urlProject?.path}`) |
| `Sidebar` | Navigation items (`Plans`, `Planning`, `Files`, `MCP`, `Skills`, `Index & Memory`, plus provider-driven `GitHub Issues` / `GitLab Issues` / `ADO Work Items` and `GitHub PRs` / `GitLab MRs` / `ADO Pull Requests`) | `AppShell` `nav` prop via `usePFactoryShellAdapter`, translated with `t()`; `Plans` (`pfactory`, fixed from `Tests` per B9) stays enabled without a project and opens `/pfactory`, other project items are disabled; client-side navigation via `onNavigate` |
| `Sidebar` | `RateLimitIndicator` | `AppShell` `topbarStatus` slot |
| `Sidebar` | `+ Task` primary button | `AppShell` `primaryAction` (`label: "+ Task"`, `disabled`, `disabledReason`, `onClick`) |
| `Sidebar` | "Initialize PFactory to create tasks" button | `AppShell` `sidebarHeader` slot + Initialize dialog in `usePFactoryShellAdapter` |
| `Sidebar` | Auto-triggered Initialize dialog & `GitSetupModal` | Kept in `usePFactoryShellAdapter` (`dialogs`) |
| `Sidebar` | Log Out button + Logout confirmation dialog | `AppShell` user menu → `Log Out` triggers the Logout confirmation dialog in `usePFactoryShellAdapter` |
| `App` | `⌘K` / `Ctrl+K` Command Palette | `AppShell` topbar search button (`onSearch`) + `⌘K` / `Ctrl+K` handler in `App.tsx` opening `CommandPalette` |
| `App` | Mixed `AppSettingsDialog` modal (App + Project tabs) | Split into routed `/preferences` (`PreferencesPage`) and `/p/<slug>/settings` (`ProjectSettingsPage`); every `onSettingsClick` / `onOpenSettings` caller points to these routes |
| `PlanBoard` | `.rail-panel` stage rings (`Plans ready`, `Human Review`, `Done`) | Replaced by `AppShell` / `PipelineStrip` (`Backlog`, `Review`, `Approved` with stage counts, failed counts, stage filter, failure filter, and clear filter) |
| `PlanBoard` | Plan session cards & columns | `WorkColumn` + `WorkCard` (approved/emitted/discarded collapsed to 36px rows, single remembered expanded card per project, 8-collapsed-row cap with "Show N more", selection opening detail in the list pane, plan type, issue count, gates-failed `needs attention` cue, plus `Process` / `Re-run` / `Dismiss` actions) |
