# video-prosody

A local Python library for transcribing video/audio, recording timing and acoustic prosody, and reviewing annotations against the original media. This directory is a standalone distribution: it does not require the parent repository or any particular dataset.

The core package and review UI use only Python's standard library. Inference engines are optional extras. Version 0.1 is experimental; the implementation currently provides MLX Whisper ASR on Apple Silicon and Japanese word/pronunciation/mora processing. The project format and review storage accept arbitrary media IDs and datasets.

## Repository setup

Source repository: `shiroinock/video-prosody`. Repository visibility and package publication are managed separately.

```sh
git clone https://github.com/shiroinock/video-prosody.git
cd video-prosody
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[build]'
.venv/bin/python -m unittest discover -s tests
```

The development setup includes the core reviewer and build tools. Install the inference extras below when you need to run models. Optional inference tests skip when those extras are absent.

## Install

```sh
python -m pip install .
# Choose only the inference features you need:
python -m pip install '.[asr,prosody,japanese]'
```

Python 3.11+ is required. ASR requires Apple Silicon/macOS with Metal and a separately downloaded MLX Whisper model. Prosody measurement uses librosa/pYIN and bundled FFmpeg; Japanese readings use Janome, Fugashi/UniDic-lite and pyopenjtalk; mora alignment uses ONNX Runtime and a separately supplied narabas model. Review of existing results requires none of these extras.

## Local file to analysis and review

After installing inference extras and models, pass an existing file:

```sh
video-prosody prepare-model --model-path work/models/large-v3
# Place narabas-v0.onnx at work/models/narabas/narabas-v0.onnx.
video-prosody analyze /absolute/path/clip.mp4
```

This transcribes the local audio/video, measures pitch/volume and word timings,
generates Japanese readings and mora alignment, then opens a localhost review
page. The combined pipeline currently supports Japanese. `--no-open` serves
without opening a browser; `--no-serve` prints the project path and exits.
`--port` selects the review port (default: a free port).

Models are discovered under `VIDEO_PROSODY_MODEL_DIR`, the default application
cache's `models/`, or `work/models/` in the checkout/current directory. The expected
layout is `large-v3/config.json` plus weights, and `narabas/narabas-v0.onnx`.
Use `--model-path` and `--alignment-model` for explicit locations.

Results and review history live under `~/Library/Application Support/video-prosody/local`
on macOS, or `$XDG_DATA_HOME/video-prosody/local` (default `~/.local/share/video-prosody/local`)
elsewhere. Override with `--output-dir` or `VIDEO_PROSODY_OUTPUT_DIR`. Completed
stages are reused after checking hashes. Changes to source/model/settings create
separate analysis runs, preserving prior estimates and human edits. Keep the
original input file available: project manifests reference it for analysis/video
playback. A separate native-rate, original-channel PCM24 WAV is generated for
precise word/mora listening; no lossy re-encoding or model-downsampled playback.
Each model decodes the original source for its own input requirements.

The public distribution accepts local files and contains no media downloader, remote-player UI,
URL acquisition command, or acquisition cooldown state. External
personal adapters can prepare local media and call
`video_prosody.pipeline.analyze_prepared`; adapters own acquisition, provenance,
source locking and retention. They are installed and distributed separately.

## Transcribe a new video

```sh
video-prosody prepare-model --model-path work/whisper --revision MODEL_COMMIT_SHA
video-prosody transcribe clip.mp4 --model-path work/whisper --output-dir data/transcripts --language ja
```

`--revision` can be omitted to resolve and record the current model revision. Each run creates a new directory containing `raw.json` and `transcript.txt`. Input SHA256, acquisition details, model revision, dependency versions, preprocessing and ASR settings are recorded. ASR outputs start with `human_verified: false`.

Dictionary corrections are opt-in. Pass your own `--dictionary spelling.json`, or run `video-prosody correct raw.json --dictionary spelling.json --context-edits edits.json`. `video-prosody punctuate contextual.json --plan punctuation.json` applies insertion-only punctuation. Raw text and ASR word timestamps remain unchanged; each derived layer has its own provenance and audit log.

