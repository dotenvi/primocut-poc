# PRIMOCUT — Catálogo de Fallbacks e Resiliência

Mapa de **todo** comportamento de recuperação/degradação do sistema. Fonte de verdade do produto:
[`PRIMOCUT.md`](./PRIMOCUT.md). Plano da resiliência por etapa: `plano aprovado` (ver
[`FASE0-STATUS-E-EXECUCAO.md`](./FASE0-STATUS-E-EXECUCAO.md)).

## Princípios

- **Degradar, não abortar.** Sempre que possível, gerar o melhor relatório com o que deu certo,
  marcando as seções indisponíveis. Só abortar quando é **impossível continuar**.
- **Essencial vs. degradável.** Áudio e transcrição são essenciais (sem eles não há o que analisar);
  metadados, chat e análise são degradáveis.
- **Cache paga-uma-vez.** Etapas caras são persistidas em `data/<video_id>.*` e reaproveitadas;
  nenhum trabalho já concluído (e pago) é refeito.
- **Nunca inventar timestamps.** A robustez da análise nunca relaxa a regra: o LLM seleciona índices
  de segmento; o código resolve em segundos e clampa índices inválidos.

### Legenda de status
- ✅ **Implementado** (no código hoje) — todos os fallbacks listados abaixo já estão implementados e cobertos por testes.

---

## 1. Fallbacks por etapa do pipeline

| Etapa | Gatilho | Comportamento de fallback | Status |
|---|---|---|---|
| **Metadados** | `yt-dlp -J` falha por erro transitório (rede/rate limit) | Retry com backoff exponencial | ✅ |
| **Metadados** | `yt-dlp -J` falha de forma permanente | **Degrada**: monta `Video` mínimo (id extraído da URL, título/canal "(desconhecido)", duração 0); adiciona aviso; a duração é preenchida depois a partir de `transcript.duration` | ✅ |
| **Download áudio** | Erro transitório do yt-dlp | Retry com backoff | ✅ |
| **Download áudio** | Extrator do YouTube quebrou p/ um client | Tenta `--extractor-args youtube:player_client=<c>` numa lista configurável (ex.: `android,web`) | ✅ |
| **Download áudio** | Falha final (essencial) | **Aborta** com mensagem clara (sem áudio não há transcrição) | ✅ |
| **Transcrição** | Erro de GPU/VRAM (cabe nos 8 GB?) | Loop degradando o `compute_type`: `float16 → int8_float16 → int8`; registra aviso quando degrada | ✅ |
| **Transcrição** | Nenhum `compute_type` coube (essencial) | **Aborta** com orientação (sem fallback para CPU, por decisão) | ✅ |
| **Transcrição** | `libcublas`/`libcudnn` não no `LD_LIBRARY_PATH` | Pré-carrega as libs CUDA dos pacotes pip da NVIDIA (`nvidia-cublas-cu12`/`nvidia-cudnn-cu12`); se ausentes, deixa o ctranslate2 tentar o PATH do sistema | ✅ |
| **Chat** | `chat-downloader` lança exceção / sem replay | **Fallback yt-dlp `--live-chat`** (parseia offsets das mensagens) | ✅ |
| **Chat** | Ambos falham / replay inexistente | Retorna `None`; o pipeline **segue sem chat** (sinal opcional) e registra aviso | ✅ |
| **Análise (tópicos/momentos)** | Erro transitório do provedor por janela (429/5xx/timeout) | Retry com backoff no mesmo modelo (sem trocar de modelo/provedor) | ✅ |
| **Análise (tópicos/momentos)** | Falha permanente numa janela (crédito, auth, sem tool use) | **Encerra a etapa mantendo o que já foi coletado** (parcial); pipeline degrada o relatório + aviso | ✅ |
| **Análise (tópicos/momentos)** | Resposta do LLM fora do formato | Leitura tolerante: normaliza dict indexado, **ignora itens malformados com aviso** (nunca derruba a etapa) | ✅ |
| **Análise (tópicos/momentos)** | LLM devolve índices fora do intervalo | `resolve_span` **clampa** aos limites reais e ordena se vierem invertidos | ✅ |
| **Relatório** | Alguma seção sem dados | Renderiza "Nenhum tópico/momento identificado" e/ou "Sem replay de chat"; nunca falha | ✅ |
| **Relatório** | Execução teve degradações | Seção **"⚠️ Execução degradada"** logo após os metadados, listando o que falhou/foi pulado | ✅ |

---

## 2. Cache / retomada (não re-paga o que já deu certo) ✅

Cada arquivo em `data/<video_id>.*` é reaproveitado em re-execuções:

| Arquivo | Etapa que evita repetir |
|---|---|
| `*.16k.wav` | Download + normalização do áudio |
| `*.transcript.json` | Transcrição (a etapa mais cara em GPU) |
| `*.topics.json` | Análise macro (LLM, paga) |
| `*.moments.json` | Análise fina (LLM, paga) |

- Os **tópicos são gravados antes** de a etapa de momentos rodar → uma falha nos momentos não
  descarta os tópicos já pagos.
- ✅ **Ajuste implementado:** resultado **parcial** de uma etapa (quando ela é encerrada no meio por
  falha permanente) alimenta o relatório atual, mas **não** é cacheado — para que um re-run tente de
  novo. Como as janelas são poucas (~2 numa live de 33 min), a re-tentativa é barata.
