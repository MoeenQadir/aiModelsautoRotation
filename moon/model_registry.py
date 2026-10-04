"""
MoonAI Model Capability Registry
Handles capability-aware model selection and handoff decisions.
"""

import json
import yaml
from pathlib import Path
from typing import List, Dict, Optional, Any
from datetime import datetime
from .rate_limiter import RateLimiter

class ModelCapability:
    """Represents a model's capabilities and characteristics."""
    
    def __init__(self, name: str, capabilities: List[str], 
                 max_output_tokens: int = 8192, context_window: int = 32768,
                 group: str = None):
        self.name = name
        self.capabilities = capabilities
        self.max_output_tokens = max_output_tokens
        self.context_window = context_window
        self.group = group  # coding, reasoning, backup
        self.last_used = None
        self.failure_count = 0
        self.cooldown_until = None
    
    def has_capability(self, capability: str) -> bool:
        return capability in self.capabilities
    
    def has_all_capabilities(self, capabilities: List[str]) -> bool:
        return all(c in self.capabilities for c in capabilities)
    
    def is_available(self) -> bool:
        """Check if model is currently available (not in cooldown)."""
        if self.cooldown_until:
            from datetime import datetime
            if datetime.now() < self.cooldown_until:
                return False
        return True
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "capabilities": self.capabilities,
            "max_output_tokens": self.max_output_tokens,
            "context_window": self.context_window,
            "group": self.group,
            "last_used": self.last_used,
            "failure_count": self.failure_count,
            "cooldown_until": self.cooldown_until.isoformat() if self.cooldown_until else None
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ModelCapability':
        model = cls.__new__(cls)
        for key, value in data.items():
            if hasattr(model, key):
                if key == "cooldown_until" and value:
                    setattr(model, key, datetime.fromisoformat(value))
                else:
                    setattr(model, key, value)
        return model


