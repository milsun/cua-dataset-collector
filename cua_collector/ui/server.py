import json
import logging
import mimetypes
import os
import shutil
import tempfile
import time
from pathlib import Path
from threading import Lock
from typing import Optional

logger = logging.getLogger(__name__)

_SESSIONS_DIR = Path.home() / ".cua-collector" / "sessions"
_LABELS_LOCK = Lock()


def _validate_session_id(session_id: str) -> Path:
    safe = os.path.basename(os.path.normpath(session_id))
    resolved = (_SESSIONS_DIR / safe).resolve()
    if not str(resolved).startswith(str(_SESSIONS_DIR.resolve())):
        raise ValueError("Invalid session_id")
    return resolved


def _find_all_jsonl(session_dir: Path) -> list[Path]:
    files = []
    for p in sorted(session_dir.iterdir()):
        if p.name.startswith("trajectory") and p.suffix == ".jsonl":
            files.append(p)
    return files


def _list_sessions():
    if not _SESSIONS_DIR.exists():
        return []
    sessions = []
    for d in sorted(_SESSIONS_DIR.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        jsonl_paths = _find_all_jsonl(d)
        if not jsonl_paths:
            continue
        labels_path = d / "labels.json"
        try:
            lines = 0
            total_size = 0
            start_ts = 0
            end_ts = 0
            first_event = True
            for jp in jsonl_paths:
                total_size += jp.stat().st_size
                with jp.open() as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        ev = json.loads(line)
                        lines += 1
                        if first_event:
                            start_ts = ev.get("timestamp", 0)
                            first_event = False
                        end_ts = ev.get("timestamp", 0)
            duration = round(end_ts - start_ts, 1) if start_ts else 0
            labels = {}
            if labels_path.exists():
                labels = json.loads(labels_path.read_text())
            screenshots = sum(1 for _ in d.rglob("*") if _.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"))
            sessions.append({
                "id": d.name,
                "path": str(d),
                "events": lines,
                "screenshots": screenshots,
                "size_bytes": total_size,
                "duration_seconds": duration,
                "start_time": start_ts,
                "end_time": end_ts,
                "labels": sum(len(v) for v in labels.values()) if labels else 0,
            })
        except (json.JSONDecodeError, OSError, StopIteration):
            continue
    return sessions


def _load_labels(session_id: str) -> dict:
    path = _validate_session_id(session_id) / "labels.json"
    if path.exists():
        return json.loads(path.read_text())
    return {}


def _save_labels(session_id: str, labels: dict):
    path = _validate_session_id(session_id) / "labels.json"
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(labels, f, indent=2, default=str)
        os.replace(tmp, str(path))
    finally:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)


def _iter_events(session_id: str, offset: int = 0, limit: int = 50, filter_type: Optional[str] = None, search: Optional[str] = None):
    session_dir = _validate_session_id(session_id)
    jsonl_paths = _find_all_jsonl(session_dir)
    if not jsonl_paths:
        return
    count = 0
    returned = 0
    for jsonl_path in jsonl_paths:
        with jsonl_path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                count += 1
                if count <= offset:
                    continue
                if returned >= limit:
                    return
                if filter_type and ev.get("type") != filter_type:
                    continue
                if search:
                    haystack = json.dumps(ev).lower()
                    if search.lower() not in haystack:
                        continue
                returned += 1
                yield ev, count - 1


def build_app():
    try:
        from flask import Flask, jsonify, request, send_file, render_template, Response
    except ImportError:
        raise ImportError(
            "Flask is required for the UI. Install it with: pip install flask"
        )

    app = Flask(
        __name__,
        template_folder=str(Path(__file__).parent / "templates"),
        static_folder=str(Path(__file__).parent / "static"),
        static_url_path="/static",
    )

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/api/sessions")
    def api_sessions():
        sessions = _list_sessions()
        return jsonify(sessions)

    @app.route("/api/sessions/<session_id>")
    def api_session_detail(session_id):
        try:
            _validate_session_id(session_id)
        except ValueError:
            return jsonify({"error": "invalid session_id"}), 400
        sessions = _list_sessions()
        for s in sessions:
            if s["id"] == session_id:
                return jsonify(s)
        return jsonify({"error": "session not found"}), 404

    @app.route("/api/sessions/<session_id>/events")
    def api_events(session_id):
        try:
            _validate_session_id(session_id)
        except ValueError:
            return jsonify({"error": "invalid session_id"}), 400
        offset = request.args.get("offset", 0, type=int)
        limit = request.args.get("limit", 50, type=int)
        filter_type = request.args.get("filter")
        search = request.args.get("search")
        labels = _load_labels(session_id)
        events = []
        total = 0
        for ev, idx in _iter_events(session_id, offset, limit, filter_type, search):
            ev["index"] = idx
            seq = ev.get("sequence_id")
            ev["labels"] = labels.get(str(seq), {})
            events.append(ev)
            total = idx + 1

        return jsonify({
            "events": events,
            "offset": offset,
            "limit": limit,
            "total": total,
        })

    @app.route("/api/sessions/<session_id>/screenshot/<path:filepath>")
    def api_screenshot(session_id, filepath):
        try:
            session_dir = _validate_session_id(session_id)
        except ValueError:
            return jsonify({"error": "invalid session_id"}), 400
        full_path = (session_dir / filepath).resolve()
        if not str(full_path).startswith(str(session_dir)):
            return jsonify({"error": "invalid path"}), 400
        if not full_path.exists() or not full_path.is_file():
            return jsonify({"error": "screenshot not found"}), 404
        mime, _ = mimetypes.guess_type(str(full_path))
        return send_file(str(full_path), mimetype=mime or "image/png")

    @app.route("/api/sessions/<session_id>/labels", methods=["GET"])
    def api_get_labels(session_id):
        try:
            _validate_session_id(session_id)
        except ValueError:
            return jsonify({"error": "invalid session_id"}), 400
        return jsonify(_load_labels(session_id))

    @app.route("/api/sessions/<session_id>/labels/<int:seq_id>", methods=["PUT"])
    def api_set_label(session_id, seq_id):
        try:
            _validate_session_id(session_id)
        except ValueError:
            return jsonify({"error": "invalid session_id"}), 400
        data = request.get_json(silent=True) or {}
        with _LABELS_LOCK:
            labels = _load_labels(session_id)
            key = str(seq_id)
            if key not in labels:
                labels[key] = {}
            if "tag" in data:
                labels[key]["tag"] = data["tag"]
            if "notes" in data:
                labels[key]["notes"] = data["notes"]
            if "action_class" in data:
                labels[key]["action_class"] = data["action_class"]
            labels[key]["updated_at"] = time.time()
            _save_labels(session_id, labels)
        return jsonify(labels[key])

    @app.route("/api/sessions/<session_id>/labels/<int:seq_id>", methods=["DELETE"])
    def api_delete_label(session_id, seq_id):
        try:
            _validate_session_id(session_id)
        except ValueError:
            return jsonify({"error": "invalid session_id"}), 400
        with _LABELS_LOCK:
            labels = _load_labels(session_id)
            key = str(seq_id)
            labels.pop(key, None)
            _save_labels(session_id, labels)
        return jsonify({"ok": True})

    @app.route("/api/sessions/<session_id>/export")
    def api_export(session_id):
        try:
            session_dir = _validate_session_id(session_id)
        except ValueError:
            return jsonify({"error": "invalid session_id"}), 400
        fmt = request.args.get("fmt", "jsonl")
        labels = _load_labels(session_id)
        if fmt == "jsonl":
            jsonl_paths = _find_all_jsonl(session_dir)
            if not jsonl_paths:
                return jsonify({"error": "session not found"}), 404

            def generate():
                for jsonl_path in jsonl_paths:
                    with jsonl_path.open() as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                ev = json.loads(line)
                            except json.JSONDecodeError:
                                continue
                            seq = ev.get("sequence_id")
                            ev_labels = labels.get(str(seq))
                            if ev_labels:
                                ev["label"] = ev_labels
                            yield json.dumps(ev, default=str) + "\n"

            return Response(generate(), mimetype="application/x-jsonlines",
                            headers={"Content-Disposition": f"attachment; filename={session_id}_labeled.jsonl"})

        return jsonify({"error": "unsupported format"}), 400

    @app.route("/api/sessions/<session_id>", methods=["DELETE"])
    def api_delete_session(session_id):
        try:
            session_dir = _validate_session_id(session_id)
        except ValueError:
            return jsonify({"error": "invalid session_id"}), 400
        if not session_dir.exists() or not session_dir.is_dir():
            return jsonify({"error": "session not found"}), 404
        shutil.rmtree(session_dir)
        logger.info("Deleted session %s", session_id)
        return jsonify({"ok": True})

    @app.route("/api/sessions/<session_id>/stats")
    def api_stats(session_id):
        try:
            _validate_session_id(session_id)
        except ValueError:
            return jsonify({"error": "invalid session_id"}), 400
        type_counts = {}
        app_counts = {}
        action_counts = {}
        total = 0
        for ev, _ in _iter_events(session_id, 0, 1_000_000_000):
            total += 1
            t = ev.get("type", "unknown")
            type_counts[t] = type_counts.get(t, 0) + 1
            if t == "action":
                at = ev.get("data", {}).get("action_type", "unknown")
                action_counts[at] = action_counts.get(at, 0) + 1
            app_name = ev.get("data", {}).get("app_name")
            if app_name:
                app_counts[app_name] = app_counts.get(app_name, 0) + 1
        return jsonify({
            "total_events": total,
            "by_type": dict(sorted(type_counts.items())),
            "by_action": dict(sorted(action_counts.items())),
            "top_apps": dict(sorted(app_counts.items(), key=lambda x: -x[1])[:10]),
        })

    return app


def run_ui(host="127.0.0.1", port=8899, debug=False, open_browser=True):
    app = build_app()
    if open_browser:
        import webbrowser
        webbrowser.open(f"http://{host}:{port}")
    url = f"http://{host}:{port}"
    print(f"  CUA Dataset Viewer launched at {url}")
    print(f"  Press Ctrl+C to stop the server")
    app.run(host=host, port=port, debug=debug, use_reloader=False)
