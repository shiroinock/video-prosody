"""Command line entry point; heavy dependencies load only for selected operations."""

import argparse
from datetime import datetime, timezone
from pathlib import Path

from . import Project


def main():
    parser = argparse.ArgumentParser(prog="video-prosody")
    commands = parser.add_subparsers(dest="command", required=True)
    analyze_parser = commands.add_parser(
        "analyze", help="Local video/audio file to analysis and review"
    )
    analyze_parser.add_argument("media", type=Path)
    analyze_parser.add_argument(
        "--output-dir",
        type=Path,
        help="Persistent results and review history directory",
    )
    analyze_parser.add_argument("--model-path", type=Path)
    analyze_parser.add_argument("--alignment-model", type=Path)
    analyze_parser.add_argument(
        "--port", type=int, default=0, help="Review port; default chooses a free port"
    )
    analyze_parser.add_argument(
        "--no-open", action="store_true", help="Do not launch the browser"
    )
    analyze_parser.add_argument(
        "--no-serve", action="store_true", help="Prepare results and exit"
    )
    prepare = commands.add_parser(
        "prepare-model", help="Download and record an MLX Whisper model revision"
    )
    prepare.add_argument("--model-path", type=Path, required=True)
    prepare.add_argument("--revision")
    asr = commands.add_parser(
        "transcribe",
        help="Local video/audio file to timestamped transcript",
    )
    asr.add_argument("audio")
    asr.add_argument("--model-path", type=Path, required=True)
    asr.add_argument("--output-dir", type=Path, required=True)
    asr.add_argument("--language", default="ja")
    asr.add_argument("--dictionary", type=Path)
    correct = commands.add_parser("correct")
    correct.add_argument("raw_json", type=Path)
    correct.add_argument("--dictionary", type=Path, required=True)
    correct.add_argument("--context-edits", type=Path)
    correct.add_argument("--output-dir", type=Path)
    punct = commands.add_parser("punctuate")
    punct.add_argument("source_json", type=Path)
    punct.add_argument("--plan", type=Path, required=True)
    readings = commands.add_parser("readings")
    readings.add_argument("source_json", type=Path)
    readings.add_argument("--output", type=Path, required=True)
    for command in ("measure", "align", "review"):
        sub = commands.add_parser(command)
        sub.add_argument("--project", type=Path, required=True)
        if command == "review":
            sub.add_argument("--port", type=int, default=8765)
        else:
            sub.add_argument("--id", required=True)
            sub.add_argument("--audio", type=Path, required=True)
            sub.add_argument("--output", type=Path)
            if command == "align":
                sub.add_argument("--model", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "analyze":
        from .pipeline import analyze

        project = analyze(
            args.media,
            output_dir=args.output_dir,
            model_path=args.model_path,
            alignment_model=args.alignment_model,
        )
        if not args.no_serve:
            project.serve(args.port, open_browser=not args.no_open)
    elif args.command == "prepare-model":
        from .transcribe import prepare_model

        print(prepare_model(args.model_path, revision=args.revision))
    elif args.command == "transcribe":
        from .transcribe import transcribe

        transcribe(
            args.audio,
            model_path=args.model_path,
            output_dir=args.output_dir,
            language=args.language,
            dictionary=args.dictionary,
        )
    elif args.command == "correct":
        from .corrections import correct_file

        correct_file(
            args.raw_json, args.dictionary, args.context_edits, args.output_dir
        )
    elif args.command == "punctuate":
        from .punctuation import punctuate

        punctuate(args.source_json, args.plan)
    elif args.command == "readings":
        from .readings import add_readings

        add_readings(args.source_json, args.output)
    else:
        project = Project(args.project)
        if args.command == "review":
            project.serve(args.port)
        elif args.command == "measure":
            import json

            from .measurement import measure

            raw = json.loads(project.record(args.id)["raw_json"].read_text())
            measure(
                project.document(args.id),
                raw,
                args.audio,
                args.output or project.prosody_dir / f"{args.id}-measured.json",
            )
        elif args.command == "align":
            from .alignment import align_moras

            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            align_moras(
                project.prosody.document(args.id),
                args.audio,
                args.model,
                args.output
                or project.prosody_dir / f"{args.id}-moras-estimated-{stamp}.json",
                project.prosody_dir / f"{args.id}-moras-active.json",
            )


if __name__ == "__main__":
    main()
