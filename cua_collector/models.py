from dataclasses import dataclass
from typing import Optional
from enum import Enum
import json


class EventType(str, Enum):
    OBSERVATION = "observation"
    ACTION = "action"
    SYSTEM_EVENT = "system_event"


class ActionType(str, Enum):
    MOUSE_CLICK = "mouse_click"
    MOUSE_MOVE = "mouse_move"
    MOUSE_DRAG = "mouse_drag"
    KEY_PRESS = "key_press"
    KEY_RELEASE = "key_release"
    SCROLL = "scroll"
    TEXT_INPUT = "text_input"
    MODIFIER_CHANGE = "modifier_change"


class SystemEventType(str, Enum):
    SESSION_START = "session_start"
    SESSION_END = "session_end"
    WINDOW_FOCUS_CHANGED = "window_focus_changed"
    APP_LAUNCHED = "app_launched"
    APP_QUIT = "app_quit"
    PAUSED = "paused"
    RESUMED = "resumed"
    STEP_MARKER = "step_marker"
    JSONL_ROTATED = "jsonl_rotated"


@dataclass
class CaptureEvent:
    event_type: EventType
    timestamp: float
    session_id: str
    sequence_id: int
    data: dict

    def to_jsonl(self) -> str:
        payload = {
            "type": self.event_type.value,
            "timestamp": self.timestamp,
            "session_id": self.session_id,
            "sequence_id": self.sequence_id,
            "data": self.data,
        }
        return json.dumps(payload, default=str)


def make_observation(
    timestamp: float,
    session_id: str,
    sequence_id: int,
    screenshot_path: Optional[str] = None,
    accessibility_tree: Optional[dict] = None,
    active_window: Optional[dict] = None,
    app_activity: Optional[list] = None,
    display_size: Optional[dict] = None,
) -> CaptureEvent:
    data = {}
    if screenshot_path:
        data["screenshot"] = screenshot_path
    if accessibility_tree is not None:
        data["accessibility_tree"] = accessibility_tree
    if active_window:
        data["active_window"] = active_window
    if app_activity:
        data["app_activity"] = app_activity
    if display_size:
        data["display_size"] = display_size
    return CaptureEvent(
        event_type=EventType.OBSERVATION,
        timestamp=timestamp,
        session_id=session_id,
        sequence_id=sequence_id,
        data=data,
    )


def make_action(
    timestamp: float,
    session_id: str,
    sequence_id: int,
    action_type: ActionType,
    position: Optional[tuple] = None,
    button: Optional[str] = None,
    key: Optional[str] = None,
    key_code: Optional[int] = None,
    modifiers: Optional[list] = None,
    delta_x: Optional[float] = None,
    delta_y: Optional[float] = None,
    text: Optional[str] = None,
    scrubbed: bool = False,
) -> CaptureEvent:
    data = {
        "action_type": action_type.value,
    }
    if position is not None:
        data["position"] = [round(position[0], 1), round(position[1], 1)]
    if button:
        data["button"] = button
    if key is not None:
        data["key"] = key
    if key_code is not None:
        data["key_code"] = key_code
    if modifiers:
        data["modifiers"] = modifiers
    if delta_x is not None:
        data["delta_x"] = round(delta_x, 1)
    if delta_y is not None:
        data["delta_y"] = round(delta_y, 1)
    if text is not None:
        data["text"] = text
    if scrubbed:
        data["scrubbed"] = True
    return CaptureEvent(
        event_type=EventType.ACTION,
        timestamp=timestamp,
        session_id=session_id,
        sequence_id=sequence_id,
        data=data,
    )


def make_system_event(
    timestamp: float,
    session_id: str,
    sequence_id: int,
    system_event_type: SystemEventType,
    details: Optional[dict] = None,
) -> CaptureEvent:
    data = {"system_event": system_event_type.value}
    if details:
        data.update(details)
    return CaptureEvent(
        event_type=EventType.SYSTEM_EVENT,
        timestamp=timestamp,
        session_id=session_id,
        sequence_id=sequence_id,
        data=data,
    )
