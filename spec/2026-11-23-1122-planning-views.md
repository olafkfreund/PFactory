---
status: approved
issue: 1122
intent: intent/2026-11-23-1122-planning-views.md
---

# Spec

- **Views:** List and Board render the same plan collection through `WorkCard`. List is a single `WorkColumn`; Board has three lanes. They share the filters and the strip.
- **Detail:** selecting a plan opens its detail in place. List uses a split pane and Board an expanded card. The existing session detail component is reused.
- **Plan sources:** the Registry, Templates and Providers panel moves unchanged into a **Plan sources** tab on the Project Settings route (`/p/<slug>/settings/plan-sources`). Deep links work.
- **Empty state:** one line plus one action.

## Verification
- typecheck, test, lint and build;
- tests for both views over one data set;
- a settings deep link;
- browser checks at 1280px and 390px.
