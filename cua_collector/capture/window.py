import logging
import threading
import time
from typing import Optional, Callable

from ..models import make_system_event, SystemEventType, CaptureEvent

logger = logging.getLogger(__name__)


class WindowTracker:
    def __init__(self, config: dict, callback: Callable[[CaptureEvent], None],
                 get_session_id: Callable[[], str],
                 get_sequence_id: Callable[[], int]):
        self.config = config["capture"]["window_tracking"]
        self.callback = callback
        self.get_session_id = get_session_id
        self.get_sequence_id = get_sequence_id
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_app = None
        self._last_window_title = None
        self._known_pids: set[int] = set()
        self._pid_info: dict[int, dict] = {}

    def start(self):
        if not self.config["enabled"]:
            return
        self._running = True
        self._known_pids, self._pid_info = self._get_running_pids()
        self._thread = threading.Thread(target=self._track_loop, daemon=True)
        self._thread.name = "cua-window-tracker"
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)

    def _track_loop(self):
        while self._running:
            try:
                self._check_active_window()
                self._check_app_events()
            except Exception:
                logger.exception("window tracking loop failed")
            time.sleep(1)

    def _check_app_events(self):
        import AppKit
        try:
            current_pids = set()
            for app in AppKit.NSWorkspace.sharedWorkspace().runningApplications():
                pid = app.processIdentifier()
                current_pids.add(pid)
                if pid not in self._known_pids:
                    self._known_pids.add(pid)
                    self._pid_info[pid] = {
                        "app_name": app.localizedName(),
                        "bundle_id": app.bundleIdentifier(),
                    }
                    self.callback(make_system_event(
                        timestamp=time.time(),
                        session_id=self.get_session_id(),
                        sequence_id=self.get_sequence_id(),
                        system_event_type=SystemEventType.APP_LAUNCHED,
                        details={
                            "app_name": app.localizedName(),
                            "bundle_id": app.bundleIdentifier(),
                            "pid": pid,
                        },
                    ))

            for pid in list(self._known_pids):
                if pid not in current_pids:
                    self._known_pids.discard(pid)
                    info = self._pid_info.pop(pid, {})
                    self.callback(make_system_event(
                        timestamp=time.time(),
                        session_id=self.get_session_id(),
                        sequence_id=self.get_sequence_id(),
                        system_event_type=SystemEventType.APP_QUIT,
                        details={
                            "app_name": info.get("app_name", "unknown"),
                            "bundle_id": info.get("bundle_id"),
                            "pid": pid,
                        },
                    ))
        except Exception:
            logger.exception("app event check failed")

    def _get_running_pids(self) -> tuple[set, dict]:
        import AppKit
        pids = set()
        info = {}
        try:
            for app in AppKit.NSWorkspace.sharedWorkspace().runningApplications():
                pid = app.processIdentifier()
                pids.add(pid)
                info[pid] = {
                    "app_name": app.localizedName(),
                    "bundle_id": app.bundleIdentifier(),
                }
        except Exception:
            logger.exception("failed to get running PIDs")
        return pids, info

    def _check_active_window(self):
        import AppKit
        ws = AppKit.NSWorkspace.sharedWorkspace()
        front_app = ws.frontmostApplication()

        app_name = front_app.localizedName()
        bundle_id = front_app.bundleIdentifier()
        pid = front_app.processIdentifier()

        window_title = self._get_window_title_fallback(pid)

        if (app_name != self._last_app or window_title != self._last_window_title):
            self._last_app = app_name
            self._last_window_title = window_title

            details = {
                "app_name": app_name,
                "bundle_id": bundle_id,
                "pid": pid,
                "window_title": window_title,
            }
            self.callback(make_system_event(
                timestamp=time.time(),
                session_id=self.get_session_id(),
                sequence_id=self.get_sequence_id(),
                system_event_type=SystemEventType.WINDOW_FOCUS_CHANGED,
                details=details,
            ))

    def _get_window_title_fallback(self, pid: int) -> Optional[str]:
        title = self._get_window_title_ax(pid)
        if title:
            return title
        title = self._get_window_title_cg(pid)
        if title:
            return title
        return None

    def _get_window_title_ax(self, pid: int) -> Optional[str]:
        try:
            import ApplicationServices as ax
            app = ax.AXUIElementCreateApplication(pid)

            err, focused = ax.AXUIElementCopyAttributeValue(
                app, "AXFocusedWindow", None
            )
            if not err and focused is not None:
                err2, title = ax.AXUIElementCopyAttributeValue(
                    focused, "AXTitle", None
                )
                if not err2 and title:
                    return title

            err, windows = ax.AXUIElementCopyAttributeValue(
                app, "AXWindows", None
            )
            if not err and windows:
                for w in windows:
                    err_main, is_main = ax.AXUIElementCopyAttributeValue(
                        w, "AXMain", None
                    )
                    if not err_main and is_main:
                        err_t, title = ax.AXUIElementCopyAttributeValue(
                            w, "AXTitle", None
                        )
                        if not err_t and title:
                            return title
                    err_t, title = ax.AXUIElementCopyAttributeValue(
                        w, "AXTitle", None
                    )
                    if not err_t and title:
                        return title
        except Exception:
            logger.exception("AX window title lookup failed")
        return None

    def _get_window_title_cg(self, pid: int) -> Optional[str]:
        try:
            import Quartz
            window_list = Quartz.CGWindowListCopyWindowInfo(
                Quartz.kCGWindowListOptionOnScreenOnly,
                Quartz.kCGNullWindowID,
            )
            for win in window_list or []:
                if win.get("kCGWindowOwnerPID") == pid:
                    title = win.get("kCGWindowName")
                    if title:
                        bounds = win.get("kCGWindowBounds", {})
                        bw = bounds.get("Width", 0)
                        bh = bounds.get("Height", 0)
                        if bw > 100 and bh > 100:
                            return title
                    owner = win.get("kCGWindowOwnerName", "")
                    if owner:
                        return owner
        except Exception:
            logger.exception("CG window title lookup failed")
        return None

    def get_current_window_info(self) -> Optional[dict]:
        import AppKit
        ws = AppKit.NSWorkspace.sharedWorkspace()
        front_app = ws.frontmostApplication()
        pid = front_app.processIdentifier()
        return {
            "app_name": front_app.localizedName(),
            "bundle_id": front_app.bundleIdentifier(),
            "pid": pid,
            "window_title": self._get_window_title_fallback(pid),
        }
