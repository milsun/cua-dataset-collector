import logging
import os
from pathlib import Path
from threading import Lock
from queue import Queue, Empty
from typing import Optional

from ..models import CaptureEvent, make_system_event, SystemEventType

logger = logging.getLogger(__name__)

_DEFAULT_MAX_QUEUE_SIZE = 50000
_BATCH_SIZE = 20
_BATCH_TIMEOUT = 0.05
_SCREENSHOTS_PER_SUBDIR = 1000
_MAX_JSONL_BYTES = 400 * 1024 * 1024


class SessionWriter:
    def __init__(self, session_dir: Path):
        self.session_dir = session_dir
        self.screenshots_dir = session_dir / "screenshots"
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)
        self.jsonl_dir = session_dir
        self._file_index = 0
        self._file = None
        self._lock = Lock()
        self._screenshot_counter = 0
        self._event_count = 0
        self._bytes_written = 0
        self._rotation_callbacks: list = []

    def _current_jsonl_path(self) -> Path:
        if self._file_index == 0:
            return self.jsonl_dir / "trajectory.jsonl"
        return self.jsonl_dir / f"trajectory_{self._file_index:03d}.jsonl"

    def open(self):
        path = self._current_jsonl_path()
        self._file = open(path, "a", buffering=1)
        try:
            import ctypes
            libc = ctypes.CDLL(None)
            libc.setiopolicy_np(0, 0, 1)
        except Exception:
            pass
        logger.info("Writing JSONL to %s", path)

    def on_rotate(self, callback):
        self._rotation_callbacks.append(callback)

    def _maybe_rotate(self):
        if self._bytes_written >= _MAX_JSONL_BYTES:
            old_path = self._current_jsonl_path()
            if self._file:
                self._file.close()
            self._file_index += 1
            self._bytes_written = 0
            path = self._current_jsonl_path()
            self._file = open(path, "a", buffering=1)
            logger.info(
                "Rotated JSONL: %s reached %d MB, continuing at %s",
                old_path, _MAX_JSONL_BYTES // (1024 * 1024), path,
            )
            for cb in self._rotation_callbacks:
                try:
                    cb(old_path, path)
                except Exception:
                    logger.exception("rotation callback failed")

    def write_event(self, event: CaptureEvent):
        with self._lock:
            if self._file:
                line = event.to_jsonl() + "\n"
                self._file.write(line)
                self._event_count += 1
                self._bytes_written += len(line.encode("utf-8"))
                self._maybe_rotate()

    def write_event_batch(self, events: list):
        with self._lock:
            if self._file:
                lines = "\n".join(e.to_jsonl() for e in events) + "\n"
                self._file.write(lines)
                self._event_count += len(events)
                self._bytes_written += len(lines.encode("utf-8"))
                self._maybe_rotate()

    def save_screenshot(self, data: bytes, ext: str = "png") -> str:
        self._screenshot_counter += 1
        subdir_num = (self._screenshot_counter - 1) // _SCREENSHOTS_PER_SUBDIR
        subdir = self.screenshots_dir / f"{subdir_num:03d}"
        subdir.mkdir(exist_ok=True)
        filename = f"{self._screenshot_counter:06d}.{ext}"
        path = subdir / filename
        with open(path, "wb") as f:
            f.write(data)
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

    def on_rotate(self, callback):
        self._writer.on_rotate(callback)

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

    def save_screenshot(self, data: bytes, ext: str = "png") -> str:
        return self._writer.save_screenshot(data, ext)

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
