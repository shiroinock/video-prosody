"""Immutable acoustic measurements and separately audited word edits."""

import json
import math
import statistics
import threading
import uuid
from datetime import datetime, timezone

from .mora import hiragana
from .mora import split as split_moras


def read(p):
    return json.loads(p.read_text())


def metrics(word, frames):
    fs = [f for f in frames if word["start"] <= f["time"] < word["end"]]
    ps = sorted(
        f["pitch_hz"]
        for f in fs
        if f["pitch_hz"] is not None and f["voiced_probability"] >= 0.5
    )
    rms = (
        10 * math.log10(sum(10 ** (f["rms_dbfs"] / 10) for f in fs) / len(fs))
        if fs
        else None
    )
    return {
        "duration": round(word["end"] - word["start"], 4),
        "pitch_hz": round(statistics.median(ps), 2) if ps else None,
        "rms_dbfs": round(rms, 2) if rms is not None else None,
        "voiced_fraction": round(len(ps) / len(fs), 3) if fs else 0,
    }


class ProsodyStore:
    def __init__(self, project):
        self.project = project
        self.STORE = project.prosody_dir
        self.LOCK = threading.Lock()

    def measured(self, vid):
        self.project.record(vid)
        if not (self.STORE / f"{vid}-measured.json").exists():
            raise ValueError("この動画はまだ未計測です")
        return read(self.STORE / f"{vid}-measured.json")

    def state(self, vid):
        p = self.STORE / f"{vid}-edits.json"
        return (
            read(p)
            if p.exists()
            else {"video_id": vid, "revision": 0, "words": {}, "history": []}
        )

    def document(self, vid):
        data = self.measured(vid)
        saved = self.state(vid)
        words = []
        dictionary_path = self.STORE / f"{vid}-readings.json"
        dictionary = (
            read(dictionary_path) if dictionary_path.exists() else {"words": {}}
        )
        for base in data["words"]:
            w = {**base, **saved["words"].get(str(base["id"]), {})}
            w["measured"] = metrics(w, data["frames"])
            w["original"] = {k: base[k] for k in ["text", "start", "end"]}
            w["dictionary"] = dictionary["words"].get(str(base["id"]), {})
            w["dictionary_text_changed"] = w["text"] != w["dictionary"].get(
                "text", w["text"]
            )
            w["pronunciation"] = w.get("pronunciation_override") or (
                None
                if w["dictionary_text_changed"]
                else w["dictionary"].get("pronunciation")
            )
            w["moras"] = split_moras(w["pronunciation"]) if w["pronunciation"] else []
            w["mora_count"] = len(w["moras"]) if w["moras"] else None
            w["pronunciation_verified"] = w.get("pronunciation_verified", False)
            words.append(w)
        for i, w in enumerate(words):
            w["gap_before"] = round(w["start"] - words[i - 1]["end"], 4) if i else None
            w["gap_after"] = (
                round(words[i + 1]["start"] - w["end"], 4)
                if i + 1 < len(words)
                else None
            )
        try:
            transcript = read(self.project.review_edits / f"{vid}.json")
            revision = transcript["revision"]
        except FileNotFoundError:
            revision = 0
        return {
            **data,
            "reading_dictionary": {k: v for k, v in dictionary.items() if k != "words"},
            "words": words,
            "revision": saved["revision"],
            "history": saved["history"],
            "transcript_changed": revision != data["transcript_revision"],
            "media_url": f"/audio/{vid}"
            if self.project.record(vid).get("audio")
            else f"/media/{vid}",
            "audio_url": f"/audio/{vid}"
            if self.project.record(vid).get("audio")
            else None,
            "source_url": self.project.record(vid).get("url", ""),
        }

    def save(self, payload):
        vid = payload.get("video_id")
        data = self.measured(vid)
        with self.LOCK:
            saved = self.state(vid)
            if payload.get("expected_revision") != saved["revision"]:
                raise RuntimeError(
                    "別の画面で更新されています。再読み込みしてください。"
                )
            w = next(
                (
                    w
                    for w in self.document(vid)["words"]
                    if type(payload.get("word_id")) is int
                    and w["id"] == payload["word_id"]
                ),
                None,
            )
            if w is None:
                raise ValueError("Unknown word")
            keys = [
                "text",
                "start",
                "end",
                "pitch_override_hz",
                "volume_override_dbfs",
                "verified",
                "pronunciation_override",
                "pronunciation_verified",
            ]
            after = {k: payload.get(k) for k in keys}
            after["pronunciation_override"] = payload.get(
                "pronunciation_override", w.get("pronunciation_override")
            )
            after["pronunciation_verified"] = payload.get(
                "pronunciation_verified", w.get("pronunciation_verified", False)
            )
            pronunciation = after["pronunciation_override"]
            if pronunciation is not None:
                if not isinstance(pronunciation, str) or len(pronunciation) > 200:
                    raise ValueError("発音の入力が不正です")
                pronunciation = hiragana(pronunciation).strip()
                split_moras(pronunciation)
                after["pronunciation_override"] = pronunciation or None
            if type(after["pronunciation_verified"]) is not bool:
                raise ValueError("Invalid pronunciation verification")
            if (
                not isinstance(after["text"], str)
                or not after["text"].strip()
                or len(after["text"]) > 200
            ):
                raise ValueError("単語を入力してください")
            if type(after["verified"]) is not bool:
                raise ValueError("Invalid verification")
            for k in ["start", "end"]:
                if type(after[k]) not in (int, float) or not math.isfinite(after[k]):
                    raise ValueError("時刻を数値で入力してください")
            if not 0 <= after["start"] < after["end"] <= data["duration"]:
                raise ValueError("開始 < 終了、動画内の時刻を指定してください")
            for k, lo, hi in [
                ("pitch_override_hz", 20, 2000),
                ("volume_override_dbfs", -160, 0),
            ]:
                v = after[k]
                if v is not None and (
                    type(v) not in (int, float)
                    or not math.isfinite(v)
                    or not lo <= v <= hi
                ):
                    raise ValueError("高さ・音量の値が範囲外です")
            reason = payload.get("reason", "")
            if not isinstance(reason, str) or len(reason) > 2000:
                raise ValueError("Invalid reason")
            before = {k: w.get(k) for k in keys}
            if before == after:
                return {"changed": False, "revision": saved["revision"]}
            event = {
                "event_id": str(uuid.uuid4()),
                "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "word_id": w["id"],
                "before": before,
                "after": after,
                "reason": reason,
                "revision": saved["revision"] + 1,
            }
            saved["words"][str(w["id"])] = after
            saved["history"].append(event)
            saved["revision"] += 1
            path = self.STORE / f"{vid}-edits.json"
            tmp = self.STORE / f".{uuid.uuid4().hex}.tmp"
            tmp.write_text(
                json.dumps(saved, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
            )
            tmp.replace(path)
            return {"changed": True, "revision": saved["revision"], "event": event}
