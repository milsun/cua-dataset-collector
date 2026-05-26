import argparse
import logging
import os
import signal
import sys
import time
from pathlib import Path

try:
    from PyObjCTools import AppHelper
    _HAVE_MACH_SIGNALS = True
except ImportError:
    _HAVE_MACH_SIGNALS = False

from .session import Collector
from .config import CONFIG_PATH

logger = logging.getLogger(__name__)

PID_FILE = Path.home() / ".cua-collector" / "collector.pid"

_LOG_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}


def _setup_logging(level: str = "INFO"):
    logging.basicConfig(
        level=_LOG_LEVELS.get(level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _write_pid():
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()))
    logger.debug("PID %d written to %s", os.getpid(), PID_FILE)


def _remove_pid():
    try:
        PID_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def _read_pid() -> int | None:
    if PID_FILE.exists():
        try:
            return int(PID_FILE.read_text().strip())
        except (ValueError, OSError):
            return None
    return None


def _is_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def cmd_start(args):
    _setup_logging(args.log_level)

    existing_pid = _read_pid()
    if existing_pid and _is_pid_alive(existing_pid):
        print(f"A collector is already running (PID {existing_pid})")
        sys.exit(1)

    collector = Collector()
    session = collector.start()
    _write_pid()

    print(f"Session started: {session.session_id}")
    print(f"Output: {session.session_dir}")
    print("Press Ctrl+C to stop recording")

    def shutdown():
        _remove_pid()
        if collector.is_running:
            collector.stop()
            print(f"\nSession saved to {session.session_dir}")
        sys.exit(0)

    if _HAVE_MACH_SIGNALS:
        AppHelper.installMachInterrupt()
    signal.signal(signal.SIGINT, lambda s, f: shutdown())
    signal.signal(signal.SIGTERM, lambda s, f: shutdown())

    try:
        while collector.is_running:
            if (collector.active_session
                    and collector.active_session._stop_requested):
                collector.stop()
                _remove_pid()
            time.sleep(0.5)
    except KeyboardInterrupt:
        shutdown()
    else:
        _remove_pid()


def cmd_status(args):
    pid = _read_pid()
    if pid and _is_pid_alive(pid):
        print(f"Collector running (PID {pid})")
    else:
        print("No active session")


def cmd_config_show(args):
    from .config import load_config
    import json
    config = load_config()
    print(json.dumps(config, indent=2, default=str))


def cmd_config_set(args):
    from .config import load_config, save_config
    config = load_config()
    parts = args.key.split(".")
    target = config
    for part in parts[:-1]:
        if part not in target:
            target[part] = {}
        target = target[part]

    val = args.value
    if val.lower() == "true":
        val = True
    elif val.lower() == "false":
        val = False
    else:
        try:
            if "." in val:
                val = float(val)
            else:
                val = int(val)
        except ValueError:
            pass

    target[parts[-1]] = val
    try:
        save_config(config)
        print(f"Set {args.key} = {val}")
    except ValueError as e:
        print(f"Config rejected: {e}")
        sys.exit(1)


def cmd_info(args):
    from .config import load_config
    config = load_config()
    sc = config["capture"]["screenshot"]

    print("CUA Dataset Collector v0.1.0")
    print()
    print("Current config:")
    print(f"  Screenshot format: {sc.get('format', 'png')} "
          f"(quality: {sc.get('jpeg_quality', 85)})")
    print(f"  Capture interval: {sc.get('interval_seconds', 1.0)}s")
    print(f"  Max width: {sc.get('max_width', 1280)}px")
    print(f"  Max screenshots: {sc.get('max_screenshots', 50000)}")
    print(f"  Max duration: {config['session']['max_duration_minutes']} min")
    print(f"  A11y tree depth: {config['capture']['accessibility_tree']['max_depth']}")
    print(f"  Mouse move sample rate: {config['capture']['input_monitor']['mouse_move_sample_rate']}")
    print()
    print("Storage estimate: ~3.5 GB/hour (PNG at 1fps, 1280px)")
    print(f"  Data stored at: {Path.home() / '.cua-collector' / 'sessions'}")
    print()
    print("Privacy notice:")
    print("  This tool records screenshots, accessibility trees,")
    print("  mouse/keyboard events, and active window info.")
    print("  Privacy scrubbing enabled by default (CC, email, SSN redaction).")
    print()
    print("Permissions required:")
    print("  - Screen Recording (System Settings > Privacy > Screen Recording)")
    print("  - Accessibility (System Settings > Privacy > Accessibility)")
    print("  - Input Monitoring (System Settings > Privacy > Input Monitoring)")
    print()
    print("Quick start:")
    print("  python -m cua_collector start")


def main():
    parser = argparse.ArgumentParser(
        description="CUA Dataset Collector - Record macOS interactions for CUA training"
    )
    parser.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Set logging level (default: INFO)",
    )

    sub = parser.add_subparsers(dest="command")

    sub.add_parser("start", help="Start a new recording session")
    sub.add_parser("status", help="Show current recording status")
    sub.add_parser("info", help="Show info and quick start guide")

    config_parser = sub.add_parser("config", help="Manage configuration")
    config_sub = config_parser.add_subparsers(dest="config_command")

    show_config = config_sub.add_parser("show", help="Show current config")
    set_config = config_sub.add_parser("set", help="Set a config value")
    set_config.add_argument("key", help="Config key (e.g., capture.screenshot.interval_seconds)")
    set_config.add_argument("value", help="Config value")

    args = parser.parse_args()

    if args.command == "start":
        cmd_start(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "info":
        cmd_info(args)
    elif args.command == "config":
        if args.config_command == "show":
            cmd_config_show(args)
        elif args.config_command == "set":
            cmd_config_set(args)
        else:
            parser.print_help()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
