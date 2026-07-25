---
name: blog
description: Draft, preview, and publish a post to the PFactory GitHub Pages blog (docs/_posts/). Writes a well-structured Jekyll post in the PFactory voice, drops it into the Gruvbox-themed Pages site, previews it with the nix-Chrome screenshot harness, and (on request) commits + syncs dev→main. Use to write or refresh PFactory blog posts.
when_to_use: When the user wants to write, draft, preview, or publish a post on the PFactory blog (the GitHub Pages site under docs/). Triggers — "/blog", "write a blog post", "post about PFactory", "draft a blog post about X", "publish that to the blog", "add a post".
allowed-tools:
  - Bash
  - Read
  - Write
  - Edit
---

# /blog

Help the user write and ship a post to the **PFactory** GitHub Pages blog. The
blog lives in this repo under `docs/` (Jekyll), is themed Gruvbox to match the
portal, and serves at `/blog/`.

## Inputs

- **topic/title** (arg or ask): what the post is about. If only a rough idea is
  given, propose a sharp title + one-line subtitle and confirm before drafting.
- **`--draft`**: write the file but do NOT commit/publish (default is to stop
  before pushing and ask).

## Where things live

| Thing | Path |
|---|---|
| Posts | `docs/_posts/YYYY-MM-DD-<slug>.md` |
| Post layout | `docs/_layouts/post.html` (auto-applied via `_config.yml` defaults) |
| Blog index | `docs/blog.md` → `/blog/` (lists `site.posts` newest-first) |
| Permalink | `/blog/:year/:month/:day/:title/` (set in `docs/_config.yml`) |
| Styling | `docs/assets/css/pfactory.css` (Gruvbox; `.post-*`, `.prose`) |

**Never** invent a date — get today's date from the environment/context and use
it for both the filename prefix and the `date:` field.

## Post front matter (required shape)

```markdown
---
layout: post
title: "A sharp, specific title — not clickbait"
subtitle: "One line that says what the reader gets. Renders under the title and as the blog-list excerpt."
date: YYYY-MM-DD
author: DataSeek Team
---
```

`layout: post` is auto-applied by `_config.yml` defaults, but include it anyway
for clarity. The filename **must** be `docs/_posts/YYYY-MM-DD-<kebab-slug>.md`
or Jekyll won't pick it up.

## Voice & persona (match the product)

PFactory's positioning is **"test quality, not test count"** — engineering-
honest, anti-hype, terminal-native, for people who care about craft. Write like
that:

- Lead with the **real problem**, not a feature. Earn the reader.
- Concrete > abstract. Name the 5 signals, the lanes, the agents.
- No marketing fluff, no exclamation marks, no "revolutionary". Confident, plain.
- British-leaning, lowercase-y, developer-to-developer.
- Short paragraphs. One idea each.

## Make it look good (structure for the Gruvbox theme)

The theme styles these well — use them:

- **Strong lead paragraph** (the `subtitle` + a 2-3 sentence hook).
- `## H2` section headers every ~150 words — the page is scannable.
- **Code / terminal blocks** render as a dark Gruvbox terminal — use one to show a
  command or the planning pipeline:
  ````
  ```
  ingest → enrich → decompose → feasibility → review gates → approval → emit
  ```
  ````
- **Numbered lists** for the stages / steps; **bold** the stage and gate names.
- Close with a **next step** linking an internal page: `[the architecture](/architecture/)`,
  `[demos](/demos/)`, or `[the credential broker](/credentials/)`.
- Aim ~400–700 words for a standard post. Longer only if it earns it.

## Procedure (PARR — announce, run, verify, continue)

### 1. Settle the angle
Confirm title + subtitle + the one thing the reader should leave with. If the
user gave a vague topic, propose 2-3 title options and pick one with them.

### 2. Write the post
Create `docs/_posts/<today>-<slug>.md` with the front matter + body. Follow the
voice + structure above. **Checkpoint:** file exists; front matter parses;
filename date == `date:` field.

### 3. Preview it (the look)
The repo can't run Jekyll headless, so preview with the nix-Chrome harness used
for the demos:

```bash
# Render the post body inside the real layout+CSS and screenshot it.
CSS="$(pwd)/docs/assets/css/pfactory.css"
# Build a quick standalone HTML: <link> the CSS, drop the post HTML inside
# <main class="site-main"><article class="prose container">…</article></main>,
# then screenshot with the nix chrome:
export CHROME_PATH="/etc/profiles/per-user/$USER/bin/google-chrome-stable"
node -e "const{chromium}=require('@playwright/test');(async()=>{const b=await chromium.launch({headless:true,executablePath:process.env.CHROME_PATH});const c=await b.newContext({viewport:{width:1280,height:1400}});const p=await c.newPage();await p.goto('file:///tmp/blog-prev.html',{waitUntil:'networkidle'});await p.waitForTimeout(1000);await p.screenshot({path:'/tmp/blog-prev.png',fullPage:true});await b.close();})()"
```

Show the screenshot to the user (SendUserFile). If it's installed, a real
`cd docs && bundle exec jekyll build` is the gold check, but the screenshot is
usually enough. **Checkpoint:** the post reads well + matches the Gruvbox brand.

### 4. Publish (only when the user says so)
Branching model: work on `dev`, then promote to `main` so Pages rebuilds.

```bash
git add docs/_posts/<file> && git commit -s -m "blog: <title>"
git push origin dev
# sync main (Pages serves from the default branch / configured branch):
git fetch origin -q && git merge-base --is-ancestor origin/main origin/dev \
  && git push origin dev:main
```

End the commit message with the repo's sign-off footer. **Checkpoint:** the
commit landed; `dev == main`; remind the user Pages takes a minute to rebuild.

## Notes
- One post per file; don't edit an old post's `date:`/filename to "bump" it.
- Keep posts in PFactory's lane (testing, QA, the pipeline, the moat) — this is
  the product blog, not a personal one.
- The blog index, nav link, and styling already exist — a new post just needs
  the `_posts/` file; it shows up automatically.
