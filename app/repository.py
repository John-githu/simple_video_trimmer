from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from werkzeug.utils import secure_filename


@dataclass
class SavedUpload:
    original_name: str
    stored_name: str
    absolute_path: str
    size: int


class VideoRepository:
    def __init__(self, data_dir: str, output_dir: str | None = None):
        self.data_dir = Path(data_dir).resolve()
        self.upload_dir = self.data_dir / "uploads"
        self.output_dir = Path(output_dir).resolve() if output_dir else self.data_dir / "outputs"
        self.state_file = self.data_dir / "state.json"

        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self._lock = threading.Lock()
        self._state = self._load_state()

    def _load_state(self) -> dict[str, Any]:
        if not self.state_file.exists():
            return {"videos": {}, "hidden_sources": []}

        try:
            payload = json.loads(self.state_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {"videos": {}, "hidden_sources": []}

        payload.setdefault("videos", {})
        payload.setdefault("hidden_sources", [])
        return payload

    def _save_state(self) -> None:
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(self._state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(self.state_file)

    @staticmethod
    def _default_settings(duration: float) -> dict[str, float]:
        duration_value = max(float(duration or 0), 0.0)
        return {
            "start": 0.0,
            "end": duration_value,
            "zoom": 1.0,
        }

    @staticmethod
    def _normalize_path(path: str) -> str:
        return str(Path(path).resolve())

    def _find_video_id_by_path_unlocked(self, absolute_path: str) -> str | None:
        normalized = self._normalize_path(absolute_path)
        for video_id, record in self._state["videos"].items():
            if self._normalize_path(record.get("path", "")) == normalized:
                return video_id
        return None

    def _is_hidden_source_unlocked(self, absolute_path: str) -> bool:
        normalized = self._normalize_path(absolute_path)
        hidden = {
            self._normalize_path(path)
            for path in self._state.get("hidden_sources", [])
            if isinstance(path, str)
        }
        return normalized in hidden

    def _hide_source_path_unlocked(self, absolute_path: str) -> None:
        normalized = self._normalize_path(absolute_path)
        hidden = list(self._state.get("hidden_sources", []))
        normalized_hidden = {
            self._normalize_path(path)
            for path in hidden
            if isinstance(path, str)
        }
        if normalized not in normalized_hidden:
            hidden.append(normalized)
            self._state["hidden_sources"] = hidden

    def save_upload(self, file_storage: Any) -> SavedUpload:
        original_name = file_storage.filename or "video"
        safe_name = secure_filename(original_name) or "video"
        file_id = uuid.uuid4().hex
        stored_name = f"{file_id}_{safe_name}"
        destination = self.upload_dir / stored_name
        file_storage.save(destination)

        return SavedUpload(
            original_name=original_name,
            stored_name=stored_name,
            absolute_path=str(destination.resolve()),
            size=destination.stat().st_size,
        )

    def remove_upload(self, absolute_path: str) -> None:
        path = Path(absolute_path)
        if path.exists():
            path.unlink()

    def register_video(self, upload: SavedUpload, metadata: dict[str, Any]) -> dict[str, Any]:
        video_id = uuid.uuid4().hex
        duration = float(metadata.get("duration") or 0.0)
        now = datetime.now(timezone.utc).isoformat()

        record = {
            "id": video_id,
            "name": upload.original_name,
            "stored_name": upload.stored_name,
            "path": self._normalize_path(upload.absolute_path),
            "size": upload.size,
            "duration": duration,
            "width": int(metadata.get("width") or 0),
            "height": int(metadata.get("height") or 0),
            "format_name": metadata.get("format_name") or "unknown",
            "uploaded_at": now,
            "source_type": "upload",
            "settings": self._default_settings(duration),
            "last_output": None,
        }

        with self._lock:
            self._state["videos"][video_id] = record
            self._save_state()

        return self.public_record(record)

    def upsert_source_video(self, absolute_path: str) -> dict[str, Any] | None:
        source_path = Path(absolute_path).resolve()
        if not source_path.exists() or not source_path.is_file():
            return None

        now = datetime.now(timezone.utc).isoformat()
        size = source_path.stat().st_size

        with self._lock:
            if self._is_hidden_source_unlocked(str(source_path)):
                return None

            existing_id = self._find_video_id_by_path_unlocked(str(source_path))
            if existing_id:
                record = self._state["videos"][existing_id]
                record["name"] = source_path.name
                record["size"] = size
                record["path"] = str(source_path)
                record["source_type"] = "source"
                record.setdefault("settings", self._default_settings(record.get("duration") or 0))
                self._save_state()
                return self.public_record(record)

            record = {
                "id": uuid.uuid4().hex,
                "name": source_path.name,
                "stored_name": None,
                "path": str(source_path),
                "size": size,
                "duration": 0.0,
                "width": 0,
                "height": 0,
                "format_name": "unknown",
                "uploaded_at": now,
                "source_type": "source",
                "settings": self._default_settings(0),
                "last_output": None,
            }
            self._state["videos"][record["id"]] = record
            self._save_state()
            return self.public_record(record)

    def remove_missing_source_videos(self, valid_paths: set[str]) -> None:
        normalized_valid = {self._normalize_path(item) for item in valid_paths}
        with self._lock:
            remove_ids = []
            for video_id, record in self._state["videos"].items():
                if record.get("source_type") != "source":
                    continue
                if self._normalize_path(record.get("path", "")) not in normalized_valid:
                    remove_ids.append(video_id)

            for video_id in remove_ids:
                self._state["videos"].pop(video_id, None)

            hidden = list(self._state.get("hidden_sources", []))
            filtered_hidden = [
                self._normalize_path(path)
                for path in hidden
                if isinstance(path, str) and self._normalize_path(path) in normalized_valid
            ]
            hidden_changed = filtered_hidden != hidden
            if hidden_changed:
                self._state["hidden_sources"] = filtered_hidden

            if remove_ids or hidden_changed:
                self._save_state()

    def update_video_metadata(self, video_id: str, metadata: dict[str, Any]) -> dict[str, Any] | None:
        with self._lock:
            record = self._state["videos"].get(video_id)
            if not record:
                return None

            duration = float(metadata.get("duration") or 0.0)
            record["duration"] = duration
            record["width"] = int(metadata.get("width") or 0)
            record["height"] = int(metadata.get("height") or 0)
            record["format_name"] = metadata.get("format_name") or record.get("format_name") or "unknown"

            settings = record.setdefault("settings", self._default_settings(duration))
            if float(settings.get("end", 0.0)) <= 0 and duration > 0:
                settings["end"] = duration

            self._save_state()
            return self.public_record(record)

    def list_videos(self) -> list[dict[str, Any]]:
        with self._lock:
            values = [self.public_record(v) for v in self._state["videos"].values()]
        values.sort(
            key=lambda item: (
                str(item.get("name", "")).lower(),
                str(item.get("uploaded_at", "")),
            )
        )
        return values

    def get_video(self, video_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._state["videos"].get(video_id)
            return dict(record) if record else None

    def update_settings(
        self,
        video_id: str,
        start: float | None = None,
        end: float | None = None,
        zoom: float | None = None,
    ) -> dict[str, Any] | None:
        with self._lock:
            record = self._state["videos"].get(video_id)
            if not record:
                return None

            settings = record.setdefault("settings", self._default_settings(record.get("duration") or 0))
            if start is not None:
                settings["start"] = float(start)
            if end is not None:
                settings["end"] = float(end)
            if zoom is not None:
                settings["zoom"] = float(zoom)

            self._save_state()
            return self.public_record(record)

    def set_last_output(self, video_id: str, output_path: str) -> None:
        with self._lock:
            record = self._state["videos"].get(video_id)
            if not record:
                return
            record["last_output"] = output_path
            self._save_state()

    def set_output_dir(self, output_dir: str) -> str:
        resolved = Path(output_dir).resolve()
        resolved.mkdir(parents=True, exist_ok=True)
        self.output_dir = resolved
        return str(resolved)

    def delete_video(self, video_id: str) -> tuple[bool, str | None]:
        with self._lock:
            record = self._state["videos"].get(video_id)
            if not record:
                return False, None

            source_type = str(record.get("source_type") or "")
            remove_path = str(record.get("path") or "")
            self._state["videos"].pop(video_id, None)

            if source_type == "source":
                self._hide_source_path_unlocked(remove_path)

            self._save_state()

        if source_type == "upload":
            try:
                file_path = Path(remove_path)
                if file_path.exists():
                    file_path.unlink()
            except OSError:
                pass

        return True, source_type

    def clear_cache(self, clear_all: bool = False) -> dict[str, int]:
        deleted_upload_records = 0
        reset_source_records = 0

        with self._lock:
            videos = self._state["videos"]
            to_delete_upload_files: list[str] = []

            if clear_all:
                for record in videos.values():
                    if record.get("source_type") == "upload":
                        to_delete_upload_files.append(record.get("path", ""))
                deleted_upload_records = sum(1 for v in videos.values() if v.get("source_type") == "upload")
                reset_source_records = sum(1 for v in videos.values() if v.get("source_type") == "source")
                videos.clear()
                self._state["hidden_sources"] = []
                self._save_state()
            else:
                remove_ids = []
                for video_id, record in videos.items():
                    if record.get("source_type") == "upload":
                        remove_ids.append(video_id)
                        to_delete_upload_files.append(record.get("path", ""))
                    else:
                        record["settings"] = self._default_settings(record.get("duration") or 0)
                        record["last_output"] = None
                        reset_source_records += 1

                deleted_upload_records = len(remove_ids)
                for video_id in remove_ids:
                    videos.pop(video_id, None)
                self._state["hidden_sources"] = []
                self._save_state()

        for path in to_delete_upload_files:
            try:
                file_path = Path(path)
                if file_path.exists():
                    file_path.unlink()
            except OSError:
                pass

        return {
            "deleted_upload_records": deleted_upload_records,
            "reset_source_records": reset_source_records,
        }

    @staticmethod
    def public_record(record: dict[str, Any]) -> dict[str, Any]:
        output_name = Path(record["last_output"]).name if record.get("last_output") else None
        return {
            "id": record["id"],
            "name": record["name"],
            "size": record["size"],
            "duration": record.get("duration") or 0.0,
            "width": record.get("width") or 0,
            "height": record.get("height") or 0,
            "format_name": record.get("format_name") or "unknown",
            "uploaded_at": record.get("uploaded_at") or "",
            "source_type": record.get("source_type") or "unknown",
            "settings": record.get("settings") or {},
            "last_output_name": output_name,
        }
