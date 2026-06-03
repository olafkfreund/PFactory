# /pfactory-from-template

Run the **pfactory-from-template** skill to render one of the 15 v0.2
starter templates (5 each for pytest, Jest, Playwright) into a working
test file. The skill is LLM-free — it lists the templates available for
the chosen framework via `templates_pkg.load_templates_for_framework`,
collects the required `vars`, calls `TemplateFile.instantiate(**values)`,
and writes the result to the framework's standard test path.

Templates take env-var NAMES (not values) for any auth field, in line
with Decision 7 of the v0.2 design spec.

See `.claude/skills/pfactory-from-template/SKILL.md` for the full
procedure.
