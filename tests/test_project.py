"""Standalone synthetic fixtures: these tests do not need the corpus or models."""

import hashlib
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from video_prosody import Project
from video_prosody.mora import split
from video_prosody.prosody import metrics
from video_prosody.server import make_handler


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def fixture(root, vid="sample", text="あ"):
    raw = {
        "metadata": {"duration_seconds": 1},
        "result": {
            "text": text,
            "segments": [
                {
                    "id": 0,
                    "text": text,
                    "start": 0,
                    "end": 1,
                    "words": [{"word": text, "start": 0, "end": 1}],
                }
            ],
        },
    }
    write(root / "raw.json", raw)
    (root / "clip.mp4").write_bytes(b"0123456789")
    write(
        root / "project.json",
        {
            "schema_version": 1,
            "videos": [{"video_id": vid, "raw_json": "raw.json", "media": "clip.mp4"}],
        },
    )
    write(
        root / f"prosody/{vid}-measured.json",
        {
            "video_id": vid,
            "duration": 1,
            "transcript_revision": 0,
            "frames": [],
            "words": [
                {
                    "id": 0,
                    "segment_id": 0,
                    "text": text,
                    "start": 0,
                    "end": 1,
                    "verified": False,
                }
            ],
        },
    )
    write(
        root / f"prosody/{vid}-readings.json",
        {"words": {"0": {"text": text, "pronunciation": "あ"}}},
    )
    alignment = root / f"prosody/{vid}-moras-estimated.json"
    write(
        alignment,
        {
            "video_id": vid,
            "duration": 1,
            "source_word_revision": 0,
            "moras": [
                {
                    "id": 0,
                    "word_id": 0,
                    "word_text": text,
                    "label": "あ",
                    "pronunciation": "あ",
                    "start": 0,
                    "end": 1,
                    "verified": False,
                }
            ],
        },
    )
    write(
        root / f"prosody/{vid}-moras-active.json",
        {
            "active": alignment.name,
            "sha256": hashlib.sha256(alignment.read_bytes()).hexdigest(),
        },
    )
    return Project(root / "project.json")


class ProjectTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.project = fixture(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_raw_only_project_and_relative_paths(self):
        doc = self.project.document("sample")
        self.assertEqual(doc["segments"][0]["text"], "あ")
        self.assertEqual(self.project.record("sample")["media"], self.root / "clip.mp4")
        self.assertEqual(
            self.project.moras.document("sample")["moras"][0]["label"], "あ"
        )

    def test_projects_with_same_ids_are_isolated(self):
        other = fixture(self.root / "other", text="い")
        payload = dict(
            video_id="sample",
            segment_id=0,
            expected_revision=0,
            text="う",
            verified=True,
            reason="listened",
        )
        self.project.transcripts.save_change(payload)
        self.assertEqual(self.project.document("sample")["revision"], 1)
        self.assertEqual(other.document("sample")["revision"], 0)
        self.assertEqual(other.document("sample")["segments"][0]["text"], "い")
        self.assertEqual(self.project.prosody.STORE, self.root / "prosody")
        self.assertEqual(other.prosody.STORE, self.root / "other/prosody")

    def test_transcript_conflicts_and_immutable_source(self):
        source = (self.root / "raw.json").read_bytes()
        payload = dict(
            video_id="sample",
            segment_id=0,
            expected_revision=0,
            text="う",
            verified=True,
            reason="listened",
        )
        self.project.transcripts.save_change(payload)
        with self.assertRaises(RuntimeError):
            self.project.transcripts.save_change(payload)
        self.assertEqual((self.root / "raw.json").read_bytes(), source)
        (self.root / "raw.json").write_bytes(source + b" ")
        with self.assertRaises(RuntimeError):
            self.project.document("sample")

    def test_word_edits_validate_ranges_and_keep_measured_values(self):
        source = (self.root / "prosody/sample-measured.json").read_bytes()
        payload = dict(
            video_id="sample",
            word_id=0,
            expected_revision=0,
            text="あ",
            start=0.1,
            end=0.8,
            verified=True,
            reason="listened",
        )
        self.project.prosody.save(payload)
        word = self.project.prosody.document("sample")["words"][0]
        self.assertEqual(word["original"]["start"], 0)
        self.assertEqual(word["measured"]["duration"], 0.7)
        with self.assertRaises(RuntimeError):
            self.project.prosody.save(payload)
        payload.update(expected_revision=1, end=float("nan"))
        with self.assertRaises(ValueError):
            self.project.prosody.save(payload)
        self.assertEqual(
            (self.root / "prosody/sample-measured.json").read_bytes(), source
        )

    def test_mora_source_hash_and_traversal(self):
        payload = dict(
            video_id="sample",
            mora_id=0,
            expected_revision=0,
            source_alignment="sample-moras-estimated.json",
            label="あ",
            start=0.1,
            end=0.8,
            verified=True,
            reason="listened",
        )
        self.project.moras.save(payload)
        self.assertEqual(self.project.moras.document("sample")["revision"], 1)
        with self.assertRaises(RuntimeError):
            self.project.moras.save(payload)
        alignment = self.root / "prosody/sample-moras-estimated.json"
        alignment.write_bytes(alignment.read_bytes() + b" ")
        with self.assertRaises(RuntimeError):
            self.project.moras.document("sample")
        write(
            self.root / "prosody/sample-moras-active.json",
            {"active": "../outside.json"},
        )
        with self.assertRaises(ValueError):
            self.project.moras.alignment_path("sample")
        with self.assertRaises(ValueError):
            self.project.moras.alignment_path("../outside")

    def test_manifest_rejects_duplicate_and_unsafe_ids(self):
        for videos in [
            [{"video_id": "../escape", "raw_json": "raw.json"}],
            [{"video_id": "x", "raw_json": "raw.json"}] * 2,
        ]:
            write(self.root / "bad.json", {"schema_version": 1, "videos": videos})
            with self.assertRaises(ValueError):
                Project(self.root / "bad.json")

    def test_kana_and_acoustic_summary(self):
        self.assertEqual(split("キョー"), ["きょ", "ー"])
        self.assertEqual(
            metrics(
                {"start": 0, "end": 0.3},
                [
                    {
                        "time": 0.1,
                        "pitch_hz": 200,
                        "voiced_probability": 0.9,
                        "rms_dbfs": -20,
                    }
                ],
            )["pitch_hz"],
            200,
        )

    def test_separate_audio_is_used_for_precision(self):
        config = json.loads((self.root / "project.json").read_text())
        config["videos"][0].update(
            url="https://example.test/source",
            audio="precision.wav",
        )
        write(self.root / "project.json", config)
        (self.root / "precision.wav").write_bytes(b"abcdefghij")
        project = Project(self.root / "project.json")
        doc = project.document("sample")
        self.assertEqual(doc["source_url"], "https://example.test/source")
        self.assertEqual(doc["audio_url"], "/audio/sample")
        self.assertEqual(
            project.prosody.document("sample")["media_url"], "/audio/sample"
        )
        self.assertEqual(project.moras.document("sample")["media_url"], "/audio/sample")
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(project))
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with urlopen(
                Request(
                    f"http://127.0.0.1:{server.server_port}/audio/sample",
                    headers={"Range": "bytes=2-4"},
                )
            ) as response:
                self.assertEqual(response.status, 206)
                self.assertTrue(response.headers["Content-Type"].startswith("audio/"))
                self.assertEqual(response.read(), b"cde")
        finally:
            server.shutdown()
            server.server_close()
            worker.join()

    def test_http_assets_media_ranges_and_audited_save(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.project))
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        origin = f"http://127.0.0.1:{server.server_port}"
        try:
            with urlopen(origin + "/api/catalog") as response:
                self.assertEqual(json.load(response)[0]["video_id"], "sample")
            for route in [
                "/",
                "/prosody",
                "/moras",
                "/app.js",
                "/prosody.js",
                "/moras.js",
                "/api/video?id=sample",
                "/api/prosody?id=sample",
                "/api/moras?id=sample",
            ]:
                with urlopen(origin + route) as response:
                    self.assertEqual(response.status, 200)
            with urlopen(
                Request(origin + "/media/sample", headers={"Range": "bytes=2-4"})
            ) as response:
                self.assertEqual(response.status, 206)
                self.assertEqual(response.read(), b"234")
            payload = json.dumps(
                dict(
                    video_id="sample",
                    segment_id=0,
                    text="え",
                    verified=True,
                    expected_revision=0,
                )
            ).encode()
            headers = {"Content-Type": "application/json", "Origin": origin}
            with urlopen(
                Request(origin + "/api/save", data=payload, headers=headers)
            ) as response:
                self.assertEqual(json.load(response)["revision"], 1)
            with self.assertRaises(HTTPError) as error:
                urlopen(Request(origin + "/api/save", data=payload, headers=headers))
            self.assertEqual(error.exception.code, 409)
            with self.assertRaises(HTTPError) as error:
                urlopen(
                    Request(
                        origin + "/api/save",
                        data=payload,
                        headers={"Content-Type": "application/json"},
                    )
                )
            self.assertEqual(error.exception.code, 403)
        finally:
            server.shutdown()
            server.server_close()
            worker.join()


if __name__ == "__main__":
    unittest.main()
