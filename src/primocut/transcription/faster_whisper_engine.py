"""Initial TranscriptionEngine implementation with faster-whisper (GPU).

Model `large-v3-turbo` on CUDA (RTX 3070 Ti, 8 GB). The `faster_whisper` import
and the model loading are lazy, so the logic (and the tests) run without a GPU.
The model can be injected (`model=`) for tests.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import Settings, get_settings
from ..logging_conf import get_logger
from ..models import Segment, Transcript, format_timestamp

logger = get_logger(__name__)


def _preload_cuda_libs() -> None:
    """Preload cuBLAS/cuDNN from the NVIDIA pip packages.

    Under WSL2 the driver lives on Windows, but the CUDA runtime libs come via
    pip (`nvidia-cublas-cu12`, `nvidia-cudnn-cu12`) and are not on
    LD_LIBRARY_PATH. Loading them with RTLD_GLOBAL here makes the symbols
    available for ctranslate2 (faster-whisper) to resolve `libcublas.so.12`
    etc. by soname.
    """
    import ctypes
    import glob
    import os

    try:
        import nvidia  # the nvidia-*-cu12 packages install under the `nvidia` namespace
    except ImportError:
        return  # without the pip packages: let ctranslate2 try the system PATH

    # `nvidia` is a namespace package (PEP 420): it has no __file__, use __path__.
    bases = list(getattr(nvidia, "__path__", []) or [])
    patterns = [
        "cublas/lib/libcublasLt.so*",  # cublas dependency — load first
        "cublas/lib/libcublas.so*",
        "cudnn/lib/libcudnn*.so*",
    ]
    sos: list[str] = []
    for base in bases:
        for pat in patterns:
            sos.extend(sorted(glob.glob(os.path.join(base, pat))))

    # Several passes: resolves dependencies between the libs regardless of order.
    pending = list(sos)
    for _ in range(3):
        if not pending:
            break
        still: list[str] = []
        for so in pending:
            try:
                ctypes.CDLL(so, mode=ctypes.RTLD_GLOBAL)
            except OSError:
                still.append(so)
        pending = still


class FasterWhisperEngine:
    def __init__(
        self,
        settings: Settings | None = None,
        model: Any | None = None,
        model_factory: Any | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._model = model  # injectable in tests; loaded on demand in production
        self._model_factory = model_factory  # (compute_type)->model, injectable in tests
        self.degradation_warnings: list[str] = []

    @property
    def name(self) -> str:
        return f"faster-whisper:{self.settings.whisper_model}"

    def _candidate_compute_types(self) -> list[str]:
        """Primary compute_type followed by the fallbacks (only makes sense on the GPU)."""
        if self.settings.whisper_device != "cuda":
            return [self.settings.whisper_compute_type]
        ordered = [self.settings.whisper_compute_type] + [
            c.strip() for c in self.settings.whisper_compute_fallbacks.split(",") if c.strip()
        ]
        out: list[str] = []
        for c in ordered:  # de-dup preserving order
            if c not in out:
                out.append(c)
        return out

    def _build_model(self, compute_type: str) -> Any:
        if self._model_factory is not None:
            return self._model_factory(compute_type)
        from faster_whisper import WhisperModel  # lazy import (heavy/GPU dep)

        return WhisperModel(
            self.settings.whisper_model,
            device=self.settings.whisper_device,
            compute_type=compute_type,
        )

    def _load_model(self) -> Any:
        if self._model is not None:
            return self._model
        if self.settings.whisper_device == "cuda":
            _preload_cuda_libs()

        candidates = self._candidate_compute_types()
        errors: list[tuple[str, Exception]] = []
        for idx, ct in enumerate(candidates):
            logger.info(
                "Loading %s on %s (%s)", self.settings.whisper_model,
                self.settings.whisper_device, ct,
            )
            try:
                model = self._build_model(ct)
            except Exception as exc:  # OOM/CUDA/runtime → try the next one
                logger.warning("compute_type '%s' failed: %s", ct, exc)
                errors.append((ct, exc))
                continue
            if idx > 0:
                msg = (
                    f"Transcription degraded the compute_type to '{ct}' "
                    f"(the preferred '{candidates[0]}' failed on the GPU)."
                )
                logger.warning(msg)
                self.degradation_warnings.append(msg)
            self._model = model
            return model

        tried = ", ".join(ct for ct, _ in errors)
        raise RuntimeError(
            f"Transcription failed on every compute_type ({tried}). "
            "Adjust PRIMOCUT_WHISPER_COMPUTE_TYPE, use a smaller model, or free VRAM. "
            f"Last error: {errors[-1][1] if errors else 'unknown'}"
        )

    def transcribe(
        self, audio_path: Path, *, video_id: str, language: str | None = None
    ) -> Transcript:
        language = language or self.settings.whisper_language
        model = self._load_model()
        raw_segments, info = model.transcribe(
            str(audio_path),
            language=language,
            vad_filter=True,
        )
        total = float(getattr(info, "duration", 0.0) or 0.0)
        if total:
            logger.info("Transcribing ~%s of audio…", format_timestamp(total))

        # Consume the generator while logging progress (faster-whisper emits
        # nothing on its own — without this it looks frozen for minutes).
        segments: list[Segment] = []
        last_logged = 0.0
        for i, s in enumerate(raw_segments):
            segments.append(Segment(
                index=i, start=float(s.start), end=float(s.end),
                text=(s.text or "").strip(),
            ))
            if total and (s.end - last_logged) >= 30.0:  # every ~30s of audio
                last_logged = s.end
                logger.info(
                    "Transcription: %s / %s (%d%%, %d segments)",
                    format_timestamp(s.end), format_timestamp(total),
                    int(min(100.0, s.end / total * 100)), i + 1,
                )

        detected = getattr(info, "language", language) or language
        logger.info("Transcription done: %d segments (language=%s)", len(segments), detected)
        return Transcript(
            video_id=video_id,
            language=detected,
            model=self.name,
            segments=segments,
        )


def get_engine(settings: Settings | None = None) -> FasterWhisperEngine:
    """Factory for the default Phase 0 engine."""
    return FasterWhisperEngine(settings=settings)
