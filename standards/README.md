# Vendored Factory shared coding standards

This directory is a **pinned, vendored copy** of the fleet-wide code-quality
baseline. The single source of truth lives in the Factory hub at
[`standards/`](https://github.com/olafkfreund/Factory/tree/main/standards).

## Pinned source

| Field | Value |
|---|---|
| Hub repo | `Factory` |
| Pinned commit | `93c3bb49ed5f3f3d3d46aae5f8a13ff601550702` |

A CI drift gate (`.github/workflows/ci.yml`, job `config-drift`) diffs these
vendored files against the hub at the pinned commit, so this repo cannot silently
fork the baseline.

## Files

| File | What it is |
|---|---|
| `coding-standards.md` | The normative standard (Python, TypeScript, cross-cutting, CI). |
| `ruff.toml` | Shared Python lint baseline. The repo's root `ruff.toml` adopts this select set. |
| `mypy.ini` | Shared `mypy --strict` baseline. The repo's root `mypy.ini` inherits it. |
| `.editorconfig` | Copied to the repo root. |

## Tighten-only rule

Per the standard, a service config may add rules or lower numeric caps. It may
**not** remove a selected rule category, raise a complexity cap, or disable a
gate. PFactory adopts the shared select set as-is.

## Updating the pin

Bump the pinned commit above, re-copy the four files from the hub at that commit,
and update the SHA referenced in the `config-drift` CI job.
