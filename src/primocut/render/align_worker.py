"""Out-of-process entrypoint for the surgical WhisperX alignment.

Run INSIDE the isolated align venv (`.venv-align`, which has the `align` extra):

    <align_python> -m primocut.render.align_worker <window.wav> --language pt

It transcribes + aligns ONLY the given audio window and prints, on stdout, the
word timestamps as JSON (`{"words": [{"text","start","end"}, ...]}`) relative to
that window (t=0 = the window start). The render worker (main venv) invokes this
via subprocess, adds the window origin, and snaps the item boundaries.

Keeping this out-of-process is what isolates the whisperx/torch/pyannote stack
from the validated worker venv AND gives the render job a killable timeout (a
hung alignment is just a subprocess that overran — the caller falls back to the
original timestamps). Anything printed to stderr is diagnostic only.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="primocut.render.align_worker")
    parser.add_argument("audio", help="path to the window WAV to align")
    parser.add_argument("--language", default=None, help="language code (default: settings)")
    args = parser.parse_args(argv)

    # Imported here so `-m ... --help` never pulls the heavy stack in.
    from ..transcription.whisperx_engine import WhisperXEngine

    engine = WhisperXEngine()
    words = engine.align_words(Path(args.audio), language=args.language)
    json.dump({"words": words}, sys.stdout)
    sys.stdout.flush()
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via subprocess
    raise SystemExit(main())
