import logging
import os
import shutil
import threading
import time
import uuid
from pathlib import Path
from datetime import datetime
from typing import Optional

from .config import load_config
from .models import CaptureEvent, make_system_event, SystemEventType
from .storage.writer import AsyncWriter
from .capture.screen import ScreenCapture
from .capture.a11y import AccessibilityCapture
from .capture.input_monitor import InputMonitor
from .capture.window import WindowTracker
from .privacy.scrubber import PrivacyScrubber

logger = logging.getLogger(__name__)

_MIN_DISK_GB = 1.0
_WATCHDOG_INTERVAL = 5.0


class Session:
    def __init__(self, config: dict):
        self.config = config
        self.session_id = str(uuid.uuid4())
        self.session_dir = self._create_session_dir()
        max_qs = config.get("storage", {}).get("max_queue_size", 10000)
        self.writer = AsyncWriter(self.session_dir, max_queue_size=max_qs)
        self.scrubber = PrivacyScrubber(config)
        self._sequence_counter = 0
        self._paused = False
        self._running = False
        self._lock = threading.Lock()
        self._latest_a11y_tree = None
        self._start_time = 0.0
        self._watchdog_stop = threading.Event()

        self._capture_modules: list = []
        self._module_threads: dict[str, Optional[threading.Thread]] = {
            "screen": None,
            "a11y": None,
            "input": None,
            "window": None,
        }

        self._max_duration_sec = (
            config.get("session", {}).get("max_duration_minutes", 120) * 60
        )
        self._max_screenshots = (
            config.get("capture", {})
            .get("screenshot", {})
            .get("max_screenshots", 50000)
        )
        self._stop_requested = False

    def _create_session_dir(self) -> Path:
        base = Path(self.config["storage"]["dir"]).expanduser()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        session_dir = base / "sessions" / f"{timestamp}_{self.session_id[:8]}"
        session_dir.mkdir(parents=True, exist_ok=True)
        return session_dir

    def _check_disk_space(self) -> bool:
        try:
            usage = shutil.disk_usage(self.session_dir)
            free_gb = usage.free / (1024**3)
            if free_gb < _MIN_DISK_GB:
                logger.critical(
                    "Only %.2f GB free disk space — below minimum %.1f GB. "
                    "Stopping session.",
                    free_gb, _MIN_DISK_GB,
                )
                return False
            return True
        except OSError:
            logger.warning("disk space check failed")
            return True

    def start(self):
        if not self._check_disk_space():
            raise RuntimeError("insufficient disk space to start session")

        self.writer.open()
        self._running = True
        self._start_time = time.time()

        self._write_system_event(SystemEventType.SESSION_START, {
            "config": {k: v for k, v in self.config.items() if k != "storage"},
        })
        logger.info("Session %s started at %s", self.session_id[:8], self.session_dir)

        self._capture_modules = []

        self._a11y = AccessibilityCapture(
            self.config, self._on_event,
            self._get_session_id, self._next_sequence_id,
        )
        self._a11y.start()
        self._capture_modules.append(self._a11y)
        self._module_threads["a11y"] = self._a11y._thread

        screen = ScreenCapture(
            self.config, self._on_event,
            self.writer.save_screenshot,
            throttle_fn=lambda: self._throttle_multiplier(),
        )
        screen.start()
        self._capture_modules.append(screen)
        self._module_threads["screen"] = screen._thread

        input_mon = InputMonitor(
            self.config, self._on_event,
            self._get_session_id, self._next_sequence_id,
        )
        input_mon.start()
        self._capture_modules.append(input_mon)
        self._module_threads["input"] = input_mon._thread

        window = WindowTracker(
            self.config, self._on_event,
            self._get_session_id, self._next_sequence_id,
        )
        window.start()
        self._capture_modules.append(window)
        self._module_threads["window"] = window._thread

        self._writer_thread = threading.Thread(target=self.writer.run, daemon=True)
        self._writer_thread.name = "cua-writer"
        self._writer_thread.start()

        self._watchdog_thread = threading.Thread(target=self._watchdog_loop, daemon=True)
        self._watchdog_thread.name = "cua-watchdog"
        self._watchdog_thread.start()

    def stop(self):
        if not self._running:
            return
        logger.info("Stopping session %s ...", self.session_id[:8])
        self._running = False
        self._watchdog_stop.set()

        for mod in reversed(self._capture_modules):
            try:
                mod.stop()
            except Exception:
                logger.exception("error stopping capture module")

        dropped = self.writer.dropped_count
        self._write_system_event(SystemEventType.SESSION_END, {
            "duration_seconds": round(time.time() - self._start_time, 2),
            "total_events": self._sequence_counter,
            "events_written": self.writer.event_count,
            "screenshots": self.writer.screenshot_count,
            "events_dropped": dropped if dropped else 0,
        })

        remaining = self.writer._queue.qsize()
        if remaining > 0:
            logger.debug("Draining %d remaining events from queue", remaining)
        self.writer.close()

        logger.info(
            "Session %s ended: %d events written, %d screenshots, %d dropped",
            self.session_id[:8],
            self.writer.event_count,
            self.writer.screenshot_count,
            dropped,
        )

    def _watchdog_loop(self):
        while not self._watchdog_stop.is_set():
            if not self._running:
                return
            for name, thread in self._module_threads.items():
                if thread is not None and not thread.is_alive():
                    logger.error(
                        "Capture module thread '%s' died unexpectedly", name
                    )
            if self._max_duration_sec > 0:
                elapsed = time.time() - self._start_time
                remaining = self._max_duration_sec - elapsed
                if remaining <= 0:
                    logger.info(
                        "Max duration reached (%.1f min), stopping session",
                        self._max_duration_sec / 60,
                    )
                    self._stop_requested = True
                    return
                elif remaining < 60 and remaining > 0:
                    logger.debug(
                        "Session will end in %.0f seconds", remaining
                    )

            if not self._check_disk_space():
                self._stop_requested = True
                return

            qsize = self.writer._queue.qsize()
            max_qs = self.config.get("storage", {}).get("max_queue_size", 10000)
            if qsize > max_qs * 0.9:
                logger.warning(
                    "Writer queue critically full (%d/%d). Events will be dropped.",
                    qsize, max_qs,
                )

            self._watchdog_stop.wait(_WATCHDOG_INTERVAL)

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def start_time(self) -> float:
        return self._start_time

    def toggle_pause(self):
        self._paused = not self._paused
        event_type = SystemEventType.RESUMED if not self._paused else SystemEventType.PAUSED
        self._write_system_event(event_type)
        logger.info("Session %s", "resumed" if not self._paused else "paused")
        return self._paused

    def _on_event(self, event: CaptureEvent):
        if self._paused or self._stop_requested:
            return
        event.session_id = self.session_id
        event.sequence_id = self._next_sequence_id()

        if event.event_type.value == "observation":
            if event.data.get("accessibility_tree"):
                self._latest_a11y_tree = event.data["accessibility_tree"]
                self.writer.put(event)
                return

            if event.data.get("screenshot"):
                if (self._max_screenshots > 0
                        and self.writer.screenshot_count >= self._max_screenshots):
                    logger.info(
                        "Max screenshots reached (%d), stopping session",
                        self._max_screenshots,
                    )
                    self._stop_requested = True
                    return
                tree = self._latest_a11y_tree
                if tree is None:
                    fallback = self._a11y.capture_now()
                    if fallback:
                        tree = fallback.data.get("accessibility_tree")
                        self._latest_a11y_tree = tree
                if tree is not None:
                    event.data["accessibility_tree"] = tree
                    if event.data.get("active_window") is None:
                        tree_title = (
                            tree.get("title") or tree.get("role")
                        )
                        event.data["active_window"] = {"app_role": tree_title}

        if event.event_type.value == "action":
            event.data = self.scrubber.scrub_action_data(event.data)

        if event.event_type.value == "system_event":
            wt = event.data.get("window_title")
            if wt:
                event.data["window_title"] = self.scrubber.scrub_window_title(wt)

        self.writer.put(event)

    def _write_system_event(self, event_type: SystemEventType, details: Optional[dict] = None):
        event = make_system_event(
            timestamp=time.time(),
            session_id=self.session_id,
            sequence_id=self._next_sequence_id(),
            system_event_type=event_type,
            details=details,
        )
        self.writer.put(event)

    def _throttle_multiplier(self) -> float:
        ratio = self.writer.queue_fill_ratio
        if ratio < 0.5:
            return 1.0
        if ratio < 0.8:
            return 2.0
        if ratio < 0.95:
            return 5.0
        return 10.0

    def _next_sequence_id(self) -> int:
        with self._lock:
            self._sequence_counter += 1
            return self._sequence_counter

    def _get_session_id(self) -> str:
        return self.session_id


class Collector:
    def __init__(self):
        self.config = load_config()
        self._session: Optional[Session] = None

    def start(self) -> Session:
        if self._session and self._session.is_running:
            raise RuntimeError("Collector already running")
        self._session = Session(self.config)
        self._session.start()
        return self._session

    def stop(self):
        if self._session:
            self._session.stop()
            self._session = None

    @property
    def active_session(self) -> Optional[Session]:
        return self._session

    @property
    def is_running(self) -> bool:
        return self._session is not None and self._session.is_running
