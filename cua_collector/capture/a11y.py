import hashlib
import logging
import threading
import time
from typing import Optional, Callable

import ApplicationServices as ax
from ..models import make_observation, CaptureEvent

logger = logging.getLogger(__name__)

_TREE_FINGERPRINT_BYTES = 10000
_AX_TIMEOUT = 2.0
_AX_RETRIES = 3


class AccessibilityCapture:
    def __init__(self, config: dict, callback: Callable[[CaptureEvent], None],
                 get_session_id: Callable[[], str],
                 get_sequence_id: Callable[[], int]):
        self.config = config["capture"]["accessibility_tree"]
        self.callback = callback
        self.get_session_id = get_session_id
        self.get_sequence_id = get_sequence_id
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_tree_hash = 0

    def start(self):
        if not self.config["enabled"]:
            return
        self._running = True
        self._thread = threading.Thread(target=self._a11y_loop, daemon=True)
        self._thread.name = "cua-a11y-capture"
        self._thread.start()

        ax.AXUIElementSetMessagingTimeout(
            ax.AXUIElementCreateSystemWide(), _AX_TIMEOUT
        )

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)

    def _a11y_loop(self):
        while self._running:
            import objc
            with objc.autorelease_pool():
                try:
                    tree = self._capture_tree()
                    if tree is None:
                        time.sleep(2)
                        continue

                    should_capture = True
                    if self.config.get("capture_on_change_only", True):
                        fp = self._fingerprint(tree)
                        if fp == self._last_tree_hash:
                            should_capture = False
                        else:
                            self._last_tree_hash = fp

                    if should_capture:
                        event = make_observation(
                            timestamp=time.time(),
                            session_id=self.get_session_id(),
                            sequence_id=self.get_sequence_id(),
                            accessibility_tree=tree,
                        )
                        self.callback(event)
                except Exception:
                    logger.exception("a11y capture failed")
                time.sleep(2)

    @staticmethod
    def _fingerprint(tree: dict) -> str:
        serialized = str(tree)
        truncated = serialized.encode("utf-8")[:_TREE_FINGERPRINT_BYTES]
        return hashlib.md5(truncated, usedforsecurity=False).hexdigest()

    def _capture_tree(self) -> Optional[dict]:
        for attempt in range(_AX_RETRIES):
            try:
                system_wide = ax.AXUIElementCreateSystemWide()
                ax.AXUIElementSetMessagingTimeout(system_wide, _AX_TIMEOUT)
                err, focused_app = ax.AXUIElementCopyAttributeValue(
                    system_wide, ax.kAXFocusedApplicationAttribute, None
                )
                if err or focused_app is None:
                    if attempt == _AX_RETRIES - 1:
                        return None
                    time.sleep(1)
                    continue

                ax.AXUIElementSetMessagingTimeout(focused_app, _AX_TIMEOUT)
                return self._get_element_info(focused_app, depth=0)
            except Exception:
                if attempt == _AX_RETRIES - 1:
                    self._check_tcc_staleness()
                    return None
                time.sleep(1)

    def _check_tcc_staleness(self):
        try:
            system_wide = ax.AXUIElementCreateSystemWide()
            ax.AXUIElementSetMessagingTimeout(system_wide, 0.5)
            err, _ = ax.AXUIElementCopyAttributeValue(
                system_wide, ax.kAXFocusedApplicationAttribute, None
            )
            if err:
                logger.error(
                    "AX API calls persistently failing with error %d — "
                    "TCC cache may be stale. Restart the collector to resolve. "
                    "Try toggling Accessibility permission in System Settings.",
                    err,
                )
            else:
                logger.debug(
                    "AX API transient failure resolved on retry"
                )
        except Exception:
            pass

    def _get_element_info(self, element, depth=0) -> Optional[dict]:
        max_depth = self.config.get("max_depth", 5)
        if depth > max_depth:
            return None

        info = {}
        try:
            err, role = ax.AXUIElementCopyAttributeValue(element, "AXRole", None)
            if not err and role:
                info["role"] = role

            err, title = ax.AXUIElementCopyAttributeValue(element, "AXTitle", None)
            if not err and title:
                info["title"] = title

            err, value = ax.AXUIElementCopyAttributeValue(element, "AXValue", None)
            if not err and value:
                info["value"] = str(value)[:200]

            err, desc = ax.AXUIElementCopyAttributeValue(element, "AXDescription", None)
            if not err and desc:
                info["description"] = desc

            err, subrole = ax.AXUIElementCopyAttributeValue(element, "AXSubrole", None)
            if not err and subrole:
                info["subrole"] = subrole

            err, enabled = ax.AXUIElementCopyAttributeValue(element, "AXEnabled", None)
            if not err:
                info["enabled"] = bool(enabled)

            err, focused = ax.AXUIElementCopyAttributeValue(element, "AXFocused", None)
            if not err and focused is not None:
                info["focused"] = bool(focused)

            err, pos_val = ax.AXUIElementCopyAttributeValue(element, "AXPosition", None)
            if not err and pos_val:
                success, point = ax.AXValueGetValue(pos_val, ax.kAXValueCGPointType, None)
                if success:
                    info["position"] = {"x": point.x, "y": point.y}

            err, size_val = ax.AXUIElementCopyAttributeValue(element, "AXSize", None)
            if not err and size_val:
                success, size = ax.AXValueGetValue(size_val, ax.kAXValueCGSizeType, None)
                if success:
                    info["size"] = {"w": size.width, "h": size.height}

            children = []
            err, child_refs = ax.AXUIElementCopyAttributeValue(element, "AXChildren", None)
            if not err and child_refs:
                for child in child_refs:
                    child_info = self._get_element_info(child, depth + 1)
                    if child_info:
                        children.append(child_info)
            if children:
                info["children"] = children

        except Exception:
            logger.debug("a11y element info failed", exc_info=True)

        return info

    def capture_now(self) -> Optional[CaptureEvent]:
        tree = self._capture_tree()
        if tree:
            return make_observation(
                timestamp=time.time(),
                session_id=self.get_session_id(),
                sequence_id=self.get_sequence_id(),
                accessibility_tree=tree,
            )
        return None
