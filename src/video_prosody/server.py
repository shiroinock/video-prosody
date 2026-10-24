#!/usr/bin/env python3
"""Local synchronized video transcript reviewer. Run with python server.py."""

import hashlib
import json
import mimetypes
import re
import threading
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


class TranscriptStore:
    def __init__(self, project):
        self.catalog = project.catalog
        self.review_edits = project.review_edits
        self.lock = threading.Lock()

    def state(self, vid):
        path = self.review_edits / f"{vid}.json"
        if path.exists():
            saved = read(path)
            source = self.catalog[vid]["punctuated_json"]
            if (
                saved["source_sha256"]
                != hashlib.sha256(Path(source).read_bytes()).hexdigest()
            ):
                raise RuntimeError(
                    "Transcript source changed; retain edits and resolve source before saving"
                )
            return saved
        record = self.catalog[vid]
        return {
            "video_id": vid,
            "revision": 0,
            "source_punctuated_json": str(record["punctuated_json"]),
            "source_sha256": hashlib.sha256(
                Path(record["punctuated_json"]).read_bytes()
            ).hexdigest(),
            "segments": {},
            "history": [],
        }

    def document(self, vid):
        if vid not in self.catalog:
            raise ValueError("Unknown video")
        record = self.catalog[vid]
        raw = read(record["raw_json"])
        punct = read(record["punctuated_json"])
        if "segments" not in punct:
            punct = punct["result"]
        audit = (
            read(record["correction_history"])
            if record.get("correction_history")
            else {"changes": []}
        )
        saved = self.state(vid)
        raw_by_id = {s["id"]: s for s in raw["result"]["segments"]}
        segments = []
        for seg in punct["segments"]:
            entry = saved["segments"].get(str(seg["id"]), {})
            notes = [
                {
                    "kind": c["stage"],
                    "reason": c["reason"],
                    "requires_audio_review": c.get("requires_audio_review", False),
                    "before": c["before"],
                    "after": c["after"],
                }
                for c in audit["changes"]
                if c["segment_id"] == seg["id"]
            ]
            notes.extend(
                {"kind": "flag", **f}
                for f in audit.get("review_flags", [])
                if f["segment_id"] == seg["id"]
            )
            segments.append(
                {
                    "id": seg["id"],
                    "start": seg["start"],
                    "end": seg["end"],
                    "raw_text": raw_by_id[seg["id"]]["text"],
                    "suggested_text": seg["text"],
                    "text": entry.get("text", seg["text"]),
                    "verified": entry.get("verified", False),
                    "notes": notes,
                }
            )
        return {
            "video_id": vid,
            "title": record["title"],
            "source_url": record["url"],
            "audio_url": f"/audio/{vid}" if record.get("audio") else None,
            "duration": raw["metadata"]["duration_seconds"],
            "revision": saved["revision"],
            "segments": segments,
            "history": saved["history"],
            "media_url": f"/media/{vid}" if record.get("media") else None,
        }

    def save_change(self, payload):
        vid = payload.get("video_id")
        if vid not in self.catalog:
            raise ValueError("Unknown video")
        if (
            type(payload.get("segment_id")) is not int
            or type(payload.get("verified")) is not bool
        ):
            raise ValueError("Invalid segment or verification status")
        if not isinstance(payload.get("text"), str) or len(payload["text"]) > 20000:
            raise ValueError("Invalid text")
        reason = payload.get("reason", "")
        if not isinstance(reason, str) or len(reason) > 2000:
            raise ValueError("Invalid reason")
        with self.lock:
            current = self.state(vid)
            if payload.get("expected_revision") != current["revision"]:
                raise RuntimeError(
                    "他の画面で更新されています。再読み込みしてください。"
                )
            doc = self.document(vid)
            segment = next(
                (s for s in doc["segments"] if s["id"] == payload["segment_id"]), None
            )
            if segment is None:
                raise ValueError("Unknown segment")
            if (
                payload["text"] == segment["text"]
                and payload["verified"] == segment["verified"]
            ):
                return {"revision": current["revision"], "changed": False}
            timestamp = datetime.now(timezone.utc).isoformat()
            event = {
                "event_id": str(uuid.uuid4()),
                "created_at_utc": timestamp,
                "actor": "user_via_local_review_ui",
                "segment_id": segment["id"],
                "start": segment["start"],
                "end": segment["end"],
                "before": segment["text"],
                "after": payload["text"],
                "previous_verified": segment["verified"],
                "verified": payload["verified"],
                "reason": reason,
                "revision": current["revision"] + 1,
            }
            current["segments"][str(segment["id"])] = {
                "text": payload["text"],
                "verified": payload["verified"],
                "updated_at_utc": timestamp,
            }
            current["history"].append(event)
            current["revision"] += 1
            self.review_edits.mkdir(parents=True, exist_ok=True)
            path = self.review_edits / f"{vid}.json"
            temporary = self.review_edits / f".{vid}-{uuid.uuid4().hex}.tmp"
            temporary.write_text(
                json.dumps(current, ensure_ascii=False, indent=2) + "\n"
            )
            temporary.replace(path)
            return {"revision": current["revision"], "changed": True, "event": event}


