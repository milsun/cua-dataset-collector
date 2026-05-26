import argparse
import signal
import sys
import time
from pathlib import Path

from .session import Collector
from .config import CONFIG_PATH


def cmd_start(args):
    collector = Collector()
    session = collector.start()
    print(f"Session started: {session.session_id}")
    print(f"Output: {session.session_dir}")
    print("Press Ctrl+C to stop recording")

    def shutdown():
        if collector.is_running:
            collector.stop()
            print(f"\nSession saved to {session.session_dir}")
        sys.exit(0)

    signal.signal(signal.SIGINT, lambda s, f: shutdown())
    signal.signal(signal.SIGTERM, lambda s, f: shutdown())

    while collector.is_running:
        time.sleep(1)


def cmd_status(args):
    collector = Collector()
    if collector.is_running:
        session = collector.active_session
        print(f"Active session: {session.session_id}")
        print(f"Directory: {session.session_dir}")
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
    save_config(config)
    print(f"Set {args.key} = {val}")


def cmd_info(args):
    print("CUA Dataset Collector v0.1.0")
    print()
    print("Privacy notice:")
    print("  This tool records screenshots, accessibility trees,")
    print("  mouse/keyboard events, and active window info.")
    print(f"  Data is stored locally at: {Path.home() / '.cua-collector' / 'sessions'}")
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
