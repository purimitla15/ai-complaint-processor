"""AI Customer Complaint & Case Processing System - command-line entry point.

Usage:
    python main.py
    python main.py --data-dir data --output-dir output --workers 2
"""

import argparse
import sys
import time
from collections import Counter
from dataclasses import replace
from pathlib import Path

from src.config import PROJECT_ROOT, ConfigError, load_settings
from src.ingestion.document_loader import DocumentLoadError
from src.llm.factory import create_llm_client
from src.logger import get_logger, setup_logging
from src.output.writers import OutputWriter
from src.workflow import ComplaintProcessingWorkflow, ProcessingStatus


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Batch-process customer complaint documents with an LLM.")
    parser.add_argument("--data-dir", type=Path, help="Folder containing input documents (default: DATA_DIR)")
    parser.add_argument("--output-dir", type=Path, help="Folder for generated outputs (default: OUTPUT_DIR)")
    parser.add_argument("--workers", type=int, help="Documents processed in parallel (default: MAX_WORKERS)")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        settings = load_settings()
        overrides = {
            key: value
            for key, value in {
                "data_dir": args.data_dir and args.data_dir.resolve(),
                "output_dir": args.output_dir and args.output_dir.resolve(),
                "max_workers": args.workers,
            }.items()
            if value
        }
        settings = replace(settings, **overrides)
        settings.validate()
    except (ConfigError, ValueError) as exc:
        print(f"Configuration error: {exc}\nCopy .env.example to .env and fill in the values.", file=sys.stderr)
        return 2

    setup_logging(settings.log_level, PROJECT_ROOT / "logs")
    logger = get_logger("main")
    logger.info(
        "Starting batch | provider=%s model=%s fallback=%s workers=%d grounding_check=%s",
        settings.llm_provider, settings.llm_model, settings.llm_fallback_model, settings.max_workers,
        settings.grounding_check,
    )

    workflow = ComplaintProcessingWorkflow(
        llm=create_llm_client(settings),
        writer=OutputWriter(settings.output_dir),
        max_workers=settings.max_workers,
        grounding_check=settings.grounding_check,
    )

    started = time.perf_counter()
    try:
        results = workflow.run(settings.data_dir)
    except DocumentLoadError as exc:
        logger.error("%s", exc)
        return 1

    counts = Counter(result.status for result in results)
    print("\n" + "=" * 64)
    print(f"Processed {len(results)} file(s) in {time.perf_counter() - started:.1f}s")
    for status in (ProcessingStatus.SUCCESS, ProcessingStatus.PARTIAL, ProcessingStatus.FAILED, ProcessingStatus.SKIPPED):
        print(f"  {status:<8} {counts.get(status, 0)}")
    print(f"Outputs: {settings.output_dir}")
    print("=" * 64)
    for result in results:
        if result.errors:
            print(f"  ! {result.file_name}: {' ; '.join(result.errors)}")

    # Individual bad files are reported, not fatal; fail the run only if nothing succeeded.
    processed_ok = counts.get(ProcessingStatus.SUCCESS, 0) + counts.get(ProcessingStatus.PARTIAL, 0)
    return 0 if processed_ok > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
