"""Evaluator worker command entrypoint."""

import asyncio
import logging
import signal
import uuid

from .config import load_settings
from .worker import WorkerState, heartbeat_loop, scheduler


async def _run() -> None:
    settings = load_settings()
    instance_id = uuid.uuid4().hex
    state = WorkerState()
    tasks = [
        asyncio.create_task(scheduler(settings, instance_id, state)),
        asyncio.create_task(heartbeat_loop(settings, instance_id, state)),
    ]

    def cancel_tasks() -> None:
        for task in tasks:
            task.cancel()

    loop = asyncio.get_running_loop()
    for exit_signal in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(exit_signal, cancel_tasks)
    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        logging.getLogger(__name__).info("evaluator_shutdown")
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(_run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
