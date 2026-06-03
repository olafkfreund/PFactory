# Run PFactory fully on your own infrastructure (BYO-LLM / air-gapped)

> **PFactory can generate, run, and triage tests without your source code or
> the generated tests ever leaving your network.** It runs against any
> model you host — Ollama, vLLM, LM Studio, or LocalAI — through the same
> provider abstraction it uses for Claude. This guide shows how, and how to
> **verify** the no-egress guarantee.

This matters for regulated and privacy-conscious teams (GDPR / HIPAA /
data-residency, defence, finance). Nothing here is a paid add-on — the
multi-provider factory ships in the box (`apps/backend/providers/`).

## TL;DR

```bash
# 1. Point PFactory at a local model (Ollama example)
export PFACTORY_MODEL="ollama:qwen3:14b"     # or set per-phase in settings

# 2. Verify the data-egress posture BEFORE running anything
cd apps/backend
python byo_llm.py "$PFACTORY_MODEL"
#   → "🔒 Local — no data egress"   (exit 0)

# 3. Run the pipeline as usual — no API key, no egress.
```

`python byo_llm.py <model>` exits **0** only when the run keeps all data on
your machine/LAN, **1** otherwise — wire it into CI as an air-gap gate.

## Supported local backends

| Backend | Model string | Endpoint env var | Default |
|---|---|---|---|
| **Ollama** | `ollama:<model>` | `OLLAMA_BASE_URL` | `http://localhost:11434` |
| **vLLM** | `openai-compatible:<model>` | `OPENAI_COMPATIBLE_BASE_URL` | — set to your vLLM URL |
| **LM Studio** | `openai-compatible:<model>` | `OPENAI_COMPATIBLE_BASE_URL` | `http://localhost:1234/v1` |
| **LocalAI** | `openai-compatible:<model>` | `OPENAI_COMPATIBLE_BASE_URL` | `http://localhost:8080/v1` |

Provider selection is driven entirely by the model-string prefix
(`phase_config.infer_provider_from_model`) — no separate provider switch.

## Subscription-backed (non-Claude) providers

If you don't need fully local but want to run PFactory off a flat-rate
subscription instead of the Claude SDK, two hosted CLIs are wired in:

| Provider | Model string | Auth / billing |
|---|---|---|
| **GitHub Copilot CLI** *(recommended non-Claude default)* | `copilot:claude-sonnet-4.5` (also `copilot:claude-sonnet-4`, `copilot:gpt-5`) | your GitHub Copilot subscription (flat-rate); `copilot` must be installed + signed in |
| **OpenAI Codex** | `gpt-5.3-codex` / `gpt-5-codex` | `OPENAI_API_KEY` (pay-per-token). The provider writes a PFactory-owned `CODEX_HOME` so it works regardless of your global `codex login` — a bare ChatGPT-account login rejects every model on the headless path |

`copilot:*` is the recommended non-Claude lane for demos: it runs
`claude-sonnet-4.5` through your Copilot subscription (flat-rate, no per-token
billing) and Copilot handles its own tool sandbox.

### Example: vLLM

```bash
# Your vLLM server (on this host or a private box)
export OPENAI_COMPATIBLE_BASE_URL="http://localhost:8000/v1"
export OPENAI_COMPATIBLE_API_KEY="not-needed-but-some-servers-want-a-token"
export PFACTORY_MODEL="openai-compatible:Qwen/Qwen2.5-Coder-32B-Instruct"

python apps/backend/byo_llm.py "$PFACTORY_MODEL"   # → 🔒 Local
```

## How egress is classified

`byo_llm.classify(model, base_url)` resolves the endpoint host and returns:

- **LOCAL** — `localhost` / loopback / RFC-1918 private / `.local` / `.internal`
  → *data never leaves your network.*
- **SELF_HOSTED** — a routable host you run (your own vLLM on a VPS, remote
  Ollama) → you control egress, but the traffic crosses the public internet.
- **MANAGED_CLOUD** — a third-party managed API (Anthropic, Google, OpenAI,
  OpenRouter, Together, Groq, …) → data leaves to a third party.

Only **LOCAL** sets `keeps_data_local = True`.

> Note: a managed model (e.g. `claude-…`) repointed at a **local proxy** via
> `ANTHROPIC_BASE_URL=http://localhost:4000` (LiteLLM etc.) is also classified
> LOCAL — the classification follows the *endpoint*, not the model name.

## Air-gapped checklist

- [ ] Model served locally (Ollama / vLLM / LM Studio / LocalAI).
- [ ] `python byo_llm.py "$MODEL"` prints **🔒 Local** and exits 0.
- [ ] No `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / cloud creds in the env.
- [ ] Pull the runner Docker images ahead of time (the executor sandbox runs
      `--network=none`, so test execution is already offline).
- [ ] (Optional, belt-and-braces) run on a host with egress firewalled; the
      classifier proves *intent*, the firewall proves *enforcement*.

## Programmatic check

```python
from byo_llm import keeps_data_local, egress_report

assert keeps_data_local("ollama:qwen3:14b")          # True
print(egress_report("openai-compatible:llama"))      # full posture dict
```

`egress_report(model)` returns `{provider, base_url, host, egress,
keeps_data_local, badge}` — the portal uses it to show a live
local/self-hosted/cloud badge for the configured model.