class ModelCapabilityRegistry:
    """Manages model capabilities and selection logic."""

    def __init__(self, config_path: str = ".moon/config/orchestration.yaml"):
        self.rate_limiter = RateLimiter()
        self.config_path = Path(config_path)
        self.models: Dict[str, List[ModelCapability]] = {}
        self.failure_config: Dict[str, Any] = {}
        self.capability_descriptions: Dict[str, Any] = {}
        self._load_config()
    
    def _load_config(self):
        """Load model capabilities from config."""
        if not self.config_path.exists():
            # Use default capabilities if config doesn't exist
            self._load_default_config()
            return
        
        with open(self.config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
        
        # Load models
        models_config = config.get('models', {})
        for group, models in models_config.items():
            self.models[group] = []
            for model_config in models:
                model = ModelCapability(
                    name=model_config['name'],
                    capabilities=model_config.get('capabilities', []),
                    max_output_tokens=model_config.get('max_output_tokens', 8192),
                    context_window=model_config.get('context_window', 32768),
                    group=group
                )
                self.models[group].append(model)
        
        # Load failure classification
        self.failure_config = config.get('failure_classification', {})
        
        # Load capability descriptions
        self.capability_descriptions = config.get('capabilities', {})
    
    def _load_default_config(self):
        """Load default model capabilities if no config file exists."""
        default_models = {
            'coding': [
                {"name": "groq/gpt-oss-120b", "capabilities": ["coding", "code_generation", "reasoning", "terminal_execution", "tool_use", "file_read", "file_write", "shell", "testing", "debugging"], "max_output_tokens": 65536, "context_window": 131072},
                {"name": "cerebras/gpt-oss-120b", "capabilities": ["coding", "code_generation", "reasoning", "terminal_execution", "tool_use", "file_read", "file_write", "shell", "testing"], "max_output_tokens": 32768, "context_window": 65536},
                {"name": "mistral/codestral-latest", "capabilities": ["coding", "code_generation", "terminal_execution", "tool_use", "file_read", "file_write", "shell", "testing", "debugging"], "max_output_tokens": 16384, "context_window": 262144}
            ],
            'reasoning': [
                {"name": "gemini/gemini-flash-latest", "capabilities": ["reasoning", "planning", "long_context", "coding", "code_generation", "terminal_execution", "tool_use", "file_read", "file_write"], "max_output_tokens": 65536, "context_window": 1048576},
                {"name": "cerebras/qwen-3.8-27b", "capabilities": ["reasoning", "coding", "code_generation", "tool_use", "file_read", "file_write"], "max_output_tokens": 16384, "context_window": 65536}
            ],
            'backup': [
                {"name": "gemini/gemini-flash-latest", "capabilities": ["reasoning", "coding", "code_generation", "terminal_execution", "tool_use", "file_read", "file_write"], "max_output_tokens": 16384, "context_window": 1048576},
                {"name": "openrouter/north-mini-code:free", "capabilities": ["coding", "code_generation", "file_read", "file_write", "testing"], "max_output_tokens": 16384, "context_window": 32768}
            ]
        }
        
        for group, models in default_models.items():
            self.models[group] = []
            for model_config in models:
                model = ModelCapability(
                    name=model_config['name'],
                    capabilities=model_config['capabilities'],
                    max_output_tokens=model_config.get('max_output_tokens', 8192),
                    context_window=model_config.get('context_window', 32768),
                    group=group
                )
                self.models[group].append(model)
    
    def get_model_by_name(self, model_name: str) -> Optional[ModelCapability]:
        """Find a model by its name."""
        for group, models in self.models.items():
            for model in models:
                if model.name == model_name:
                    return model
        return None
    
    def find_models_by_capability(self, capability: str) -> List[ModelCapability]:
        """Find all models that have a specific capability."""
        results = []
        for group, models in self.models.items():
            for model in models:
                if model.has_capability(capability) and model.is_available():
                    results.append(model)
        return results
    
    def find_models_by_capabilities(self, capabilities: List[str]) -> List[ModelCapability]:
        """Find all models that have all specified capabilities."""
        results = []
        for group, models in self.models.items():
            for model in models:
                if model.has_all_capabilities(capabilities) and model.is_available():
                    results.append(model)
        return results
    
    def select_model_for_capability(self, capability: str,
                                    prefer_group: str = None,
                                    context_size: int = 0) -> Optional[ModelCapability]:
        """Select the best model for a specific capability."""
        candidates = self.find_models_by_capability(capability)
        
        if not candidates:
            return None
        
        # Filter by preferred group if specified
        if prefer_group:
            group_candidates = [m for m in candidates if m.group == prefer_group]
            if group_candidates:
                candidates = group_candidates
        
        # Filter by context size if needed
        if context_size > 0:
            context_candidates = [m for m in candidates if m.context_window >= context_size]
            if context_candidates:
                candidates = context_candidates
        
        # Filter by rate limits - exclude models that are approaching limits
        healthy_candidates = []
        for model in candidates:
            # Parse model name to get provider and model identifier
            if '/' in model.name:
                provider, model_name = model.name.split('/', 1)
                # Check if we should switch away from this model due to rate limits
                if not self.rate_limiter.should_switch_model(provider.lower(), model_name.lower()):
                    healthy_candidates.append(model)
            else:
                # If we can't parse, assume it's healthy
                healthy_candidates.append(model)
        
        # If all models are rate-limited, fall back to original candidates
        if not healthy_candidates:
            healthy_candidates = candidates
        
        # Select model with most capabilities and least failures
        best_model = max(healthy_candidates, key=lambda m: (
            len(m.capabilities),
            -m.failure_count
        ))
        
        return best_model
    
    def select_model_for_capabilities(self, capabilities: List[str],
                                      prefer_group: str = None,
                                      context_size: int = 0,
                                      skip_provider_id: Optional[str] = None) -> Optional[ModelCapability]:
        """Select the best model for multiple capabilities.

        Parameters
        ----------
        capabilities
            Capabilities the model must have.
        prefer_group
            Prefer a model from this group when possible.
        context_size
            Minimum context window in tokens.
        skip_provider_id
            When set, models whose provider (the ``provider/`` prefix of the
            model name) matches this ID are excluded from selection. Used during
            auth failover so we don't replay a misconfigured key.
        """
        """Select the best model for multiple capabilities."""
        candidates = self.find_models_by_capabilities(capabilities)
        
        if not candidates:
            # Try with fewer capabilities if exact match not found
            if len(capabilities) > 1:
                return self.select_model_for_capabilities(
                    capabilities[:-1], 
                    prefer_group, 
                    context_size
                )
            return None
        
        # Filter by preferred group if specified
        if prefer_group:
            group_candidates = [m for m in candidates if m.group == prefer_group]
            if group_candidates:
                candidates = group_candidates
        
        # Filter by context size if needed
        if context_size > 0:
            context_candidates = [m for m in candidates if m.context_window >= context_size]
            if context_candidates:
                candidates = context_candidates
        
        # Filter by rate limits - exclude models that are approaching limits,
        # and (for auth-failover) skip models from a provider whose key just
        # 401'd so we don't repeat the misconfigured key.
        healthy_candidates = []
        for model in candidates:
            if '/' in model.name:
                provider_part, model_name = model.name.split('/', 1)
                provider_lower = provider_part.lower()
                if skip_provider_id and provider_lower == skip_provider_id.lower():
                    continue
                if not self.rate_limiter.should_switch_model(provider_lower, model_name.lower()):
                    healthy_candidates.append(model)
            else:
                healthy_candidates.append(model)

        if not healthy_candidates:
            healthy_candidates = candidates

        best_model = max(healthy_candidates, key=lambda m: (
            len([c for c in capabilities if m.has_capability(c)]),
            -m.failure_count
        ))
        
        return best_model
    
    def mark_model_failure(self, model_name: str, failure_type: str,
                           cooldown_seconds: int = 300):
        """Mark a model as failed and set cooldown."""
        model = self.get_model_by_name(model_name)
        if model:
            model.failure_count += 1
            model.last_used = datetime.now().isoformat()
            
            # Set cooldown based on failure type
            if failure_type in ("RATE_LIMIT", "PROVIDER_ERROR", "TIMEOUT"):
                import datetime as dt
                model.cooldown_until = dt.datetime.now() + dt.timedelta(seconds=cooldown_seconds)
            
            # Mark model as unhealthy in rate limiter
            self.rate_limiter.mark_model_unhealthy(model.name.split('/')[0], model.name.split('/')[1])
    
    def mark_model_success(self, model_name: str):
        """Mark a model as successful."""
        model = self.get_model_by_name(model_name)
        if model:
            model.last_used = datetime.now().isoformat()
            # Reset failure count on success
            model.failure_count = 0
            
            # Mark model as healthy in rate limiter
            self.rate_limiter.mark_model_healthy(model.name.split('/')[0], model.name.split('/')[1])
    
    def get_model_groups(self) -> List[str]:
        """Get all available model groups."""
        return list(self.models.keys())
    
    def get_models_in_group(self, group: str) -> List[ModelCapability]:
        """Get all models in a specific group."""
        return self.models.get(group, [])
    
    def get_failure_strategy(self, failure_type: str) -> Dict[str, Any]:
        """Get recovery strategy for a failure type."""
        return self.failure_config.get(failure_type, {
            "recovery": "log_and_retry",
            "max_retries": 2
        })
    
    def get_all_models(self) -> List[ModelCapability]:
        """Get all models across all groups."""
        all_models = []
        for group, models in self.models.items():
            all_models.extend(models)
        return all_models
    
    def save_state(self, state_file: str = ".moon/config/model_state.json"):
        """Save model state to disk."""
        state = {
            "models": {
                group: [m.to_dict() for m in models]
                for group, models in self.models.items()
            },
            "saved_at": datetime.now().isoformat()
        }
        state_path = Path(state_file)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        with open(state_path, 'w', encoding='utf-8') as f:
            json.dump(state, f, indent=2)
    
    def load_state(self, state_file: str = ".moon/config/model_state.json"):
        """Load model state from disk."""
        state_path = Path(state_file)
        if not state_path.exists():
            return False
        
        with open(state_path, 'r', encoding='utf-8') as f:
            state = json.load(f)
        
        for group, models in state.get('models', {}).items():
            if group in self.models:
                self.models[group] = [ModelCapability.from_dict(m) for m in models]
        
        return True
