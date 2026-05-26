import time
import uuid
import threading
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


class Session:
    def __init__(self, config: dict):
        self.config = config
        self.session_id = str(uuid.uuid4())
        self.session_dir = self._create_session_dir()
        self.writer = AsyncWriter(self.session_dir)
        self.scrubber = PrivacyScrubber(config)
        self._sequence_counter = 0
        self._paused = False
        self._running = False
        self._lock = threading.Lock()
        self._latest_a11y_tree = None
        self._start_time = 0.0

        self._capture_modules = []

    def _create_session_dir(self) -> Path:
        base = Path(self.config["storage"]["dir"]).expanduser()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        session_dir = base / "sessions" / f"{timestamp}_{self.session_id[:8]}"
        session_dir.mkdir(parents=True, exist_ok=True)
        return session_dir

    def start(self):
        self.writer.open()
        self._running = True
        self._start_time = time.time()

        self._write_system_event(SystemEventType.SESSION_START, {
            "config": {k: v for k, v in self.config.items() if k != "storage"},
        })

        self._capture_modules = []

        self._a11y = AccessibilityCapture(
            self.config, self._on_event,
            self._get_session_id, self._next_sequence_id,
        )
        self._a11y.start()
        self._capture_modules.append(self._a11y)

        screen = ScreenCapture(
            self.config, self._on_event,
            self.writer.save_screenshot,
        )
        screen.start()
        self._capture_modules.append(screen)

        input_mon = InputMonitor(
            self.config, self._on_event,
            self._get_session_id, self._next_sequence_id,
        )
        input_mon.start()
        self._capture_modules.append(input_mon)

        window = WindowTracker(
            self.config, self._on_event,
            self._get_session_id, self._next_sequence_id,
        )
        window.start()
        self._capture_modules.append(window)

        self._writer_thread = threading.Thread(target=self.writer.run, daemon=True)
        self._writer_thread.start()

    def stop(self):
        self._running = False
        for mod in reversed(self._capture_modules):
            try:
                mod.stop()
            except Exception:
                pass

        self._write_system_event(SystemEventType.SESSION_END, {
            "duration_seconds": round(time.time() - self._start_time, 2),
            "total_events": self._sequence_counter,
        })
        self.writer.close()

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
        return self._paused

    def _on_event(self, event: CaptureEvent):
        if self._paused:
            return
        event.session_id = self.session_id
        event.sequence_id = self._next_sequence_id()

        if event.event_type.value == "observation":
            if event.data.get("accessibility_tree"):
                self._latest_a11y_tree = event.data["accessibility_tree"]
                self.writer.put(event)
                return

            if event.data.get("screenshot"):
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
