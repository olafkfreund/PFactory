---
status: approved
issue: 3858
spec: spec/2026-11-19-3858-factory-ui-breadcrumb.md
coders:
  2: codex   # contained edit to two existing files with small tricky logic (mode switch, href building)
reviewers:
  - codex    # read-only review of each draft and of the final diff (agy is off by owner decision)
---

# Plan: shared project breadcrumb, Switch-project command, slug-keeping portal switcher

Approved spec decisions (carried over): framework-neutral React+Tailwind files in `shared/factory-ui`; `ProjectBreadcrumb` (searchable switch list, no delete/add, optional "All projects"); `CommandPalette` optional `projects`+`onSwitchProject` adds "Switch project..." that keeps the palette open and lists projects; `PortalSwitcher` optional `project={{slug, portals}}` links only the named siblings to `/p/<slug>`; drift gate lists the new file with a `NOT_YET_PINNED` tolerance; no behaviour change for hosts that pass nothing new.

## Steps

1. `shared/factory-ui/ProjectBreadcrumb.tsx` (new): component per spec item 1; header comment like the siblings; `useState` for open/query/highlight, `useRef` + document `mousedown` for outside click. → verify by `tsc --noEmit` and the throwaway vitest in step 4.
   Traps: no imports beyond `react`; no lucide/radix; no `any`.
2. (codex) `shared/factory-ui/CommandPalette.tsx` and `PortalSwitcher.tsx`: add the optional props per spec items 2-3, preserving every existing behaviour. → verify by the throwaway vitest in step 4 and a diff that touches only those two files.
   Traps: do not rename existing props; `PaletteCommand.group` union unchanged; new project rows use group `'Projects'` as a plain string `Row.group` (already `string`).
3. `scripts/check_factory_ui_drift.py`: add `ProjectBreadcrumb.tsx` to `CANONICAL_MODULES` (layouts follow by comprehension); add `NOT_YET_PINNED = ("ProjectBreadcrumb.tsx",)`; in `check_service`, a layout module whose canonical file is absent and is in `NOT_YET_PINNED` is skipped instead of "canonical missing". Extend the self-test and the python test for the script. Update `shared/factory-ui/README.md` (components table, who vendors). → verify by `python scripts/check_factory_ui_drift.py --self-test` and `pytest -q` on the script's test file.
   Traps: `_missing_from_contract` globs `*.tsx`, so the file must be in the tuple in the same commit.
4. Throwaway check (not committed): copy the three hub files into the AIFactory worktree `apps/frontend-web/src/components/`, write a vitest for breadcrumb filter/select/escape, palette switch-project mode and switcher hrefs, run `npx vitest run` and `npx tsc --noEmit`; before copying, save the worktree's original `CommandPalette.tsx` and `PortalSwitcher.tsx` bytes to the scratchpad and restore them afterwards (`git checkout -- <file>` in that worktree); only `ProjectBreadcrumb.tsx` stays (AIFactory plan step 2 keeps it with its test).

## Tests

`python scripts/check_factory_ui_drift.py --self-test` -> OK; the script's pytest file -> pass; step 4 vitest -> pass; each fails against the pre-change files (new props absent).

## Rollback

Revert the PR; the new file is unreferenced by any portal until adopted, and the checker tolerance disappears with it.
