"""Optional inference tests use a generated waveform, never corpus media."""

import importlib.util
import json
import math
import tempfile
import unittest
import wave
from pathlib import Path

HAS_AUDIO = all(
    importlib.util.find_spec(name)
    for name in ["numpy", "librosa", "imageio_ffmpeg", "janome"]
)
HAS_JAPANESE = all(
    importlib.util.find_spec(name) for name in ["numpy", "fugashi", "unidic_lite"]
)


class PipelineTest(unittest.TestCase):
    @unittest.skipUnless(HAS_AUDIO, "requires prosody extra")
    def test_measure_known_pitch_and_volume_and_refuse_overwrite(self):
        import numpy as np

        from video_prosody.measurement import measure

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audio = root / "tone.wav"
            samples = (
                0.2 * np.sin(2 * np.pi * 220 * np.arange(16000) / 16000) * 32767
            ).astype("<i2")
            with wave.open(str(audio), "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(16000)
                handle.writeframes(samples.tobytes())
            segment = {"id": 0, "text": "あ", "start": 0, "end": 1}
            document = {
                "video_id": "synthetic",
                "title": "Tone",
                "revision": 0,
                "segments": [segment],
            }
            raw = {
                "result": {
                    "segments": [
                        {**segment, "words": [{"word": "あ", "start": 0, "end": 1}]}
                    ]
                }
            }
            result = measure(document, raw, audio, root / "measured.json")
            frames = [frame for frame in result["frames"] if 0.2 < frame["time"] < 0.8]
            pitch = np.median(
                [frame["pitch_hz"] for frame in frames if frame["pitch_hz"]]
            )
            rms = np.median([frame["rms_dbfs"] for frame in frames])
            self.assertAlmostEqual(pitch, 220, delta=3)
            self.assertAlmostEqual(rms, 20 * math.log10(0.2 / math.sqrt(2)), delta=0.2)
            self.assertEqual(result["words"][0]["text"], "あ")
            with self.assertRaises(FileExistsError):
                measure(document, raw, audio, root / "measured.json")

    @unittest.skipUnless(HAS_AUDIO, "requires prosody extra")
    def test_native_stereo_energy_survives_opposite_phase_and_high_frequencies(self):
        import numpy as np
        import soundfile as sf

        from video_prosody.measurement import measure

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            segment = {"id": 0, "text": "あ", "start": 0, "end": 1}
            doc = {
                "video_id": "stereo",
                "title": "Native audio",
                "revision": 0,
                "segments": [segment],
            }
            raw = {
                "result": {
                    "segments": [
                        {**segment, "words": [{"word": "あ", "start": 0, "end": 1}]}
                    ]
                }
            }
            sr = 48000
            for frequency in [220, 12000]:
                y = 0.2 * np.sin(2 * np.pi * frequency * np.arange(sr) / sr)
                source = root / f"{frequency}.wav"
                sf.write(source, np.stack([y, -y], axis=1), sr, subtype="PCM_24")
                result = measure(doc, raw, source, root / f"{frequency}-measured.json")
                self.assertEqual(result["method"]["sample_rate"], sr)
                self.assertEqual(result["method"]["channels"], 2)
                frames = [f for f in result["frames"] if 0.2 < f["time"] < 0.8]
                volume = np.median([f["rms_dbfs"] for f in frames])
                self.assertAlmostEqual(
                    volume, 20 * math.log10(0.2 / math.sqrt(2)), delta=0.2
                )
                if frequency == 220:
                    pitch = np.median([f["pitch_hz"] for f in frames if f["pitch_hz"]])
                    self.assertAlmostEqual(pitch, 220, delta=3)
                    self.assertTrue(all(f["pitch_channel"] in (0, 1) for f in frames))

    @unittest.skipUnless(HAS_JAPANESE, "requires japanese extra")
    def test_dictionary_readings_on_an_unrelated_recording(self):
        from video_prosody.readings import add_readings

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "measured.json"
            source.write_text(
                json.dumps(
                    {
                        "video_id": "synthetic",
                        "words": [{"id": 0, "segment_id": 0, "text": "こんにちは"}],
                    }
                )
            )
            output = Path(directory) / "readings.json"
            result = add_readings(source, output)
            self.assertEqual(result["video_id"], "synthetic")
            self.assertEqual(
                result["words"]["0"]["moras"], ["こ", "ん", "に", "ち", "わ"]
            )
            with self.assertRaises(FileExistsError):
                add_readings(source, output)

    @unittest.skipUnless(importlib.util.find_spec("numpy"), "requires numpy")
    def test_ctc_repeated_phone_needs_blank(self):
        import numpy as np

        from video_prosody.alignment import SYMBOLS, align

        a = SYMBOLS.index("a")
        scores = np.full((7, len(SYMBOLS)), -9.0)
        for i, token in enumerate([0, a, a, 0, a, a, 0]):
            scores[i, token] = 9
        spans = align(scores, [a, a])
        self.assertLessEqual(spans[0]["frame_end"], spans[1]["frame_start"])
        with self.assertRaises(ValueError):
            align(scores[:1], [a, a])


if __name__ == "__main__":
    unittest.main()
