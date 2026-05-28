from copy import deepcopy
from pathlib import Path
import json
import logging

logger = logging.getLogger(__name__)

DEFAULT_CONFIG = {
    "storage": {
        "dir": Path.home() / ".cua-collector",
        "max_sessions_gb": 50,
        "max_queue_size": 10000,
    },
    "session": {
        "auto_start": True,
        "max_duration_minutes": 0,
    },
    "capture": {
        "screenshot": {
            "enabled": True,
            "interval_seconds": 1.0,
            "max_width": 1280,
            "max_screenshots": 0,
            "format": "png",
            "jpeg_quality": 85,
        },
        "accessibility_tree": {
            "enabled": True,
            "max_depth": 5,
            "capture_on_change_only": True,
        },
        "input_monitor": {
            "enabled": True,
            "capture_mouse_moves": True,
            "mouse_move_sample_rate": 0.1,
        },
        "window_tracking": {
            "enabled": True,
        },
    },
    "privacy": {
        "enabled": True,
        "scrub_passwords": True,
        "pause_on_sensitive_apps": ["loginwindow"],
    },
}

CONFIG_PATH = Path.home() / ".cua-collector" / "config.json"


def load_config() -> dict:
    config = deepcopy(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH) as f:
                user_config = json.load(f)
            _deep_merge(config, user_config)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("failed to load config from %s: %s", CONFIG_PATH, e)
    _validate_config(config)
    return config


def save_config(config: dict):
    _validate_config(config)
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2, default=str)


def _validate_config(config: dict):
    errors = []
    cap = config.get("capture", {})
    sc = cap.get("screenshot", {})

    interval = sc.get("interval_seconds", 1.0)
    if not isinstance(interval, (int, float)) or interval < 0.1:
        errors.append("capture.screenshot.interval_seconds must be >= 0.1")

    max_ss = sc.get("max_screenshots", 0)
    if not isinstance(max_ss, int) or max_ss < 0:
        errors.append("capture.screenshot.max_screenshots must be >= 0 (0 = unlimited)")

    fmt = sc.get("format", "png")
    if fmt not in ("png", "jpeg"):
        errors.append("capture.screenshot.format must be 'png' or 'jpeg'")

    jpeg_q = sc.get("jpeg_quality", 85)
    if not isinstance(jpeg_q, int) or jpeg_q < 1 or jpeg_q > 100:
        errors.append("capture.screenshot.jpeg_quality must be 1-100")

    max_w = sc.get("max_width", 0)
    if max_w and (not isinstance(max_w, int) or max_w < 320):
        errors.append("capture.screenshot.max_width must be >= 320 or 0 (disable)")

    ax = cap.get("accessibility_tree", {})
    depth = ax.get("max_depth", 5)
    if not isinstance(depth, int) or depth < 1 or depth > 20:
        errors.append("capture.accessibility_tree.max_depth must be 1-20")

    im = cap.get("input_monitor", {})
    rate = im.get("mouse_move_sample_rate", 0.1)
    if not isinstance(rate, (int, float)) or rate < 0 or rate > 1:
        errors.append("capture.input_monitor.mouse_move_sample_rate must be 0.0-1.0")

    sess = config.get("session", {})
    dur = sess.get("max_duration_minutes", 0)
    if not isinstance(dur, (int, float)) or dur < 0:
        errors.append("session.max_duration_minutes must be >= 0 (0 = unlimited)")

    storage = config.get("storage", {})
    gb = storage.get("max_sessions_gb", 50)
    if not isinstance(gb, (int, float)) or gb < 1:
        errors.append("storage.max_sessions_gb must be >= 1")

    qs = storage.get("max_queue_size", 10000)
    if not isinstance(qs, int) or qs < 100:
        errors.append("storage.max_queue_size must be >= 100")

    if errors:
        for err in errors:
            logger.warning("config validation: %s", err)
        raise ValueError("invalid configuration:\n" + "\n".join(errors))


def _deep_merge(base: dict, override: dict):
    for key, val in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(val, dict):
            _deep_merge(base[key], val)
        else:
            base[key] = val
