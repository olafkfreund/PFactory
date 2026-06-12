# Factory Design System

> The shared brand & UI rules for **every** Factory service — PFactory, AIFactory,
> TFactory, and the CFactory cockpit. Follow these so the suite looks like one
> product, not four. CFactory is the reference implementation; when in doubt, match
> what ships in `CFactory/apps/frontend-web/src/index.css`.

The goal: each service is instantly recognizable as part of the Factory family,
while the *cockpit* (CFactory) remains the place where all three converge. One
palette, one type system, one set of component shapes, one motion language.

---

## 1. Principles

1. **Tell the truth about now.** Never show a number the data doesn't support — no
   "100%" when work failed, no fabricated percentages, no lifetime totals dressed as
   live state. A status the operator can't trust is worse than no status.
2. **Terminal-cockpit voice.** This is operator tooling, not marketing. Monospace is
   the dominant UI voice; data is first-class; chrome is quiet.
3. **Calm until it isn't.** Idle UI is still and low-contrast. Trouble (failures,
   anomalies) is loud and unmissable. Don't spend attention on decoration; spend it
   on state changes.
4. **One accent, sharp stage colors.** A single chrome accent (aqua) carries
   interactivity; the three stage colors mean exactly one thing each; red means alarm.
5. **Accessible by default.** Every animation honors `prefers-reduced-motion`; dialogs
   trap focus and announce themselves; nothing relies on color alone; text floors at
   0.7rem.

---

## 2. Color tokens (gruvbox)

Copy this `:root` block verbatim into every service's stylesheet — it is the shared
foundation. Reference **tokens**, never raw hex, in component CSS.

```css
:root {
  /* Surfaces */
  --bg: #282828;          /* page background (bg0) */
  --sidebar: #1d2021;     /* sidebar / deepest chrome (bg0_h) */
  --panel: #32302f;       /* cards & panels (bg0_s) */
  --panel-2: #3c3836;     /* raised / hover surface (bg1) */
  --border: #504945;      /* card & control borders (bg2) */
  --border-soft: #3c3836; /* hairline dividers (bg1) */

  /* Text */
  --text: #ebdbb2;        /* primary text (fg1) */
  --muted: #a89984;       /* secondary text / labels (fg4) */
  --faint: #7c6f64;       /* tertiary / disabled (gray) */

  /* Accent — the ONE chrome accent. Interactivity only. */
  --cyan: #83a598;        /* nav active, focus, links, primary actions */
  --brand: linear-gradient(135deg, #83a598, #8ec07c);

  /* Stage identity — reserved; never use these for chrome */
  --plan: #d3869b;        /* PFactory  (purple) */
  --code: #fabd2f;        /* AIFactory (yellow) */
  --test: #b8bb26;        /* TFactory  (green)  */

  /* Semantic */
  --green: #b8bb26;       /* success */
  --amber: #fabd2f;       /* warning */
  --yellow: #fabd2f;      /* alias of --amber */
  --red: #fb4934;         /* failure / alarm */
  --violet: #d3869b;      /* = --plan; kept for legacy */
}
```

**Rules**
- **Aqua (`--cyan`) is the only chrome accent** — nav-active, focus rings, links,
  primary buttons, hovers. Do **not** use violet/amber/green for chrome; they carry
  stage meaning and using them elsewhere is semantic noise.
- **Stage colors are a contract** across all services: plan = purple, code = yellow,
  test = green. A pipeline node, a column header, a chip, and a timeline label for
  the same stage must use the same token everywhere.
- **Red is for alarms only.** Failed tasks, high-severity anomalies, destructive
  actions. Never decorative.
- Tints come from the token, not a new literal:
  `background: color-mix(in srgb, var(--cyan) 14%, transparent)`. Never paste a raw
  `rgba()` from another palette — that's how themes drift (CFactory carried a ghost
  indigo/teal palette for months before it was purged; don't reintroduce one).

---

## 3. Typography

Three faces, three jobs. Loaded via one Google Fonts `@import`:

```css
@import url("https://fonts.googleapis.com/css2?family=Archivo:wght@600;700;800;900&family=Hanken+Grotesk:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600;700&display=swap");

:root {
  --font-display: "Archivo", system-ui, sans-serif;   /* H1s + wordmark ONLY */
  --font-mono: "JetBrains Mono", ui-monospace, monospace; /* dominant chrome + data */
  --font-ui: "Hanken Grotesk", system-ui, sans-serif; /* prose / long copy */
}
```

| Role | Face | Notes |
|---|---|---|
| Wordmark, page H1 | **Archivo** 800–900, expanded, **UPPERCASE**, tracked | The only place the display face appears. ≥2rem for H1. |
| Nav, tabs, chips, labels, buttons, table text, stats, numbers | **JetBrains Mono** | The cockpit's voice. Uppercase + letter-spacing for labels. |
| Paragraphs, descriptions, copilot prose, docs | **Hanken Grotesk** | Only where there's real running text. |

**Rules**
- **No generic sans as the lead.** Inter/Roboto/Arial/system-ui as a *primary* face
  is forbidden — that's the "AI slop" look the suite exists to avoid.
- **Minimum text size is 0.7rem (≈11px).** Below that, use weight and color for
  hierarchy, not smaller type. (Always-on, second-monitor glanceability.)
