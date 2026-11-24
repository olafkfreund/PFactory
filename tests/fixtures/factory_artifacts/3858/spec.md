---
status: approved
issue: 3858
intent: intent/2026-11-19-3858-factory-ui-breadcrumb.md
---

# Spec: shared project breadcrumb, Switch-project command, slug-keeping portal switcher

## Design

All in `shared/factory-ui`, plain React + Tailwind on the HSL token contract, no router/store/i18n imports.

1. **`ProjectBreadcrumb.tsx` (new).** Props: `projects: {slug, name}[]`, `currentSlug?`, `onSelect(slug)`, `onViewAll?()`. Renders a button (`aria-haspopup="listbox"`, `aria-expanded`) showing the current project name (or "Select project"). Open: a panel with a search input (case-insensitive substring on name and slug) and a `role="listbox"` of `role="option"` rows, the current one `aria-selected`. Up/Down move the highlight, Enter selects, Escape or outside click closes; an optional "All projects" row calls `onViewAll`. No delete, no add: those live on the host's Projects page.
2. **`CommandPalette.tsx`.** New optional props `projects?: {slug, name}[]` and `onSwitchProject?(slug)`. When both are given the Actions group gains "Switch project...", which keeps the palette open and turns the list into the project list (filtered by the query); Enter/click calls `onSwitchProject` and closes. Without the props nothing changes.
3. **`PortalSwitcher.tsx`.** New optional prop `project?: {slug: string; portals: FactoryPortal[]}`. A sibling chip whose key is in `portals` links to `<url>/p/<encoded slug>`; all others keep linking to their root. Without the prop nothing changes. (Explicit prop, not `window.location`: siblings without `/p/` routes are not linked into 404s during rollout.)
4. **Drift gate.** `scripts/check_factory_ui_drift.py` lists `ProjectBreadcrumb.tsx` in `CANONICAL_MODULES` and the three layouts. A module that is absent from the *pinned* canonical is tolerated only if named in a new `NOT_YET_PINNED` tuple, so portals that pin an older hub SHA keep passing until they re-vendor and bump the pin; once the pinned canonical has the file, the vendored copy is required and byte-checked as before. README documents the three components.

## Alternatives rejected

- Slug read from `window.location` inside `PortalSwitcher`: hidden input, breaks siblings without `/p/` routes, harder to test.
- A router-aware breadcrumb (`Link`): the file is vendored into portals with different routers; callbacks keep it neutral.
- Listing the new file in every layout with no tolerance: breaks every portal's drift gate the moment this lands.
- Radix/shadcn popover inside the shared file: not every portal has the same ui kit.

## Risks

- Vendored copies drift if edited locally: the gate (byte-exact) covers it.
- `NOT_YET_PINNED` could mask a deleted canonical file: it names exactly one file and is removed when all portals have adopted.
- No JS test runner exists in this repo; the components are exercised by the AIFactory vitest suite (breadcrumb test is vendored with the consumer) and by throwaway vitest runs against the hub files before merge.

## Verification

`python scripts/check_factory_ui_drift.py --self-test` and the repo's python tests for the script (added case: module absent from pinned canonical tolerated only when in `NOT_YET_PINNED`); `tsc --noEmit` and vitest component tests run in the AIFactory worktree against byte-copies of the hub files; Codex review of the diff.
