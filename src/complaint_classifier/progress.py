"""Progress tracking with tqdm and checkpoint support."""

import json
import time
from pathlib import Path

from tqdm import tqdm

from .models import CheckpointData, ClassifyResult


class ProgressTracker:
    """Track classification progress with tqdm bar and checkpoint saving."""

    def __init__(
        self, total: int, checkpoint_interval: int, checkpoint_dir: str
    ) -> None:
        """Initialize progress tracker.

        Args:
            total: Total number of items to process.
            checkpoint_interval: Save checkpoint every N items.
            checkpoint_dir: Directory for checkpoint files.
        """
        self._total = total
        self._checkpoint_interval = checkpoint_interval
        self._checkpoint_dir = Path(checkpoint_dir)
        self._checkpoint_dir.mkdir(parents=True, exist_ok=True)
        self._bar = tqdm(total=total, desc="分类进度", unit="条")
        self._count = 0
        self._start_time = time.time()

    def update(self, count: int) -> None:
        """Update progress by one item.

        Args:
            count: Increment count (usually 1).
        """
        self._count += count
        self._bar.update(count)

    def save_checkpoint(
        self, results: list[ClassifyResult], last_index: int
    ) -> None:
        """Save checkpoint to disk.

        Args:
            results: Completed classification results so far.
            last_index: Index of last processed row.
        """
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        filename = f"checkpoint_{timestamp}.json"
        filepath = self._checkpoint_dir / filename

        data: CheckpointData = {
            "processed_count": len(results),
            "completed_results": results,
            "last_row_index": last_index,
            "timestamp": timestamp,
        }

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)

    @property
    def should_checkpoint(self) -> bool:
        """Check if it is time to save a checkpoint."""
        return self._count > 0 and self._count % self._checkpoint_interval == 0

    def close(self) -> None:
        """Close the progress bar."""
        self._bar.close()
        elapsed = time.time() - self._start_time
        rate = (self._count / elapsed) if elapsed > 0 else 0
        print(f"\n{'='*50}")
        print(f"分类完成!")
        print(f"  总耗时: {elapsed:.0f}秒")
        print(f"  处理条数: {self._count}")
        print(f"  速率: {rate:.1f}条/秒")
        print(f"{'='*50}")


def load_checkpoint(checkpoint_dir: str) -> CheckpointData | None:
    """Load the most recent checkpoint.

    Args:
        checkpoint_dir: Directory containing checkpoint files.

    Returns:
        Latest CheckpointData or None.
    """
    path = Path(checkpoint_dir)
    if not path.exists():
        return None

    checkpoints = sorted(path.glob("checkpoint_*.json"), reverse=True)
    if not checkpoints:
        return None

    latest = checkpoints[0]
    try:
        with open(latest, "r", encoding="utf-8") as f:
            return json.load(f)  # type: ignore[no-any-return]
    except (json.JSONDecodeError, OSError):
        return None
