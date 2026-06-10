"""CLI entry point for complaint classifier."""

import argparse
import logging
import sys
import time
from pathlib import Path

from .cache import Cache
from .classifier import classify_batch, classify_single
from .config import load_config
from .progress import ProgressTracker, load_checkpoint
from .reader import detect_archive_column, read_excel
from .summary import generate_summary, write_summary
from .models import AppConfig, ClassifyResult, ComplaintRow
from .writer import write_results, write_test_results


def setup_logging(log_dir: str) -> None:
    """Configure structured logging to file and stderr."""
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    log_path = Path(log_dir) / f"run_{timestamp}.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(sys.stderr),
        ],
    )


def cmd_test(args: argparse.Namespace) -> None:
    """Run a sample test with concurrent classification and incremental saves."""
    config = load_config(args.config)
    headers, all_rows = read_excel(
        args.input,
        args.sheet if hasattr(args, "sheet") and args.sheet else None,
    )

    sample_size = getattr(args, "sample", 5)
    sample_rows = all_rows[:sample_size]

    cache = Cache(str(Path(args.output) / "cache"))
    concurrency = config["runtime"]["concurrency"]

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(args.input).stem

    classified_path = output_dir / f"{stem}_test_{sample_size}_classified.xlsx"
    summary_path = output_dir / f"{stem}_test_{sample_size}_summary.xlsx"

    print(f"\n[小样本测试] 共 {len(sample_rows)} 条 | 并发: {concurrency}")
    print(f"[输出文件] {classified_path}\n")

    # Process in chunks, saving incrementally
    chunk_size = 50
    all_results: list[ClassifyResult] = []
    total_chunks = (len(sample_rows) + chunk_size - 1) // chunk_size
    start_time = time.time()

    for chunk_idx in range(total_chunks):
        chunk_start = chunk_idx * chunk_size
        chunk_end = min(chunk_start + chunk_size, len(sample_rows))
        chunk = sample_rows[chunk_start:chunk_end]

        print(f"--- 批次 {chunk_idx+1}/{total_chunks} (行 {chunk_start+1}-{chunk_end}) ---")

        chunk_results = classify_batch(
            chunk, config, cache,
            concurrency=concurrency,
        )

        # Print results
        for r in chunk_results:
            print(
                f"  行 {r['row_index']}: [{r['confidence']}] {r['primary_category']} > "
                f"{r['secondary_category']} | Token: {r['token_usage']}"
            )

        all_results.extend(chunk_results)

        # Incremental save
        write_test_results(str(classified_path), sample_rows[:chunk_end], all_results)
        summary = generate_summary(sample_rows[:chunk_end], all_results)
        write_summary(summary, str(summary_path), all_results, sample_rows[:chunk_end])

        elapsed = time.time() - start_time
        rate = (chunk_end) / elapsed if elapsed > 0 else 0
        remaining = (len(sample_rows) - chunk_end) / rate if rate > 0 else 0
        print(f"  [进度] {chunk_end}/{len(sample_rows)} ({chunk_end*100//len(sample_rows)}%)"
              f" | 速率: {rate:.1f}条/秒 | 预计剩余: {remaining:.0f}秒\n")

    total_time = time.time() - start_time
    print(f"\n[完成] 总耗时: {total_time:.0f}秒 | 速率: {len(sample_rows)/total_time:.1f}条/秒")
    print(f"[缓存] 命中: {cache.hits}/{cache.total} (命中率: {cache.hit_rate:.1f}%)")
    print(f"\n[输出文件] 分类结果: {classified_path}")
    print(f"[输出文件] 汇总统计: {summary_path}")

    cache.close()