def make_handler(project, *, assets=None, document_transform=None):
    """Build a local handler; explicit trusted adapters may add assets/doc fields."""
    assets = dict(assets or {})

    def document(vid):
        result = project.transcripts.document(vid)
        return document_transform(result) if document_transform else result

    class Handler(BaseHTTPRequestHandler):
        def send_json(self, status, data):
            self.send_bytes(
                status,
                json.dumps(data, ensure_ascii=False).encode(),
                "application/json; charset=utf-8",
            )

        def send_bytes(self, status, body, kind):
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            parsed = urlparse(self.path)
            try:
                if parsed.path == "/api/catalog":
                    return self.send_json(
                        200,
                        [
                            {
                                "video_id": v,
                                "title": r["title"],
                                "has_prosody": (
                                    project.prosody_dir / f"{v}-measured.json"
                                ).exists(),
                                "has_moras": (
                                    project.prosody_dir / f"{v}-moras-active.json"
                                ).exists()
                                or (
                                    project.prosody_dir / f"{v}-moras-estimated.json"
                                ).exists(),
                            }
                            for v, r in project.catalog.items()
                        ],
                    )
                if parsed.path == "/api/prosody":
                    return self.send_json(
                        200,
                        project.prosody.document(
                            parse_qs(parsed.query).get("id", [""])[0]
                        ),
                    )
                if parsed.path == "/api/moras":
                    return self.send_json(
                        200,
                        project.moras.document(
                            parse_qs(parsed.query).get("id", [""])[0]
                        ),
                    )
                if parsed.path == "/api/video":
                    vid = parse_qs(parsed.query).get("id", [""])[0]
                    if vid not in project.catalog:
                        return self.send_json(404, {"error": "Unknown video"})
                    return self.send_json(200, document(vid))
                if parsed.path == "/api/export":
                    vid = parse_qs(parsed.query).get("id", [""])[0]
                    if vid not in project.catalog:
                        return self.send_json(404, {"error": "Unknown video"})
                    doc = document(vid)
                    return self.send_json(
                        200,
                        {
                            "document": doc,
                            "review_state": project.transcripts.state(vid),
                        },
                    )
                if parsed.path.startswith(("/media/", "/audio/")):
                    vid = parsed.path.split("/", 2)[2]
                    if vid not in project.catalog:
                        return self.send_json(404, {"error": "Unknown video"})
                    files = {
                        v: (r.get("audio") or r.get("media"))
                        if parsed.path.startswith("/audio/")
                        else (r.get("media") or r.get("audio"))
                        for v, r in project.catalog.items()
                        if r.get("audio") or r.get("media")
                    }
                    if vid not in files:
                        return self.send_json(
                            404, {"error": "Local media is unavailable"}
                        )
                    return self.serve_media(files[vid])
                if parsed.path in assets:
                    body, kind = assets[parsed.path]
                    return self.send_bytes(200, body, kind)
                name = {
                    "/": "index.html",
                    "/app.js": "app.js",
                    "/common.js": "common.js",
                    "/style.css": "style.css",
                    "/prosody": "prosody.html",
                    "/prosody.js": "prosody.js",
                    "/moras": "moras.html",
                    "/moras.js": "moras.js",
                }.get(parsed.path)
                if name:
                    return self.send_bytes(
                        200,
                        (Path(__file__).with_name("web") / name).read_bytes(),
                        mimetypes.guess_type(name)[0] or "text/plain",
                    )
                self.send_json(404, {"error": "Not found"})
            except (ValueError, OSError, RuntimeError) as exc:
                self.send_json(500, {"error": str(exc)})

        def serve_media(self, path):
            size = path.stat().st_size
            start, end = 0, size - 1
            header = self.headers.get("Range")
            if header:
                match = re.fullmatch(r"bytes=(\d*)-(\d*)", header)
                if not match or not any(match.groups()):
                    return self.send_bytes(416, b"Invalid byte range", "text/plain")
                a, b = match.groups()
                if a:
                    start = int(a)
                    end = min(int(b) if b else size - 1, size - 1)
                else:
                    start = max(0, size - int(b))
                if start > end or start >= size:
                    return self.send_bytes(416, b"Range out of bounds", "text/plain")
            self.send_response(206 if header else 200)
            self.send_header(
                "Content-Type", mimetypes.guess_type(path.name)[0] or "video/mp4"
            )
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(end - start + 1))
            if header:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            if self.command == "HEAD":
                return
            with path.open("rb") as handle:
                handle.seek(start)
                remaining = end - start + 1
                while remaining:
                    chunk = handle.read(min(262144, remaining))
                    if not chunk:
                        break
                    try:
                        self.wfile.write(chunk)
                    except (BrokenPipeError, ConnectionResetError):
                        break
                    remaining -= len(chunk)

        def do_POST(self):
            if self.path not in ("/api/save", "/api/prosody/save", "/api/moras/save"):
                return self.send_json(404, {"error": "Not found"})
            if self.headers.get("Origin") != f"http://{self.headers.get('Host')}":
                return self.send_json(
                    403, {"error": "同じ確認画面から保存してください。"}
                )
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                return self.send_json(415, {"error": "JSON required"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length < 100000:
                    raise ValueError("Invalid request size")
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError("JSON object required")
                result = (
                    project.moras.save(payload)
                    if self.path == "/api/moras/save"
                    else project.prosody.save(payload)
                    if self.path == "/api/prosody/save"
                    else project.transcripts.save_change(payload)
                )
                self.send_json(200, result)
            except RuntimeError as exc:
                self.send_json(409, {"error": str(exc)})
            except (ValueError, TypeError) as exc:
                self.send_json(400, {"error": str(exc)})

    return Handler
