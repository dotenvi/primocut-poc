# PRIMOCUT — Fase 0 (Spike CLI) — Plano de Implementação

> Documento de planejamento da Fase 0 descrita no roadmap de [`PRIMOCUT.md`](./PRIMOCUT.md).
> Fonte de verdade do projeto continua sendo `PRIMOCUT.md`; este arquivo detalha **como**
> a Fase 0 (Spike CLI) será implementada.

## Contexto

A Fase 0 é um **spike de linha de comando**: dada a URL de uma live do YouTube, produzir um
relatório em Markdown com os tópicos discutidos, seguindo o pipeline
**áudio → chat replay → transcrição → análise por LLM**.

Objetivos do spike (do roadmap): validar **GPU no WSL2**, comparar a qualidade de segmentação
(**tópicos macro** vs. **detecção fina de momentos**) e validar a **detecção de picos do chat**.

O código já nasce **modular** para evoluir à Fase 1 (fila + banco + web), com fronteiras limpas
entre módulos e uma abstração `TranscriptionEngine`.

### Recon do ambiente (executado em 2026-07-12)
- GPU RTX 3070 Ti (8 GB), driver 595.97, CUDA 13.2 — **visível** via `nvidia-smi`. ✅
- **Faltando** no host: `ffmpeg`, `yt-dlp`, e `ANTHROPIC_API_KEY` não setada. → É exatamente o que
  o comando `doctor` deve detectar; construiremos e demonstraremos o `doctor` contra este ambiente
  "não-saudável".
- Python 3.13.5 disponível.

### Decisões confirmadas
- **Ferramentas nativas do Python** (sem uv por ora, conforme spec §Stack): `python -m venv .venv`
  + `pip` + `requirements.txt`. Um `pyproject.toml` mínimo (setuptools) só registra o entrypoint
  `primocut` via `pip install -e .`. (uv fica para quando for autorizado.)
- **Duas passagens de LLM independentes** (macro topics / fine-grained moments) — melhor comparação
  da qualidade de segmentação.
- Modelo de análise default: **`claude-sonnet-5`** (sobrescrevível por env).
- `doctor` **reporta + imprime comandos de correção** (sem auto-install).

---

## Estrutura de diretórios

```
primocut/
├── pyproject.toml               # setuptools mínimo: metadados + entrypoint `primocut` (pip install -e .)
├── requirements.txt             # deps de runtime (pip)
├── requirements-dev.txt         # pytest, pytest-mock
├── README.md                    # como rodar + testar cada módulo
├── .env.example                 # ANTHROPIC_API_KEY=...
├── .gitignore
├── src/primocut/
│   ├── __init__.py
│   ├── config.py                # Settings (pydantic-settings): paths, modelo, janela, retenção
│   ├── logging_conf.py          # logging estruturado + progresso de etapas
│   ├── models.py                # modelos pydantic (Video, Transcript, Segment, ChatSignal, Topic, CutMoment, Report)
│   ├── cli.py                   # Typer app: `doctor`, `run`
│   ├── doctor.py                # diagnóstico do ambiente (CUDA/GPU, ffmpeg, yt-dlp, chave API)
│   ├── pipeline.py              # orquestra as etapas, emite progresso
│   ├── download/
│   │   ├── __init__.py
│   │   └── audio.py             # yt-dlp: metadados + áudio; FFmpeg → 16kHz mono wav
│   ├── chat/
│   │   ├── __init__.py
│   │   ├── downloader.py        # chat-downloader (fallback yt-dlp live_chat); ausência = None
│   │   └── signals.py           # densidade msgs/min, z-score de picos, risadas/emotes
│   ├── transcription/
│   │   ├── __init__.py
│   │   ├── engine.py            # interface TranscriptionEngine (ABC/Protocol)
│   │   └── faster_whisper_engine.py  # impl. large-v3-turbo GPU
│   ├── analysis/
│   │   ├── __init__.py
│   │   ├── windowing.py         # janelas com sobreposição sobre segmentos
│   │   ├── llm.py               # client Anthropic + chamada estruturada (tool use / JSON)
│   │   ├── prompts.py           # prompts macro e fino (seleção de segmentos, nunca inventar ts)
│   │   ├── topics.py            # passagem macro (10–20 tópicos) + consolidação entre janelas
│   │   └── moments.py           # passagem fina (candidatos a corte, início/fim precisos)
│   └── report/
│       ├── __init__.py
│       └── markdown.py          # renderiza o relatório .md
└── tests/
    ├── conftest.py              # fixtures: transcript sintético, chat sintético
    ├── fixtures/                # transcript.json, chat.jsonl de exemplo
    ├── test_models.py
    ├── test_doctor.py
    ├── test_chat_signals.py
    ├── test_windowing.py
    ├── test_analysis_selection.py   # garante que ts vêm dos segmentos (nunca inventados)
    ├── test_report_markdown.py
    └── test_transcription_engine.py # com engine fake, valida a interface
```

