from copy import deepcopy
from pathlib import Path
import json

DEFAULT_CONFIG = {
    "storage": {
        "dir": Path.home() / ".cua-collector",
        "max_sessions_gb": 50,
    },
    "session": {
        "auto_start": True,
        "max_duration_minutes": 120,
    },
    "capture": {
        "screenshot": {
            "enabled": True,
            "interval_seconds": 1.0,
            "max_width": 1440,
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
        with open(CONFIG_PATH) as f:
            user_config = json.load(f)
        _deep_merge(config, user_config)
    return config


def save_config(config: dict):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2, default=str)


def _deep_merge(base: dict, override: dict):
    for key, val in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(val, dict):
            _deep_merge(base[key], val)
        else:
            base[key] = val
