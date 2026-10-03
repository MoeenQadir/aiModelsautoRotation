# Moon AI Auto Router

A personal, provider-independent coding backend for [OpenCode](https://opencode.ai).
OpenCode sees **one stable endpoint** (`http://127.0.0.1:4000/v1`) with three
logical models — `coding`, `reasoning`, `backup` — while LiteLLM handles
provider selection, health, cooldowns, and failover.

```
VS Code → OpenCode → Moon Router (LiteLLM :4000) → Groq / Cerebras / Gemini / OpenRouter (free)
                                                 ↘ Mistral* / SambaNova* / Together* / Fireworks*
                                                   NVIDIA* / Cloudflare* / Qwen* / GitHub Models*
                                                   * pending API keys — see "Keys you still owe the router"
```

## Files

| File | Purpose |
|---|---|
| `docker-compose.yml` | `moon-ai-router` container, bound to `127.0.0.1:4000` only |
| `litellm-config.yaml` | Logical model groups + deployments + failover rules |
| `.env` | API keys (gitignored — never commit) |
| `.env.example` | Template documenting every variable |

## Logical models

Priority inside a group is **top-to-bottom** — strongest/fastest first.

| Group | Active chain (verified free deployments, live-checked 2026-10-03) |
|---|---|
| `coding` | Groq `gpt-oss-120b` (Key #1 → Key #2 rotation) → Groq `qwen3.8-27b` → Cerebras `gpt-oss-120b` → **Mistral `codestral-latest`** → **Cloudflare `llama-3.3-70b-fp8-fast`** → OpenRouter `qwen3.8-27b:free` → OpenRouter `nemotron-3-ultra:free` |
| `reasoning` | Gemini `gemini-flash-latest` (rolling alias) → OpenRouter `nemotron-3-super:free` → Cerebras `qwen-3.8-27b` |
| `backup` | Gemini `gemini-flash-latest` → Gemini `gemini-flash-lite-latest` → OpenRouter `north-mini-code:free` (end of the line — no further fallback) |

Failover: `coding → reasoning → backup`. A deployment that fails twice is
cooled down for 5 minutes instead of being hammered (`allowed_fails: 2`,
`cooldown_time: 300`). OpenRouter `:free` models are shared infrastructure
and often show temporary upstream 429s — the cooldown handles this and they
reappear as healthy without config changes.

## Requested-models integration status (2026-10-03)

Every model from your original list is now in the router. Model IDs were
**verified live against each provider API on 2026-10-03**; dead IDs were
replaced with the same provider's current equivalent rather than silently
dropped.

| Requested | Status | What happened |
|---|---|---|
| `groq/llama-3.3-70b-versatile` | ♻️ replaced | Model retired by Groq; live check shows only `gpt-oss-*` / `qwen-qwen3.8-27b`. Now `groq/gpt-oss-120b` ×2 keys + `groq/qwen3.8-27b` |
| `cerebras/llama-3.3-70b` | ♻️ replaced | Retired; Cerebras now serves `gpt-oss-120b`, `qwen-3.8-27b` |
| `mistral/codestral-latest` | ✅ active | Key verified live 2026-10-03; deployed in `coding` group |
| `sambanova/Meta-Llama-3.3-70B-Instruct` | 🅿️ parked | Key valid, but inference returns `PAYMENT_METHOD_REQUIRED` — add a payment method at cloud.sambanova.ai/plans/billing (free tier not charged), then uncomment the block |
| `together/llama-3.3-70b-instruct` | ♻️+⏳ | Prefix corrected (`together_ai/`), free variant `-Turbo-Free`; needs `TOGETHER_API_KEY` |
| `fireworks/llama-v3p1-70b-instruct` | ♻️+⏳ | Prefix corrected (`fireworks_ai/` + full account path); needs `FIREWORKS_API_KEY` |
| `nvidia/llama-3.1-70b` | ♻️+⏳ | Prefix corrected (`nvidia_nim/meta/llama-3.1-70b-instruct`); needs `NVIDIA_API_KEY` |
| `qwen/qwen2.5-coder-32b-instruct` | ♻️+⏳ | Routed via DashScope compatible-mode (`openai/` passthrough); needs `QWEN_API_KEY` |
| `deepseek/deepseek-chat` | ⏸ paid | All `:free` deepseek variants are gone from OpenRouter; kept as commented last-resort (uses OpenRouter credits) |
| `gemini/gemini-2.0-flash` | ♻️ replaced | Google retired it (live 404); replaced with rolling alias `gemini-flash-lite-latest` on the same free key |
| `github/gpt-4o-mini` | ⚠️ blocked upstream | `models.github.ai` still returns non-JSON `OK` (re-verified 2026-10-03); block commented, regenerate PAT with **models: read** to retry |
| `cloudflare/@cf/meta/llama-3.3-70b-instruct-fp8-fast` | ✅ active | Key + account ID verified live 2026-10-03; deployed in `coding` group |

## Keys: status & what's left

**Active & verified (nothing owed):** Groq ×2, Cerebras, OpenRouter,
Gemini, Mistral, Cloudflare — 14/14 deployments healthy (2026-10-03).

| Action (optional, in priority order) | Get it from | Unlocks |
|---|---|---|
| Add a **payment method** to SambaNova account | cloud.sambanova.ai/plans/billing (free tier not charged) | Llama-3.3-70B at wafer-scale speed — then uncomment its block |
| `QWEN_API_KEY` | bailian.console.alibabacloud.com (free quota) | qwen2.5-coder-32b-instruct |
| `TOGETHER_API_KEY` | api.together.ai (free tier) | Llama-3.3-70B-Turbo-Free |
| `FIREWORKS_API_KEY` | fireworks.ai (trial credits) | llama-v3p1-70b-instruct |
| `NVIDIA_API_KEY` | build.nvidia.com (free dev tier) | llama-3.1-70b-instruct |
| New GitHub PAT | github.com/settings/tokens (fine-grained, **models: read**) | gpt-4o-mini backup — blocked by GitHub's endpoint, not by config |

Nothing here requires a paid plan.

## Quick start

```powershell
cd D:\MoonAI
docker compose up -d                      # start the router
docker ps --filter name=moon-ai-router    # Test 1: expect "Up (healthy)"
```

OpenCode (`~/.config/opencode/opencode.jsonc`) already points at the router
and authenticates with `LITELLM_MASTER_KEY` from your Windows user
environment (set once via `setx`; restart terminals after setting it).

## Test sequence (PowerShell)

```powershell
# Test 2 - port
Test-NetConnection 127.0.0.1 -Port 4000          # TcpTestSucceeded : True

# Test 3 - health
$mk = (Get-Content .env | Select-String '^LITELLM_MASTER_KEY=').Line.Split('=')[1]
Invoke-RestMethod -Uri "http://127.0.0.1:4000/health/liveliness"

# Test 4 - models (expect coding, reasoning, backup)
Invoke-RestMethod -Uri "http://127.0.0.1:4000/v1/models" `
  -Headers @{ Authorization = "Bearer $mk" }

# Test 5 - minimal chat
$body = @{ model = "coding"
           messages = @(@{ role = "user"; content = "Say hello in one sentence." })
           max_tokens = 100; stream = $false } | ConvertTo-Json -Depth 5
Invoke-RestMethod -Uri "http://127.0.0.1:4000/v1/chat/completions" -Method Post `
  -ContentType "application/json" -Headers @{ Authorization = "Bearer $mk" } -Body $body
```

## Provider health report (spec §19)

```powershell
$h = Invoke-RestMethod -Uri "http://127.0.0.1:4000/health" `
       -Headers @{ Authorization = "Bearer $mk" }
$h.healthy_endpoints   | ForEach-Object { "UP   $($_.model) :: $($_.model_info.litellm_params.model)" }
$h.unhealthy_endpoints | ForEach-Object { "DOWN $($_.model) :: $($_.error)" }
```

Or from bash:

```bash
curl -s -H "Authorization: Bearer $MK" http://127.0.0.1:4000/health | jq .
```

Never shows API keys.

## Troubleshooting (spec §27 order)

1. `docker ps` — is `moon-ai-router` Up? If not: `docker logs moon-ai-router --tail 50`
2. Port 4000 — `Test-NetConnection 127.0.0.1 -Port 4000`
3. Health — `GET /health/liveliness` → `"I'm alive!"`
4. Models — `GET /v1/models` → `coding, reasoning, backup`
5. Env vars — keys present in `.env`? (`docker exec moon-ai-router env` lists names only)
6. Provider auth — see the health report's `unhealthy_endpoints` errors
7. Model IDs — re-verify against provider docs; update only `litellm-config.yaml`
8. Limits — per-deployment `model_info.max_output_tokens` caps OpenCode's large `max_tokens` requests
9. Routing — check `docker logs moon-ai-router` for fallback events
10. OpenCode — `opencode debug config` must show `model: moon/coding`

Restart everything:

```powershell
docker compose restart        # config reload
docker compose up -d --force-recreate   # full recreate (picks up .env changes)
```

## Security

- `.env` is gitignored; keys only enter LiteLLM via `os.environ/...`
- `LITELLM_MASTER_KEY` is set as a Windows user env var for OpenCode
- Router binds to `127.0.0.1` only; never exposed publicly
- Logs never contain keys, headers, prompts, or source code
