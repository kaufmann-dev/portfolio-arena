"""Evaluator worker command entrypoint."""

import argparse
import asyncio
import logging
import signal
import time
from pathlib import Path

from .config import EvaluatorSettings, load_settings
from .worker import heartbeat, scheduler


def _healthcheck(path: Path, max_age_seconds: int) -> int:
    try:
        age = time.time() - path.stat().st_mtime
    except FileNotFoundError:
        return 1
    return 0 if age <= max_age_seconds else 1


async def _run_scheduler(settings: EvaluatorSettings) -> None:
    task = asyncio.create_task(scheduler(settings))
    heartbeat_task = asyncio.create_task(heartbeat(settings))
    loop = asyncio.get_running_loop()
    for exit_signal in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(exit_signal, task.cancel)
    try:
        await task
    except asyncio.CancelledError:
        logging.getLogger(__name__).info("evaluator_shutdown")
    finally:
        heartbeat_task.cancel()
        await asyncio.gather(heartbeat_task, return_exceptions=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--healthcheck", action="store_true")
    args = parser.parse_args()
    settings = load_settings()
    if args.healthcheck:
        return _healthcheck(settings.heartbeat_file, max(settings.poll_seconds * 3, 180))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(_run_scheduler(settings))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