## Describe your dataset

Create `project.json` beside your data:

```json
{
  "schema_version": 1,
  "name": "My recordings",
  "review_edits": "review-edits",
  "prosody": "prosody",
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

Paths resolve relative to this manifest, independent of the current working directory. `video_id` permits letters, digits, `_` and `-`; IDs must be unique. `media`, `title`, and `url` are optional. `punctuated_json` and `correction_history` may point to derived layers; otherwise review starts from the raw transcript. A project manifest is trusted local configuration: its paths may refer outside the manifest directory.

```sh
video-prosody measure --project project.json --id clip-001 --audio media/clip.mp4
video-prosody readings prosody/clip-001-measured.json --output prosody/clip-001-readings.json
video-prosody align --project project.json --id clip-001 --audio media/clip.mp4 --model work/narabas-v0.onnx
video-prosody review --project project.json
```

Open `http://127.0.0.1:8765/` for transcript review, `/prosody?id=clip-001` for word timings and acoustic measurements, and `/moras?id=clip-001` for mora boundary loops. The selectors list the manifest's videos with results available. Media is served locally with byte ranges for seeking. Saves require the same origin, validate ranges, reject stale revisions, and keep the original estimates separate from audited edits. This is a localhost tool, not a multi-user hosted service.

Measurement and reading commands reject existing destination files. Supply `--output` for new measurements. Mora alignment creates a timestamped estimate and atomically switches `{id}-moras-active.json`; old estimates and their hash-scoped review histories remain available.

## Python API

```python
from video_prosody import Project
from video_prosody.transcribe import transcribe
from video_prosody.measurement import measure
from video_prosody.readings import add_readings
from video_prosody.alignment import align_moras

run = transcribe("clip.mp4", model_path="work/whisper", output_dir="data/transcripts")
project = Project("project.json")
transcript = project.document("clip-001")
words = project.prosody.document("clip-001")
moras = project.moras.document("clip-001")
# project.serve(port=8765)
```

Each `Project` owns separate storage and locks; importing the package neither loads datasets nor starts inference. Core import/CLI help/review work without optional model dependencies.

## Data contract and meaning of tone

The current JSON format is described in [FORMAT.md](FORMAT.md). “Tone” means measured acoustic prosody: frame-level F0 in Hz, voicing probability, RMS in dBFS, word durations and adjacent gaps. It does not classify emotion, speaker identity, or rhetorical intent. Pitch estimates may follow background music in mixed audio; gaps are not necessarily silence. Word alignment maps ASR character times onto corrected text and remains approximate. Japanese mora timing uses phoneme CTC alignment, not equal duration splitting. Estimated values, manual overrides and verification flags are separate.

## Development and distribution

```sh
PYTHONPATH=src python -m unittest discover -s tests
node --test tests/player.test.cjs
python -m pip install '.[build]'
python -m build
python -m twine check dist/*
```

The wheel includes Python modules and review UI assets, without media, model weights, datasets or corpus-specific correction rules. CI tests the standalone core and builds distributions. Before a first public release, select an available distribution/repository name and review the optional models' own terms. No publishing credentials or automated release upload are included.

## License and third-party models

Library code and UI: [MIT](LICENSE). This license does not cover user datasets, source media, optional dependencies or model weights.

The optional Japanese aligner expects the [narabas model interface](https://github.com/darashi/narabas); weights are distributed separately by [narabas-models](https://github.com/darashi/narabas-models). MLX Whisper models likewise remain external. Model preparation and URL acquisition happen only when explicitly invoked. On its first use, pyopenjtalk may download its Open JTalk dictionary; prepare that dictionary in advance for offline alignment.

Review adapters may explicitly pass `assets={route: (body_bytes, content_type)}`
and `document_transform=callable` to `Project.serve`. A browser extension asset can
provide `window.createReviewPlayback({localPlayer, setPlayer, sync, status})`,
returning an object with async `load(document, {position})`. The adapter owns
remote playback, fallback and cancellation; the core retains local review/storage.
