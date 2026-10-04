"""
MoonAI Semantic Caching Middleware
Caches previous prompts and responses using lightweight prompt hashing
and local embedding distance for cache hits.
"""

import hashlib
import json
import time
import os
from typing import Dict, List, Optional, Any, Tuple
from pathlib import Path
from collections import OrderedDict


class SemanticCache:
    """Semantic cache for prompts and responses with similarity matching."""
    
    def __init__(self, cache_dir: str = ".moon/cache", max_entries: int = 10000,
                 similarity_threshold: float = 0.7):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.max_entries = max_entries
        self.similarity_threshold = similarity_threshold
        
        # In-memory cache with LRU eviction
        self._memory_cache: OrderedDict[str, Dict[str, Any]] = OrderedDict()
        self._embedding_cache: Dict[str, List[float]] = {}
        
        # Load existing cache from disk
        self._load_cache()
    
    def _load_cache(self):
        """Load cache from disk."""
        cache_file = self.cache_dir / "semantic_cache.json"
        if cache_file.exists():
            try:
                with open(cache_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for entry in data.get('entries', []):
                        key = entry['hash']
                        self._memory_cache[key] = entry
                        if 'embedding' in entry:
                            self._embedding_cache[key] = entry['embedding']
            except Exception:
                pass
    
    def _save_cache(self):
        """Save cache to disk."""
        cache_file = self.cache_dir / "semantic_cache.json"
        try:
            entries = list(self._memory_cache.values())
            data = {
                'entries': entries,
                'saved_at': time.time()
            }
            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump(data, f)
        except Exception:
            pass
    
    def _compute_hash(self, prompt: str, model: str, temperature: float = 0.0) -> str:
        """Compute a hash for the prompt, model, and temperature."""
        content = f"{prompt}|{model}|{temperature}"
        return hashlib.sha256(content.encode('utf-8')).hexdigest()[:32]
    
    def _compute_embedding(self, text: str) -> List[float]:
        """Compute a simple embedding for text using character n-grams.
        
        This is a lightweight alternative to full embeddings that works
        well for caching similar prompts.
        """
        # Use character trigrams for a simple but effective similarity measure
        trigrams = {}
        for i in range(len(text) - 2):
            trigram = text[i:i+3].lower()
            trigrams[trigram] = trigrams.get(trigram, 0) + 1
        
        # Convert to normalized vector
        total = sum(trigrams.values())
        if total == 0:
            return []
        
        # Create a fixed-size vector using hashing
        vector_size = 256
        vector = [0.0] * vector_size
        for trigram, count in trigrams.items():
            idx = hash(trigram) % vector_size
            vector[idx] += count / total
        
        return vector
    
    def _cosine_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        """Compute cosine similarity between two vectors."""
        if not vec1 or not vec2 or len(vec1) != len(vec2):
            return 0.0
        
        dot_product = sum(a * b for a, b in zip(vec1, vec2))
        norm1 = sum(a * a for a in vec1) ** 0.5
        norm2 = sum(b * b for b in vec2) ** 0.5
        
        if norm1 == 0 or norm2 == 0:
            return 0.0
        
        return dot_product / (norm1 * norm2)
    
    def get(self, prompt: str, model: str, temperature: float = 0.0) -> Optional[Dict[str, Any]]:
        """Get cached response if available with similarity matching."""
        # First try exact match
        exact_key = self._compute_hash(prompt, model, temperature)
        if exact_key in self._memory_cache:
            entry = self._memory_cache.pop(exact_key)
            self._memory_cache[exact_key] = entry  # Move to end (LRU)
            return entry['response']
        
        # Try semantic similarity match
        prompt_embedding = self._compute_embedding(prompt)
        if not prompt_embedding:
            return None
        
        best_match = None
        best_similarity = 0.0
        
        for key, entry in self._memory_cache.items():
            # Only match same model and similar temperature
            if entry.get('model') != model:
                continue
            if abs(entry.get('temperature', 0.0) - temperature) > 0.1:
                continue
            
            cached_embedding = self._embedding_cache.get(key)
            if not cached_embedding:
                continue
            
            similarity = self._cosine_similarity(prompt_embedding, cached_embedding)
            if similarity > best_similarity and similarity >= self.similarity_threshold:
                best_similarity = similarity
                best_match = entry
        
        if best_match:
            # Move to end (LRU)
            key = self._compute_hash(best_match['prompt'], model, temperature)
            if key in self._memory_cache:
                self._memory_cache.move_to_end(key)
            return best_match['response']
        
        return None
    
    def put(self, prompt: str, response: Dict[str, Any], model: str, temperature: float = 0.0):
        """Store a prompt-response pair in the cache."""
        key = self._compute_hash(prompt, model, temperature)
        
        # Compute embedding for similarity matching
        embedding = self._compute_embedding(prompt)
        
        entry = {
            'hash': key,
            'prompt': prompt,
            'response': response,
            'model': model,
            'temperature': temperature,
            'timestamp': time.time(),
            'embedding': embedding
        }
        
        # Add to memory cache
        self._memory_cache[key] = entry
        if embedding:
            self._embedding_cache[key] = embedding
        
        # Evict oldest entries if over limit
        while len(self._memory_cache) > self.max_entries:
            oldest_key, _ = self._memory_cache.popitem(last=False)
            self._embedding_cache.pop(oldest_key, None)
        
        # Save to disk periodically (every 100 entries)
        if len(self._memory_cache) % 100 == 0:
            self._save_cache()
    
    def clear(self):
        """Clear the cache."""
        self._memory_cache.clear()
        self._embedding_cache.clear()
        cache_file = self.cache_dir / "semantic_cache.json"
        if cache_file.exists():
            cache_file.unlink()
    
    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        return {
            'entries': len(self._memory_cache),
            'max_entries': self.max_entries,
            'similarity_threshold': self.similarity_threshold,
            'memory_usage_mb': len(json.dumps(list(self._memory_cache.values()))) / (1024 * 1024)
        }