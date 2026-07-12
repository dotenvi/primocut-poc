# PRIMOCUT — Fase 0: Status e Guia de Execução

> O que já foi implementado no Spike CLI e o passo a passo para você rodar sozinho.
> Fonte de verdade: [`PRIMOCUT.md`](./PRIMOCUT.md). Plano: [`FASE0-SPIKE-CLI-PLANO.md`](./FASE0-SPIKE-CLI-PLANO.md).

## 1. Status — o que foi entregue

Projeto Python modular (já preparado para a Fase 1), **58 testes passando**, construído em
TDD (teste-primeiro). Todas as diretrizes técnicas do MVP foram atendidas na Fase 0.

| Área | Módulo | O que faz | Testes |
|---|---|---|---|
| Diagnóstico | `doctor.py` + `cli.py` | Valida GPU/CUDA (WSL2), FFmpeg, yt-dlp e `ANTHROPIC_API_KEY`; imprime o comando de correção de cada item ausente | `test_doctor.py` (10) |
| Modelos | `models.py` | Modelos pydantic (`Video`, `Transcript`, `Segment`, `ChatSignal`, `Topic`, `CutMoment`, `Report`) + `resolve_span` e `youtube_url` | `test_models.py` (10) |
| Chat (opcional) | `chat/signals.py` | Densidade msgs/min, risadas/emotes, **picos por z-score** | `test_chat_signals.py` (7) |
| Chat (download) | `chat/downloader.py` | Replay via chat-downloader; ausência retorna `None` (não bloqueia o pipeline) | `test_downloaders.py` |
| Transcrição | `transcription/engine.py` + `faster_whisper_engine.py` | Interface `TranscriptionEngine` (troca fácil na Fase 2) + faster-whisper `large-v3-turbo` em GPU (import preguiçoso) | `test_transcription_engine.py` (4) |
| Janelamento | `analysis/windowing.py` | Divide transcrições longas em janelas com sobreposição, preservando índices globais | `test_windowing.py` (5) |
| Análise LLM | `analysis/{llm,prompts,topics,moments,consolidate}.py` | **Duas passagens independentes**: tópicos macro (10–20) e detecção fina de momentos; consolida entre janelas | `test_analysis_selection.py` (6) |
| Download áudio | `download/audio.py` | yt-dlp + FFmpeg → WAV 16kHz mono | `test_downloaders.py` (9 no total) |
| Relatório | `report/markdown.py` | Metadados + atividade do chat (sparkline/tabela/picos) + 2 listas com links `&t=SEGUNDOS`, marcador 🔥 nos picos | `test_report_markdown.py` (5) |
| Orquestração | `pipeline.py` | Encadeia as etapas com progresso; comando `run` | `test_pipeline.py` (2) |

