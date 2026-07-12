"""Renderização do relatório final em Markdown (requisito #7).

Contém: metadados da live, atividade do chat (sparkline + tabela + picos) e as
DUAS listas de tópicos com timestamps clicáveis. Tópicos/momentos que coincidem
com um pico de chat recebem o marcador 🔥.
"""

from __future__ import annotations

from ..models import ChatSignal, CutMoment, Report, Topic, format_timestamp

_SPARK_BLOCKS = "▁▂▃▅▇"


def _sparkline(values: list[int]) -> str:
    if not values:
        return ""
    top = max(values)
    if top == 0:
        return _SPARK_BLOCKS[0] * len(values)
    out = []
    for v in values:
        idx = int(round((v / top) * (len(_SPARK_BLOCKS) - 1)))
        out.append(_SPARK_BLOCKS[idx])
    return "".join(out)


def _coincides_with_peak(start: float, end: float, signal: ChatSignal | None) -> bool:
    if signal is None:
        return False
    return any(start <= p.ts <= end for p in signal.peaks)


def _topic_line(report: Report, topic: Topic) -> str:
    fire = " 🔥" if _coincides_with_peak(topic.start, topic.end, report.chat_signal) else ""
    link = report.youtube_url(topic.start)
    span = f"{format_timestamp(topic.start)}–{format_timestamp(topic.end)}"
    return (
        f"- [**{topic.title}**]({link}) · {span} · score {topic.score:.2f}{fire}\n"
        f"  {topic.summary}"
    )


def _moment_line(report: Report, moment: CutMoment) -> str:
    fire = " 🔥" if _coincides_with_peak(moment.start, moment.end, report.chat_signal) else ""
    link = report.youtube_url(moment.start)
    span = f"{format_timestamp(moment.start)}–{format_timestamp(moment.end)}"
    return (
        f"- [**{moment.title}**]({link}) · {span} · score {moment.score:.2f}{fire}\n"
        f"  {moment.reason}"
    )


def _chat_section(report: Report) -> list[str]:
    signal = report.chat_signal
    lines = ["## Atividade do chat", ""]
    if signal is None or not signal.per_minute:
        lines.append("_Sem replay de chat disponível para esta live._")
        return lines

    counts = [b.msgs for b in signal.per_minute]
    lines.append(f"Densidade de mensagens/minuto: `{_sparkline(counts)}`")
    lines.append("")
    lines.append("| Minuto | Msgs | Risadas | Emotes |")
    lines.append("|---|---|---|---|")
    for b in signal.per_minute:
        lines.append(f"| {b.minute} | {b.msgs} | {b.laughs} | {b.emotes} |")
    lines.append("")

    lines.append("### Picos (z-score)")
    if signal.peaks:
        for p in signal.peaks:
            link = report.youtube_url(p.ts)
            lines.append(
                f"- [{format_timestamp(p.ts)}]({link}) · {int(p.intensity)} msgs · z={p.z:.1f}"
            )
    else:
        lines.append("_Nenhum pico significativo detectado._")
    return lines


def render_report(report: Report) -> str:
    v = report.video
    lines: list[str] = []

    # 1. Metadados
    lines.append(f"# {v.title}")
    lines.append("")
    lines.append(f"- **Canal:** {v.channel}")
    lines.append(f"- **URL:** {v.url}")
    lines.append(f"- **Duração:** {format_timestamp(v.duration)}")
    lines.append(f"- **Transcrição:** {report.transcript_model}")
    lines.append(f"- **Gerado em:** {report.generated_at:%Y-%m-%d %H:%M UTC}")
    lines.append("")

    # 1b. Avisos de execução degradada (fallbacks acionados)
    if report.warnings:
        lines.append("## ⚠️ Execução degradada")
        lines.append("")
        lines.append("Algumas etapas usaram fallback ou foram parciais:")
        for w in report.warnings:
            lines.append(f"- {w}")
        lines.append("")

    # 2. Atividade do chat
    lines.extend(_chat_section(report))
    lines.append("")

    # 3. Tópicos macro
    lines.append(f"## Tópicos macro ({len(report.topics)})")
    lines.append("")
    if report.topics:
        for topic in report.topics:
            lines.append(_topic_line(report, topic))
    else:
        lines.append("_Nenhum tópico identificado._")
    lines.append("")

    # 4. Momentos candidatos a corte (detecção fina)
    lines.append(f"## Momentos candidatos a corte ({len(report.moments)})")
    lines.append("")
    if report.moments:
        for moment in report.moments:
            lines.append(_moment_line(report, moment))
    else:
        lines.append("_Nenhum momento identificado._")
    lines.append("")

    return "\n".join(lines)
