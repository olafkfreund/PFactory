# Dependencies

## Tech stack

| Layer | Technology |
|---|---|
| Language | Python 3.13 |
| Web | FastAPI (REST + WebSocket), `:3114` |
| Agent runtime | Claude Agent SDK |
| Frontend | React 19 + TypeScript + Vite, `:3115` |
| Packaging | uv (Python), npm (frontend) |
| Containers | Docker (sandboxing) |
| Dev env | Nix flake + direnv |

## Key Python dependencies

| Package | Role |
|---|---|
| `claude-agent-sdk` | Primary LLM/agent client |
| `anthropic` | Message Batches API (explicit pin) |
| `graphiti-core` | Graph memory across sessions |
| `pypdf`, `python-docx` | Plan ingestion (PDF / DOCX) |
| `tree-sitter-{python,javascript,typescript}` | AST code analysis |
| `pydantic` ≥ 2 | Data models & validation |

## External systems (read-only enrichment)

- **Cloud:** Kubernetes/OpenShift, AWS, Azure, GCP introspection; Terraform state;
  Prowler/CIS misconfiguration scan (OCSF).
- **Catalog:** Backstage entities, golden-path templates, TechDocs.
- **Knowledge:** Confluence, GitBook, Notion, SharePoint, local/Git wikis.
- **Policy engines:** Checkov, OPA/Rego (deterministic gate half).

## Secrets

Credential broker backends: HashiCorp Vault, Azure Key Vault, AWS Secrets Manager,
GCP Secret Manager, sops/age. Credentials are ephemeral and redacted from logs.

## Cross-service dependencies (Factory family)

- **AIFactory** (downstream) — consumes PFactory's governed issues. AIFactory `dependsOn`
  PFactory in the catalog graph.
- **CFactory** (observer) — reads PFactory session state and completion events.
- **Contract:** [RFC-0001](https://factory.freundcloud.com/rfc/correlation-key/) —
  shared correlation key + completion-event envelope.
