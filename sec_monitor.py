from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from secmon.config import get_settings
from secmon.logging_utils import configure_logging
from secmon.pipeline import IngestionService


def run_worker(service: IngestionService) -> None:
    settings = get_settings()
    while True:
        stats = service.ingest_once()
        print(json.dumps(stats, ensure_ascii=False))
        time.sleep(settings.poll_interval_seconds)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Industrial SEC filing monitor")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("ingest-once", help="Poll the SEC feed once and store results")
    subparsers.add_parser("run-worker", help="Run the continuous polling worker")

    export_parser = subparsers.add_parser(
        "export-llm-dataset",
        help="Export filings, analyses, and chunks into JSONL for future LLM training or RAG",
    )
    export_parser.add_argument(
        "--output",
        default="data/llm_corpus.jsonl",
        help="Output JSONL path",
    )

    return parser


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    service = IngestionService()

    parser = build_parser()
    args = parser.parse_args()
    command = args.command or "run-worker"

    if command == "ingest-once":
        print(json.dumps(service.ingest_once(), ensure_ascii=False, indent=2))
        return

    if command == "export-llm-dataset":
        output_path = Path(args.output).resolve()
        count = service.export_llm_dataset(output_path)
        print(json.dumps({"output": str(output_path), "records": count}, ensure_ascii=False, indent=2))
        return

    run_worker(service)


if __name__ == "__main__":
    main()

