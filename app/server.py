from __future__ import annotations

import json
import mimetypes
import os
import subprocess
import threading
import webbrowser
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template, request, send_file, send_from_directory

try:
    from .repository import VideoRepository
    from .trim_service import (
        TrimError,
        is_supported_video,
        make_output_path,
        parse_time_to_seconds,
        probe_video,
        resolve_binary,
        trim_video_copy,
        validate_trim_window,
    )
except ImportError:
    from repository import VideoRepository
    from trim_service import (
        TrimError,
        is_supported_video,
        make_output_path,
        parse_time_to_seconds,
        probe_video,
        resolve_binary,
        trim_video_copy,
        validate_trim_window,
    )


def pick_directory_dialog(initial_dir: str | None = None) -> str | None:
    if os.name != "nt":
        raise RuntimeError("Directory picker is currently supported on Windows only.")

    selected_path = ""
    if initial_dir:
        selected_path = str(Path(initial_dir).resolve())

    ps_selected = selected_path.replace("'", "''")
    ps_script = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$dialog = New-Object System.Windows.Forms.FolderBrowserDialog;"
        f"$dialog.SelectedPath = '{ps_selected}';"
        "if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {"
        "  [Console]::OutputEncoding = [System.Text.Encoding]::UTF8;"
        "  Write-Output $dialog.SelectedPath;"
        "}"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-STA", "-Command", ps_script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        detail = (result.stderr or "").strip()
        raise RuntimeError(detail or "Failed to open directory picker.")

    picked = (result.stdout or "").strip()
    if not picked:
        return None
    return str(Path(picked).resolve())


