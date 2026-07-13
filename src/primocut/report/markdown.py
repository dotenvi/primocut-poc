"""Final report rendering in Markdown (requirement #7).

Contains: live metadata, chat activity (sparkline + table + peaks) and the macro
topic list with clickable timestamps. Topics coinciding with a chat peak get the
🔥 marker.
"""

from __future__ import annotations

from ..models import ChatSignal, Report, Topic, format_timestamp

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


def _chat_section(report: Report) -> list[str]:
    signal = report.chat_signal
    lines = ["## Chat activity", ""]
    if signal is None or not signal.per_minute:
        lines.append("_No chat replay available for this live._")
        return lines

    counts = [b.msgs for b in signal.per_minute]
    lines.append(f"Message density per minute: `{_sparkline(counts)}`")
    lines.append("")
    lines.append("| Minute | Msgs | Laughs | Emotes |")
    lines.append("|---|---|---|---|")
    for b in signal.per_minute:
        lines.append(f"| {b.minute} | {b.msgs} | {b.laughs} | {b.emotes} |")
    lines.append("")

    lines.append("### Peaks (z-score)")
    if signal.peaks:
        for p in signal.peaks:
            link = report.youtube_url(p.ts)
            lines.append(
                f"- [{format_timestamp(p.ts)}]({link}) · {int(p.intensity)} msgs · z={p.z:.1f}"
            )
    else:
        lines.append("_No significant peak detected._")
    return lines


def render_report(report: Report) -> str:
    v = report.video
    lines: list[str] = []

    # 1. Metadata
    lines.append(f"# {v.title}")
    lines.append("")
    lines.append(f"- **Channel:** {v.channel}")
    lines.append(f"- **URL:** {v.url}")
    lines.append(f"- **Duration:** {format_timestamp(v.duration)}")
    lines.append(f"- **Transcription:** {report.transcript_model}")
    lines.append(f"- **Generated at:** {report.generated_at:%Y-%m-%d %H:%M UTC}")
    lines.append("")

    # 1b. Degraded-run warnings (fallbacks triggered)
    if report.warnings:
        lines.append("## ⚠️ Degraded run")
        lines.append("")
        lines.append("Some stages used a fallback or were partial:")
        for w in report.warnings:
            lines.append(f"- {w}")
        lines.append("")

    # 2. Chat activity
    lines.extend(_chat_section(report))
    lines.append("")

    # 3. Macro topics
    lines.append(f"## Macro topics ({len(report.topics)})")
    lines.append("")
    if report.topics:
        for topic in report.topics:
            lines.append(_topic_line(report, topic))
    else:
        lines.append("_No topic identified._")
    lines.append("")

    return "\n".join(lines)
