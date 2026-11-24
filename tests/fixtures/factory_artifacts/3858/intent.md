---
status: approved
issue: 3858
author: olafkfreund
---

# Intent: shared project breadcrumb, Switch-project command, slug-keeping portal switcher

## Problem

The portals pick the current project with a header dropdown (`ProjectSelector`) that also carries a one-click delete. The project is not in the URL, so cross-portal hops lose it. The shared chrome in `shared/factory-ui` has no project concept (epic #3857, phase 4).

## Proposed outcome

`shared/factory-ui` gains: a `ProjectBreadcrumb` (current project name, searchable switch list, no delete, no add); a "Switch project..." command in `CommandPalette`; and `PortalSwitcher` links that keep the current `/p/:slug` for the sibling portals the host says support it (others keep linking to their root). All three stay vendorable byte-identical, and the drift gate knows the new file.

## Affected users and systems

All portal users (header and Cmd-K). Maintainers of PFactory/AIFactory/TFactory vendored copies; `scripts/check_factory_ui_drift.py`. AIFactory (#2158) is the first consumer; PFactory/TFactory/CFactory follow in a later wave.

## Constraints

- Framework-light React + Tailwind on the shared HSL token contract; no router, store or i18n imports (the file is vendored into portals with different stacks).
- Existing props keep working: a host that passes nothing new renders exactly as before.
- Hub-canonical rule: land here first, then re-vendor and bump the pin.
- No backend, no portal code in this repo.

## Open questions

None. Decision (owner delegation 2026-11-19): the slug reaches `PortalSwitcher` as an explicit optional prop `project={{slug, portals}}` naming the destinations that have `/p/` routes, not read from `window.location`; other siblings keep linking to their root during rollout (Codex review finding 1).
