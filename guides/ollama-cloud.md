# Ollama Cloud provider

> Issue #94. Adds **Ollama Cloud** — hosted, OpenAI-compatible inference at
> `https://ollama.com/v1`, authed by `OLLAMA_API_KEY` — as a usable LLM
> provider. Routes through PFactory's openai-compatible backend, mirroring the
> GitHub Models provider (epic #87).

## Local Ollama vs Ollama Cloud

| | Local Ollama | **Ollama Cloud** |
| --- | --- | --- |
| Endpoint | `http://localhost:11434` | `https://ollama.com` |
| Auth | none | **`OLLAMA_API_KEY` required** |
| Reachable from k3d/k8s pods | ✗ (private IP, no route) | ✓ (public egress) |
| Provider name | `ollama` | `ollama-cloud` |

This guide covers **cloud only**. Running host-local Ollama from inside the
cluster is a separate networking problem (run Ollama in-cluster as
`ollama.<ns>.svc.cluster.local:11434`, or expose it via ingress).

## Configure

Set two env vars on the backend (see `apps/backend/.env.example`):

```bash
OLLAMA_CLOUD_BASE_URL=https://ollama.com   # optional; this is the default
OLLAMA_API_KEY=<key from https://ollama.com/settings/keys>
```

The base URL is stored **without** the `/v1` suffix — the openai-compatible
layer appends `/v1/chat/completions` itself. If you include `/v1`, it is
normalised away.

## Selecting a cloud model

A model routes to Ollama Cloud when its name:

- carries a **cloud suffix** — `:cloud` or `-cloud`
  (e.g. `glm-5:cloud`, `qwen3-coder:480b-cloud`), **or**
- has an explicit **`ollama-cloud:` prefix**
  (e.g. `ollama-cloud:gpt-oss:120b` — needed when the bare name, like
  `gpt-oss:120b`, has no cloud suffix and would otherwise look like a
  GPT/Codex model).

Browse available cloud models at <https://ollama.com/search?c=cloud>.

```bash
# Suffix form — auto-detected:
PFACTORY_MODEL=glm-5:cloud
# Prefix form — for cloud models without a :cloud/-cloud suffix:
PFACTORY_MODEL=ollama-cloud:gpt-oss:120b
```

The `ollama-cloud:` prefix is stripped before the request; the model tag
(the second colon, e.g. `:120b`) is preserved.

## Connectivity check

Verify the key and list cloud models with a direct probe:

```bash
curl -s https://ollama.com/v1/models \
  -H "Authorization: Bearer $OLLAMA_API_KEY" | jq '.data[].id'
```

The openai-compatible provider performs the same `GET /v1/models` probe as its
health check, so the portal's endpoint **Test** button and the pipeline use the
identical path.

## Cluster secret wiring (factory-gitops — separate repo)

The agenix key lives on the host; it is **not** automatically in the cluster.
To run this in the deployed service:

1. Add `OLLAMA_API_KEY` to the `factory-secrets` k8s secret (sourced from the
   existing agenix key).
2. Reference it in this service's deployment env in **`factory-gitops`**.

This repo only consumes the env var; the secret plumbing is a `factory-gitops`
change.

## Implementation notes

- Routing + defaults: `apps/backend/providers/factory.py`
  (`ollama-cloud` registry entries, aliases, `_apply_ollama_cloud_defaults`).
- Model inference: `apps/backend/phase_config.py`
  (`infer_provider_from_model`, `strip_provider_prefix`).
- Backend: the openai-compatible provider
  (`apps/backend/providers/openai_compatible.py`) — Bearer auth, POSTs to
  `{base_url}/v1/chat/completions`.
- Tests: `tests/test_ollama_cloud_provider.py`.
