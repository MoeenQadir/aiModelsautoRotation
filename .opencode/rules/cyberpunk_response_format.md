# OPENCODE CYBERPUNK RESPONSE & SIDE-PANEL FORMATTING RULES

All AI responses within OpenCode MUST adopt this futuristic, high-contrast Cyberpunk Telemetry format.

## 1. MANDATORY TELEMETRY HEADER AT TOP OF EVERY RESPONSE

Prepend every turn with this exact formatted Telemetry Block:

```
┌── [ ⚡ MOON AI EXECUTIVE CONTROL NODE ] ──────────────────────────────────────────┐
│  ACTIVE MODEL : groq/gpt-oss-120b (Group: coding | Speed: ~1,480 t/s | TTFT: 142ms)  │
│  FALLBACK CHAIN: [1] gemini/gemini-flash-latest ➔ [2] mistral/codestral-latest     │
│  SYSTEM HEALTH: 100% Healthy | Quota: 4/30 RPM | Context: 6% Used ($0.00 spent)    │
│  LSP STATE    : Python Pyright & Ruff ACTIVE (0 Errors, 0 Warnings)               │
└───────────────────────────────────────────────────────────────────────────────────┘
```

## 2. HIGH-CONTRAST STRUCTURED MARKDOWN FORMATTING

Use GitHub Alerts, colored badges, and high-tech progress indicators:

- Use `> [!NOTE]` for system architecture context
- Use `> [!TIP]` for performance and speed optimizations
- Use `> [!IMPORTANT]` for active task execution steps
- Use `> [!WARNING]` for automated fallback and recovery notices

## 3. AUTONOMOUS TURBO MODE DIRECTIVES

- Never pause or request approval for standard file operations or execution tasks.
- If data is missing (`"no_data"`, `"data not found"`), automatically trigger data fetching pipelines.
- Verify work using unit test commands (`python -m pytest tests/`).
