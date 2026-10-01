# Moon AI Auto Router

A personal, provider-independent coding backend for [OpenCode](https://opencode.ai).
OpenCode sees **one stable endpoint** (`http://127.0.0.1:4000/v1`) with three
logical models — `coding`, `reasoning`, `backup` — while LiteLLM handles
provider selection, health, cooldowns, and failover.

```
VS Code → OpenCode → Moon Router (LiteLLM :4000) → Cerebras / OpenRouter (free) / Groq* / Gemini* / GitHub Models*
                                                       * optional — see "Optional providers"
```

## Files

| File | Purpose |
|---|---|
| `docker-compose.yml` | `moon-ai-router` container, bound to `127.0.0.1:4000` only |
| `litellm-config.yaml` | Logical model groups + deployments + failover rules |
| `.env` | API keys (gitignored — never commit) |
| `.env.example` | Template documenting every variable |

## Logical models

| Group | Chain (verified free deployments) |
|---|---|
| `coding` | Cerebras `gpt-oss-120b` → Groq `gpt-oss-120b` (Key #1 → Key #2 rotation) → OpenRouter `qwen3.8-27b:free` → OpenRouter `nemotron-3-ultra:free` |
| `reasoning` | Gemini `gemini-flash-latest` (rolling alias) → OpenRouter `nemotron-3-super:free` → Cerebras `qwen-3.8-27b` |
| `backup` | Gemini `gemini-flash-latest` → OpenRouter `north-mini-code:free` (end of the line — no further fallback) |

Failover: `coding → reasoning → backup`. A deployment that fails twice is
cooled down for 5 minutes instead of being hammered (`allowed_fails: 2`,
`cooldown_time: 300`). OpenRouter `:free` models are shared infrastructure
and often show temporary upstream 429s — the cooldown handles this and they
reappear as healthy without config changes.

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
$h.healthy_endpoints   | ForEach-Object { "UP   $($_.model)" }
$h.unhealthy_endpoints | ForEach-Object { "DOWN $($_.model) :: $($_.error)" }
```

Or from bash:

```bash
curl -s -H "Authorization: Bearer $MK" http://127.0.0.1:4000/health | jq .
```

Never shows API keys.

## Optional providers

All disabled deployments are kept in `litellm-config.yaml` as commented
blocks. To enable one: add the key to `.env`, uncomment the block, then
`docker compose restart`.

| Provider | Status | Action to enable |
|---|---|---|
| Groq (2-key rotation) | ✅ enabled | — |
| Gemini | ✅ enabled | — |
| GitHub Models | commented | `models.github.ai` currently returns a non-JSON `OK` body for this token — regenerate the PAT (fine-grained, **models: read**) and uncomment |

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
