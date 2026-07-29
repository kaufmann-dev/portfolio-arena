"""Production supervisor for the web application and integrated evaluator."""

import asyncio
import logging
import os
import secrets
import signal

import httpx

from .log import setup_logging

logger = logging.getLogger(__name__)


async def _terminate(process: asyncio.subprocess.Process | None) -> None:
    if process is None or process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=10)
    except TimeoutError:
        process.kill()
        await process.wait()


async def _wait_for_web(port: int, process: asyncio.subprocess.Process) -> None:
    url = f"http://127.0.0.1:{port}/api/health"
    async with httpx.AsyncClient(timeout=2) as client:
        for _ in range(120):
            if process.returncode is not None:
                raise RuntimeError(f"Web process exited with status {process.returncode}")
            try:
                response = await client.get(url)
                if response.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(1)
    raise RuntimeError("Web application did not become healthy within 120 seconds")


async def _evaluator_supervisor(environment: dict[str, str], stop: asyncio.Event) -> None:
    delay = 1
    while not stop.is_set():
        process = await asyncio.create_subprocess_exec(
            "python",
            "-m",
            "app.evaluator",
            env=environment,
        )
        wait_task = asyncio.create_task(process.wait())
        stop_task = asyncio.create_task(stop.wait())
        done, _ = await asyncio.wait(
            {wait_task, stop_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if stop_task in done:
            await _terminate(process)
            wait_task.cancel()
            await asyncio.gather(wait_task, return_exceptions=True)
            return
        stop_task.cancel()
        await asyncio.gather(stop_task, return_exceptions=True)
        logger.error(
            "evaluator process exited with status %s; restarting in %s seconds",
            process.returncode,
            delay,
        )
        try:
            await asyncio.wait_for(stop.wait(), timeout=delay)
        except TimeoutError:
            pass
        delay = min(delay * 2, 30)


async def run() -> int:
    port = int(os.environ.get("PORT", "8000"))
    internal_token = secrets.token_urlsafe(48)
    base_environment = os.environ.copy()
    base_environment["ARENA_INTERNAL_MCP_API_KEY"] = internal_token
    base_environment.setdefault("CODEX_HOME", "/var/lib/codex")

    web_environment = base_environment.copy()
    for name in ("OPENAI_API_KEY", "CODEX_API_KEY"):
        web_environment.pop(name, None)
    evaluator_environment = base_environment.copy()
    evaluator_environment.pop("OPENAI_API_KEY", None)
    evaluator_environment.pop("CODEX_API_KEY", None)

    web = await asyncio.create_subprocess_exec(
        "uvicorn",
        "app.main:app",
        "--host",
        "0.0.0.0",
        "--port",
        str(port),
        env=web_environment,
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for exit_signal in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(exit_signal, stop.set)

    evaluator_task: asyncio.Task | None = None
    try:
        await _wait_for_web(port, web)
        evaluator_task = asyncio.create_task(_evaluator_supervisor(evaluator_environment, stop))
        web_wait = asyncio.create_task(web.wait())
        stop_wait = asyncio.create_task(stop.wait())
        done, _ = await asyncio.wait(
            {web_wait, stop_wait},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if web_wait in done:
            logger.error("web process exited with status %s", web.returncode)
            stop.set()
            return web.returncode or 1
        await _terminate(web)
        web_wait.cancel()
        await asyncio.gather(web_wait, return_exceptions=True)
        return 0
    finally:
        stop.set()
        await _terminate(web)
        if evaluator_task is not None:
            await evaluator_task


def main() -> int:
    setup_logging()
    return asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(main())
