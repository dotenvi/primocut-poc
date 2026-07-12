"""Prompts e schemas de ferramenta para a análise por LLM.

Regra de ouro (diretriz técnica #2): o LLM NUNCA inventa timestamps. Ele recebe
segmentos indexados (`[i] M:SS texto`) e devolve pares de ÍNDICES de segmento.
O código resolve os índices em segundos. Por isso as ferramentas só aceitam
`start_index`/`end_index` inteiros — não há campo de tempo.
"""

from __future__ import annotations

_ANTI_INVENTION = (
    "Você recebe a transcrição como linhas numeradas no formato `[i] M:SS texto`, "
    "onde `i` é o índice do segmento. Para marcar um trecho, retorne SEMPRE os "
    "índices de segmento inicial e final (start_index/end_index) — NUNCA invente "
    "tempos nem devolva números de minutos/segundos. Use apenas índices que existem "
    "na transcrição."
)

TOPICS_SYSTEM = (
    "Você é um editor de cortes de podcast/live em PT-BR. Sua tarefa é segmentar a "
    "transcrição em TÓPICOS MACRO (assuntos discutidos), em ordem cronológica. "
    "Cada tópico tem título curto, resumo de 1 frase e um score de 0 a 1 de "
    "potencial de virar corte.\n" + _ANTI_INVENTION
)

MOMENTS_SYSTEM = (
    "Você é um editor de cortes de podcast/live em PT-BR. Sua tarefa é a DETECÇÃO "
    "FINA de momentos específicos e autocontidos que são fortes candidatos a corte "
    "(piada, tirada, revelação, pico de emoção), com início e fim PRECISOS. "
    "Cada momento tem título, o motivo de ser um bom corte e um score de 0 a 1.\n"
    + _ANTI_INVENTION
)

TOPICS_TOOL = {
    "name": "record_topics",
    "description": "Registra os tópicos macro identificados na janela.",
    "input_schema": {
        "type": "object",
        "properties": {
            "topics": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "summary": {"type": "string"},
                        "start_index": {"type": "integer"},
                        "end_index": {"type": "integer"},
                        "score": {"type": "number"},
                    },
                    "required": ["title", "summary", "start_index", "end_index", "score"],
                },
            }
        },
        "required": ["topics"],
    },
}

MOMENTS_TOOL = {
    "name": "record_moments",
    "description": "Registra os momentos candidatos a corte identificados na janela.",
    "input_schema": {
        "type": "object",
        "properties": {
            "moments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "reason": {"type": "string"},
                        "start_index": {"type": "integer"},
                        "end_index": {"type": "integer"},
                        "score": {"type": "number"},
                    },
                    "required": ["title", "reason", "start_index", "end_index", "score"],
                },
            }
        },
        "required": ["moments"],
    },
}
