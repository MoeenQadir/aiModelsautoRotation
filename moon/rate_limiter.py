# Rate Limiter for Moon AI Proxy
# Implements proactive rate limiting, dynamic route switching, and automated quota reset timers

import os
import re
import time
import asyncio
import threading
from typing import Dict, List, Optional, Any
from collections import defaultdict
from datetime import datetime, timezone, timedelta


class RateLimiter:
    """Proactive rate limiter and route switcher for AI model endpoints with multi-key support
    and automated quota reset schedules.
    """

    # Default limits applied to providers without an explicit entry
    DEFAULT_LIMITS = {"rpm": 60, "rpd": 2000}

    # Provider Quota Reset Schedule Knowledge Base
    PROVIDER_RESET_RULES = {
        "groq": {"rpm_window": 60, "rpd_reset_type": "utc_midnight"},
        "gemini": {"rpm_window": 60, "rpd_reset_type": "utc_midnight"},
        "openrouter": {"rpm_window": 60, "rpd_reset_type": "sliding_24h"},
        "mistral": {"rpm_window": 60, "rpd_reset_type": "utc_midnight"},
        "cerebras": {"rpm_window": 60, "rpd_reset_type": "sliding_24h"},
        "cloudflare": {"rpm_window": 60, "rpd_reset_type": "utc_midnight"},
        "sambanova": {"rpm_window": 60, "rpd_reset_type": "utc_midnight"},
        "together": {"rpm_window": 60, "rpd_reset_type": "utc_midnight"},
        "huggingface": {"rpm_window": 60, "rpd_reset_type": "utc_midnight"},
    }

    def __init__(self):
        self.provider_limits = {
            "groq": {"rpm": 30, "rpd": 14400},
            "gemini": {"rpm": 15, "rpd": 1500},
            "openrouter": {"rpm": 20, "rpd": 200},
            "together": {"rpm": 20, "rpd": 200},
            "huggingface": {"rpm": 10, "rpd": 100},
            "mistral": {"rpm": 15, "rpd": 1500},
            "cerebras": {"rpm": 30, "rpd": 14400},
            "cloudflare": {"rpm": 15, "rpd": 10000},
            "sambanova": {"rpm": 10, "rpd": 100},
        }

        # Track requests per minute and per day with key support
        self.request_counts: Dict[str, Dict[str, List[float]]] = defaultdict(
            lambda: {"rpm": [], "rpd": []}
        )

        # Track model health status with key support
        self.model_status: Dict[str, Dict[str, bool]] = defaultdict(
            lambda: {"healthy": True, "last_check": time.time()}
        )

        # Track API keys and their usage
        self.api_keys: Dict[str, List[str]] = defaultdict(list)
        self.current_key_index: Dict[str, int] = defaultdict(int)

        # Load API keys from environment variables
        self._load_api_keys()

    # ------------------------------------------------------------------
    # API key management (multi-key rotation)
    # ------------------------------------------------------------------
    def _load_api_keys(self) -> None:
        """Discover provider API keys from environment variables.

        Recognises ``<PROVIDER>_API_KEY`` and numbered variants such as
        ``GROQ_API_KEY_1`` / ``GROQ_API_KEY_2`` (comma-separated values in a
        single variable are also supported for key rotation). Key values are
        never logged.
        """
        key_pattern = re.compile(r"^([A-Z0-9]+)_API_KEY(?:_(\d+))?$")
        for env_name, value in os.environ.items():
            match = key_pattern.match(env_name)
            if not match or not value:
                continue
            provider = match.group(1).lower()
            for key in value.split(","):
                key = key.strip()
                if key and key not in self.api_keys[provider]:
                    self.api_keys[provider].append(key)

    def get_api_key(self, provider: str) -> Optional[str]:
        """Return the next API key for a provider (round-robin rotation)."""
        keys = self.api_keys.get(provider.lower())
        if not keys:
            return None
        index = self.current_key_index[provider.lower()] % len(keys)
        self.current_key_index[provider.lower()] += 1
        return keys[index]

    # ------------------------------------------------------------------
    # Rate limit tracking & Quota Reset
    # ------------------------------------------------------------------
    def get_provider_limits(self, provider: str) -> Dict[str, int]:
        """Get configured rate limits for a provider (safe defaults if unknown)."""
        return self.provider_limits.get(provider.lower(), dict(self.DEFAULT_LIMITS))

    def record_request(self, provider: str, model: str) -> bool:
        """Record a request to track rate limits.

        Proactively enforces the provider limits: when the RPM or RPD budget
        is already exhausted the request is NOT recorded and ``False`` is
        returned so the caller can switch to another model instead.
        """
        now = time.time()
        key = f"{provider.lower()}_{model.lower()}"
        limits = self.get_provider_limits(provider)

        # Clean up old requests first
        self._clean_old_requests(key)

        # Proactive check - refuse to track requests beyond the limits
        if len(self.request_counts[key]["rpm"]) >= limits["rpm"]:
            return False
        if len(self.request_counts[key]["rpd"]) >= limits["rpd"]:
            return False

        # Record RPM (requests per minute)
        self.request_counts[key]["rpm"].append(now)

        # Record RPD (requests per day)
        self.request_counts[key]["rpd"].append(now)
        return True

    def _clean_old_requests(self, key: str) -> None:
        """Remove expired requests based on RPM sliding windows and RPD reset rules."""
        now = time.time()
        provider = key.split("_", 1)[0].lower() if "_" in key else key.lower()
        reset_rule = self.PROVIDER_RESET_RULES.get(provider, {"rpm_window": 60, "rpd_reset_type": "sliding_24h"})
        rpm_window = reset_rule.get("rpm_window", 60)

        # Clean RPM (older than rpm_window seconds)
        self.request_counts[key]["rpm"] = [
            t for t in self.request_counts[key]["rpm"] if now - t < rpm_window
        ]

        # Clean RPD
        if reset_rule.get("rpd_reset_type") == "utc_midnight":
            # Remove requests prior to the most recent 00:00 UTC midnight
            now_utc = datetime.now(timezone.utc)
            midnight_utc = datetime(now_utc.year, now_utc.month, now_utc.day, tzinfo=timezone.utc)
            midnight_timestamp = midnight_utc.timestamp()
            self.request_counts[key]["rpd"] = [
                t for t in self.request_counts[key]["rpd"] if t >= midnight_timestamp
            ]
        else:
            # Default sliding 24-hour window (86400 seconds)
            self.request_counts[key]["rpd"] = [
                t for t in self.request_counts[key]["rpd"] if now - t < 86400
            ]

    def should_switch_model(self, provider: str, model: str) -> bool:
        """Determine if we should switch to a different model due to rate limits"""
        key = f"{provider.lower()}_{model.lower()}"
        self._clean_old_requests(key)
        limits = self.get_provider_limits(provider)

        rpm_count = len(self.request_counts[key]["rpm"])
        rpd_count = len(self.request_counts[key]["rpd"])

        # Check if we're at 85% of RPM limit
        if rpm_count >= limits["rpm"] * 0.85:
            return True

        # Check if we're at 85% of RPD limit
        if rpd_count >= limits["rpd"] * 0.85:
            return True

        return False

    def force_reset_provider_limits(self, provider_name: Optional[str] = None) -> int:
        """Force reset provider RPM/RPD rate limit counters.

        Parameters
        ----------
        provider_name
            If specified (e.g. 'groq'), resets limits for that provider only.
            If None, clears all rate limit counters across all providers.

        Returns
        -------
        int
            Number of model limit entries reset.
        """
        reset_count = 0
        if provider_name:
            prefix = f"{provider_name.lower()}_"
            for key in list(self.request_counts.keys()):
                if key.startswith(prefix) or key == provider_name.lower():
                    self.request_counts[key] = {"rpm": [], "rpd": []}
                    reset_count += 1
        else:
            reset_count = len(self.request_counts)
            self.request_counts.clear()
        return reset_count

    # ------------------------------------------------------------------
    # Model health tracking
    # ------------------------------------------------------------------
    def mark_model_unhealthy(self, provider: str, model: str) -> None:
        """Mark a model as unhealthy"""
        key = f"{provider.lower()}_{model.lower()}"
        self.model_status[key] = {
            "healthy": False,
            "last_check": time.time()
        }

    def mark_model_healthy(self, provider: str, model: str) -> None:
        """Mark a model as healthy again after a successful request."""
        key = f"{provider.lower()}_{model.lower()}"
        self.model_status[key] = {
            "healthy": True,
            "last_check": time.time()
        }

    def is_model_healthy(self, provider: str, model: str) -> bool:
        """Check if a model is healthy"""
        key = f"{provider.lower()}_{model.lower()}"
        return self.model_status[key]["healthy"]

    def get_healthy_models(self, provider: str) -> List[str]:
        """Get list of healthy models for a provider (models seen so far)."""
        provider = provider.lower()
        prefix = f"{provider}_"
        healthy_models = []
        for key in list(self.request_counts.keys()):
            if key.startswith(prefix):
                model = key[len(prefix):]
                if model and self.is_model_healthy(provider, model):
                    healthy_models.append(model)
        return healthy_models


class QuotaResetWorker:
    """Background worker that executes scheduled quota resets for providers."""

    def __init__(self, rate_limiter: RateLimiter, check_interval: float = 15.0):
        self.rate_limiter = rate_limiter
        self.check_interval = check_interval
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Start the background quota reset timer worker."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_worker,
            name="moon-quota-reset-worker",
            daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop the background quota reset timer worker."""
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=5)
        self._thread = None

    def _run_worker(self) -> None:
        """Worker thread loop checking and flushing expired rate limit counters."""
        while not self._stop_event.is_set():
            try:
                for key in list(self.rate_limiter.request_counts.keys()):
                    self.rate_limiter._clean_old_requests(key)
            except Exception:
                pass
            self._stop_event.wait(self.check_interval)

    @staticmethod
    def get_seconds_until_utc_midnight() -> float:
        """Calculate exact seconds remaining until next 00:00 UTC midnight."""
        now_utc = datetime.now(timezone.utc)
        tomorrow_utc = datetime(now_utc.year, now_utc.month, now_utc.day, tzinfo=timezone.utc) + timedelta(days=1)
        return (tomorrow_utc - now_utc).total_seconds()
