import time
import random
import threading
from typing import Optional, Callable
from ..models import make_action, ActionType, CaptureEvent

_KEY_MAP = {
    0: "a", 1: "s", 2: "d", 3: "f", 4: "h", 5: "g", 6: "z", 7: "x",
    8: "c", 9: "v", 11: "b", 12: "q", 13: "w", 14: "e", 15: "r",
    16: "y", 17: "t", 18: "1", 19: "2", 20: "3", 21: "4", 22: "5",
    23: "6", 24: "7", 25: "8", 26: "9", 27: "0", 28: "return",
    29: "escape", 30: "delete", 31: "tab", 32: "space", 33: "-",
    34: "=", 35: "[", 36: "]", 37: "\\", 38: ";", 39: "'", 40: "`",
    41: ",", 42: ".", 43: "/", 44: "caps_lock",
    48: "tab", 49: "space",
    51: "delete", 53: "escape", 55: "command", 56: "shift",
    57: "caps_lock", 58: "option", 59: "control",
    65: ".", 67: "*", 69: "+", 71: "clear", 75: "/",
    76: "keypad_enter", 78: "-", 81: "=", 82: "0", 83: "1",
    84: "2", 85: "3", 86: "4", 87: "5", 88: "6", 89: "7",
    91: "8", 92: "9",
    96: "f5", 97: "f6", 98: "f7", 99: "f3", 100: "f8",
    101: "f9", 103: "f11", 105: "f13",
    106: "f14", 107: "f15", 109: "f10", 111: "f12",
    114: "help", 115: "home", 116: "page_up", 117: "forward_delete",
    118: "f4", 119: "end", 120: "f2", 121: "page_down", 122: "f1",
    123: "left_arrow", 124: "right_arrow", 125: "down_arrow", 126: "up_arrow",
}

_TEXT_INPUT_TIMEOUT = 0.4


