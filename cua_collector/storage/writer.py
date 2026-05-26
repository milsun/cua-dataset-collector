import json
import os
from pathlib import Path
from threading import Lock
from queue import Queue, Empty
from typing import Optional
from datetime import datetime

from ..models import CaptureEvent


class SessionWriter:
    def __init__(self, session_dir: Path):
        self.session_dir = session_dir
        self.screenshots_dir = session_dir / "screenshots"
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)
        self.jsonl_path = session_dir / "trajectory.jsonl"
        self._file = None
        self._lock = Lock()
        self._screenshot_counter = 0

    def open(self):
        self._file = open(self.jsonl_path, "a", buffering=1)

    def write_event(self, event: CaptureEvent):
        with self._lock:
            if self._file:
                self._file.write(event.to_jsonl() + "\n")

    def write_event_batch(self, events: list):
        with self._lock:
            if self._file:
                lines = "\n".join(e.to_jsonl() for e in events) + "\n"
                self._file.write(lines)

    def save_screenshot(self, png_data: bytes) -> str:
        self._screenshot_counter += 1
        filename = f"{self._screenshot_counter:06d}.png"
        path = self.screenshots_dir / filename
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
        with self._lock:
            if not self.jsonl_path.exists():
                return 0
            return sum(1 for _ in open(self.jsonl_path))


class AsyncWriter:
    def __init__(self, session_dir: Path):
        self._writer = SessionWriter(session_dir)
        self._queue: Queue = Queue(maxsize=10000)
        self._running = False

    def open(self):
        self._writer.open()
        self._running = True

    def put(self, event: CaptureEvent):
        try:
            self._queue.put_nowait(event)
        except Exception:
            pass

    def save_screenshot(self, png_data: bytes) -> str:
        return self._writer.save_screenshot(png_data)

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
        while self._running:
            try:
                event = self._queue.get(timeout=1)
                self._writer.write_event(event)
            except Empty:
                pass

    def close(self):
        self._running = False
        self.flush()
        self._writer.close()
