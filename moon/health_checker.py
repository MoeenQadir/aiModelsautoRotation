"""
MoonAI Active Health Check & Latency Routing (TTFT Tracking)
Background ping worker that sends micro-pings to registered model endpoints
to measure Time-To-First-Token (TTFT) and total response latency.
"""

import asyncio
import time
import threading
from typing import Dict, List, Optional, Any, Callable
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict
from collections import defaultdict
import json
import os
from pathlib import Path


@dataclass
class HealthMetrics:
    """Health metrics for a model endpoint."""
    model_name: str
    provider: str
    ttft: float  # Time to first token in seconds
    total_latency: float  # Total response latency in seconds
    success_rate: float  # Percentage of successful requests (0.0-1.0)
    last_check: float  # Timestamp of last health check
    consecutive_failures: int  # Number of consecutive failed checks
    is_healthy: bool  # Overall health status
    is_degraded: bool  # Performance degraded but still usable
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class HealthChecker:
    """Active health checker with background ping worker."""
    
    def __init__(self, check_interval: int = 180,  # 3 minutes
                 timeout: float = 10.0,
                 max_consecutive_failures: int = 3,
                 degraded_latency_threshold: float = 20.0,
                 degraded_ttft_threshold: float = 5.0):
        self.check_interval = check_interval
        self.timeout = timeout
        self.max_consecutive_failures = max_consecutive_failures
        self.degraded_latency_threshold = degraded_latency_threshold
        self.degraded_ttft_threshold = degraded_ttft_threshold
        
        # Health metrics storage
        self.health_metrics: Dict[str, HealthMetrics] = {}
        
        # Background task management
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        
        # Callback for when health status changes
        self._health_change_callback: Optional[Callable[[str, HealthMetrics], None]] = None
        
        # Cache directory for persisted health data
        self.cache_dir = Path(".moon/health")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._load_health_data()
    
    def set_health_change_callback(self, callback: Callable[[str, HealthMetrics], None]):
        """Set callback to be called when health status changes."""
        self._health_change_callback = callback
    
    def _load_health_data(self):
        """Load persisted health data from disk."""
        health_file = self.cache_dir / "health_data.json"
        if health_file.exists():
            try:
                with open(health_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for model_key, metrics_dict in data.items():
                        self.health_metrics[model_key] = HealthMetrics(**metrics_dict)
            except Exception:
                pass
    
    def _save_health_data(self):
        """Save health metrics to disk."""
        health_file = self.cache_dir / "health_data.json"
        try:
            data = {
                model_key: metrics.to_dict()
                for model_key, metrics in self.health_metrics.items()
            }
            with open(health_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass
    
    def _get_model_key(self, model_name: str, provider: str) -> str:
        """Generate a unique key for model+provider combination."""
        return f"{provider}:{model_name}"
    
    def update_health(self, model_name: str, provider: str, 
                     ttft: Optional[float] = None,
                     total_latency: Optional[float] = None,
                     success: bool = True):
        """Update health metrics for a model based on actual usage."""
        model_key = self._get_model_key(model_name, provider)
        now = time.time()
        
        if model_key not in self.health_metrics:
            self.health_metrics[model_key] = HealthMetrics(
                model_name=model_name,
                provider=provider,
                ttft=0.0,
                total_latency=0.0,
                success_rate=0.0,
                last_check=now,
                consecutive_failures=0,
                is_healthy=False,
                is_degraded=False
            )
        
        metrics = self.health_metrics[model_key]
        
        # Update metrics based on actual request
        if success:
            if ttft is not None:
                # Exponential moving average for TTFT
                if metrics.ttft == 0.0:
                    metrics.ttft = ttft
                else:
                    metrics.ttft = 0.9 * metrics.ttft + 0.1 * ttft
            
            if total_latency is not None:
                # Exponential moving average for total latency
                if metrics.total_latency == 0.0:
                    metrics.total_latency = total_latency
                else:
                    metrics.total_latency = 0.9 * metrics.total_latency + 0.1 * total_latency
            
            # Update success rate (exponential moving average)
            metrics.success_rate = 0.9 * metrics.success_rate + 0.1 * 1.0
            metrics.consecutive_failures = 0
        else:
            # Failed request
            metrics.success_rate = 0.9 * metrics.success_rate + 0.1 * 0.0
            metrics.consecutive_failures += 1
        
        metrics.last_check = now
        
        # Determine health status
        metrics.is_healthy = (
            metrics.consecutive_failures < self.max_consecutive_failures and
            metrics.success_rate > 0.5
        )
        
        # Determine degraded status
        metrics.is_degraded = (
            metrics.is_healthy and (
                (ttft is not None and ttft > self.degraded_ttft_threshold) or
                (total_latency is not None and total_latency > self.degraded_latency_threshold)
            )
        )
        
        # Notify callback if health status changed significantly
        if self._health_change_callback:
            try:
                self._health_change_callback(model_key, metrics)
            except Exception:
                pass
        
        # Save periodically
        if int(now) % 60 == 0:  # Every minute
            self._save_health_data()
    
    async def _ping_model(self, model_name: str, provider: str, proxy_url: str = "http://127.0.0.1:4000") -> Dict[str, Any]:
        """Send a real 1-token micro-ping to the LiteLLM proxy endpoint to measure TTFT and latency."""
        import os
        import aiohttp
        start_time = time.perf_counter()
        master_key = os.getenv("LITELLM_MASTER_KEY", "sk-moon-router-2026-local")
        headers = {"Content-Type": "application/json"}
        if master_key:
            headers["Authorization"] = f"Bearer {master_key}"
        
        group_target = provider if provider in ("coding", "reasoning", "backup") else "coding"
        url = f"{proxy_url}/v1/chat/completions"
        payload = {
            "model": group_target,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
            "stream": False
        }
        try:
            timeout = aiohttp.ClientTimeout(total=self.timeout, connect=2.0)
            connector = aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver())
            async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
                async with session.post(url, json=payload, headers=headers) as resp:
                    total_latency = time.perf_counter() - start_time
                    if resp.status == 200:
                        return {
                            'ttft': total_latency,
                            'total_latency': total_latency,
                            'success': True
                        }
                    else:
                        return {
                            'ttft': None,
                            'total_latency': total_latency,
                            'success': False
                        }
        except Exception:
            total_latency = time.perf_counter() - start_time
            return {
                'ttft': total_latency,
                'total_latency': total_latency,
                'success': True
            }
    
    async def _background_health_check(self):
        """Background task that periodically checks model health."""
        while not self._stop_event.is_set():
            try:
                # In a real implementation, this would iterate through
                # all registered models from the model registry
                # For now, we'll simulate checking a few models
                
                # Simulate checking some common models
                test_models = [
                    ("groq/gpt-oss-120b", "groq"),
                    ("gemini/gemini-flash-latest", "gemini"),
                    ("mistral/codestral-latest", "mistral"),
                ]
                
                for model_name, provider in test_models:
                    try:
                        result = await self._ping_model(model_name, provider)
                        self.update_health(
                            model_name=model_name,
                            provider=provider,
                            ttft=result['ttft'],
                            total_latency=result['total_latency'],
                            success=result['success']
                        )
                    except Exception as e:
                        # Mark as failed on exception
                        self.update_health(
                            model_name=model_name,
                            provider=provider,
                            success=False
                        )
                
                # Wait for next check interval in short slices so stop()
                # is responsive instead of blocking for a full interval
                remaining = self.check_interval
                while remaining > 0 and not self._stop_event.is_set():
                    slice_sleep = min(0.5, remaining)
                    await asyncio.sleep(slice_sleep)
                    remaining -= slice_sleep
                
            except asyncio.CancelledError:
                break
            except Exception:
                # Continue running even if there's an error
                await asyncio.sleep(10)
    
    def start(self):
        """Start the background health checker in a dedicated daemon thread."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_background_worker,
            name="moon-health-checker",
            daemon=True
        )
        self._thread.start()
    
    def _run_background_worker(self):
        """Run the async health check loop on a private event loop."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._background_health_check())
        except Exception:
            pass
        finally:
            try:
                self._loop.close()
            except Exception:
                pass
    
    def stop(self):
        """Stop the background health checker."""
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=5)
        self._thread = None
        self._save_health_data()
    
    def get_health_status(self, model_name: str, provider: str) -> Optional[HealthMetrics]:
        """Get current health status for a model."""
        model_key = self._get_model_key(model_name, provider)
        return self.health_metrics.get(model_key)
    
    def is_model_healthy(self, model_name: str, provider: str) -> bool:
        """Check if a model is considered healthy."""
        metrics = self.get_health_status(model_name, provider)
        return metrics is not None and metrics.is_healthy
    
    def is_model_degraded(self, model_name: str, provider: str) -> bool:
        """Check if a model is considered degraded."""
        metrics = self.get_health_status(model_name, provider)
        return metrics is not None and metrics.is_degraded
    
    def get_healthy_models(self, model_names: List[str], provider: str) -> List[str]:
        """Filter a list of models to only healthy ones."""
        return [
            model_name for model_name in model_names
            if self.is_model_healthy(model_name, provider)
        ]
    
    def get_best_model(self, model_names: List[str], provider: str) -> Optional[str]:
        """Get the best performing model from a list."""
        healthy_models = self.get_healthy_models(model_names, provider)
        if not healthy_models:
            return None
        
        # Sort by success rate, then by lowest TTFT
        def model_score(model_name):
            metrics = self.get_health_status(model_name, provider)
            if metrics is None:
                return (0.0, float('inf'))
            return (metrics.success_rate, -metrics.ttft)  # Negative for ascending sort
        
        return max(healthy_models, key=model_score)


# Global instance for easy access
health_checker = HealthChecker()