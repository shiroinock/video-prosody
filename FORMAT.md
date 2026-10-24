# JSON contract (0.1)

Project manifests carry `schema_version: 1`. Existing annotation artifacts retain their original field names and provenance bytes; `video_id` is an arbitrary local recording ID. Seconds are relative to the media start; intervals are start-inclusive and end-exclusive. Text offsets in correction logs use Python Unicode character indices.

| Layer | Required structure | Time/verification meaning |
| --- | --- | --- |
| raw.json | `metadata.duration_seconds`, `result.text`, `result.segments[]` with `id,text,start,end,words[]` | ASR segment/word estimates; `metadata.human_verified` defaults false |
| corrected transcript | `metadata,text,segments[]` | Preserves raw ASR times; does not invent realigned word times |
| correction audit | `changes[]` with `segment_id,stage,before,after,reason`; optional `review_flags[]` | Text proposal, not evidence of audio verification |
| `{id}-measured.json` | `video_id,title,duration,transcript_revision,frames[],words[]` | `frames`: `time,pitch_hz,voiced_probability,rms_dbfs` (new measurements also record `pitch_channel`); null pitch means unvoiced/unknown. `words`: `id,segment_id,text,start,end,verified` |
| `{id}-readings.json` | `video_id,source_measured_sha256,words` keyed by word ID strings | `text,reading,pronunciation,moras,needs_review,notes`; dictionary readings are not verified speech |
| mora estimate | `video_id,title,duration,source_word_revision,moras[],segments[],unaligned_segments[]` | Each mora has `id,word_id,word_text,label,pronunciation,start,end,verified`; CTC score is not calibrated accuracy |
| `{id}-moras-active.json` | `active` basename, `sha256` | Selects an immutable estimate; content hash is verified on read |
| transcript review | `video_id,revision,source_sha256,segments,history` | Source digest is verified; saves use `expected_revision` |
| word review | `video_id,revision,words,history` | Base measurements remain separate; timing changes recompute summaries; pitch/volume overrides remain separate fields |
| mora review | `video_id,revision,source_alignment,source_alignment_sha256,moras,history` | File is scoped by the estimate's SHA256; saves require matching estimate and revision |

A history event records the entity ID, before/after values, reason, UTC time and new revision. Verification flags represent explicit human review, never inferred confidence. Corrections preserve source data, and historical source paths are metadata only; operational file paths come from `project.json`.

The acoustic frame rate is approximately 10 ms, pYIN frame length approximately 64 ms at the native sample rate, and pitch search range 65–600 Hz. RMS is the root of mean channel energy; F0 is estimated independently per channel and the highest voicing probability selects each frame's result. These are measurement settings, not guarantees of accuracy. Narabas frame step comes from the supplied ONNX model metadata. The library currently handles Japanese dictionary/mora analysis only; ASR language can be selected separately.

## Local projects and prepared media

A local video record uses `media` for its original audio/video file and `audio`
for optional decoded playback audio. `analysis_audio` records the original signal
used for inference. Paths resolve relative to the project manifest. Local `analyze`
runs live under `local/{source_sha256}/analyses/{signature}/` in the persistent
results directory. A sibling `audio/playback.wav` preserves the original sample
rate and channels as PCM24; `playback.json` stores its path and hash. The original
media must remain available and unchanged to reproduce the analysis.

Each run's `state.json` records immutable layers and their SHA256 values. Mutable
review edits and the active alignment selection remain separate. Source, model,
settings or inference-version changes select a new run. F0/RMS use original-rate,
original-channel float32 samples; models receive their own required rate as
float32 without an intermediate PCM16 file.

External adapters may retain arbitrary provenance metadata in project records.
The public reviewer exposes a generic `source_url`, `audio_url` and `media_url`
(null if the corresponding local media is not configured). Word/mora APIs prefer
`/audio/{id}` for precision playback. Local media/audio endpoints support byte
ranges. The core reviewer never embeds a remote player. Trusted local adapters
may explicitly pass additional byte assets and a document transform to
`Project.serve`; no adapter is automatically imported or discovered.
