"""Interface de linha de comando do PRIMOCUT (Typer)."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from . import doctor as doctor_mod
from .config import get_settings
from .logging_conf import setup_logging

app = typer.Typer(help="PRIMOCUT — Fase 0 (Spike CLI)", no_args_is_help=True)
console = Console()


@app.command()
def doctor() -> None:
    """Valida o ambiente: GPU/CUDA (WSL2), FFmpeg, yt-dlp e a chave da API."""
    setup_logging()
    report = doctor_mod.run_diagnostics(get_settings())

    table = Table(title="primocut doctor", show_lines=False)
    table.add_column("Check", style="bold")
    table.add_column("Status")
    table.add_column("Detalhe", overflow="fold")
    for c in report.checks:
        status = "[green]OK[/green]" if c.ok else "[red]FALHOU[/red]"
        table.add_row(c.name, status, c.detail)
    console.print(table)

    failures = [c for c in report.checks if not c.ok]
    if failures:
        console.print("\n[bold yellow]Como corrigir:[/bold yellow]")
        for c in failures:
            if c.fix:
                console.print(f"  • [bold]{c.name}[/bold]: {c.fix}")
        console.print(
            f"\n[red]{len(failures)} de {len(report.checks)} checks falharam.[/red]"
        )
        raise typer.Exit(code=1)

    console.print("\n[green]Ambiente pronto ✔[/green]")


@app.command()
def run(
    url: str = typer.Argument(..., help="URL da live do YouTube"),
    output: str = typer.Option("report.md", "-o", "--output", help="Arquivo Markdown de saída"),
    provider: str | None = typer.Option(
        None, "-p", "--provider", help="Provedor da análise: anthropic | openai | openrouter"
    ),
    model: str | None = typer.Option(
        None, "-m", "--model", help="Sobrescreve o modelo do provedor selecionado"
    ),
) -> None:
    """Executa o pipeline: áudio → chat → transcrição → análise → relatório."""
    setup_logging()

    from .download.audio import validate_youtube_url

    try:
        url = validate_youtube_url(url)
    except ValueError as exc:
        console.print(f"[red]URL inválida:[/red] {exc}")
        raise typer.Exit(code=2)

    settings = get_settings()
    if provider:
        settings.llm_provider = provider.lower()
    if model:
        if settings.llm_provider == "openai":
            settings.openai_model = model
        elif settings.llm_provider == "openrouter":
            settings.openrouter_model = model
        else:
            settings.anthropic_model = model

    from .analysis.llm import ToolCallError
    from .pipeline import PipelineError, run_pipeline  # import tardio: deps pesadas

    try:
        result = run_pipeline(url, output_path=output, settings=settings)
    except PipelineError as exc:
        console.print(f"[red]Etapa essencial falhou:[/red] {exc}")
        raise typer.Exit(code=1)
    except ToolCallError as exc:
        console.print(f"[red]Análise falhou:[/red] {exc}")
        console.print(
            "[yellow]Dica:[/yellow] rode "
            "[bold]primocut models --tools[/bold] para ver modelos do OpenRouter que "
            "suportam ferramentas."
        )
        raise typer.Exit(code=1)

    console.print(f"[green]Relatório gerado:[/green] {result}")


@app.command()
def models(
    free: bool = typer.Option(False, "--free", help="Só modelos gratuitos"),
    tools: bool = typer.Option(True, "--tools/--all", help="Só modelos com suporte a ferramentas"),
    limit: int = typer.Option(40, "--limit", help="Máximo de linhas a exibir"),
) -> None:
    """Lista modelos do OpenRouter (por padrão, os que suportam tool use)."""
    setup_logging()
    from .openrouter import fetch_models, filter_models

    try:
        raw = fetch_models()
    except Exception as exc:  # rede/API
        console.print(f"[red]Não consegui consultar o OpenRouter:[/red] {exc}")
        raise typer.Exit(code=1)

    rows = filter_models(raw, free_only=free, tools_only=tools)
    table = Table(title=f"OpenRouter — {len(rows)} modelo(s)"
                  + (" grátis" if free else "") + (" com ferramentas" if tools else ""))
    table.add_column("Modelo (slug)", style="bold")
    table.add_column("Grátis")
    table.add_column("Tools")
    for r in rows[:limit]:
        table.add_row(r["id"], "✅" if r["free"] else "—", "✅" if r["tools"] else "—")
    console.print(table)
    if len(rows) > limit:
        console.print(f"[dim]… e mais {len(rows) - limit}. Use --limit para ver mais.[/dim]")


if __name__ == "__main__":
    app()