**Princípio anti-invenção de timestamps** (diretriz técnica #2): o LLM recebe segmentos
**indexados** (`[i] mm:ss texto`) e retorna **índices de segmento** (`start_index`/`end_index`),
não números de tempo. O código resolve os índices → segundos a partir da transcrição. Assim é
*impossível* o LLM inventar timestamps.

---

## Modelos de dados (pydantic, alinhados à spec §"Modelo de dados")

- `Segment{index:int, start:float, end:float, text:str}`
- `Transcript{video_id, language, model, segments:list[Segment]}` + helpers `duration`, `window()`
- `Video{id, url, title, channel, duration, status}` (enum de status da spec)
- `ChatMessage{offset_seconds:float, author, text}` e
  `ChatSignal{video_id, per_minute:list[MinuteBucket{minute, msgs, laughs, emotes}], peaks:list[Peak{ts, intensity, z}]}`
- `Topic{title, summary, start, end, score, status}` (macro)
- `CutMoment{title, reason, start, end, score}` (fino) — `start/end` sempre resolvidos de segmentos
- `Report{video, transcript_meta, chat_signal|None, topics, moments}` → método `youtube_url(ts)`

`youtube_url(ts)` → `https://youtube.com/watch?v={id}&t={int(ts)}`.

---

## Interface TranscriptionEngine (diretriz técnica #1)

```python
class TranscriptionEngine(Protocol):
    def transcribe(self, audio_path: Path, *, language: str = "pt") -> Transcript: ...
    @property
    def name(self) -> str: ...
```
- `FasterWhisperEngine`: `large-v3-turbo`, `device="cuda"`, `compute_type="float16"`
  (fallback `int8_float16` se a VRAM de 8 GB apertar), `vad_filter=True`. Constrói `Segment`
  com índice sequencial.
- Fase 2 (WhisperX) pluga aqui sem tocar no resto do sistema.

---

## Análise por LLM (diretrizes técnicas #2 e #3) — DUAS passagens independentes

1. **Windowing** (`windowing.py`): particiona `segments` em janelas por duração-alvo
   (default ~20 min de áudio / ~8k tokens) com **sobreposição** (default ~60 s). Cada janela
   mantém os índices globais de segmento.
2. **Passagem MACRO** (`topics.py`): por janela, o LLM seleciona intervalos de segmentos → tópicos;
   depois **consolida** entre janelas (deduplica os que cruzam a sobreposição, funde adjacentes),
   alvo **10–20 tópicos** na live inteira.
3. **Passagem FINA** (`moments.py`): por janela, o LLM marca **momentos candidatos a corte** com
   `start_index/end_index` precisos + motivo + score; consolida sem forçar o teto de 10–20.
4. **LLM client** (`llm.py`): Anthropic SDK, **tool use / structured output** para forçar o schema
   (retornar índices). Modelo default `claude-sonnet-5` (via `config.py`, sobrescrevível por env).
   Chave lida de `ANTHROPIC_API_KEY`. Nos testes, o client é **injetável/mockado** (sem rede).

---

## Chat (diretriz técnica #4) — sinal opcional

- `downloader.py`: tenta `chat-downloader`; se não houver replay → retorna `None` e o pipeline
  segue sem ele.
- `signals.py`: buckets por minuto (msgs, risadas via regex `k{4,}`/"haha", emotes), **z-score**
  sobre msgs/min → `peaks` (z ≥ threshold, default 2.0). Puro/determinístico → testável sem rede.
- No relatório: tabela/sparkline textual de atividade + picos **cruzados** com tópicos/momentos
  (marca tópicos que coincidem com um pico).

---

## Relatório Markdown

`markdown.py` gera, nesta ordem:
1. **Metadados**: título, canal, URL, duração, modelo de transcrição, data.
2. **Atividade do chat**: tabela por minuto + sparkline ASCII (blocos `▁▂▃▅▇`) + lista de picos;
   ou aviso "sem replay de chat" quando ausente.
3. **Tópicos macro** (10–20): título · resumo · [início–fim] · score, com **link clicável**
   `https://youtube.com/watch?v=ID&t=SEGUNDOS`.
4. **Momentos candidatos a corte** (fino): mesmo formato, timestamps precisos.

Tópicos/momentos coincidentes com picos de chat recebem um marcador (🔥).

---

## Ordem de implementação (TDD, teste-primeiro, módulo por módulo)

Cada etapa: escrever teste(s) → implementar → mostrar o comando de teste isolado.

0. **Scaffold**: `python -m venv .venv` → `pip install -r requirements.txt -r requirements-dev.txt`
   → `pip install -e .`. `pyproject.toml` (setuptools) só p/ metadados + entrypoint `primocut`.
   logging, `config.py`.
1. **`doctor` PRIMEIRO** (`test_doctor.py` com checagens mockadas): valida GPU/CUDA, ffmpeg,
   yt-dlp, chave. Demo real: `primocut doctor` (venv ativa) → mostra os itens faltando + comandos
   de correção.
2. **`models.py`** (`test_models.py`): validação pydantic + `youtube_url`, `window()`.
3. **`chat/signals.py`** (`test_chat_signals.py`): z-score/picos/risadas com chat sintético.
4. **`transcription/engine.py`** + fake engine (`test_transcription_engine.py`): contrato da interface.
5. **`analysis/windowing.py`** (`test_windowing.py`): janelas + sobreposição + índices globais.
6. **`analysis` (topics/moments/llm)** (`test_analysis_selection.py`): com **LLM fake** que devolve
   índices → prova que timestamps saem dos segmentos (nunca inventados) + consolidação.
7. **`report/markdown.py`** (`test_report_markdown.py`): snapshot do `.md` com links corretos.
8. **`download/audio.py`** + **`chat/downloader.py`**: wrappers finos de yt-dlp/chat-downloader
   (testes leves com subprocess mockado; validação real depende de ffmpeg/yt-dlp instalados).
9. **`pipeline.py`** + `run` no CLI: orquestra tudo com progresso; end-to-end.

### Dependências (`requirements.txt`)
`typer`, `pydantic`, `pydantic-settings`, `anthropic`, `faster-whisper`, `chat-downloader`,
`yt-dlp`, `rich` (logging/progresso), `nvidia-cublas-cu12`, `nvidia-cudnn-cu12` (CUDA no WSL).
`requirements-dev.txt`: `pytest`, `pytest-mock`.

---

## Verificação (como testar)

Comandos assumem a venv ativa (`source .venv/bin/activate`).
- **Por módulo (isolado, sem rede/GPU)**: `pytest tests/test_<mod>.py -v`. Toda a lógica pura
  (signals, windowing, seleção de ts, report) roda sem GPU e sem API.
- **doctor (real)**: `primocut doctor` — deve listar ffmpeg/yt-dlp/chave faltando com o fix.
- **Transcrição (real, GPU)**: após instalar deps, transcrever um clipe curto e conferir `device=cuda`.
- **End-to-end**: `primocut run "<url>" -o report.md` com uma live curta; abrir `report.md`
  e clicar nos timestamps.

## Fora de escopo (Fase 0)
Fila/Redis, SQLite, FastAPI, web UI, corte/render de clipes, upload — ficam para Fases 1+.
