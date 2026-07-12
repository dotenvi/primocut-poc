# PRIMOCUT — Resumo da Fase 0 (Spike CLI) — concluída ✅

> Handoff para orquestração. Fonte de verdade do produto: [`PRIMOCUT.md`](./PRIMOCUT.md).
> Guia de execução: [`FASE0-STATUS-E-EXECUCAO.md`](./FASE0-STATUS-E-EXECUCAO.md).
> Catálogo de resiliência: [`FALLBACKS.md`](./FALLBACKS.md). Plano: [`FASE0-SPIKE-CLI-PLANO.md`](./FASE0-SPIKE-CLI-PLANO.md).

**Objetivo:** validar, via CLI, o pipeline URL de live → áudio → transcrição → chat → tópicos em
Markdown; provar GPU no WSL2, qualidade de segmentação (tópicos macro vs. momentos finos) e
detecção de picos do chat.

## Estado
- **Concluída.** Pipeline roda ponta a ponta; testado num vídeo real de ~33 min (transcrição de
  914 segmentos em ~82s na RTX 3070 Ti). Suíte de testes 100% verde.

## Stack / ambiente
- Python nativo (**venv + pip + requirements**, sem uv — decisão do usuário). No WSL2 a venv foi
  criada `--without-pip` e instala via `/usr/bin/python3 -m pip --python .venv/bin/python`
  (falta o pacote `python3.13-venv`).
- yt-dlp (áudio) → FFmpeg (WAV 16kHz mono) → **faster-whisper `large-v3-turbo` GPU** → LLM
  (tool use) → relatório Markdown.
- CUDA no WSL2: libs `nvidia-cublas-cu12`/`nvidia-cudnn-cu12` via pip, pré-carregadas no processo.

## Entregas (arquitetura modular, pensada p/ Fase 1)
- **CLI** (`typer`): `doctor` (diagnóstico GPU/FFmpeg/yt-dlp/chave/SDK), `run` (pipeline),
  `models` (lista modelos OpenRouter com tool use, inclusive grátis).
- **Transcrição** atrás da interface `TranscriptionEngine` (troca fácil p/ WhisperX na Fase 2).
- **Análise LLM = 2 passagens independentes**: tópicos macro (10–20) e detecção fina de momentos.
- **Regra de ouro respeitada**: o LLM **nunca inventa timestamps** — seleciona *índices de
  segmento* (tool use forçado); o código resolve em segundos e **clampa** índices inválidos.
- **Janelamento** com sobreposição p/ lives longas (3–4h) + consolidação entre janelas.
- **Chat opcional**: picos por z-score (msgs/min), risadas/emotes; ausência de replay não bloqueia.
- **Relatório Markdown**: metadados, atividade do chat (sparkline+tabela+picos), 2 listas com
  **links clicáveis `&t=SEGUNDOS`**, marcador 🔥 em tópicos/momentos que coincidem com picos.
- **Modelos** em pydantic; **logging estruturado** com progresso por etapa.

## Multi-provedor de LLM
- **Anthropic** (default, `claude-sonnet-5`), **OpenAI** (`gpt-4o`), **OpenRouter** (compatível
  OpenAI; slugs `provedor/modelo`). Seleção por `PRIMOCUT_LLM_PROVIDER` ou flags `--provider/--model`.

## Resiliência / fallbacks (implementados + testados)
- **Degrada em vez de abortar**: gera o melhor relatório possível com seção "⚠️ Execução degradada";
  só aborta em falha de áudio/transcrição (essenciais).
- **Cache paga-uma-vez** em `data/<id>.*` (áudio, transcrição, tópicos, momentos) — nada já
  concluído/pago é refeito.
- Retry+backoff (erros transitórios); metadados degradam p/ `Video` mínimo; **player_client
  fallback** no yt-dlp; **downgrade de compute_type** na GPU (float16→int8_float16→int8);
  **chat fallback via yt-dlp `--live-chat`**; análise com retry por janela e **parcial preservado**
  em falha permanente; leitura tolerante de respostas malformadas do LLM.

## Como rodar
```bash
source .venv/bin/activate
primocut doctor
primocut run "https://www.youtube.com/watch?v=<ID>" -o report.md
# opcional: --provider openrouter --model meta-llama/llama-3.3-70b-instruct:free
```

## Fora de escopo / próximos passos (Fase 1)
Fila (RQ+Redis), banco (SQLite), API (FastAPI), web UI navegável, marcação/exportação de cortes,
limpeza de áudios. Candidatos técnicos: cache incremental por janela; troca automática de provedor
(hoje é só retry, por decisão); WhisperX na Fase 2.