def create_app(config: dict[str, Any] | None = None) -> Flask:
    project_root = Path(__file__).resolve().parents[1]
    default_data_dir = project_root / "data"
    app = Flask(__name__, static_folder="static", template_folder="templates")

    app.config.from_mapping(
        DATA_DIR=str(default_data_dir),
        SOURCE_DIR=str(project_root / "原视频"),
        OUTPUT_DIR=str(project_root / "新视频"),
        MAX_CONTENT_LENGTH=8 * 1024 * 1024 * 1024,
        FFMPEG_BIN=resolve_binary("ffmpeg"),
        FFPROBE_BIN=resolve_binary("ffprobe"),
        PROBE_FUNC=probe_video,
        TRIM_FUNC=trim_video_copy,
    )

    if config:
        app.config.update(config)

    repository = VideoRepository(app.config["DATA_DIR"], app.config["OUTPUT_DIR"])
    app.config["REPOSITORY"] = repository

    def save_runtime_config() -> None:
        data_dir = Path(app.config["DATA_DIR"]).resolve()
        data_dir.mkdir(parents=True, exist_ok=True)
        runtime_config_file = data_dir / "app_config.json"
        payload = {
            "source_dir": str(Path(app.config["SOURCE_DIR"]).resolve()),
            "output_dir": str(Path(app.config["OUTPUT_DIR"]).resolve()),
        }
        runtime_config_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def load_runtime_config() -> None:
        runtime_config_file = Path(app.config["DATA_DIR"]).resolve() / "app_config.json"
        if not runtime_config_file.exists():
            return
        try:
            payload = json.loads(runtime_config_file.read_text(encoding="utf-8"))
        except Exception:
            return

        configured_source = payload.get("source_dir")
        if isinstance(configured_source, str) and configured_source.strip():
            resolved_source = Path(configured_source).resolve()
            legacy_source = (project_root / "bin" / "原视频").resolve()
            if resolved_source == legacy_source:
                resolved_source = (project_root / "原视频").resolve()
            app.config["SOURCE_DIR"] = str(resolved_source)

        configured_output = payload.get("output_dir")
        if isinstance(configured_output, str) and configured_output.strip():
            resolved_output = Path(configured_output).resolve()
            legacy_output = (project_root / "bin" / "新视频").resolve()
            if resolved_output == legacy_output:
                resolved_output = (project_root / "新视频").resolve()
            app.config["OUTPUT_DIR"] = str(resolved_output)

    load_runtime_config()
    repository.set_output_dir(app.config["OUTPUT_DIR"])

    # Clear runtime cache on each service start to avoid showing stale session records.
    repository.clear_cache(clear_all=False)

    def sync_source_videos() -> None:
        source_dir = Path(app.config["SOURCE_DIR"]).resolve()
        source_dir.mkdir(parents=True, exist_ok=True)

        valid_paths: set[str] = set()
        for file_path in sorted(source_dir.iterdir(), key=lambda p: p.name.lower()):
            if not file_path.is_file() or not is_supported_video(file_path.name):
                continue
            absolute = str(file_path.resolve())
            valid_paths.add(absolute)
            repository.upsert_source_video(absolute)

        repository.remove_missing_source_videos(valid_paths)

    def get_video_or_404(video_id: str) -> tuple[dict[str, Any] | None, tuple[Any, int] | None]:
        record = repository.get_video(video_id)
        if not record:
            return None, (jsonify({"error": "Video not found."}), 404)
        return record, None

    def ensure_video_metadata(video_id: str) -> dict[str, Any] | None:
        record = repository.get_video(video_id)
        if not record:
            return None

        duration = float(record.get("duration") or 0)
        width = int(record.get("width") or 0)
        height = int(record.get("height") or 0)

        if duration > 0 and width > 0 and height > 0:
            return record

        try:
            metadata = app.config["PROBE_FUNC"](app.config["FFPROBE_BIN"], record["path"])
            repository.update_video_metadata(video_id, metadata)
            refreshed = repository.get_video(video_id)
            return refreshed or record
        except Exception:
            return record

    @app.get("/")
    def index() -> str:
        return render_template("index.html")

    @app.get("/api/health")
    def health() -> Any:
        return jsonify({"ok": True})

    @app.get("/api/videos")
    def list_videos() -> Any:
        sync_source_videos()
        return jsonify({"videos": repository.list_videos()})

    @app.get("/api/config")
    def get_config() -> Any:
        return jsonify(
            {
                "source_dir": str(Path(app.config["SOURCE_DIR"]).resolve()),
                "output_dir": str(Path(app.config["OUTPUT_DIR"]).resolve()),
            }
        )

    @app.put("/api/config/source-dir")
    def set_source_dir() -> Any:
        payload = request.get_json(silent=True) or {}
        source_dir = str(payload.get("source_dir") or "").strip()
        if not source_dir:
            return jsonify({"error": "source_dir is required."}), 400

        resolved = Path(source_dir).expanduser().resolve()
        try:
            resolved.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            return jsonify({"error": f"Failed to create/access source directory: {exc}"}), 400

        app.config["SOURCE_DIR"] = str(resolved)
        save_runtime_config()
        sync_source_videos()
        return jsonify(
            {
                "ok": True,
                "source_dir": str(resolved),
                "output_dir": str(Path(app.config["OUTPUT_DIR"]).resolve()),
            }
        )

    @app.post("/api/config/source-dir/pick")
    def pick_source_dir() -> Any:
        payload = request.get_json(silent=True) or {}
        initial_dir = str(payload.get("initial_dir") or app.config["SOURCE_DIR"])
        try:
            picked = pick_directory_dialog(initial_dir)
        except Exception as exc:
            return jsonify({"error": f"Failed to open picker: {exc}"}), 500

        if not picked:
            return jsonify({"cancelled": True})
        return jsonify({"cancelled": False, "source_dir": picked})

    @app.put("/api/config/output-dir")
    def set_output_dir() -> Any:
        payload = request.get_json(silent=True) or {}
        output_dir = str(payload.get("output_dir") or "").strip()
        if not output_dir:
            return jsonify({"error": "output_dir is required."}), 400

        resolved = Path(output_dir).expanduser().resolve()
        try:
            resolved.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            return jsonify({"error": f"Failed to create/access output directory: {exc}"}), 400

        app.config["OUTPUT_DIR"] = str(resolved)
        repository.set_output_dir(str(resolved))
        save_runtime_config()
        return jsonify(
            {
                "ok": True,
                "source_dir": str(Path(app.config["SOURCE_DIR"]).resolve()),
                "output_dir": str(resolved),
            }
        )

    @app.post("/api/config/output-dir/pick")
    def pick_output_dir() -> Any:
        payload = request.get_json(silent=True) or {}
        initial_dir = str(payload.get("initial_dir") or app.config["OUTPUT_DIR"])
        try:
            picked = pick_directory_dialog(initial_dir)
        except Exception as exc:
            return jsonify({"error": f"Failed to open picker: {exc}"}), 500

        if not picked:
            return jsonify({"cancelled": True})
        return jsonify({"cancelled": False, "output_dir": picked})

    @app.get("/api/videos/<video_id>")
    def get_video(video_id: str) -> Any:
        sync_source_videos()
        record, error = get_video_or_404(video_id)
        if error:
            return error

        ensure = request.args.get("ensure_metadata", "1") != "0"
        if ensure:
            record = ensure_video_metadata(video_id) or record

        return jsonify({"video": repository.public_record(record)})

    @app.post("/api/upload")
    def upload_videos() -> Any:
        files = request.files.getlist("files")
        if not files:
            return jsonify({"error": "No files uploaded."}), 400

        uploaded: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []

        for file_item in files:
            filename = file_item.filename or ""
            if not filename:
                failed.append({"name": "unknown", "error": "Empty file name."})
                continue

            if not is_supported_video(filename):
                failed.append(
                    {
                        "name": filename,
                        "error": "Unsupported format. Allowed: mp4/mkv/mov/avi/wmv/flv/m4v/webm",
                    }
                )
                continue

            saved = repository.save_upload(file_item)
            try:
                metadata = app.config["PROBE_FUNC"](app.config["FFPROBE_BIN"], saved.absolute_path)
                if float(metadata.get("duration") or 0) <= 0:
                    raise ValueError("Failed to detect video duration.")
                uploaded.append(repository.register_video(saved, metadata))
            except Exception as exc:
                repository.remove_upload(saved.absolute_path)
                failed.append({"name": filename, "error": str(exc)})

        if not uploaded:
            return jsonify({"uploaded": [], "failed": failed}), 400

        return jsonify({"uploaded": uploaded, "failed": failed})

    @app.get("/api/videos/<video_id>/preview")
    def preview_video(video_id: str) -> Any:
        sync_source_videos()
        record, error = get_video_or_404(video_id)
        if error:
            return error

        path = Path(record["path"])
        if not path.exists():
            return jsonify({"error": "Source file missing."}), 404

        mime_type, _ = mimetypes.guess_type(path.name)
        return send_file(path, mimetype=mime_type or "application/octet-stream", conditional=True)

    @app.put("/api/videos/<video_id>/settings")
    def update_settings(video_id: str) -> Any:
        sync_source_videos()
        record, error = get_video_or_404(video_id)
        if error:
            return error

        record = ensure_video_metadata(video_id) or record
        payload = request.get_json(silent=True) or {}
        current = record.get("settings") or {}
        duration = float(record.get("duration") or 0)

        try:
            start = parse_time_to_seconds(payload.get("start", current.get("start", 0.0)))
            end = parse_time_to_seconds(payload.get("end", current.get("end", duration)))
            zoom = float(payload.get("zoom", current.get("zoom", 1.0)))
        except (ValueError, TypeError) as exc:
            return jsonify({"error": f"Invalid setting value: {exc}"}), 400

        if zoom < 1.0:
            zoom = 1.0
        if zoom > 20.0:
            zoom = 20.0

        try:
            validate_trim_window(duration, start, end)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        updated = repository.update_settings(video_id, start=start, end=end, zoom=zoom)
        return jsonify({"video": updated})

    def process_single_job(
        video_id: str,
        start_value: Any,
        end_value: Any,
        output_name: str | None,
    ) -> dict[str, Any]:
        record = ensure_video_metadata(video_id)
        if not record:
            raise ValueError("Video not found.")

        duration = float(record.get("duration") or 0)
        start = parse_time_to_seconds(start_value)
        end = parse_time_to_seconds(end_value)
        validate_trim_window(duration, start, end)

        output_path = make_output_path(
            str(repository.output_dir),
            record["name"],
            start,
            end,
            output_name,
        )

        command = app.config["TRIM_FUNC"](
            app.config["FFMPEG_BIN"],
            record["path"],
            output_path,
            start,
            end,
        )

        repository.update_settings(video_id, start=start, end=end)
        repository.set_last_output(video_id, output_path)

        output_name_only = Path(output_path).name
        return {
            "video_id": video_id,
            "video_name": record["name"],
            "start": start,
            "end": end,
            "output_name": output_name_only,
            "download_url": f"/api/outputs/{output_name_only}",
            "command": command,
        }

    @app.post("/api/trim/single")
    def trim_single() -> Any:
        payload = request.get_json(silent=True) or {}
        video_id = payload.get("video_id")
        if not video_id:
            return jsonify({"error": "video_id is required."}), 400

        record, error = get_video_or_404(video_id)
        if error:
            return error

        settings = record.get("settings") or {}
        start_value = payload.get("start", settings.get("start", 0.0))
        end_value = payload.get("end", settings.get("end", record.get("duration", 0.0)))

        try:
            result = process_single_job(video_id, start_value, end_value, payload.get("output_name"))
        except (ValueError, TrimError) as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"error": f"Trim failed: {exc}"}), 500

        return jsonify({"result": result})

    @app.post("/api/trim/batch")
    def trim_batch() -> Any:
        payload = request.get_json(silent=True) or {}
        mode = payload.get("mode", "common")

        jobs: list[dict[str, Any]] = []
        if mode == "common":
            video_ids = payload.get("video_ids") or []
            if not video_ids:
                return jsonify({"error": "video_ids is required for common mode."}), 400
            start = payload.get("start")
            end = payload.get("end")
            for video_id in video_ids:
                jobs.append(
                    {
                        "video_id": video_id,
                        "start": start,
                        "end": end,
                        "output_name": None,
                    }
                )
        elif mode == "individual":
            jobs = payload.get("jobs") or []
            if not jobs:
                return jsonify({"error": "jobs is required for individual mode."}), 400
        else:
            return jsonify({"error": "mode must be common or individual."}), 400

        success: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []

        for job in jobs:
            video_id = job.get("video_id")
            if not video_id:
                failed.append({"video_id": None, "error": "video_id is missing."})
                continue

            record = repository.get_video(video_id)
            if not record:
                failed.append({"video_id": video_id, "error": "Video not found."})
                continue

            settings = record.get("settings") or {}
            start_value = job.get("start", settings.get("start", 0.0))
            end_value = job.get("end", settings.get("end", record.get("duration", 0.0)))

            try:
                success.append(
                    process_single_job(
                        video_id,
                        start_value,
                        end_value,
                        job.get("output_name"),
                    )
                )
            except Exception as exc:
                failed.append(
                    {
                        "video_id": video_id,
                        "video_name": record.get("name"),
                        "error": str(exc),
                    }
                )

        return jsonify(
            {
                "mode": mode,
                "total": len(jobs),
                "success_count": len(success),
                "failed_count": len(failed),
                "success": success,
                "failed": failed,
            }
        )

    @app.post("/api/videos/clear")
    def clear_videos() -> Any:
        payload = request.get_json(silent=True) or {}
        mode = payload.get("mode")
        if not mode:
            mode = request.form.get("mode", "all")

        mode = str(mode).lower()
        if mode not in {"all", "cache"}:
            return jsonify({"error": "mode must be all or cache."}), 400

        summary = repository.clear_cache(clear_all=(mode == "all"))
        return jsonify({"ok": True, "mode": mode, "summary": summary})

    @app.delete("/api/videos/<video_id>")
    def delete_video(video_id: str) -> Any:
        sync_source_videos()
        deleted, source_type = repository.delete_video(video_id)
        if not deleted:
            return jsonify({"error": "Video not found."}), 404
        return jsonify({"ok": True, "video_id": video_id, "source_type": source_type})

    @app.get("/api/outputs/<path:filename>")
    def download_output(filename: str) -> Any:
        return send_from_directory(repository.output_dir, filename, as_attachment=True)

    @app.post("/api/outputs/open")
    def open_output_dir() -> Any:
        output_dir = repository.output_dir
        output_dir.mkdir(parents=True, exist_ok=True)

        try:
            if os.name == "nt":
                os.startfile(str(output_dir))  # type: ignore[attr-defined]
            else:
                return jsonify({"error": "Open output folder is currently supported on Windows only."}), 400
        except Exception as exc:
            return jsonify({"error": f"Failed to open output folder: {exc}"}), 500

        return jsonify({"ok": True, "path": str(output_dir)})

    @app.post("/api/source/open")
    def open_source_dir() -> Any:
        source_dir = Path(app.config["SOURCE_DIR"]).resolve()
        source_dir.mkdir(parents=True, exist_ok=True)

        try:
            if os.name == "nt":
                os.startfile(str(source_dir))  # type: ignore[attr-defined]
            else:
                return jsonify({"error": "Open source folder is currently supported on Windows only."}), 400
        except Exception as exc:
            return jsonify({"error": f"Failed to open source folder: {exc}"}), 500

        return jsonify({"ok": True, "path": str(source_dir)})

    return app


if __name__ == "__main__":
    application = create_app()
    host = os.getenv("TRIM_UI_HOST", "127.0.0.1")
    port = int(os.getenv("TRIM_UI_PORT", "5000"))

    if os.getenv("TRIM_UI_OPEN_BROWSER", "0") == "1":
        open_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
        url = f"http://{open_host}:{port}"
        threading.Timer(1.2, lambda: webbrowser.open(url, new=2)).start()

    application.run(host=host, port=port, debug=False)
