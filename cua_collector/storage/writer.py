import logging
import os
from pathlib import Path
from threading import Lock
from queue import Queue, Empty
from typing import Optional

from ..models import CaptureEvent

logger = logging.getLogger(__name__)

_DEFAULT_MAX_QUEUE_SIZE = 50000
_BATCH_SIZE = 20
_BATCH_TIMEOUT = 0.05
_SCREENSHOTS_PER_SUBDIR = 1000


class SessionWriter:
    def __init__(self, session_dir: Path):
        self.session_dir = session_dir
        self.screenshots_dir = session_dir / "screenshots"
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)
        self.jsonl_path = session_dir / "trajectory.jsonl"
        self._file = None
        self._lock = Lock()
        self._screenshot_counter = 0
        self._event_count = 0

    def open(self):
        self._file = open(self.jsonl_path, "a", buffering=1)

    def write_event(self, event: CaptureEvent):
        with self._lock:
            if self._file:
                self._file.write(event.to_jsonl() + "\n")
                self._event_count += 1

    def write_event_batch(self, events: list):
        with self._lock:
            if self._file:
                lines = "\n".join(e.to_jsonl() for e in events) + "\n"
                self._file.write(lines)
                self._event_count += len(events)

    def save_screenshot(self, png_data: bytes) -> str:
        self._screenshot_counter += 1
        subdir_num = (self._screenshot_counter - 1) // _SCREENSHOTS_PER_SUBDIR
        subdir = self.screenshots_dir / f"{subdir_num:03d}"
        subdir.mkdir(exist_ok=True)
        filename = f"{self._screenshot_counter:06d}.png"
        path = subdir / filename
        with open(path, "wb") as f:
            f.write(png_data)
        return str(path.relative_to(self.session_dir))

    def close(self):
        with self._lock:
            if self._file:
                self._file.close()
                self._file = None

    @property
    def event_count(self) -> int:
        return self._event_count

    @property
    def screenshot_count(self) -> int:
        return self._screenshot_counter


class AsyncWriter:
    def __init__(self, session_dir: Path, max_queue_size: int = _DEFAULT_MAX_QUEUE_SIZE):
        self._writer = SessionWriter(session_dir)
        self._queue: Queue = Queue(maxsize=max_queue_size)
        self._max_queue_size = max_queue_size
        self._running = False
        self._dropped_count = 0

    def open(self):
        self._writer.open()
        self._running = True

    def put(self, event: CaptureEvent):
        try:
            self._queue.put_nowait(event)
        except Exception:
            self._dropped_count += 1
            qsize = self._queue.qsize()
            if self._dropped_count % 100 == 1:
                logger.warning(
                    "Writer queue full (%d/%d), dropped %d events. "
                    "Consider increasing storage.max_queue_size or reducing capture rate.",
                    qsize, self._max_queue_size, self._dropped_count,
                )

    def save_screenshot(self, png_data: bytes) -> str:
        return self._writer.save_screenshot(png_data)

    @property
    def queue_fill_ratio(self) -> float:
        return self._queue.qsize() / self._max_queue_size

    def flush(self):
        events = []
        while True:
            try:
                events.append(self._queue.get_nowait())
            except Empty:
                break
        if events:
            self._writer.write_event_batch(events)

    def run(self):
        batch = []
        while self._running:
            try:
                event = self._queue.get(timeout=_BATCH_TIMEOUT)
                batch.append(event)
                if len(batch) >= _BATCH_SIZE:
                    self._writer.write_event_batch(batch)
                    batch = []
            except Empty:
                if batch:
                    self._writer.write_event_batch(batch)
                    batch = []

        if batch:
            self._writer.write_event_batch(batch)

    def close(self):
        if not self._running:
            self.flush()
            return
        self._running = False
        import time
        time.sleep(0.05)
        self.flush()
        self._writer.close()

    @property
    def event_count(self) -> int:
        return self._writer.event_count

    @property
    def screenshot_count(self) -> int:
        return self._writer.screenshot_count

    @property
    def dropped_count(self) -> int:
        return self._dropped_count
