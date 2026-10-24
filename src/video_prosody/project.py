"""Portable project manifest shared by pipelines and the local reviewer."""

import json
import re
from pathlib import Path


class Project:
    def __init__(self, manifest):
        self.manifest = Path(manifest).resolve()
        config = json.loads(self.manifest.read_text(encoding="utf-8"))
        if config.get("schema_version") != 1:
            raise ValueError("Unsupported project schema_version")
        self.root = self.manifest.parent
        self.name = config.get("name", "Video transcript review")
        self.review_edits = self.path(config.get("review_edits", "review-edits"))
        self.prosody_dir = self.path(config.get("prosody", "prosody"))
        self.catalog = {}
        for item in config["videos"]:
            vid = item["video_id"]
            if not isinstance(vid, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", vid):
                raise ValueError("video_id must contain only letters, digits, _ or -")
            if vid in self.catalog:
                raise ValueError("Duplicate video_id")
            record = {
                **item,
                "url": item.get("url", ""),
                "title": item.get("title", vid),
            }
            for key in (
                "raw_json",
                "punctuated_json",
                "correction_history",
                "media",
                "audio",
                "analysis_audio",
            ):
                if record.get(key):
                    record[key] = self.path(record[key])
            if not record.get("raw_json"):
                raise ValueError("raw_json is required")
            record.setdefault("punctuated_json", record["raw_json"])
            self.catalog[vid] = record
        from .mora_review import MoraStore
        from .prosody import ProsodyStore
        from .server import TranscriptStore

        self.prosody = ProsodyStore(self)
        self.moras = MoraStore(self)
        self.transcripts = TranscriptStore(self)

    def path(self, value):
        return (self.root / value).resolve()

    def record(self, video_id):
        if video_id not in self.catalog:
            raise ValueError("Unknown video")
        return self.catalog[video_id]

    def document(self, video_id):
        self.record(video_id)
        return self.transcripts.document(video_id)

    def serve(
        self, port=8765, open_browser=False, *, assets=None, document_transform=None
    ):
        from http.server import ThreadingHTTPServer

        from .server import make_handler

        server = ThreadingHTTPServer(
            ("127.0.0.1", port),
            make_handler(self, assets=assets, document_transform=document_transform),
        )
        url = f"http://127.0.0.1:{server.server_port}/"
        print(f"Local review: {url}", flush=True)
        if open_browser:
            import webbrowser

            webbrowser.open(url)
        try:
            server.serve_forever()
        finally:
            server.server_close()