**Garantia anti-invenção de timestamps (diretriz #2):** o LLM recebe segmentos indexados
(`[i] M:SS texto`) e devolve apenas `start_index`/`end_index`; o código resolve em segundos e
**clampa índices fora do intervalo**. É impossível o LLM inventar um tempo.

## 2. Pré-requisitos do ambiente

Já verificado com `nvidia-smi`: **RTX 3070 Ti (8 GB) visível via CUDA no WSL2** ✅.
Faltam (o `doctor` confirma): **FFmpeg**, **yt-dlp**, a **chave da API** e as libs pesadas de runtime.

> **Nuance deste ambiente:** falta o pacote `python3.13-venv` (sem `ensurepip`) e o `sudo` pede
> senha, por isso a venv foi criada com `--without-pip` e as instalações são feitas apontando o
> pip do sistema para o interpretador da venv. Os comandos abaixo já usam essa forma.

## 3. Setup (ferramentas nativas do Python)

A venv (`.venv/`) e as dependências leves (typer, pydantic, anthropic, rich, pytest) já estão
instaladas, e o pacote `primocut` já está registrado (`pip install -e .`). Para recriar do zero:

```bash
cd ~/projects/primocut

# venv sem pip (porque falta python3.13-venv) + instalação via pip do SISTEMA.
# Não ative a venv aqui: use /usr/bin/python3 (o python da venv não tem pip).
python3 -m venv .venv --without-pip
/usr/bin/python3 -m pip --python .venv/bin/python install -r requirements-dev.txt
/usr/bin/python3 -m pip --python .venv/bin/python install --no-deps -e .
```

Se você instalar o pacote `python3.13-venv` (`sudo apt install python3.13-venv`), passa a valer
o fluxo padrão: `python -m venv .venv && source .venv/bin/activate && pip install -r requirements-dev.txt && pip install -e .`.

## 4. Rodar os testes (por módulo, sem GPU/rede)

```bash
source .venv/bin/activate

pytest -q                                     # tudo (58 testes)
pytest tests/test_doctor.py -v                # diagnóstico
pytest tests/test_models.py -v                # modelos + youtube_url
pytest tests/test_chat_signals.py -v          # z-score / picos
pytest tests/test_windowing.py -v             # janelas com sobreposição
pytest tests/test_analysis_selection.py -v    # seleção de índices (nunca inventa ts)
pytest tests/test_report_markdown.py -v       # relatório
pytest tests/test_transcription_engine.py -v  # interface de transcrição
pytest tests/test_downloaders.py -v           # yt-dlp/chat mockados
pytest tests/test_pipeline.py -v              # end-to-end com fakes
```

## 5. Diagnóstico do ambiente (rode primeiro)

```bash
source .venv/bin/activate
primocut doctor
```

Mostra uma tabela com GPU/FFmpeg/yt-dlp/API key e, para cada falha, o comando de correção.
Sai com código `1` enquanto houver pendências.

## 6. Habilitar o pipeline real

### 6.1. Dependências de sistema e de runtime

```bash
# FFmpeg (sistema) — precisa de sudo
sudo apt update && sudo apt install -y ffmpeg

# Libs pesadas de runtime (áudio, transcrição, chat) na venv.
# IMPORTANTE: use o pip do SISTEMA (/usr/bin/python3). Se você chamar apenas
# `python3` com a venv ATIVADA, ele usa o python da venv, que não tem pip
# (erro: "No module named pip"). O caminho absoluto funciona sempre:
/usr/bin/python3 -m pip --python .venv/bin/python install faster-whisper yt-dlp chat-downloader

# (Opcional) libs CUDA por pip, se o faster-whisper não achar cuBLAS/cuDNN no WSL:
/usr/bin/python3 -m pip --python .venv/bin/python install nvidia-cublas-cu12 nvidia-cudnn-cu12
```

### 6.2. Provedor de LLM e chave da API

A análise é **agnóstica de provedor** (interface `SelectionLLM`). Suporta **Anthropic**
(default), **OpenAI** e **OpenRouter**. Cada provedor cobra pela sua própria API (assinatura de
consumidor — Claude Pro/Max ou ChatGPT Plus — **não** dá acesso à API; é preciso saldo/créditos).

**Anthropic (default):**
```bash
cp .env.example .env
# edite .env: ANTHROPIC_API_KEY=sk-ant-...
```
Modelo padrão `claude-sonnet-5` (troque com `PRIMOCUT_ANTHROPIC_MODEL` ou `-m`).

**OpenAI:**
```bash
/usr/bin/python3 -m pip --python .venv/bin/python install openai   # dep opcional
# no .env:  OPENAI_API_KEY=sk-...  e  PRIMOCUT_LLM_PROVIDER=openai
```
Modelo padrão `gpt-4o` (troque com `PRIMOCUT_OPENAI_MODEL` ou `-m`).

**OpenRouter** (uma chave, acesso a muitos modelos; compatível com a API da OpenAI):
```bash
/usr/bin/python3 -m pip --python .venv/bin/python install openai   # mesmo SDK
# no .env:  OPENROUTER_API_KEY=sk-or-...  e  PRIMOCUT_LLM_PROVIDER=openrouter
```
Modelo padrão `openai/gpt-4o-mini` — use os *slugs* do OpenRouter (`provedor/modelo`), ex.:
`anthropic/claude-3.5-sonnet`, `google/gemini-2.0-flash-001`, `meta-llama/llama-3.3-70b-instruct`.
Troque com `PRIMOCUT_OPENROUTER_MODEL` ou `-m`.

Você também pode escolher o provedor/modelo direto no comando, sem editar o `.env`:
```bash
primocut run "<url>" -o report.md --provider openai --model gpt-4o-mini
primocut run "<url>" -o report.md --provider anthropic --model claude-haiku-4-5
primocut run "<url>" -o report.md --provider openrouter --model anthropic/claude-3.5-sonnet
```

O `primocut doctor` valida a chave do **provedor selecionado** (`PRIMOCUT_LLM_PROVIDER`).

> **A análise exige tool use (function calling).** Se o modelo não suportar, o pipeline
> falha com uma mensagem clara (`ToolCallError`) em vez de gerar relatório vazio.
> Para achar modelos do OpenRouter compatíveis (inclusive **gratuitos**, sem precisar de saldo):
> ```bash
> primocut models --free        # grátis + com ferramentas
> primocut models               # todos com ferramentas
> ```
> Ex. de modelo grátis com tools para testar: `meta-llama/llama-3.3-70b-instruct:free`.
> (Modelos `:free` têm limites diários de requisição, mas sobram para validar o pipeline.)

### 6.3. Confirmar que está tudo pronto

```bash
source .venv/bin/activate
primocut doctor        # deve ficar tudo OK / "Ambiente pronto ✔"
```

### 6.4. Rodar de ponta a ponta

```bash
# Comece por uma live/vídeo CURTO para validar rápido (GPU, custo de API, qualidade)
primocut run "https://www.youtube.com/watch?v=<ID>" -o report.md

# Abra o relatório e clique nos timestamps
```

## 6.5. Cache / retomada (não re-paga o que já deu certo)

Cada etapa cara é persistida em `data/<video_id>.*` e reaproveitada em re-execuções:
- `*.16k.wav` — áudio normalizado (não rebaixa).
- `*.transcript.json` — transcrição (não re-transcreve).
- `*.topics.json` — tópicos macro (não re-paga a análise macro).
- `*.moments.json` — momentos de corte (não re-paga a análise fina).

Os tópicos são gravados **antes** de a etapa de momentos rodar, então uma falha nos
momentos não descarta os tópicos já pagos. Além disso, itens malformados na resposta
do LLM são **ignorados com aviso** (nunca derrubam a análise inteira). Para forçar
recomputar uma etapa, apague o arquivo de cache correspondente em `data/`.

## 6.6. Resiliência / fallbacks

Cada etapa tem estratégia de recuperação e o pipeline **degrada em vez de abortar** (gera o melhor
relatório possível, marcando o que falhou numa seção **"⚠️ Execução degradada"**). Só aborta quando
é impossível continuar (sem áudio ou sem transcrição). Catálogo completo: [`FALLBACKS.md`](./FALLBACKS.md).
Env vars relacionadas: `PRIMOCUT_RETRY_*`, `PRIMOCUT_WHISPER_COMPUTE_FALLBACKS`, `PRIMOCUT_YTDLP_PLAYER_CLIENTS`.

## 7. Dicas de primeira execução

- O primeiro `run` baixa o modelo `large-v3-turbo` (alguns GB) na primeira vez — depois fica em cache.
- Se a VRAM de 8 GB apertar, troque o `compute_type`:
  `export PRIMOCUT_WHISPER_COMPUTE_TYPE=int8_float16`.
- Ajuste de janelas da análise (para lives de 3–4h): `PRIMOCUT_WINDOW_TARGET_SECONDS`,
  `PRIMOCUT_WINDOW_OVERLAP_SECONDS`. Threshold de pico do chat: `PRIMOCUT_CHAT_ZSCORE_THRESHOLD`.
- Se o download falhar, atualize o yt-dlp (quebra com frequência):
  `/usr/bin/python3 -m pip --python .venv/bin/python install -U yt-dlp`.
- O chat é opcional: se a live não tiver replay, o relatório mostra "Sem replay de chat" e o
  restante do pipeline segue normalmente.

## 8. O que fica para as próximas fases
Fila (RQ+Redis), banco (SQLite), API (FastAPI), web UI, marcação/exportação de cortes, limpeza
de áudios e a renderização automática de clipes (Fase 2) — nada disso entra na Fase 0.