def cmd_run(args: argparse.Namespace) -> None:
    """Execute full classification pipeline (concurrent)."""
    logger = logging.getLogger("run")

    config: AppConfig = load_config(args.config)
    logger.info("Config loaded from %s", args.config)

    input_path = args.input
    sheet = args.sheet if hasattr(args, "sheet") and args.sheet else None
    headers, rows = read_excel(input_path, sheet)

    archive_col = detect_archive_column(headers, config["runtime"]["archive_column_name"])
    logger.info("Detected column '%s' at index %d", config["runtime"]["archive_column_name"], archive_col)

    valid_rows: list[ComplaintRow] = [r for r in rows if r.归档意见.strip()]
    empty_count = len(rows) - len(valid_rows)

    print(f"\n[文件信息] {Path(input_path).name}")
    print(f"[数据统计] 总行数: {len(rows)} | 有效归档意见: {len(valid_rows)} | 空值: {empty_count}")
    print(f"[LLM配置] {config['llm']['model']} | 并发: {config['runtime']['concurrency']}")

    cache = Cache(str(Path(args.output) / "cache"))

    start_idx = 0
    existing_results: list[ClassifyResult] = []
    if not getattr(args, "no_resume", False):
        cp = load_checkpoint(str(Path(args.output) / "checkpoints"))
        if cp and cp.get("completed_results"):
            existing_results = cp["completed_results"]
            start_idx = cp.get("last_row_index", 0)
            print(f"[断点续跑] 已加载 {len(existing_results)} 条结果，从第 {start_idx} 行继续")

    remaining = valid_rows[start_idx:]
    total = len(remaining)

    print(f"\n[开始分类] 待处理: {total} 条 (并发: {config['runtime']['concurrency']})...\n")

    progress = ProgressTracker(
        total=total,
        checkpoint_interval=config["runtime"]["checkpoint_interval"],
        checkpoint_dir=str(Path(args.output) / "checkpoints"),
    )

    def on_prog(completed: int, total_count: int) -> None:
        progress.update(1)

    new_results = classify_batch(
        remaining, config, cache,
        concurrency=config["runtime"]["concurrency"],
        on_progress=on_prog,
    )

    progress.close()

    all_results: list[ClassifyResult] = existing_results + new_results

    print(f"\n[缓存统计] 命中: {cache.hits} | 总计: {cache.total} | 命中率: {cache.hit_rate:.1f}%")

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(input_path).stem

    classified_path = output_dir / f"{stem}_classified.xlsx"
    write_results(input_path, str(classified_path), rows, all_results)
    print(f"\n[输出文件] 分类结果: {classified_path}")

    summary = generate_summary(rows, all_results)
    summary_path = output_dir / f"{stem}_summary.xlsx"
    write_summary(summary, str(summary_path), all_results, rows)
    print(f"[输出文件] 汇总统计: {summary_path}")

    cache.close()


def cmd_check_config(args: argparse.Namespace) -> None:
    """Validate config.yaml."""
    config = load_config(args.config)
    print("Config is valid.")
    print(f"  Provider: {config['llm']['provider']}")
    print(f"  Model: {config['llm']['model']}")
    cats = config["classify"]["categories"]
    total_sec = sum(len(v) for v in cats.values())
    print(f"  Categories: {len(cats)} primary, {total_sec} secondary")
    print(f"  Concurrency: {config['runtime']['concurrency']}")
    print(f"  Checkpoint interval: {config['runtime']['checkpoint_interval']}")


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="投诉归档意见AI分类工具 v0.1.0")
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    run_parser = subparsers.add_parser("run", help="Execute full classification")
    run_parser.add_argument("input", help="Input Excel file path")
    run_parser.add_argument("--config", default="config.yaml", help="Config file path")
    run_parser.add_argument("--output", default="./output", help="Output directory")
    run_parser.add_argument("--sheet", default=None, help="Sheet name")
    run_parser.add_argument("--no-cache", action="store_true", help="Disable cache")
    run_parser.add_argument("--no-resume", action="store_true", help="Disable resume")

    test_parser = subparsers.add_parser("test", help="Test with sample")
    test_parser.add_argument("input", help="Input Excel file path")
    test_parser.add_argument("--config", default="config.yaml", help="Config file path")
    test_parser.add_argument("--output", default="./output", help="Output directory")
    test_parser.add_argument("--sample", type=int, default=5, help="Sample size")

    check_parser = subparsers.add_parser("check-config", help="Validate config.yaml")
    check_parser.add_argument("--config", default="config.yaml", help="Config file path")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    setup_logging("logs")

    if args.command == "run":
        cmd_run(args)
    elif args.command == "test":
        cmd_test(args)
    elif args.command == "check-config":
        cmd_check_config(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