- Para forçar recomputar, apague o arquivo de cache correspondente em `data/`.

---

## 3. Guardas de robustez transversais ✅

Comportamentos que evitam falhas antes de virarem erro (já no código):

| Guarda | O que faz |
|---|---|
| **Validação de URL** (`validate_youtube_url`) | Rejeita links fora do formato (host não-YouTube, ID inválido, contra-barras de escape) com mensagem clara e **não dispara** o pipeline; devolve a URL canônica `watch?v=<ID>` |
| **Extração de ID** (`extract_video_id`) | Aceita `watch?v=`, `youtu.be/`, `/live/`, `/embed/`, `/shorts/` e cai para o último segmento do path |
| **yt-dlp via módulo** | Invoca `python -m yt_dlp` pelo interpretador atual → funciona mesmo sem `.venv/bin` no `PATH` |
| **doctor: yt-dlp** | Considera OK se o **módulo** `yt_dlp` está instalado (não exige binário no PATH) |
| **doctor: chave da API** | Valida a chave do **provedor selecionado** (`llm_provider`) |
| **doctor: SDK do provedor** | Avisa se o SDK do provedor (openai/anthropic) não está instalado |
| **Import do SDK OpenAI** | Se faltar o pacote `openai` (usado por OpenAI/OpenRouter), erro claro dizendo o comando de instalação |
| **Sem suporte a tools** (`ToolCallError`) | Se o modelo não retorna a function/tool call forçada, erro claro (provavelmente não suporta tool use) + dica do comando `primocut models` |
| **Sinais do chat** | `z-score` com desvio-padrão 0 → sem picos (sem divisão por zero); lista de mensagens vazia → sinal vazio |
| **`primocut models`** | Lista modelos do OpenRouter com suporte a ferramentas (inclusive **gratuitos**) para escolher um compatível sem gastar |

---

## 4. Seleção de provedor de LLM ✅

A análise é agnóstica de provedor (interface `SelectionLLM`). Seleção automática por `PRIMOCUT_LLM_PROVIDER`:

| Provedor | SDK | Observação |
|---|---|---|
| `anthropic` (default) | `anthropic` | Modelo default `claude-sonnet-5` |
| `openai` | `openai` | Modelo default `gpt-4o` |
| `openrouter` | `openai` (base_url própria) | Modelo default `openai/gpt-4o-mini`; slugs `provedor/modelo` |

> **Não há troca automática de provedor/modelo em caso de falha** (decisão: LLM = "só retry").
> Trocar de provedor/modelo é sempre explícito, via `.env` ou flags `--provider`/`--model`.

---

## 5. Matriz: essencial vs. degradável

| Etapa | Se falhar de vez | Relatório sai? |
|---|---|---|
| Metadados | Degrada (Video mínimo) | ✅ Sim, com aviso |
| Download áudio | Aborta | ❌ Não (essencial) |
| Transcrição | Aborta | ❌ Não (essencial) |
| Chat | Segue sem chat | ✅ Sim, com aviso |
| Análise (tópicos/momentos) | Usa o parcial (ou vazio) | ✅ Sim, com aviso |
| Relatório | — | ✅ Sempre gera |

---

## 6. Variáveis de ambiente relacionadas

| Variável | Efeito | Status |
|---|---|---|
| `PRIMOCUT_LLM_PROVIDER` | `anthropic` \| `openai` \| `openrouter` | ✅ |
| `PRIMOCUT_ANTHROPIC_MODEL` / `PRIMOCUT_OPENAI_MODEL` / `PRIMOCUT_OPENROUTER_MODEL` | Modelo por provedor | ✅ |
| `PRIMOCUT_WHISPER_COMPUTE_TYPE` | Força o `compute_type` (ex.: `int8_float16` p/ caber na VRAM) | ✅ |
| `PRIMOCUT_WHISPER_DEVICE` | `cuda` (default) \| `cpu` | ✅ |
| `PRIMOCUT_CHAT_ZSCORE_THRESHOLD` | Limiar de detecção de picos do chat | ✅ |
| `PRIMOCUT_WINDOW_TARGET_SECONDS` / `PRIMOCUT_WINDOW_OVERLAP_SECONDS` | Janelamento da análise | ✅ |
| `PRIMOCUT_RETRY_ATTEMPTS` / `PRIMOCUT_RETRY_BASE_DELAY` / `PRIMOCUT_RETRY_MAX_DELAY` | Política de retry (backoff) das etapas | ✅ |
| `PRIMOCUT_WHISPER_COMPUTE_FALLBACKS` | Sequência de degradação do `compute_type` na GPU | ✅ |
| `PRIMOCUT_YTDLP_PLAYER_CLIENTS` | Lista de `player_client` a tentar no download | ✅ |

---

## 7. Ponteiros de código

- Pipeline / orquestração: `src/primocut/pipeline.py`
- Análise (leitura tolerante, provedores, tool use): `src/primocut/analysis/{parse,llm,topics,moments}.py`
- Transcrição (preload CUDA, engine): `src/primocut/transcription/faster_whisper_engine.py`
- Download (URL, yt-dlp, ffmpeg): `src/primocut/download/audio.py`
- Chat (opcional): `src/primocut/chat/downloader.py`
- Modelos (resolve_span, clamp): `src/primocut/models.py`
- Diagnóstico: `src/primocut/doctor.py`
- (✅) Resiliência transversal (retry/backoff/classificação): `src/primocut/resilience.py`
