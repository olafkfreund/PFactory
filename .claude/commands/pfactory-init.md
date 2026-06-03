# /pfactory-init

Run the **pfactory-init** skill to scaffold a `.pfactory.yml` plus an empty
`.pfactory/tests-catalog.json` for the current repo. The skill interactively
collects the targets (HTTP / Kubernetes / docker-compose / feature-flag),
auth env-var names (never values), optional `test_data` seed/reset hooks,
and validates the rendered YAML against the `PFactoryConfig` Pydantic
schema before writing.

See `.claude/skills/pfactory-init/SKILL.md` for the full procedure.