- Labels: mono, `text-transform: uppercase`, `letter-spacing: 0.04–0.08em`,
  `color: var(--muted)` or `--faint`.

---

## 4. Spacing, radius, elevation

- **Spacing:** rem-based scale — `0.3 / 0.5 / 0.7 / 1 / 1.4 / 2rem`. Don't mix px and
  rem dialects in the same surface.
- **Radius:** three steps only — `8px` (controls/chips), `12px` (cards/panels),
  `50%` (dots / FAB). Pick one per element class and stick to it.
- **Borders:** `1px solid var(--border)` for cards/controls; `1px solid
  var(--border-soft)` for internal dividers.
- **Elevation:** flat by default. Shadows only for things that float above the page
  (modals, popovers, the Copilot FAB): `0 16px 48px rgba(0,0,0,0.5)`.

---

## 5. Components

Shared shapes. Build these the same way in every service.

- **Cards / panels** — `var(--panel)`, 12px radius, 1px `--border`. Hover (if
  interactive) raises border to `--cyan` and lifts `translateY(-1px)` — **never** a
  stage color on hover.
- **Chips / pills** — mono, uppercase, 8px radius, tinted from the relevant token via
  `color-mix`. State chips (running / review / queued / failed) and stage chips
  (plan / code / test) share this shape.
- **Buttons** — primary = aqua fill, dark text; ghost = transparent + `--muted`;
  **destructive** = red outline/text. Disabled controls explain *why* (tooltip or
  adjacent subtext), and stay focusable so the reason is reachable.
- **Sidebar nav** — `--sidebar` background, mono items, active item = faint aqua tint
  + a 2px aqua left-edge bar + aqua icon. One active accent, nothing else.
- **The PARR pipeline strip** — the three glyph nodes (document = plan, robot = code,
  flask = test) joined by marching-dash connectors is the **family signature**. Any
  service that visualizes the pipeline uses these glyphs and the stage colors. In the
  cockpit it's a persistent strip under the topbar; elsewhere it can be a compact
  inline motif. Don't invent alternate pipeline iconography.
- **Stat / KPI cards** — big mono number, uppercase `--muted` label beneath. Show an
  em-dash and an "awaiting…" sub-label when data isn't instrumented yet — never a
  fake zero.
- **Empty states** — one honest muted line ("No active work items", "Live agents are
  off"), never a full-height blank or a spinner that never resolves. Collapse empty
  sections to a single row; promote sections that have signal.
- **Modals / dialogs** — `role="dialog"`, `aria-modal`, focus trap, Escape to close.
  Do **not** dismiss on overlay click if the user has typed into a field
  (destructive-input guard). Small × top-right.
- **Floating assistant (Copilot)** — a fixed aqua **robot FAB** bottom-right toggles a
  compact chat popup (chat thread + bottom-pinned input); it is not a nav destination.
  Other surfaces (toasts) lift above the FAB so they never overlap.
- **Toasts / alerts** — bottom-right stack, color-coded by kind, capped. **Failure
  toasts persist** until dismissed; informational ones auto-clear (~7s).

---

## 6. Iconography

- Thin line icons, consistent stroke weight, `currentColor` (so they inherit text/
  accent color). No filled/duotone mixing.
- The three **factory glyphs are identity, not decoration**: document = PFactory/plan,
  robot = AIFactory/code, flask = TFactory/test. Reuse the same glyph for a service
  wherever it appears (pipeline node, live-agent tile, nav).

---

## 7. Motion

- **Idle = subtle.** Slow, low-contrast (marching connector dashes, a gentle live
  pulse). It should read as "alive", never demand attention. Activity-gate it where
  possible (animate the connector only when work is flowing).
- **Transitions = quick.** 0.15–0.18s ease for hovers, popovers, view changes.
- **Alarms = loud.** A failure flares the affected pipeline node red, pins the event,
  and runs a one-shot red border-sweep across the frame. This is where motion budget
  goes.
- **`prefers-reduced-motion` is mandatory.** Every keyframe animation needs a reduce
  override that disables it. No exceptions.
- Use `framer-motion` for orchestrated React motion; CSS keyframes for ambient loops.

---

## 8. Adopting this in a service — checklist

- [ ] Drop in the shared `:root` token block (§2) and the font `@import` (§3).
- [ ] Wordmark + page H1 in Archivo expanded uppercase; everything else mono; prose in Hanken.
- [ ] Aqua is the only chrome accent; stage colors used only for plan/code/test; red only for alarms.
- [ ] No raw cross-palette `rgba()` literals — all tints via `color-mix` from tokens.
- [ ] No text below 0.7rem.
- [ ] Cards/chips/buttons match §5 shapes; hovers use aqua, not stage colors.
- [ ] Pipeline visuals use the document/robot/flask glyphs + stage colors.
- [ ] Empty states are one honest line; no fake zeros; failed ≠ 100%.
- [ ] Modals trap focus + guard destructive dismissal; alarms loud; reduced-motion honored.

---

## 9. Source of truth

The living implementation is `CFactory/apps/frontend-web/src/index.css` (tokens,
type, components) and its React components (`PipelineStrip`, `App` shell, `TaskDetail`,
`CopilotPanel`). When this document and the cockpit disagree, the cockpit wins — then
update this document. Changes to the shared tokens or type system are brand-level and
should be reviewed across services, not made unilaterally in one repo.
