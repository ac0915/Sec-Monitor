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


def run_telegram_worker(service: IngestionService) -> None:
    settings = get_settings()
    while True:
        try:
            stats = service.sync_telegram_once()
            if stats["updates_seen"] or stats["errors"]:
                print(json.dumps(stats, ensure_ascii=False))
        except Exception as exc:
            print(json.dumps({"telegram_worker_error": str(exc)}, ensure_ascii=False))
        finally:
            time.sleep(settings.telegram_updates_poll_interval_seconds)


def run_price_worker(service: IngestionService) -> None:
    settings = get_settings()
    while True:
        try:
            stats = service.sync_prices_once()
            print(json.dumps(stats, ensure_ascii=False))
        except Exception as exc:
            print(json.dumps({"price_worker_error": str(exc)}, ensure_ascii=False))
        finally:
            time.sleep(settings.price_poll_interval_seconds)


def run_llm_usage_worker(service: IngestionService) -> None:
    settings = get_settings()
    while True:
        try:
            stats = service.sync_llm_usage_once()
            print(json.dumps(stats, ensure_ascii=False))
        except Exception as exc:
            print(json.dumps({"llm_usage_worker_error": str(exc)}, ensure_ascii=False))
        finally:
            time.sleep(settings.llm_usage_sync_interval_seconds)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Industrial SEC filing monitor")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("ingest-once", help="Poll the SEC feed once and store results")
    subparsers.add_parser("run-worker", help="Run the continuous polling worker")
    subparsers.add_parser("prices-sync-once", help="Fetch and store latest real market prices for the watchlist")
    subparsers.add_parser("run-price-worker", help="Run the continuous price polling worker")
    subparsers.add_parser("llm-usage-sync-once", help="Sync account-level LLM usage where the provider supports it")
    subparsers.add_parser("run-llm-usage-worker", help="Run the continuous LLM usage sync worker")
    subparsers.add_parser("telegram-sync-once", help="Pull Telegram updates once and process commands")
    subparsers.add_parser("run-telegram-worker", help="Run the Telegram assistant long-poll worker")

    embed_parser = subparsers.add_parser("embed-chunks-once", help="Generate and store embeddings for filing chunks")
    embed_parser.add_argument("--limit", type=int, default=get_settings().embedding_batch_limit, help="Maximum chunks to embed")

    label_parser = subparsers.add_parser("seed-label-tasks", help="Seed labeling tasks from stored real filings")
    label_parser.add_argument("--limit", type=int, default=get_settings().eval_default_limit, help="Maximum tasks to create")

    eval_parser = subparsers.add_parser("run-eval", help="Run model evaluation against stored labels")
    eval_parser.add_argument("--limit", type=int, default=get_settings().eval_default_limit, help="Maximum labeled examples to evaluate")

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

    if command == "prices-sync-once":
        print(json.dumps(service.sync_prices_once(), ensure_ascii=False, indent=2))
        return

    if command == "run-price-worker":
        run_price_worker(service)
        return

    if command == "llm-usage-sync-once":
        print(json.dumps(service.sync_llm_usage_once(), ensure_ascii=False, indent=2))
        return

    if command == "run-llm-usage-worker":
        run_llm_usage_worker(service)
        return

    if command == "telegram-sync-once":
        print(json.dumps(service.sync_telegram_once(), ensure_ascii=False, indent=2))
        return

    if command == "run-telegram-worker":
        run_telegram_worker(service)
        return

    if command == "embed-chunks-once":
        print(json.dumps(service.embed_chunks_once(limit=args.limit), ensure_ascii=False, indent=2))
        return

    if command == "seed-label-tasks":
        print(json.dumps(service.seed_label_tasks(limit=args.limit), ensure_ascii=False, indent=2))
        return

    if command == "run-eval":
        print(json.dumps(service.run_evaluation(limit=args.limit), ensure_ascii=False, indent=2))
        return

    run_worker(service)


if __name__ == "__main__":
    main()