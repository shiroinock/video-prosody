# video-prosody

[![Library CI](https://github.com/shiroinock/video-prosody/actions/workflows/ci.yml/badge.svg)](https://github.com/shiroinock/video-prosody/actions/workflows/ci.yml)

Transcribe a **local video or audio file**, measure speech timing and acoustic
prosody, and review the results in a local browser UI. The Japanese analysis
pipeline produces a transcript, word timings, pitch/volume measurements,
pronunciations and estimated mora boundaries.

Version 0.1 is experimental. Estimates and human corrections are stored separately;
model output is never treated as human verification. The review UI is currently
in Japanese.

## Requirements

| Use | Requirements |
| --- | --- |
| Full `analyze` pipeline | **Apple Silicon Mac** with an [MLX-supported macOS](https://ml-explore.github.io/mlx/build/html/install.html), native arm64 Python **3.11+**, Metal GPU access, inference extras and both models below; Japanese speech |
| Review an existing project | Python 3.11+ and the core package; no inference extras or models |
| Development checks | Python 3.11+; Node.js for JavaScript syntax checking |

The full inference flow has been exercised on Apple Silicon with Python 3.11.
CI checks the core and packaging on Linux with Python 3.11 and 3.13; it does not
run the full model inference flow. Intel Macs, Windows and Linux are not supported
for the bundled MLX transcription backend.

The two model files total about **4.4 GB**. Allow additional space for Python
dependencies, download caches, decoded playback audio and analysis results.
Installation/model preparation require internet access. With the models and
pronunciation dictionary prepared, analysis and review use local files.

## Quick start: local file to review

Run the following steps from the same terminal on an Apple Silicon Mac. Keep the
terminal open while using the review page.

### 1. Install the library and inference dependencies

Check that `python3 --version` is 3.11 or newer and that
`python3 -c "import platform; print(platform.machine())"` prints `arm64`.
Then clone the repository and create a virtual environment:

```sh
git clone https://github.com/shiroinock/video-prosody.git
cd video-prosody
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[asr,prosody,japanese]'
```

The extras install MLX Whisper, the acoustic measurement tools and the Japanese
reading/alignment dependencies. FFmpeg is supplied by `imageio-ffmpeg`; a separate
FFmpeg installation is not required for these commands. No media downloader or
remote playback extension is needed.

### 2. Prepare both models

Download the [MLX Whisper large-v3 model](https://huggingface.co/mlx-community/whisper-large-v3-mlx).
This pins the revision used for the initial setup and records it in
`provenance.json`:

```sh
video-prosody prepare-model \
  --model-path work/models/large-v3 \
  --revision 49e6aa286ad60c14352c404340ded53710378a11
```

Download the separate [narabas v0 ONNX model](https://github.com/darashi/narabas-models/releases/tag/v0)
from its publisher's release. Only the ONNX file is needed; do not install the
narabas training project for this workflow. The temporary filename keeps an
interrupted download out of the model search path:

```sh
mkdir -p work/models/narabas
curl --fail --location --retry 3 \
  --output work/models/narabas/narabas-v0.onnx.part \
  https://github.com/darashi/narabas-models/releases/download/v0/narabas-v0.onnx \
  && mv work/models/narabas/narabas-v0.onnx.part work/models/narabas/narabas-v0.onnx
```

The prepared files should look like this:

```text
work/models/
├── large-v3/
│   ├── config.json
│   ├── weights.npz
│   └── provenance.json
└── narabas/
    └── narabas-v0.onnx
```

The Whisper weights are about 3.08 GB and the ONNX model about 1.26 GB. Model
weights are downloaded separately and are not included in this repository.

### 3. Prepare the dictionary and check the runtime

The first `pyopenjtalk` use may download its Open JTalk dictionary. Run this while
online, before starting the analysis:

```sh
python -c "import pyopenjtalk; print(pyopenjtalk.g2p('こんにちは'))"
```

Confirm that the active Python environment can access Metal and bundled FFmpeg:

```sh
python - <<'PY'
import imageio_ffmpeg
import mlx.core as mx
from video_prosody.pipeline import find_models

assert mx.metal.is_available(), "Metal GPU is unavailable"
whisper, aligner = find_models()
print("Whisper:", whisper)
print("Aligner:", aligner)
print("FFmpeg:", imageio_ffmpeg.get_ffmpeg_exe())
PY
```

If a check fails, use the [troubleshooting](#troubleshooting) notes below before
running the full pipeline.

### 4. Analyze your local recording

Replace the example input with an **existing local file containing audio**.
Keep quotes around paths containing spaces:

```sh
video-prosody analyze "/absolute/path/to/clip.mp4" --output-dir work/results
```

This runs transcription, acoustic measurements, Japanese readings and mora
alignment, then opens the transcript review page. It prints the generated
`project.json` path and a URL such as `http://127.0.0.1:PORT/`. The port is chosen
automatically; use the URL printed by your run. A first analysis can take time,
particularly for longer recordings.

The example stores results under `work/results/`, which is ignored by Git in this
checkout. Models and local recordings are not uploaded by the analysis commands.
Keep your original input file in place: the generated project references it for
original-media playback and reproducibility.

### 5. Review and save corrections

The review is three steps, shown at the top of every page. The recording picker
and step links keep the same recording selected.

| Step | What to check |
| --- | --- |
| 1 文字起こし (`/`) | Play each segment, edit recognized text and save; mark **聴いて確認済み** only after listening. Filter to unverified or flagged segments |
| 2 単語と声 (`/prosody`) | Inspect word timings, readings, pitch, volume and gaps; adjust word boundaries |
| 3 モーラ (`/moras`) | Loop a mora or its start/end boundary, adjust times, and save the reason and verification state |

Playback controls stay at the bottom of each page. **確認済みにして次へ** saves the
current item as verified and plays the next one. Keyboard shortcuts:

| Key | Action |
| --- | --- |
| Space | Play / pause |
| R | Play the selected segment, word or mora |
| ← / → | Previous / next item |
| Ctrl+Enter (⌘+Enter) | Mark verified, save and go to the next item |
| Ctrl+S (⌘+S) | Save without changing the verification state |
| ? | Show the shortcut list |

Word and mora review use a separate PCM24 playback WAV at the original sample rate
and channel count. Boundary controls allow 0.01/0.05-second adjustments; those
controls do not imply that the model's estimates have that precision.

Saves keep the original estimates and append revisioned correction history. Press
**Ctrl+C in the terminal** to stop the review server; saved changes remain on disk.
Do not delete the results directory if you want to retain those changes.

## Reopen, resume and choose paths

From a new terminal, activate the same environment and rerun the same command:

```sh
cd video-prosody
source .venv/bin/activate
video-prosody analyze "/absolute/path/to/clip.mp4" --output-dir work/results
```

Completed stages are reused after hash checks. An interrupted analysis resumes at
the first incomplete stage. Changes to the source content, models or analysis
settings/dependencies select a separate analysis run, preserving earlier results
and corrections.

Results from the quick start have this layout:

```text
work/results/local/<source-sha256>/
├── audio/playback.wav
├── playback.json
└── analyses/<signature>/
    ├── project.json
    ├── state.json
    ├── transcripts/
    └── prosody/
```

To open a completed project without running model setup or analysis again, pass
the actual path printed after `Ready:`:

```sh
video-prosody review --project "/path/from/Ready/project.json" --port 8765
```

Then open `http://127.0.0.1:8765/` yourself. This `review` command serves the project
without automatically launching a browser.

Other analysis options:

```sh
# Fixed port; open the printed URL yourself.
video-prosody analyze "/absolute/path/to/clip.mp4" --output-dir work/results --no-open --port 8765

# Produce results, print the project path and exit without starting a server.
video-prosody analyze "/absolute/path/to/clip.mp4" --output-dir work/results --no-serve
```

Without `--output-dir`, results go to
`~/Library/Application Support/video-prosody/local` on macOS, or
`$XDG_DATA_HOME/video-prosody/local` (default `~/.local/share/video-prosody/local`)
on other POSIX systems. `VIDEO_PROSODY_OUTPUT_DIR` also sets the results root.

Models are found under `VIDEO_PROSODY_MODEL_DIR`, the application cache's `models/`,
or `work/models/` in the source checkout/current directory. The expected model
subdirectories are always `large-v3/` and `narabas/`. To use other locations,
provide explicit paths:

```sh
video-prosody analyze "/absolute/path/to/clip.mp4" \
  --model-path "/path/to/whisper-model-directory" \
  --alignment-model "/path/to/narabas-v0.onnx" \
  --output-dir work/results
```

## Troubleshooting

| Symptom | Check or action |
| --- | --- |
| `video-prosody: command not found` | Run `source .venv/bin/activate` in the checkout, or use `.venv/bin/video-prosody` directly |
| `Models are not ready` | Complete both model downloads; check the directory layout or pass the two explicit model flags |
| `Whisper model weights are missing` | `config.json` alone is insufficient; rerun `prepare-model` to obtain `weights.npz` |
| `Metal GPU is unavailable` / MLX cannot import | Use an Apple Silicon Mac and native arm64 Python, rather than an Intel/Rosetta environment; check the process has GPU access |
| Missing inference module | Activate the intended environment and rerun `python -m pip install -e '.[asr,prosody,japanese]'` |
| Native dependency fails to build | Read the package's build error; if Apple's compiler tools are missing, install them with `xcode-select --install` and retry |
| Dictionary download fails | Run the dictionary command in step 3 with internet access before retrying alignment |
| Media cannot be decoded | Check that the local file contains an audio stream and can be decoded by FFmpeg |
| `Address already in use` | Let `analyze` choose its default free port, or choose a different `--port` |
| Browser does not open | Use `--no-open` and open the printed localhost URL manually; keep the server terminal running |
| Source/playback artifact is missing or changed | Restore the original file. Do not edit cached WAVs or immutable JSON estimates; use a separate output directory for a fresh run if needed |

## Optional features and review-only install

Inference engines are opt-in. The core package and local review UI use Python's
standard library:

```sh
# In an activated environment, from this checkout:
python -m pip install -e .
video-prosody review --project "/path/to/existing/project.json"
```

The project and its referenced media/results must already exist for review-only
use. No model download or inference extras are required to review them.

| Extra | Purpose |
| --- | --- |
| `asr` | MLX Whisper transcription on Apple Silicon and model preparation |
| `prosody` | Pitch/volume measurements and dictionary readings |
| `japanese` | Japanese pronunciation processing and ONNX phoneme/mora alignment |
| `build` | Distribution build and metadata validation tools |

## Individual processing commands

The combined `analyze` command creates its project manifest automatically. For
separate transcription or existing datasets, the lower-level commands remain
available:

```sh
video-prosody transcribe "clip.mp4" \
  --model-path work/models/large-v3 --output-dir work/transcripts --language ja
```

Each transcription writes a new run containing `raw.json` and `transcript.txt`.
Source hashes, model provenance, dependency versions and settings are recorded.
ASR output starts with `human_verified: false`. Standalone transcription can
select its ASR language; the combined word/mora pipeline currently supports
Japanese only.

For an existing transcript, create a manifest with paths relative to that file:

```json
{
  "schema_version": 1,
  "name": "My recordings",
  "videos": [
    {
      "video_id": "clip-001",
      "title": "Example recording",
      "raw_json": "transcripts/clip-run/raw.json",
      "media": "media/clip.mp4"
    }
  ]
}
```

IDs may contain letters, digits, `_` and `-`, and must be unique. A project
manifest is trusted local configuration; paths may refer outside its directory.
Then process its layers or open the reviewer:

```sh
video-prosody measure --project project.json --id clip-001 --audio media/clip.mp4
video-prosody readings prosody/clip-001-measured.json --output prosody/clip-001-readings.json
video-prosody align --project project.json --id clip-001 \
  --audio media/clip.mp4 --model work/models/narabas/narabas-v0.onnx
video-prosody review --project project.json
```

Dictionary corrections are opt-in: pass your own `--dictionary spelling.json` to
`transcribe`, or use `correct` and `punctuate`. Run
`video-prosody COMMAND --help` for each command's required arguments. Raw text and
ASR timestamps are retained separately from derived layers and correction logs.

## Python API

```python
from video_prosody.pipeline import analyze

project = analyze("clip.mp4", output_dir="work/results")
project.serve(port=8765, open_browser=True)
```

Use `Project("project.json")` to load an existing project without inference:

```python
from video_prosody import Project

project = Project("project.json")
recording_id = next(iter(project.catalog))
transcript = project.document(recording_id)
words = project.prosody.document(recording_id)
moras = project.moras.document(recording_id)
```

Each project owns its storage and locks. Importing the package neither starts
inference nor loads a dataset. Explicit external adapters can prepare local files
and call `video_prosody.pipeline.analyze_prepared`; their acquisition and playback
features are installed and distributed separately. See [FORMAT.md](FORMAT.md) for
the data and review-extension contracts.

## What “tone” means

Tone here means **acoustic prosody**: frame-level F0 in Hz, voicing probability,
RMS in dBFS, durations and gaps. It does not classify emotion, speaker identity
or rhetorical intent. Pitch can follow background music; a gap between words is
not necessarily silence. Word timings are approximate, and mora estimates use
phoneme CTC alignment. Listen to difficult regions and retain the distinction
between estimates, manual corrections and verified annotations.

## Development

```sh
python -m pip install -e '.[build]'
python -m unittest discover -s tests
for f in src/video_prosody/web/*.js; do node --check "$f"; done
python -m build
python -m twine check --strict dist/*
```

Tests use synthetic fixtures. Optional inference tests skip when their extras are
absent. The wheel contains code and review assets, with no recordings, model
weights or datasets. No release-upload credentials or automatic publishing steps
are included.

## License and external models

Library code and UI: [MIT](LICENSE). This does not cover recordings, datasets,
optional dependencies or model weights, whose terms are set by their publishers.
The Japanese aligner follows the [narabas model interface](https://github.com/darashi/narabas);
its [model release](https://github.com/darashi/narabas-models/releases/tag/v0) and
[MLX Whisper model](https://huggingface.co/mlx-community/whisper-large-v3-mlx) remain
external downloads.
