# PRIMOCUT — Fase 0 (Spike CLI)

CLI que recebe a URL de uma live do YouTube e produz um relatório em Markdown com os
tópicos discutidos. Pipeline: **áudio → chat replay → transcrição → análise por LLM**.

Fonte de verdade: [`docs/PRIMOCUT.md`](docs/PRIMOCUT.md).
**Fase 1 (MVP web): [`docs/FASE1-MVP.md`](docs/FASE1-MVP.md).**
Resumo da Fase 0 (handoff): [`docs/FASE0-RESUMO.md`](docs/FASE0-RESUMO.md).
Plano da Fase 0: [`docs/FASE0-SPIKE-CLI-PLANO.md`](docs/FASE0-SPIKE-CLI-PLANO.md).
Guia de execução: [`docs/FASE0-STATUS-E-EXECUCAO.md`](docs/FASE0-STATUS-E-EXECUCAO.md).
Catálogo de fallbacks: [`docs/FALLBACKS.md`](docs/FALLBACKS.md).

## Setup (ferramentas nativas do Python)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt   # runtime + pytest
pip install -e .                      # registra o comando `primocut`
```

> **Nuance deste ambiente (WSL2):** se `python -m venv` falhar com
> `ensurepip is not available`, falta o pacote `python3.13-venv` (precisa de
> `sudo apt install python3.13-venv`). Sem sudo, dá para usar só stdlib + o pip
> do sistema: crie a venv sem pip e instale apontando o pip para o interpretador dela:
> ```bash
> python3 -m venv .venv --without-pip
> python3 -m pip --python .venv/bin/python install -r requirements-dev.txt
> python3 -m pip --python .venv/bin/python install --no-deps -e .
> ```

Dependências de sistema (validadas pelo `doctor`): **FFmpeg** e **yt-dlp**, além da
`ANTHROPIC_API_KEY`. Copie `.env.example` para `.env` e preencha a chave.

## Diagnóstico do ambiente (rode primeiro)

```bash
primocut doctor
```

Valida GPU/CUDA (WSL2), FFmpeg, yt-dlp e a chave da API — e imprime o comando de
correção para cada item ausente.

## Executar o pipeline (CLI, Fase 0)

```bash
primocut run "https://www.youtube.com/watch?v=<ID>" -o report.md
```

## App web (Fase 1)

Precisa de Redis (`sudo apt install redis-server` ou `docker run -p 6379:6379 redis`).

```bash
cd frontend && npm install && npm run build && cd ..   # build da UI (uma vez)
primocut worker    # processa a fila (terminal 1)
primocut serve     # API + UI em http://127.0.0.1:8000 (terminal 2)
```

Detalhes em [`docs/FASE1-MVP.md`](docs/FASE1-MVP.md).

## Testes por módulo (isolados, sem GPU/rede)

```bash
pytest tests/test_doctor.py -v
pytest tests/test_models.py -v
pytest tests/test_chat_signals.py -v
pytest tests/test_windowing.py -v
pytest tests/test_analysis_selection.py -v
pytest tests/test_report_markdown.py -v
pytest tests/test_transcription_engine.py -v
pytest -v   # tudo
```
