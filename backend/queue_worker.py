"""
queue_worker.py — Async parallel job queue (N concurrent workers).

Provides a singleton JobQueue used by campaign runners and enrichment
pipelines to process leads in parallel without blocking the event loop.

Usage:
  from .queue_worker import get_queue, init_queue

  # In main.py lifespan:
  q = init_queue(n_workers=4)
  await q.start()

  # Enqueue a job:
  await get_queue().enqueue("ENRICH", {"lead_id": 42}, enrich_handler)
"""
import asyncio
import logging
from typing import Any, Callable, Coroutine, Dict, Optional

logger = logging.getLogger(__name__)

_SENTINEL = object()


class JobQueue:
    """Bounded async FIFO queue processed by N concurrent coroutine workers."""

    def __init__(self, n_workers: int = 4, maxsize: int = 1000) -> None:
        self._n_workers = n_workers
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._tasks: list[asyncio.Task] = []
        self._running   = False
        self._completed = 0
        self._failed    = 0

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._tasks = [
            asyncio.create_task(self._worker(i), name=f"job-worker-{i}")
            for i in range(self._n_workers)
        ]
        logger.info("JobQueue started — %d workers", self._n_workers)

    async def stop(self, timeout: float = 30.0) -> None:
        self._running = False
        # Send one sentinel per worker to unblock them
        for _ in range(self._n_workers):
            try:
                self._queue.put_nowait(_SENTINEL)
            except asyncio.QueueFull:
                pass
        try:
            await asyncio.wait_for(
                asyncio.gather(*self._tasks, return_exceptions=True),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            for t in self._tasks:
                t.cancel()
        self._tasks.clear()
        logger.info(
            "JobQueue stopped — completed=%d failed=%d",
            self._completed, self._failed,
        )

    # ── Public enqueue API ────────────────────────────────────────────────────

    async def enqueue(
        self,
        job_type: str,
        payload: Dict[str, Any],
        handler: Callable[[Dict[str, Any]], Coroutine],
    ) -> None:
        """Add a job. Blocks (back-pressure) when queue is full."""
        await self._queue.put((job_type, payload, handler))

    def enqueue_nowait(
        self,
        job_type: str,
        payload: Dict[str, Any],
        handler: Callable[[Dict[str, Any]], Coroutine],
    ) -> bool:
        """Non-blocking add. Returns False if queue is full (drops the job)."""
        try:
            self._queue.put_nowait((job_type, payload, handler))
            return True
        except asyncio.QueueFull:
            logger.warning("JobQueue full — dropped %s job (payload=%s)", job_type, list(payload.keys()))
            return False

    # ── Stats ─────────────────────────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def stats(self) -> Dict[str, Any]:
        return {
            "running":   self._running,
            "workers":   self._n_workers,
            "queued":    self._queue.qsize(),
            "completed": self._completed,
            "failed":    self._failed,
        }

    # ── Worker loop ───────────────────────────────────────────────────────────

    async def _worker(self, worker_id: int) -> None:
        logger.debug("Worker %d: started", worker_id)
        while True:
            item = await self._queue.get()
            try:
                if item is _SENTINEL:
                    break
                job_type, payload, handler = item
                try:
                    await handler(payload)
                    self._completed += 1
                except Exception as exc:
                    self._failed += 1
                    logger.error(
                        "Worker %d: %s job failed — %s (keys=%s)",
                        worker_id, job_type, exc, list(payload.keys()),
                        exc_info=True,
                    )
            finally:
                self._queue.task_done()
        logger.debug("Worker %d: stopped", worker_id)


# ── Module-level singleton ─────────────────────────────────────────────────────

_queue: Optional[JobQueue] = None


def init_queue(n_workers: int = 4, maxsize: int = 1000) -> JobQueue:
    global _queue
    _queue = JobQueue(n_workers=n_workers, maxsize=maxsize)
    return _queue


def get_queue() -> Optional[JobQueue]:
    return _queue
