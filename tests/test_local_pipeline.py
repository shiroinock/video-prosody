"""The public local-file pipeline works without an acquisition adapter."""

import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from video_prosody import pipeline
from video_prosody.pipeline import analyze, digest


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


class LocalPipelineTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.models = self.root / "models"
        self.models.mkdir()
        (self.models / "config.json").write_text("{}")
        (self.models / "weights.npz").write_bytes(b"model")
        self.aligner = self.root / "narabas.onnx"
        self.aligner.write_bytes(b"aligner")
        self.audio = self.root / "clip.mp4"
        self.audio.write_bytes(b"original local audio")
        self.calls = []

    def tearDown(self):
        self.temp.cleanup()

    def fake_transcribe(self, audio, **kwargs):
        self.calls.append("transcript")
        self.assertIsNone(kwargs["acquisition"])
        run = kwargs["output_dir"] / "run"
        raw = {
            "metadata": {"duration_seconds": 1},
            "result": {
                "text": "あ",
                "segments": [
                    {
                        "id": 0,
                        "text": "あ",
                        "start": 0,
                        "end": 1,
                        "words": [{"word": "あ", "start": 0, "end": 1}],
                    }
                ],
            },
        }
        write(run / "raw.json", raw)
        return run

    def fake_measure(self, document, raw, audio, output):
        self.calls.append("prosody")
        write(
            output,
            {
                "video_id": document["video_id"],
                "duration": 1,
                "title": "Synthetic",
                "transcript_revision": 0,
                "frames": [],
                "words": [
                    {
                        "id": 0,
                        "segment_id": 0,
                        "text": "あ",
                        "start": 0,
                        "end": 1,
                        "verified": False,
                    }
                ],
            },
        )

    def fake_readings(self, source, output):
        self.calls.append("readings")
        write(output, {"words": {"0": {"text": "あ", "pronunciation": "あ"}}})

    def fake_align(self, doc, audio, model, output, manifest=None):
        self.calls.append("moras")
        write(
            output,
            {
                "video_id": doc["video_id"],
                "duration": 1,
                "source_word_revision": 0,
                "moras": [],
                "unaligned_segments": [],
            },
        )
        if manifest is not None:
            write(manifest, {"active": output.name, "sha256": digest(output)})
        return {"moras": [], "unaligned_segments": []}

    def run_analysis(self, readings=None):
        modules = {
            "video_prosody.measurement": types.SimpleNamespace(
                measure=self.fake_measure
            ),
            "video_prosody.readings": types.SimpleNamespace(
                add_readings=readings or self.fake_readings
            ),
            "video_prosody.alignment": types.SimpleNamespace(
                align_moras=self.fake_align
            ),
            "imageio_ffmpeg": types.SimpleNamespace(get_ffmpeg_exe=lambda: "ffmpeg"),
        }

        def decode(command, check):
            self.calls.append("playback")
            self.assertEqual(command[command.index("-i") + 1], str(self.audio))
            self.assertNotIn("-ar", command)
            self.assertNotIn("-ac", command)
            Path(command[-1]).write_bytes(b"native rate pcm24")

        with (
            patch.dict(sys.modules, modules),
            patch("video_prosody.transcribe.transcribe", self.fake_transcribe),
            patch.object(pipeline, "version", return_value="test"),
            patch.object(pipeline.subprocess, "run", decode),
        ):
            return analyze(
                self.audio,
                output_dir=self.root / "results",
                model_path=self.models,
                alignment_model=self.aligner,
                progress=lambda _: None,
            )

    def test_local_file_runs_all_stages_and_reuses_without_adapter(self):
        project = self.run_analysis()
        self.assertEqual(
            self.calls, ["playback", "transcript", "prosody", "readings", "moras"]
        )
        vid = "local_" + digest(self.audio)[:16]
        record = project.record(vid)
        self.assertEqual(record["media"], self.audio)
        self.assertEqual(record["analysis_audio"], self.audio)
        self.assertEqual(project.document(vid)["source_url"], "")
        self.assertEqual(project.document(vid)["media_url"], f"/media/{vid}")
        self.assertEqual(project.prosody.document(vid)["media_url"], f"/audio/{vid}")
        project.transcripts.save_change(
            dict(
                video_id=vid,
                segment_id=0,
                text="い",
                verified=True,
                expected_revision=0,
                reason="test",
            )
        )
        self.calls.clear()
        again = self.run_analysis()
        self.assertEqual(self.calls, [])
        self.assertEqual(again.document(vid)["segments"][0]["text"], "い")

    def test_failure_resumes_without_repeating_completed_stages(self):
        def fail(*args):
            raise RuntimeError("interrupted readings")

        with self.assertRaisesRegex(RuntimeError, "interrupted"):
            self.run_analysis(readings=fail)
        self.calls.clear()
        self.run_analysis()
        self.assertEqual(self.calls, ["readings", "moras"])

    def test_source_change_preserves_old_analysis(self):
        first = self.run_analysis()
        self.audio.write_bytes(b"changed source")
        self.calls.clear()
        second = self.run_analysis()
        self.assertEqual(
            self.calls, ["playback", "transcript", "prosody", "readings", "moras"]
        )
        manifests = list(
            (self.root / "results").glob("local/*/analyses/*/project.json")
        )
        self.assertEqual(len(manifests), 2)
        self.assertIsNot(first, second)

    def test_modified_playback_is_rejected(self):
        self.run_analysis()
        playback = next((self.root / "results").glob("local/*/audio/playback.wav"))
        playback.write_bytes(b"tampered")
        with self.assertRaisesRegex(RuntimeError, "missing or changed"):
            self.run_analysis()

    def test_public_apis_reject_remote_input_before_inference(self):
        from video_prosody.transcribe import transcribe

        with self.assertRaisesRegex(ValueError, "local audio/video"):
            analyze("https://example.test/clip.mp4")
        with self.assertRaisesRegex(ValueError, "local audio/video"):
            transcribe(
                "https://youtu.be/abcdefghijk",
                model_path=self.models,
                output_dir=self.root,
            )
        with self.assertRaises(FileNotFoundError):
            analyze(self.root / "missing.wav")

    def test_public_cli_passes_a_local_path_and_opens_review(self):
        from video_prosody.__main__ import main

        project = Mock()
        with (
            patch.object(pipeline, "analyze", return_value=project) as prepare,
            patch.object(sys, "argv", ["video-prosody", "analyze", str(self.audio)]),
        ):
            main()
        self.assertEqual(prepare.call_args.args, (self.audio,))
        project.serve.assert_called_once_with(0, open_browser=True)
