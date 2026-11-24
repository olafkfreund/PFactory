---
status: approved
issue: 1122
spec: spec/2026-11-23-1122-planning-views.md
coders:
  1: agy   # view and settings restructuring, mostly moving components
reviewers:
  - codex
---

# Plan

1. Implement the spec in `apps/frontend-web`.
   - **Verify:** typecheck, test, lint (0 errors), build, and `hsl(var(--` count 0.
   - **Traps:**
     - never edit vendored `src/components/factory-ui/*` or the shared CSS;
     - keep every Configuration capability reachable;
     - client-side navigation only.
