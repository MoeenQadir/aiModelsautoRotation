"""
Custom LiteLLM proxy hooks for Moon AI.

ReasoningStripper: removes model-specific reasoning fields from replayed
assistant messages before the request is forwarded to any provider.

Why this exists
---------------
OpenCode stores ``reasoning_content`` (returned by Groq gpt-oss and OpenRouter
models) and ``thinking_blocks`` in its conversation history and replays them
on every subsequent turn. Strict OpenAI-compatible providers reject unknown
message fields with hard 400 errors, e.g.:

  Cerebras:  "messages.N.assistant.reasoning_content: property ... unsupported"
  Groq / Mistral / Cloudflare: similar "unknown field" 400s

Reasoning from previous turns is never required to continue a conversation
(models regenerate it), so stripping it on replay is safe and lets the
router fall back across providers without breaking mid-session.
"""

from typing import Any, List, Literal, Optional

from litellm.integrations.custom_logger import CustomLogger

try:  # Proxy-internal types are only used for annotations; be defensive.
    from litellm.proxy._types import UserAPIKeyAuth  # noqa: F401
    from litellm.proxy.proxy_server import DualCache  # noqa: F401
except Exception:  # pragma: no cover - layout differs across LiteLLM versions
    pass


# Fields that various clients/providers attach to assistant messages and
# that strict providers reject when replayed on subsequent turns.
STRIP_FIELDS = (
    "reasoning_content",
    "thinking_blocks",
    "reasoning",
    "reasoning_summary",
    "reasoning_details",
    "signature",
    "provider_specific_fields",
)


class ReasoningStripper(CustomLogger):
    """Strip replayed reasoning fields from assistant messages (pre-call)."""

    async def async_pre_call_hook(
        self,
        user_api_key_dict: Optional[Any],
        cache: Optional[Any],
        data: dict,
        call_type: Literal[
            "completion",
            "text_completion",
            "embeddings",
            "image_generation",
            "moderation",
            "audio_transcription",
        ],
    ) -> dict:
        messages: Optional[List[Any]] = data.get("messages")
        if isinstance(messages, list):
            for message in messages:
                if isinstance(message, dict) and message.get("role") == "assistant":
                    for field in STRIP_FIELDS:
                        message.pop(field, None)
        return data


# Callbacks in the proxy config must point at an *instance* (dotted path).
reasoning_stripper = ReasoningStripper()
