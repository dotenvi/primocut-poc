"""Implementação inicial do TranscriptionEngine com faster-whisper (GPU).

Modelo `large-v3-turbo` em CUDA (RTX 3070 Ti, 8 GB). O import de `faster_whisper`
e o carregamento do modelo são preguiçosos, para que a lógica (e os testes) rodem
sem GPU. O modelo pode ser injetado (`model=`) para testes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import Settings, get_settings
from ..logging_conf import get_logger
from ..models import Segment, Transcript, format_timestamp

logger = get_logger(__name__)


def _preload_cuda_libs() -> None:
    """Pré-carrega cuBLAS/cuDNN dos pacotes pip da NVIDIA.

    No WSL2 o driver fica no Windows, mas as libs de runtime CUDA vêm por pip
    (`nvidia-cublas-cu12`, `nvidia-cudnn-cu12`) e não estão no LD_LIBRARY_PATH.
    Carregá-las com RTLD_GLOBAL aqui deixa os símbolos disponíveis para o
    ctranslate2 (faster-whisper) resolver `libcublas.so.12` etc. por soname.
    """
    import ctypes
    import glob
    import os

    try:
        import nvidia  # pacotes nvidia-*-cu12 instalam sob o namespace `nvidia`
    except ImportError:
        return  # sem os pacotes pip: deixa o ctranslate2 tentar o PATH do sistema

    # `nvidia` é um namespace package (PEP 420): não tem __file__, use __path__.
    bases = list(getattr(nvidia, "__path__", []) or [])
    patterns = [
        "cublas/lib/libcublasLt.so*",  # dependência do cublas — carregar antes
        "cublas/lib/libcublas.so*",
        "cudnn/lib/libcudnn*.so*",
    ]
    sos: list[str] = []
    for base in bases:
        for pat in patterns:
            sos.extend(sorted(glob.glob(os.path.join(base, pat))))

    # Várias passagens: resolve dependências entre as libs independentemente da ordem.
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
        self._model = model  # injetável nos testes; carregado sob demanda no real
        self._model_factory = model_factory  # (compute_type)->model, injetável nos testes
        self.degradation_warnings: list[str] = []

    @property
    def name(self) -> str:
        return f"faster-whisper:{self.settings.whisper_model}"

    def _candidate_compute_types(self) -> list[str]:
        """compute_type primário seguido dos fallbacks (só faz sentido na GPU)."""
        if self.settings.whisper_device != "cuda":
            return [self.settings.whisper_compute_type]
        ordered = [self.settings.whisper_compute_type] + [
            c.strip() for c in self.settings.whisper_compute_fallbacks.split(",") if c.strip()
        ]
        out: list[str] = []
        for c in ordered:  # de-dup preservando ordem
            if c not in out:
                out.append(c)
        return out

    def _build_model(self, compute_type: str) -> Any:
        if self._model_factory is not None:
            return self._model_factory(compute_type)
        from faster_whisper import WhisperModel  # import tardio (dep pesada/GPU)

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
                "Carregando %s em %s (%s)", self.settings.whisper_model,
                self.settings.whisper_device, ct,
            )
            try:
                model = self._build_model(ct)
            except Exception as exc:  # OOM/CUDA/runtime → tenta o próximo
                logger.warning("compute_type '%s' falhou: %s", ct, exc)
                errors.append((ct, exc))
                continue
            if idx > 0:
                msg = (
                    f"Transcrição degradou o compute_type para '{ct}' "
                    f"(o preferido '{candidates[0]}' falhou na GPU)."
                )
                logger.warning(msg)
                self.degradation_warnings.append(msg)
            self._model = model
            return model

        tried = ", ".join(ct for ct, _ in errors)
        raise RuntimeError(
            f"Transcrição falhou em todos os compute_types ({tried}). "
            "Ajuste PRIMOCUT_WHISPER_COMPUTE_TYPE, reduza o modelo, ou libere VRAM. "
            f"Último erro: {errors[-1][1] if errors else 'desconhecido'}"
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
            logger.info("Transcrevendo ~%s de áudio…", format_timestamp(total))

        # Consome o gerador logando progresso (o faster-whisper não emite nada
        # por conta própria — sem isto, parece travado por minutos).
        segments: list[Segment] = []
        last_logged = 0.0
        for i, s in enumerate(raw_segments):
            segments.append(Segment(
                index=i, start=float(s.start), end=float(s.end),
                text=(s.text or "").strip(),
            ))
            if total and (s.end - last_logged) >= 30.0:  # a cada ~30s de áudio
                last_logged = s.end
                logger.info(
                    "Transcrição: %s / %s (%d%%, %d segmentos)",
                    format_timestamp(s.end), format_timestamp(total),
                    int(min(100.0, s.end / total * 100)), i + 1,
                )

        detected = getattr(info, "language", language) or language
        logger.info("Transcrição concluída: %d segmentos (idioma=%s)", len(segments), detected)
        return Transcript(
            video_id=video_id,
            language=detected,
            model=self.name,
            segments=segments,
        )


def get_engine(settings: Settings | None = None) -> FasterWhisperEngine:
    """Factory do engine default da Fase 0."""
    return FasterWhisperEngine(settings=settings)
