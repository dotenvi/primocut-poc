# PRIMOCUT

CLI + local web app that takes the URL of a YouTube live and produces a report with the
transcript and chat activity. Pipeline: **audio → chat replay → transcription**. Macro-topic
extraction (the LLM analysis) is opt-in: pass `--create-topics` on the CLI, or use the
**Generate topics with AI** button in the web report.

Source of truth: [`docs/PRIMOCUT.md`](docs/PRIMOCUT.md).
**Phase 1 (web MVP): [`docs/FASE1-MVP.md`](docs/FASE1-MVP.md).**
Phase 0 summary (handoff): [`docs/FASE0-RESUMO.md`](docs/FASE0-RESUMO.md).
Phase 0 plan: [`docs/FASE0-SPIKE-CLI-PLANO.md`](docs/FASE0-SPIKE-CLI-PLANO.md).
Execution guide: [`docs/FASE0-STATUS-E-EXECUCAO.md`](docs/FASE0-STATUS-E-EXECUCAO.md).
Fallback catalog: [`docs/FALLBACKS.md`](docs/FALLBACKS.md).

## Setup (native Python tooling)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt   # runtime + pytest
pip install -e .                      # registers the `primocut` command
```

> **Nuance of this environment (WSL2):** if `python -m venv` fails with
> `ensurepip is not available`, the `python3.13-venv` package is missing (needs
> `sudo apt install python3.13-venv`). Without sudo, you can use just stdlib + the system
> pip: create the venv without pip and install by pointing pip at its interpreter:
> ```bash
> python3 -m venv .venv --without-pip
> python3 -m pip --python .venv/bin/python install -r requirements-dev.txt
> python3 -m pip --python .venv/bin/python install --no-deps -e .
> ```

System dependencies (validated by `doctor`): **FFmpeg** and **yt-dlp**, plus the
`ANTHROPIC_API_KEY`. Copy `.env.example` to `.env` and fill in the key.

## Environment diagnostics (run this first)

```bash
primocut doctor
```

Validates GPU/CUDA (WSL2), FFmpeg, yt-dlp and the API key — and prints the fix command
for each missing item.

## Run the pipeline (CLI, Phase 0)

```bash
primocut run "https://www.youtube.com/watch?v=<ID>" -o report.md
```

## Web app (Phase 1)

Needs Redis (`sudo apt install redis-server` or `docker run -p 6379:6379 redis`).

```bash
cd frontend && npm install && npm run build && cd ..   # build the UI (once)
primocut worker    # processes the queue (terminal 1)
primocut serve     # API + UI at http://127.0.0.1:8000 (terminal 2)
```

Details in [`docs/FASE1-MVP.md`](docs/FASE1-MVP.md).

## Per-module tests (isolated, no GPU/network)

```bash
pytest tests/test_doctor.py -v
pytest tests/test_models.py -v
pytest tests/test_chat_signals.py -v
pytest tests/test_windowing.py -v
pytest tests/test_analysis_selection.py -v
pytest tests/test_report_markdown.py -v
pytest tests/test_transcription_engine.py -v
pytest -v   # everything
```
