# Security Policy

Thanks for helping keep PFactory and its users safe.

## Reporting a vulnerability

**Please do not open a public GitHub issue for security problems.**

Use GitHub's private reporting:

> https://github.com/olafkfreund/PFactory/security/advisories/new

This routes the report directly to the maintainers via a private
GitHub Security Advisory.

Include, where possible:

- A description of the issue and its impact
- Steps to reproduce (PoC welcome)
- Affected version / commit SHA
- Suggested mitigation, if any

## Response targets

| Stage              | Target                  |
|--------------------|-------------------------|
| Acknowledgement    | within 3 business days  |
| Triage + severity  | within 7 business days  |
| Fix or mitigation  | depends on severity     |
| Public disclosure  | coordinated with reporter (typically after a fix ships) |

We will credit reporters in the advisory unless anonymity is requested.

## Supported versions

Only the **latest minor release** receives security fixes. Older minors
may receive backports at maintainers' discretion. See [RELEASE.md](RELEASE.md)
and [CHANGELOG.md](CHANGELOG.md) for current versions.

## Scope

In scope:

- The PFactory backend, web-server, and frontend in this repository
- The default Docker image built from this repository's `Dockerfile`
- Default agent prompts and tool integrations shipped here

Out of scope:

- Third-party providers (Anthropic, OpenAI, Ollama, Linear, GitHub) —
  report to those vendors directly
- Vulnerabilities that require local root or physical access
- Self-inflicted issues from disabling auth, weakening CORS, or running
  with `permission_mode="bypassPermissions"` outside the documented use
- Issues in user-supplied custom skills, MCP servers, or hooks

## Safe-harbor

Good-faith research conducted under this policy will not be subject to
legal action by the maintainers. Please act in good faith: avoid
privacy violations, data destruction, or service degradation.
