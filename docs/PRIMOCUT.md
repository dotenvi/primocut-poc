# PRIMOCUT

**Pri**nt **Mo**ney with **Cut** — automação de canais de cortes do YouTube.

## Visão

A aplicação recebe a URL de uma live do YouTube (podcast, stream), baixa o áudio e o replay do chat, transcreve com timestamps e usa IA para gerar um índice de tópicos do que foi falado. O usuário lê o relatório — em vez de assistir 3-4 horas de vídeo — e identifica os trechos que quer transformar em cortes.

## Escopo do MVP

Entregar, a partir de uma URL, um relatório navegável contendo:

- Tópicos em ordem cronológica: título, resumo curto, timestamp de início/fim, score de potencial de corte.
- Gráfico de atividade do chat ao longo da live (picos de mensagens = candidatos a corte).
- Transcrição completa pesquisável, com timestamps clicáveis (`https://youtube.com/watch?v=ID&t=SEGUNDOS`).
- Marcação de tópicos como "corte" e exportação da lista de timestamps.

**Fora do MVP:** corte/renderização automática de clipes, geração de título/thumbnail, upload para YouTube, formato vertical, multi-usuário, autenticação, deploy remoto.

## Contexto de execução

- Roda 100% local: Debian via WSL2, single-user, sem auth.
- GPU: NVIDIA RTX 3070 Ti (8 GB VRAM) via CUDA no WSL2. Driver fica no Windows; dentro do WSL usar bibliotecas CUDA/cuDNN via pip (`nvidia-cublas-cu12`, `nvidia-cudnn-cu12`). Validar com `nvidia-smi`.
- Lives em PT-BR apenas (modelos distil do Whisper são English-only — não usar).

## Arquitetura

```
[Web UI] → [FastAPI] → [Fila (RQ + Redis)] → Workers:
                                              1. Download do áudio (yt-dlp, apenas áudio)
                                              2. Download do chat replay (chat-downloader)
                                              3. Transcrição (faster-whisper, GPU)
                                              4. Análise (LLM → tópicos + timestamps,
                                                 cruzando sinais do chat)
[SQLite] ← metadados, jobs, transcrições, tópicos, sinais do chat
[Disco local] ← áudios (retenção temporária)
```

## Stack (decidida)

| Camada | Tecnologia |
|---|---|
| Download de áudio | yt-dlp (extrair só áudio, normalizar com FFmpeg para 16kHz mono) |
| Download do chat | chat-downloader (fallback: yt-dlp live_chat) |
| Transcrição | faster-whisper, modelo `large-v3-turbo`, compute em GPU |
| Análise/tópicos | LLM via API (Anthropic) |
| API | FastAPI |
| Fila | RQ + Redis |
| Banco | SQLite |
| Frontend | Svelte (SPA compilada com Vite), servida como estático pelo FastAPI |
| Dependências | Python padrão: venv + pip + requirements.txt (sem uv/poetry) |

## Modelo de dados

- **Video**: id, url, título, canal, duração, status (queued/downloading/transcribing/analyzing/done/error)
- **Transcript**: video_id, segmentos [{início, fim, texto}], idioma, modelo usado
- **ChatSignal**: video_id, série temporal [{minuto, msgs, risadas, emotes}], picos detectados [{ts, intensidade}]
- **Topic**: video_id, título, resumo, ts_início, ts_fim, score, status (novo/marcado/descartado)
- **Job**: video_id, etapa, progresso, erro

## Diretrizes técnicas

1. **TranscriptionEngine como interface**: o worker de transcrição deve ficar atrás de uma abstração para permitir trocar faster-whisper por WhisperX (Fase 2) sem tocar no resto do sistema.
2. **Timestamps nunca inventados**: a análise por LLM deve referenciar exclusivamente timestamps existentes na transcrição. Estruturar o prompt/dados de forma que o LLM selecione segmentos, não gere números.
3. **Transcrições longas**: lives de 3-4h excedem contexto útil do LLM — processar em janelas com sobreposição e consolidar os tópicos.
4. **Chat é sinal opcional**: nem toda live preserva o replay do chat; ausência não pode bloquear o pipeline. Detecção de picos por densidade de mensagens/minuto (z-score) e marcadores de reação (risadas "KKKK", emotes).
5. **Retenção**: áudio apagado por job de limpeza após N dias (configurável, default 7); transcrições, tópicos e sinais do chat são permanentes.
6. **Jobs assíncronos**: nenhum processamento pesado dentro de request HTTP; tudo via fila com status/progresso persistidos.
7. **yt-dlp atualizado**: quebra com frequência por mudanças do YouTube; fixar processo de atualização fácil.

## Roadmap

- **Fase 0 — Spike CLI (atual):** script de linha de comando: URL → áudio → transcrição → chat → tópicos em Markdown. Objetivo: validar GPU no WSL2, qualidade da segmentação (tópicos macro vs. detecção fina de momentos) e detecção de picos do chat.
- **Fase 1 — MVP:** fila + banco + web UI + relatório navegável + marcação/exportação de cortes + limpeza de áudios.
- **Fase 2 — Corte assistido:** geração automática de clipes com FFmpeg; avaliar WhisperX (timestamps por palavra + diarização).
- **Fase 3 — Publicação:** títulos/descrições via IA, formato vertical, upload via YouTube API, multi-usuário.
