# PRIMOCUT — Fase 1 (MVP): fila + banco + API + Web UI

Transforma o pipeline CLI (Fase 0) numa aplicação web local, **reusando** os módulos do
pipeline (nada de lógica reescrita). Fonte de verdade: [`PRIMOCUT.md`](./PRIMOCUT.md).
Resiliência: [`FALLBACKS.md`](./FALLBACKS.md).

## Arquitetura

```
[Web UI Svelte] ──/api──> [FastAPI] ──enqueue──> [RQ + Redis] ──> [Worker]
                              │                                      │
                              └──────────────── [SQLite] <──────────┘
                     (videos, jobs, transcripts, chat_signals, topics, cut_moments)
[Disco] ← áudios (retenção temporária; limpeza agendada)
```

- **1 job RQ por vídeo** executa as etapas sequencialmente (`worker/jobs.py:process_video`),
  reusando `run_stages` (mesma lógica/resiliência do CLI) com `DbStore` (cache no banco) e
  `DbReporter` (status/progresso ao vivo).
- **Cache paga-uma-vez respaldado pelo banco:** reenfileirar pula etapas cujo artefato já existe
  (transcrição/tópicos/momentos) — nada já concluído/pago é reprocessado.
- **Progresso na UI = polling** (~2 s) — escolhido por simplicidade/robustez (single-user); status
  e progresso já vivem no banco.
- **Concorrência de GPU:** a etapa de transcrição adquire um **lock distribuído (Redis)**
  (`primocut:gpu`), garantindo **1 transcrição por vez** independentemente de quantos `primocut
  worker` estejam rodando. Assim, subir N workers é **seguro por construção**: as etapas leves
  (download/chat/análise) de vídeos diferentes se sobrepõem, mas só uma entra na GPU. `timeout` do
  lock (auto-libera se um worker morrer): `PRIMOCUT_GPU_LOCK_TIMEOUT` (default 2h).
- **Degradação (FALLBACKS.md) preservada** e refletida na UI: falha degradável → relatório com
  seção "⚠️ Execução degradada"; falha essencial (áudio/transcrição) → `Video.error`/`Job.failed`.

## Banco (SQLite)

Tabelas: `videos, jobs, transcripts, chat_signals, topics, cut_moments, schema_migrations`.
Migrations simples: schema base via `create_all` (v1) + deltas `db/migrations/NNN_*.sql`.
- `Video.status`: `queued → downloading → transcribing → analyzing → done | error`; **`stopped`** quando interrompido (via `stop`). Reprocessar (`reenqueue`) reusa o cache; deletar (`DELETE`) apaga tudo e exige reenviar a URL para recomeçar do zero.
- Áudio = arquivo em disco (apagável). Transcrição/tópicos/momentos/sinais = **permanentes** (banco).

## Pré-requisitos

- **Redis** (fila): `sudo apt install redis-server` ou `docker run -p 6379:6379 redis`.
- **Node** (build da UI): já validado (v22). Deps Python via `/usr/bin/python3 -m pip --python .venv/bin/python install -r requirements.txt`.
- `primocut doctor` valida GPU/FFmpeg/yt-dlp/chave/SDK **+ Redis + Banco**.

## Como rodar (produção local)

```bash
# 1. build da UI (uma vez, ou quando mudar o frontend)
cd frontend && npm install && npm run build && cd ..

# 2. Redis no ar (ver acima), depois em terminais separados:
source .venv/bin/activate
primocut worker      # processa a fila (precisa de Redis e GPU p/ transcrição real)
primocut serve       # API + UI em http://127.0.0.1:8000
```
Abra o navegador em `http://127.0.0.1:8000`: envie uma URL, acompanhe o status ao vivo na lista,
abra o relatório (tópicos macro + momentos, atividade do chat com picos, transcrição pesquisável,
timestamps clicáveis, 🔥 nos itens que coincidem com picos), marque cortes e exporte JSON.

## Desenvolvimento do frontend

```bash
cd frontend && npm run dev     # Vite em :5173, proxy /api → :8000 (rode `primocut serve` junto)
```

## API (resumo)

| Método | Rota | Ação |
|---|---|---|
| POST | `/api/videos` `{url}` | valida, cria Video(queued) + Job, enfileira |
| GET | `/api/videos` | lista com status/progresso |
| GET | `/api/videos/{id}` | detalhe (video + job) |
| GET | `/api/videos/{id}/report` | JSON do relatório (tópicos/momentos/chat/transcrição, 🔥, links) |
| POST | `/api/videos/{id}/topics/{tid}` `{status}` | marcar/desmarcar (novo/marcado/descartado) |
| POST | `/api/videos/{id}/moments/{mid}` `{status}` | idem para momentos |
| GET | `/api/videos/{id}/export?format=json` | download dos cortes marcados (JSON) |
| POST | `/api/videos/{id}/reenqueue` | reprocessa (idempotente; reseta status→queued) |
| POST | `/api/videos/{id}/stop` | interrompe o processamento (cancela o job/worker; status→stopped) |
| DELETE | `/api/videos/{id}` | apaga TUDO sobre o vídeo (banco + arquivos); recusa (409) se estiver ativo — pare antes |

### Formato de exportação (JSON)
```json
{ "video_id": "...", "url": "...", "title": "...", "generated_at": "...",
  "cuts": [ { "type": "topic|moment", "title": "...", "start_seconds": 0, "end_seconds": 20,
              "start_hms": "0:00", "end_hms": "0:20",
              "youtube_url": "https://youtube.com/watch?v=ID&t=0", "score": 0.7 } ] }
```

## Limpeza agendada

```bash
primocut cleanup                 # one-shot (bom para cron); apaga áudios > N dias
primocut cleanup --days 3
primocut cleanup --schedule      # registra limpeza diária no rq-scheduler (rode um `rqscheduler`)
```
Só apaga `*.wav/*.m4a/*.mp3`; **nunca** toca transcrições/tópicos/sinais (permanentes no banco).

## O que continua da Fase 0
`primocut doctor/run/models` seguem operacionais. O CLI `run` usa `FileStore` (cache JSON) +
relatório Markdown, comportamento idêntico. Multi-provedor de LLM (Anthropic/OpenAI/OpenRouter) e a
regra de ouro (LLM seleciona índices, nunca gera timestamps) preservados.

## Testes (você roda)
`pytest -q` cobre: migrations/DAO, transições de estado do job, **reenfileiramento idempotente
(cache respeitado)**, endpoints da API, marcação e exportação de cortes, e a limpeza (não apaga
áudio recente, nunca apaga transcrição). Sem necessidade de Redis/GPU reais (fakes + execução inline).
