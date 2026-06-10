"""SHA256-based result cache backed by diskcache."""

import hashlib
import json
from pathlib import Path

try:
    from diskcache import Cache as DiskCache
    HAS_DISKCACHE = True
except ImportError:
    HAS_DISKCACHE = False

from .models import ClassifyResult


class Cache:
    """Hash-based cache for LLM classification results.

    Uses diskcache if available, falls back to in-memory dict.
    """

    def __init__(self, db_path: str) -> None:
        """Initialize cache.

        Args:
            db_path: Path to cache directory.
        """
        self._db_path = db_path
        if HAS_DISKCACHE:
            Path(db_path).mkdir(parents=True, exist_ok=True)
            self._store: DiskCache = DiskCache(db_path)
            self._hits = 0
            self._total = 0
        else:
            self._store = {}  # type: ignore[assignment]
            self._hits = 0
            self._total = 0

    def lookup(self, text_hash: str) -> ClassifyResult | None:
        """Look up cached result by text hash.

        Args:
            text_hash: SHA256 hash of the archive opinion text.

        Returns:
            Cached ClassifyResult or None.
        """
        self._total += 1
        if HAS_DISKCACHE:
            raw = self._store.get(text_hash)  # type: ignore[union-attr]
        else:
            raw = self._store.get(text_hash)  # type: ignore[union-attr]
        if raw is not None:
            self._hits += 1
            if isinstance(raw, str):
                return json.loads(raw)  # type: ignore[no-any-return]
            return raw  # type: ignore[return-value]
        return None

    def store(self, text_hash: str, result: ClassifyResult) -> None:
        """Store classification result in cache.

        Args:
            text_hash: SHA256 hash of the archive opinion text.
            result: Classification result to cache.
        """
        if HAS_DISKCACHE:
            self._store[text_hash] = json.dumps(result, ensure_ascii=False)  # type: ignore[index]
        else:
            self._store[text_hash] = result  # type: ignore[index]

    def close(self) -> None:
        """Close cache connection."""
        if HAS_DISKCACHE:
            self._store.close()  # type: ignore[union-attr]

    @property
    def hit_rate(self) -> float:
        """Cache hit rate as percentage."""
        if self._total == 0:
            return 0.0
        return (self._hits / self._total) * 100

    @property
    def hits(self) -> int:
        """Number of cache hits."""
        return self._hits

    @property
    def total(self) -> int:
        """Total cache lookups."""
        return self._total


def compute_hash(text: str) -> str:
    """Compute SHA256 hash of text.

    Args:
        text: Text to hash.

    Returns:
        64-character hex digest string.
    """
    cleaned = text.strip()
    return hashlib.sha256(cleaned.encode("utf-8")).hexdigest()
