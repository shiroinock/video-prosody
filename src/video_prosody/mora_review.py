"""Immutable alignment with separate, revisioned mora edits."""

import hashlib
import json
import math
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .mora import hiragana, split


def read(p):
    return json.loads(p.read_text())


class MoraStore:
    def __init__(self, project):
        self.project = project
        self.STORE = project.prosody_dir
        self.LOCK = threading.Lock()

    def alignment_path(self, vid):
        self.project.record(vid)
        manifest = self.STORE / f"{vid}-moras-active.json"
        name = (
            read(manifest)["active"]
            if manifest.exists()
            else f"{vid}-moras-estimated.json"
        )
        if Path(name).name != name:
            raise ValueError("Invalid alignment path")
        path = self.STORE / name
        if (
            manifest.exists()
            and read(manifest).get("sha256")
            != hashlib.sha256(path.read_bytes()).hexdigest()
        ):
            raise RuntimeError("Alignment source checksum changed")
        return path

    def base(self, vid):
        self.project.record(vid)
        return read(self.alignment_path(vid))

    def state_path(self, vid):
        digest = hashlib.sha256(self.alignment_path(vid).read_bytes()).hexdigest()[:12]
        return self.STORE / f"{vid}-moras-edits-{digest}.json"

    def state(self, vid):
        p = self.state_path(vid)
        return (
            read(p)
            if p.exists()
            else {
                "video_id": vid,
                "source_alignment": self.alignment_path(vid).name,
                "source_alignment_sha256": hashlib.sha256(
                    self.alignment_path(vid).read_bytes()
                ).hexdigest(),
                "revision": 0,
                "moras": {},
                "history": [],
            }
        )

    def document(self, vid):
        data = self.base(vid)
        saved = self.state(vid)
        current = self.project.prosody.document(vid)
        words = {w["id"]: w for w in current["words"]}
        units = []
        for original in data["moras"]:
            m = {**original, **saved["moras"].get(str(original["id"]), {})}
            m["original"] = {k: original[k] for k in ["label", "start", "end"]}
            m["duration"] = round(m["end"] - m["start"], 4)
            w = words.get(m["word_id"], {})
            m["source_changed"] = (
                w.get("text") != original["word_text"]
                or w.get("pronunciation") != original["pronunciation"]
            )
            units.append(m)
        for i, m in enumerate(units):
            m["gap_before"] = round(m["start"] - units[i - 1]["end"], 4) if i else None
            m["gap_after"] = (
                round(units[i + 1]["start"] - m["end"], 4)
                if i + 1 < len(units)
                else None
            )
        return {
            **data,
            "alignment_file": self.alignment_path(vid).name,
            "edits_file": self.state_path(vid).name,
            "moras": units,
            "revision": saved["revision"],
            "history": saved["history"],
            "frames": current["frames"],
            "media_url": f"/audio/{vid}"
            if self.project.record(vid).get("audio")
            else f"/media/{vid}",
            "audio_url": f"/audio/{vid}"
            if self.project.record(vid).get("audio")
            else None,
            "source_url": self.project.record(vid).get("url", ""),
            "source_word_revision_changed": current["revision"]
            != data["source_word_revision"],
        }

    def save(self, p):
        vid = p.get("video_id")
        data = self.base(vid)
        with self.LOCK:
            saved = self.state(vid)
            if p.get("source_alignment") != self.alignment_path(vid).name:
                raise RuntimeError("推定元が変わっています。再読み込みしてください。")
            if p.get("expected_revision") != saved["revision"]:
                raise RuntimeError(
                    "別の画面で更新されています。再読み込みしてください。"
                )
            m = next(
                (
                    m
                    for m in self.document(vid)["moras"]
                    if type(p.get("mora_id")) is int and m["id"] == p["mora_id"]
                ),
                None,
            )
            if m is None:
                raise ValueError("Unknown mora")
            label = p.get("label")
            if not isinstance(label, str) or len(label) > 10 or len(split(label)) != 1:
                raise ValueError("1モーラの仮名を入力してください")
            after = {
                "label": hiragana(label).strip(),
                "start": p.get("start"),
                "end": p.get("end"),
                "verified": p.get("verified"),
            }
            if type(after["verified"]) is not bool:
                raise ValueError("Invalid verification")
            for k in ["start", "end"]:
                if type(after[k]) not in (int, float) or not math.isfinite(after[k]):
                    raise ValueError("時刻を数値で指定してください")
            if not 0 <= after["start"] < after["end"] <= data["duration"]:
                raise ValueError("開始 < 終了となる動画内の時刻を指定してください")
            reason = p.get("reason", "")
            if not isinstance(reason, str) or len(reason) > 2000:
                raise ValueError("Invalid reason")
            before = {k: m[k] for k in after}
            if before == after:
                return {"revision": saved["revision"], "changed": False}
            event = {
                "event_id": str(uuid.uuid4()),
                "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "mora_id": m["id"],
                "word_id": m["word_id"],
                "before": before,
                "after": after,
                "reason": reason,
                "revision": saved["revision"] + 1,
            }
            saved["moras"][str(m["id"])] = after
            saved["history"].append(event)
            saved["revision"] += 1
            path = self.state_path(vid)
            tmp = self.STORE / f".{uuid.uuid4().hex}.tmp"
            tmp.write_text(
                json.dumps(saved, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
            )
            tmp.replace(path)
            return {"revision": saved["revision"], "changed": True, "event": event}
