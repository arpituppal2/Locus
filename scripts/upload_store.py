"""Upload persistence for dashboard-provided files."""
from __future__ import annotations

import base64
import binascii
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.runtime_policy import ROOT

UPLOAD_DIR = ROOT / "uploads"
MAX_UPLOAD_BYTES = 20 * 1024 * 1024


def _safe_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", name.strip())
    cleaned = cleaned.strip("._")
    return cleaned or "upload.bin"


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def save_uploads(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    saved: list[dict[str, Any]] = []

    for item in items:
        name = _safe_name(str(item.get("name") or "upload.bin"))
        encoded = str(item.get("content_b64") or "")
        if "," in encoded and encoded.split(",", 1)[0].startswith("data:"):
            encoded = encoded.split(",", 1)[1]
        try:
            payload = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            saved.append({"name": name, "ok": False, "error": "invalid base64 payload"})
            continue
        if len(payload) > MAX_UPLOAD_BYTES:
            saved.append({"name": name, "ok": False, "error": "file exceeds 20 MB upload limit"})
            continue

        path = UPLOAD_DIR / f"{_utc_stamp()}-{name}"
        path.write_bytes(payload)
        meta = {
            "ok": True,
            "name": name,
            "path": str(path),
            "size": len(payload),
            "type": str(item.get("type") or ""),
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        (path.with_suffix(path.suffix + ".meta.json")).write_text(json.dumps(meta, indent=2) + "\n")
        saved.append(meta)
    return saved


def list_uploads(limit: int = 20) -> list[dict[str, Any]]:
    if not UPLOAD_DIR.exists():
        return []
    uploads: list[dict[str, Any]] = []
    for path in sorted(UPLOAD_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if path.name.endswith(".meta.json") or not path.is_file():
            continue
        meta_path = path.with_suffix(path.suffix + ".meta.json")
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text())
            except json.JSONDecodeError:
                meta = {}
        else:
            meta = {}
        uploads.append(
            {
                "ok": True,
                "name": meta.get("name") or path.name,
                "path": str(path),
                "size": meta.get("size") or path.stat().st_size,
                "type": meta.get("type") or "",
                "created_at": meta.get("created_at") or "",
            }
        )
        if len(uploads) >= limit:
            break
    return uploads
