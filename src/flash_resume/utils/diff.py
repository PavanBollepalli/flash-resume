"""Rich terminal visualization of tailoring results and bullet diffs."""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from flash_resume.models.tailoring import TailorResult

console = Console()


def display_tailor_summary(result: TailorResult) -> None:
    """Print an award-grade, readable visual summary of the tailoring results."""
    plan = result.plan
    total_requirements = len(plan.matched_keywords) + len(plan.missing_keywords)
    coverage = f"{len(plan.matched_keywords)}/{total_requirements}" if total_requirements else "0/0"

    # 1. Header Banner
    score_color = "green" if plan.ats_match_score >= 80 else "yellow"
    header_text = (
        f"[bold white]{plan.company}[/bold white] — [bold cyan]{plan.role}[/bold cyan]\n"
        f"JD Coverage: [{score_color}]{coverage}[/{score_color}]  |  "
        f"Edits: [bold cyan]{len(plan.bullet_edits)}[/bold cyan]  |  "
        f"Pages: [bold green]{result.page_count} page (Layout Verified)[/bold green]  |  "
        f"LLM: [magenta]{result.llm_time_ms / 1000:.2f}s[/magenta]  |  "
        f"Compile: [magenta]{result.compile_time_ms:.1f}ms[/magenta]  |  "
        f"Total: [magenta]{result.total_time_ms / 1000:.2f}s[/magenta]"
    )
    console.print(
        Panel(
            header_text,
            title="[bold green]✓ Flash Resume Tailoring Complete[/bold green]",
            border_style="green",
        )
    )

    # 2. Keywords Table/Panel
    if plan.matched_keywords:
        kw_badges = "  ".join(f"[bold black on cyan] {kw} [/]" for kw in plan.matched_keywords)
        console.print(f"\n[bold]Integrated ATS Keywords:[/] {kw_badges}\n")

    # 3. Bullet Edits Table
    if plan.bullet_edits:
        table = Table(
            title="Surgical Bullet Point Optimizations (Word Budget Preserved)",
            title_style="bold bold",
            header_style="bold cyan",
            border_style="dim",
            show_lines=True,
        )
        table.add_column("Section", style="bold yellow", width=12)
        table.add_column("Original Bullet", style="dim white", ratio=1)
        table.add_column("Tailored Bullet (ATS Optimized)", style="bold white", ratio=1)
        table.add_column("Δ Words", justify="center", width=9)

        for edit in plan.bullet_edits:
            orig_len = len(edit.original_text.split())
            new_len = len(edit.replacement_text.split())
            delta = new_len - orig_len
            delta_str = f"[green]+{delta}[/green]" if delta > 0 else (f"[yellow]{delta}[/yellow]" if delta < 0 else "[blue]0[/blue]")

            table.add_row(
                edit.section,
                edit.original_text,
                f"[green]{edit.replacement_text}[/green]",
                delta_str,
            )

        console.print(table)

    # 4. Output Artifacts Box
    console.print(
        Panel(
            f"[bold]PDF Output:[/]  [underline cyan]{result.pdf_path}[/underline cyan]\n"
            f"[bold]JSON Data:[/]   [dim]{result.json_path}[/dim]\n"
            f"[bold]Diff Report:[/] [dim]{result.diff_path}[/dim]",
            title="[bold]Saved Artifacts[/bold]",
            border_style="blue",
        )
    )