class InputMonitor:
    def __init__(self, config: dict, callback: Callable[[CaptureEvent], None],
                 get_session_id: Callable[[], str],
                 get_sequence_id: Callable[[], int]):
        self.config = config["capture"]["input_monitor"]
        self.callback = callback
        self.get_session_id = get_session_id
        self.get_sequence_id = get_sequence_id
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._run_loop_ref = None
        self._text_buffer: list[str] = []
        self._last_key_time = 0.0
        self._text_lock = threading.Lock()

    def start(self):
        if not self.config["enabled"]:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_event_tap, daemon=True)
        self._thread.start()
        self._text_flush_thread = threading.Thread(
            target=self._text_flush_loop, daemon=True
        )
        self._text_flush_thread.start()

    def stop(self):
        self._running = False
        if self._run_loop_ref is not None:
            import Quartz
            Quartz.CFRunLoopStop(self._run_loop_ref)
            self._run_loop_ref = None
        self._flush_text_buffer()

    def _run_event_tap(self):
        import Quartz

        event_mask = 0
        event_mask |= Quartz.CGEventMaskBit(Quartz.kCGEventLeftMouseDown)
        event_mask |= Quartz.CGEventMaskBit(Quartz.kCGEventLeftMouseUp)
        event_mask |= Quartz.CGEventMaskBit(Quartz.kCGEventRightMouseDown)
        event_mask |= Quartz.CGEventMaskBit(Quartz.kCGEventRightMouseUp)
        event_mask |= Quartz.CGEventMaskBit(Quartz.kCGEventLeftMouseDragged)
        event_mask |= Quartz.CGEventMaskBit(Quartz.kCGEventRightMouseDragged)
        event_mask |= Quartz.CGEventMaskBit(Quartz.kCGEventScrollWheel)

        if self.config.get("capture_mouse_moves", True):
            event_mask |= Quartz.CGEventMaskBit(Quartz.kCGEventMouseMoved)

        event_mask |= Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown)
        event_mask |= Quartz.CGEventMaskBit(Quartz.kCGEventKeyUp)
        event_mask |= Quartz.CGEventMaskBit(Quartz.kCGEventFlagsChanged)

        self._tap = Quartz.CGEventTapCreate(
            Quartz.kCGHIDEventTap,
            Quartz.kCGHeadInsertEventTap,
            0,
            event_mask,
            self._handle_event,
            None,
        )

        if self._tap is None:
            return

        self._run_loop_ref = Quartz.CFRunLoopGetCurrent()
        self._run_loop_source = Quartz.CFMachPortCreateRunLoopSource(None, self._tap, 0)
        Quartz.CFRunLoopAddSource(
            self._run_loop_ref,
            self._run_loop_source,
            Quartz.kCFRunLoopDefaultMode,
        )
        Quartz.CFRunLoopRun()

    def _handle_event(self, proxy, event_type, event, user_info):
        if not self._running:
            return event

        import Quartz
        timestamp = time.time()
        event_type_name = Quartz.CGEventGetType(event)

        try:
            if event_type_name == Quartz.kCGEventMouseMoved:
                sample_rate = self.config.get("mouse_move_sample_rate", 0.1)
                if random.random() > sample_rate:
                    return event

            location = Quartz.CGEventGetLocation(event)
            pos = round(location.x, 1), round(location.y, 1)
            modifiers = self._get_modifiers(event)
            session_id = self.get_session_id()
            seq = self.get_sequence_id()

            if event_type_name in (Quartz.kCGEventLeftMouseDown, Quartz.kCGEventRightMouseDown):
                button = "left" if event_type_name == Quartz.kCGEventLeftMouseDown else "right"
                self.callback(make_action(
                    timestamp=timestamp, session_id=session_id, sequence_id=seq,
                    action_type=ActionType.MOUSE_CLICK, position=pos,
                    button=button, modifiers=modifiers,
                ))

            elif event_type_name in (Quartz.kCGEventLeftMouseUp, Quartz.kCGEventRightMouseUp):
                button = "left" if event_type_name == Quartz.kCGEventLeftMouseUp else "right"
                self.callback(make_action(
                    timestamp=timestamp, session_id=session_id, sequence_id=seq,
                    action_type=ActionType.MOUSE_CLICK, position=pos,
                    button=button, modifiers=modifiers,
                ))

            elif event_type_name in (Quartz.kCGEventLeftMouseDragged, Quartz.kCGEventRightMouseDragged):
                button = "left" if event_type_name == Quartz.kCGEventLeftMouseDragged else "right"
                self.callback(make_action(
                    timestamp=timestamp, session_id=session_id, sequence_id=seq,
                    action_type=ActionType.MOUSE_DRAG, position=pos,
                    button=button, modifiers=modifiers,
                ))

            elif event_type_name == Quartz.kCGEventMouseMoved:
                self.callback(make_action(
                    timestamp=timestamp, session_id=session_id, sequence_id=seq,
                    action_type=ActionType.MOUSE_MOVE, position=pos,
                    modifiers=modifiers,
                ))

            elif event_type_name == Quartz.kCGEventKeyDown:
                key_code = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)
                key = self._key_code_to_str(key_code)
                self.callback(make_action(
                    timestamp=timestamp, session_id=session_id, sequence_id=seq,
                    action_type=ActionType.KEY_PRESS, key=key, key_code=key_code,
                    modifiers=modifiers,
                ))
                self._buffer_key(key, modifiers)

            elif event_type_name == Quartz.kCGEventKeyUp:
                key_code = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)
                key = self._key_code_to_str(key_code)
                self.callback(make_action(
                    timestamp=timestamp, session_id=session_id, sequence_id=seq,
                    action_type=ActionType.KEY_RELEASE, key=key, key_code=key_code,
                    modifiers=modifiers,
                ))

            elif event_type_name == Quartz.kCGEventFlagsChanged:
                self.callback(make_action(
                    timestamp=timestamp, session_id=session_id, sequence_id=seq,
                    action_type=ActionType.MODIFIER_CHANGE, modifiers=modifiers,
                ))

            elif event_type_name == Quartz.kCGEventScrollWheel:
                delta_y = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGScrollWheelEventDeltaAxis1)
                delta_x = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGScrollWheelEventDeltaAxis2)
                self.callback(make_action(
                    timestamp=timestamp, session_id=session_id, sequence_id=seq,
                    action_type=ActionType.SCROLL, position=pos,
                    delta_x=delta_x, delta_y=delta_y, modifiers=modifiers,
                ))

        except Exception:
            pass

        return event

    def _buffer_key(self, key: str, modifiers: list):
        with self._text_lock:
            printable = (
                len(key) == 1 and key.isprintable()
                and "command" not in modifiers
                and "control" not in modifiers
            )
            if printable:
                if modifiers and "shift" in modifiers:
                    key = key.upper()
                self._text_buffer.append(key)
                self._last_key_time = time.time()
            elif key == "space":
                self._text_buffer.append(" ")
                self._last_key_time = time.time()
            elif key == "return":
                self._text_buffer.append("\n")
                self._last_key_time = time.time()
            elif key == "tab":
                self._text_buffer.append("\t")
                self._last_key_time = time.time()
            elif key == "delete":
                if self._text_buffer:
                    self._text_buffer.pop()
                self._last_key_time = time.time()

    def _text_flush_loop(self):
        while self._running:
            time.sleep(0.1)
            with self._text_lock:
                if (self._text_buffer
                        and time.time() - self._last_key_time > _TEXT_INPUT_TIMEOUT):
                    self._emit_text_input()

    def _flush_text_buffer(self):
        with self._text_lock:
            if self._text_buffer:
                self._emit_text_input()

    def _emit_text_input(self):
        if not self._text_buffer:
            return
        text = "".join(self._text_buffer)
        self._text_buffer.clear()
        self.callback(make_action(
            timestamp=time.time(),
            session_id=self.get_session_id(),
            sequence_id=self.get_sequence_id(),
            action_type=ActionType.TEXT_INPUT, text=text,
        ))

    def _get_modifiers(self, event):
        import Quartz
        flags = Quartz.CGEventGetFlags(event)
        mods = []
        if flags & 1 << 20:
            mods.append("caps_lock")
        if flags & 1 << 21:
            mods.append("shift")
        if flags & 1 << 22:
            mods.append("control")
        if flags & 1 << 23:
            mods.append("option")
        if flags & 1 << 24:
            mods.append("command")
        return mods

    def _key_code_to_str(self, code: int) -> str:
        return _KEY_MAP.get(code, f"key_{code}")
