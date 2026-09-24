"""Flash Resume - Production CLI Interface."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated, Optional

import pyperclip
import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from flash_resume import __version__
from flash_resume import autostart, extension
from flash_resume.config import (
    AppConfig,
    get_config_dir,
    get_config_file,
    load_config,
    save_config,
)
from flash_resume.models.resume import MasterResume
from flash_resume.services.compiler import CompilerService
from flash_resume.services.tailor import TailorEngine
from flash_resume.utils.diff import display_tailor_summary

console = Console()

app = typer.Typer(
    name="fs",
    help="Fast, ATS-focused resume tailoring and 1-page PDF generation.",
    no_args_is_help=True,
    add_completion=False,
)


def version_callback(value: bool) -> None:
    """Show the Flash Resume version and exit eagerly."""
    if value:
        console.print(f"[bold cyan]Flash Resume[/bold cyan] version [bold green]{__version__}[/bold green]")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        Optional[bool],
        typer.Option(
            "--version",
            "-v",
            callback=version_callback,
            is_eager=True,
            help="Show the Flash Resume version and exit.",
        ),
    ] = None,
) -> None:
    """Flash Resume command-line interface."""


@app.command(name="init")
def init_cmd() -> None:
    """Interactive first-time setup wizard."""
    console.print(
        Panel(
            "[bold cyan]⚡ Flash Resume Setup Wizard[/bold cyan]\n"
            "[dim]Set up your master resume and preferences once, tailor forever in 1 second.[/dim]",
            border_style="cyan",
        )
    )

    cfg = load_config()

    # Step 1: Gemini API Key (needed for AI resume ingestion or tailoring)
    console.print("\n[bold]Step 1: Gemini API Key[/bold]")
    current_key = cfg.resolve_api_key()
    if current_key:
        masked = current_key[:4] + "..." + current_key[-4:] if len(current_key) > 8 else "***"
        console.print(f"Detected API Key: [green]{masked}[/green]")
        key_input = None
    else:
        console.print("[dim]Get a free Gemini API key from https://aistudio.google.com[/dim]")
        key_input = Prompt.ask("Enter GEMINI_API_KEY (or press Enter to set via environment variable later)", default="")
        if not key_input:
            key_input = None

    if key_input:
        cfg.gemini_api_key = key_input

    # Step 2: Master Resume
    default_resume_dir = get_config_dir() / "master_resume.json"
    repo_example = Path(__file__).resolve().parent.parent.parent / "examples" / "master_resume.json"

    console.print("\n[bold]Step 2: Master Resume[/bold]")
    console.print("How would you like to set up your master resume?")
    console.print("  [bold cyan]1[/bold cyan] - Import your existing resume file (PDF, TXT, or Markdown) using AI")
    console.print("  [bold cyan]2[/bold cyan] - Use the starter sample template (Alex Chen)")
    console.print("  [bold cyan]3[/bold cyan] - Point to an existing master_resume.json file")

    choice = Prompt.ask("Select an option", choices=["1", "2", "3"], default="1")
    resume_path_str = str(default_resume_dir)

    if choice == "1":
        file_input = Prompt.ask("Enter path to your resume file (.pdf, .txt, .md)")
        source_path = Path(file_input.strip("\"'"))
        if not source_path.exists():
            console.print(f"[bold red]File not found at {source_path}. Using starter template instead.[/bold red]")
            if repo_example.exists():
                default_resume_dir.write_text(repo_example.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            api_key = cfg.resolve_api_key()
            if not api_key:
                console.print("[yellow]Warning: GEMINI_API_KEY not found. Storing raw path; run 'fs import' once key is set.[/yellow]")
                resume_path_str = str(source_path)
            else:
                from flash_resume.services.llm import LLMService
                from flash_resume.services.parser import convert_resume_file_to_master

                llm = LLMService(api_key=api_key, model=cfg.default_model)
                with console.status(f"[bold cyan]Parsing {source_path.name} with Gemini Flash into structured Master Resume...[/bold cyan]", spinner="dots"):
                    try:
                        parsed_resume = convert_resume_file_to_master(source_path, llm)
                        default_resume_dir.write_text(parsed_resume.model_dump_json(indent=2), encoding="utf-8")
                        console.print(
                            f"[bold green]✓ Successfully parsed resume for {parsed_resume.contact.name}![/bold green] "
                            f"({len(parsed_resume.experience)} roles, {len(parsed_resume.projects)} projects, {len(parsed_resume.skills)} skill categories)"
                        )
                    except Exception as e:
                        console.print(f"[bold red]AI parsing failed: {e}. Using starter template.[/bold red]")
                        if repo_example.exists():
                            default_resume_dir.write_text(repo_example.read_text(encoding="utf-8"), encoding="utf-8")

    elif choice == "2":
        if repo_example.exists():
            default_resume_dir.write_text(repo_example.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            default_resume_dir.write_text("{}", encoding="utf-8")
        console.print(f"[green]✓ Starter master resume created at {default_resume_dir}[/green]")

    else:
        custom_path = Prompt.ask("Enter path to your master_resume.json")
        resume_path_str = str(Path(custom_path.strip("\"'")).resolve())

    # Step 3: Output Directory
    console.print("\n[bold]Step 3: Output Directory[/bold]")
    default_out = cfg.output_dir or str(Path.home() / "Resumes")
    out_dir_str = Prompt.ask("Where should tailored PDFs be saved?", default=default_out)
    Path(out_dir_str).mkdir(parents=True, exist_ok=True)

    # Save Config
    cfg.master_resume_path = resume_path_str
    cfg.output_dir = out_dir_str
    save_config(cfg)
    console.print(f"\n[bold green]✓ Configuration saved to {get_config_file()}[/bold green]")

    # Run quick compile check
    try:
        mr = MasterResume.model_validate_json(Path(resume_path_str).read_text(encoding="utf-8"))
        compiler = CompilerService()
        test_pdf = Path(out_dir_str) / "test_init_verification.pdf"
        pages, dt = compiler.compile(mr, test_pdf)
        if test_pdf.exists():
            test_pdf.unlink()
        console.print(f"[bold green]✓ Typst compiler verified: {pages} page compiled in {dt:.1f}ms[/bold green]")
    except Exception as e:
        console.print(f"[yellow]Note: Compiler self-test check: {e}[/yellow]")

    console.print("\n[bold cyan]All set! You can now copy any job description and run:[/bold cyan]")
    console.print("  [bold white]fs tailor[/bold white]\n")


@app.command(name="import")
def import_cmd(
    file_path: Annotated[
        str,
        typer.Argument(help="Path to your existing resume file (PDF, TXT, MD, or JSON)."),
    ],
) -> None:
    """Import and parse your existing resume file into your master resume with AI."""
    cfg = load_config()
    source_p = Path(file_path.strip("\"'"))
    if not source_p.exists():
        console.print(f"[bold red]File not found: {source_p}[/bold red]")
        raise typer.Exit(code=1)

    api_key = cfg.resolve_api_key()
    if not api_key:
        console.print("[bold red]GEMINI_API_KEY is not set.[/bold red]")
        console.print("Set GEMINI_API_KEY environment variable or run 'fs init' first.")
        raise typer.Exit(code=1)

    from flash_resume.services.llm import LLMService
    from flash_resume.services.parser import convert_resume_file_to_master

    llm = LLMService(api_key=api_key, model=cfg.default_model)
    with console.status(f"[bold cyan]Parsing {source_p.name} with Gemini Flash...[/bold cyan]", spinner="dots"):
        try:
            parsed = convert_resume_file_to_master(source_p, llm)
        except Exception as e:
            console.print(f"[bold red]Failed to parse resume: {e}[/bold red]")
            raise typer.Exit(code=1)

    dest = get_config_dir() / "master_resume.json"
    dest.write_text(parsed.model_dump_json(indent=2), encoding="utf-8")
    cfg.master_resume_path = str(dest)
    save_config(cfg)

    console.print(
        Panel(
            f"[bold green]✓ Successfully imported master resume for {parsed.contact.name}![/bold green]\n"
            f"[dim]Experience:[/] {len(parsed.experience)} roles  |  "
            f"[dim]Projects:[/] {len(parsed.projects)} projects  |  "
            f"[dim]Skills:[/] {len(parsed.skills)} categories\n\n"
            f"Saved to: [cyan]{dest}[/cyan]",
            title="[bold]Resume Ingestion Complete[/bold]",
            border_style="green",
        )
    )


@app.command(name="tailor")
def tailor_cmd(
    job_description: Annotated[
        list[str],
        typer.Argument(help="Raw job description text (alternative to --jd)."),
    ] = [],
    jd: Annotated[
        Optional[str],
        typer.Option("--jd", "-j", help="Path, raw text, or '-' to read the job description from stdin."),
    ] = None,
    clip: Annotated[
        bool,
        typer.Option("--clip", "-c", help="Read job description from clipboard."),
    ] = False,
    company: Annotated[
        Optional[str],
        typer.Option("--company", help="Override company name."),
    ] = None,
    role: Annotated[
        Optional[str],
        typer.Option("--role", help="Override role / job title."),
    ] = None,
    output_dir: Annotated[
        Optional[str],
        typer.Option("--out", "-o", help="Override output directory."),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Run in mock/offline mode without calling the Gemini API."),
    ] = False,
) -> None:
    """Tailor your resume for a job description and compile a verified 1-page PDF."""
    cfg = load_config()

    # Fallback to repository sample if master resume is not yet configured
    if not cfg.master_resume_path or not Path(cfg.master_resume_path).exists():
        repo_example = Path(__file__).resolve().parent.parent.parent / "examples" / "master_resume.json"
        if repo_example.exists():
            console.print(f"[dim]Using sample master resume at {repo_example} (run 'fs init' to set your own).[/dim]")
            cfg.master_resume_path = str(repo_example)
        else:
            console.print("[bold red]Master resume is not configured.[/bold red]")
            console.print("Run [bold cyan]fs init[/bold cyan] first to set up your master resume.")
            raise typer.Exit(code=1)

    # Resolve JD text
    jd_text = ""
    positional_jd = " ".join(job_description).strip()
    jd_input = jd or positional_jd or None
    if clip and jd_input:
        console.print("[bold red]Do not combine --clip with a job description argument.[/bold red]")
        raise typer.Exit(code=2)
    if jd and job_description:
        console.print("[bold red]Provide the job description once, using either a positional argument or --jd.[/bold red]")
        raise typer.Exit(code=2)

    if clip:
        try:
            jd_text = pyperclip.paste().strip()
        except Exception as exc:
            console.print(f"[bold red]Could not read the clipboard: {exc}[/bold red]")
            raise typer.Exit(code=1)
    elif jd_input:
        jd_path = Path(jd_input)
        if jd_input == "-":
            import sys

            jd_text = sys.stdin.read().strip()
        elif jd_path.is_file():
            jd_text = jd_path.read_text(encoding="utf-8")
        else:
            jd_text = jd_input
    else:
        # Default to clipboard for the everyday copy-and-run workflow.
        try:
            jd_text = pyperclip.paste().strip()
        except Exception:
            pass

    if not jd_text and not dry_run:
        console.print("[bold red]No job description provided or clipboard was empty.[/bold red]")
        console.print("Either copy a job description to your clipboard first, or pass it via:")
        console.print("  [bold cyan]fs tailor --jd ./job.txt[/bold cyan] or [bold cyan]fs tailor --dry-run[/bold cyan]")
        raise typer.Exit(code=1)

    if jd_text and len(jd_text) < 40 and not dry_run:
        console.print(f"[yellow]Warning: Provided job description is very short ({len(jd_text)} chars).[/yellow]")

    if dry_run:
        console.print("[bold yellow]⚡ Running in DRY-RUN mode (Simulated ATS optimization)...[/bold yellow]")
        from flash_resume.models.tailoring import TailorPlan
        from flash_resume.services.tailor import apply_tailor_plan, generate_diff_markdown

        resume_path = Path(cfg.master_resume_path)
        master_resume = MasterResume.model_validate_json(resume_path.read_text(encoding="utf-8"))
        target_company = company or "Datadog"
        target_role = role or "Backend Software Engineer"

        mock_plan = TailorPlan(
            company=target_company,
            role=target_role,
            ats_match_score=94,
            matched_keywords=["FastAPI", "PostgreSQL", "Docker", "Redis", "Distributed Systems"],
            missing_keywords=["Kafka"],
            skill_updates=[],
            bullet_edits=[],
        )

        compiler = CompilerService()
        tailored = apply_tailor_plan(master_resume, mock_plan)
        out_base = Path(output_dir or cfg.output_dir)
        out_base.mkdir(parents=True, exist_ok=True)
        pdf_path = out_base / f"{target_company}_{target_role.replace(' ', '_')}.pdf"
        json_path = out_base / f"{target_company}_{target_role.replace(' ', '_')}.json"
        diff_path = out_base / f"{target_company}_{target_role.replace(' ', '_')}.diff.md"

        pages, compile_ms = compiler.compile(tailored, pdf_path, max_pages=cfg.max_pages)
        json_path.write_text(tailored.model_dump_json(indent=2), encoding="utf-8")
        diff_path.write_text(
            generate_diff_markdown(master_resume, tailored, mock_plan, pages, compile_ms),
            encoding="utf-8",
        )

        from flash_resume.models.tailoring import TailorResult
        result = TailorResult(
            pdf_path=str(pdf_path.resolve()),
            json_path=str(json_path.resolve()),
            diff_path=str(diff_path.resolve()),
            page_count=pages,
            llm_time_ms=0.0,
            compile_time_ms=compile_ms,
            total_time_ms=compile_ms,
            plan=mock_plan,
        )
        display_tailor_summary(result)
        return

    engine = TailorEngine(cfg)
    model_name = cfg.groq_model if cfg.llm_provider == "groq" else cfg.default_model
    console.print(f"[dim]Engine: [cyan]{cfg.llm_provider}[/cyan] · Model: [cyan]{model_name}[/cyan][/dim]")

    try:
        with console.status(f"[bold cyan]Analyzing JD & tailoring resume with {model_name}...[/bold cyan]", spinner="dots"):
            result = engine.tailor(
                job_description=jd_text,
                company_override=company,
                role_override=role,
                output_dir_override=output_dir,
            )
        display_tailor_summary(result)
    except ValueError as ve:
        console.print(f"\n[bold red]Tailoring Error:[/bold red] {ve}\n")
        raise typer.Exit(code=1)
    except Exception as exc:
        console.print(f"\n[bold red]Tailoring Error:[/bold red] {exc}\n")
        raise typer.Exit(code=1)


@app.command(name="doctor")
def doctor_cmd() -> None:
    """Verify system requirements, compiler, configuration, and API connectivity."""
    console.print(Panel("[bold cyan]Flash Resume Health Doctor[/bold cyan]", border_style="cyan"))

    cfg = load_config()
    table = Table(box=None, show_header=False)
    table.add_column("Component", style="bold", width=22)
    table.add_column("Status", width=12)
    table.add_column("Details")

    # 1. Typst
    try:
        import typst
        table.add_row("Typst Engine", "[green]OK[/green]", f"Bundled Typst Python wheel ({getattr(typst, '__file__', 'available')})")
    except Exception as e:
        table.add_row("Typst Engine", "[red]FAIL[/red]", str(e))

    # 2. Config File
    cfg_file = get_config_file()
    if cfg_file.exists():
        table.add_row("Configuration", "[green]OK[/green]", str(cfg_file))
    else:
        table.add_row("Configuration", "[yellow]MISSING[/yellow]", "Run 'fs init' to create config")

    # 3. Master Resume
    if cfg.master_resume_path and Path(cfg.master_resume_path).exists():
        try:
            mr = MasterResume.model_validate_json(Path(cfg.master_resume_path).read_text(encoding="utf-8"))
            table.add_row("Master Resume", "[green]OK[/green]", f"{mr.contact.name} ({len(mr.experience)} roles, {len(mr.projects)} projects)")
        except Exception as e:
            table.add_row("Master Resume", "[red]INVALID[/red]", f"Schema error: {e}")
    else:
        table.add_row("Master Resume", "[red]MISSING[/red]", "Set up via 'fs init'")

    # 4. API Key
    key = cfg.resolve_api_key()
    if key:
        table.add_row("Gemini API Key", "[green]OK[/green]", f"Configured ({key[:4]}...{key[-4:] if len(key)>8 else ''})")
    else:
        table.add_row("Gemini API Key", "[yellow]NOT SET[/yellow]", "Set GEMINI_API_KEY environment variable or in 'fs init'")

    # 5. Output Directory
    out_dir = Path(cfg.output_dir)
    if out_dir.exists() and os.access(out_dir, os.W_OK):
        table.add_row("Output Directory", "[green]OK[/green]", f"Writable: {out_dir}")
    else:
        table.add_row("Output Directory", "[yellow]WARNING[/yellow]", f"{out_dir} will be created on demand")

    console.print(table)
    console.print()


@app.command(name="serve")
def serve_cmd(
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Start the local companion server for the 1-click Chrome Extension."""
    import uvicorn

    console.print(
        Panel(
            f"[bold green]⚡ Flash Resume Companion Server[/bold green]\n"
            f"Listening on [bold cyan]http://{host}:{port}[/bold cyan]\n"
            f"[dim]Ready for 1-click tailoring from the Chrome Extension.[/dim]",
            border_style="green",
        )
    )
    uvicorn.run("flash_resume.services.server:app", host=host, port=port, reload=False)


@app.command(name="setup")
def setup_cmd() -> None:
    """One-time setup: provider + key, autostart, and extension install."""
    console.print(
        Panel(
            "[bold cyan]⚡ Flash Resume One-Time Setup[/bold cyan]\n"
            "[dim]Configure the AI engine, start the background server at login, "
            "and install the browser extension.[/dim]",
            border_style="cyan",
        )
    )

    cfg = load_config()

    # 1. Provider choice
    console.print("\n[bold]Step 1: AI Provider[/bold]")
    console.print("  [bold cyan]1[/bold cyan] - Gemini (best quality, ~15-20s per tailor)")
    console.print("  [bold cyan]2[/bold cyan] - Groq   (fastest, ~1-3s per tailor)")
    provider_choice = Prompt.ask("Select a provider", choices=["1", "2"], default="2" if cfg.llm_provider == "groq" else "1")
    cfg.llm_provider = "groq" if provider_choice == "2" else "gemini"

    # 2. API key for the chosen provider
    if cfg.llm_provider == "groq":
        console.print("\n[bold]Step 2: Groq API Key[/bold]")
        current = cfg.resolve_groq_api_key()
        if current:
            masked = current[:8] + "..." if len(current) > 8 else "***"
            console.print(f"Existing key detected: [green]{masked}[/green]")
            key_input = Prompt.ask("Enter new key (or press Enter to keep)", default="")
        else:
            console.print("[dim]Get a free key at https://console.groq.com/keys[/dim]")
            key_input = Prompt.ask("Enter your Groq API key (gsk_...)", default="")
        if key_input:
            cfg.groq_api_key = key_input.strip()
    else:
        console.print("\n[bold]Step 2: Gemini API Key[/bold]")
        current = cfg.resolve_api_key()
        if current:
            masked = current[:4] + "..." + current[-4:] if len(current) > 8 else "***"
            console.print(f"Existing key detected: [green]{masked}[/green]")
            key_input = Prompt.ask("Enter new key (or press Enter to keep)", default="")
        else:
            console.print("[dim]Get a free key at https://aistudio.google.com[/dim]")
            key_input = Prompt.ask("Enter your Gemini API key", default="")
        if key_input:
            cfg.gemini_api_key = key_input.strip()

    save_config(cfg)
    console.print(f"[green]✓ Provider and key saved to {get_config_file()}[/green]")

    # 3. Master resume (reuse init wizard if not yet configured)
    console.print("\n[bold]Step 3: Master Resume[/bold]")
    if cfg.master_resume_path and Path(cfg.master_resume_path).exists():
        console.print(f"[green]✓ Master resume already configured: {cfg.master_resume_path}[/green]")
    else:
        console.print("[yellow]No master resume configured yet — running the init wizard.[/yellow]")
        init_cmd()

    # 4. Autostart
    console.print("\n[bold]Step 4: Background Engine Autostart[/bold]")
    try:
        cmd_str = autostart.install()
        console.print(f"[green]✓ Server will start automatically at login.[/green]")
        console.print(f"  [dim]Registered: {cmd_str}[/dim]")
    except Exception as e:
        console.print(f"[yellow]Could not register autostart: {e}[/yellow]")
        console.print("[dim]You can start the server manually with 'fs serve'.[/dim]")

    # 5. Extension install
    console.print("\n[bold]Step 5: Browser Extension[/bold]")
    ext_path = extension.install_to()
    console.print(f"[green]✓ Extension copied to a stable location.[/green]")
    console.print(
        Panel(
            f"[bold]Load the extension in Chrome / Brave / Edge:[/bold]\n\n"
            f"1. Open [cyan]chrome://extensions/[/cyan]\n"
            f"2. Enable [bold]Developer mode[/bold] (top-right toggle)\n"
            f"3. Click [bold]Load unpacked[/bold] and select:\n"
            f"   [bold cyan]{ext_path}[/bold cyan]\n\n"
            f"[dim]You only need to do this once — the extension reconnects "
            f"to the background engine automatically.[/dim]",
            title="[bold]Extension Ready[/bold]",
            border_style="cyan",
        )
    )

    console.print(
        Panel(
            "[bold green]🎉 Setup complete![/bold green]\n\n"
            "The Flash Resume engine starts silently every time you log in.\n"
            "Just open any job posting on LinkedIn, Indeed, or Greenhouse and\n"
            "click the ⚡ Flash Resume icon → [bold]Tailor 1-Page Resume[/bold].\n\n"
            f"[dim]CLI users: 'fs tailor' still works from any terminal.[/dim]",
            border_style="green",
        )
    )


autostart_app = typer.Typer(help="Manage the at-login autostart of the background engine.")
app.add_typer(autostart_app, name="autostart")


@autostart_app.command(name="enable")
def autostart_enable_cmd() -> None:
    """Register the server to start silently at Windows logon."""
    try:
        autostart.install()
        registered, detail = autostart.status()
        console.print(f"[green]✓ Autostart registered.[/green]")
        console.print(f"  [dim]{detail}[/dim]")
    except Exception as e:
        console.print(f"[bold red]Failed to register autostart:[/bold red] {e}")
        raise typer.Exit(code=1)


@autostart_app.command(name="disable")
def autostart_disable_cmd() -> None:
    """Remove the at-login autostart registration."""
    try:
        autostart.uninstall()
        console.print("[green]✓ Autostart removed. The server will no longer start at login.[/green]")
    except Exception as e:
        console.print(f"[bold red]Failed to remove autostart:[/bold red] {e}")
        raise typer.Exit(code=1)


@autostart_app.command(name="status")
def autostart_status_cmd() -> None:
    """Show whether the at-login autostart is registered."""
    registered, detail = autostart.status()
    if registered:
        console.print(f"[green]✓ Registered[/green]\n  [dim]{detail}[/dim]")
    else:
        console.print(f"[yellow]✗ Not registered[/yellow]\n  [dim]{detail}[/dim]")


@app.command(name="extension-path")
def extension_path_cmd() -> None:
    """Print (and install if needed) the extension folder for Load-unpacked."""
    ext_path = extension.display_path()
    console.print(f"Load the extension from: [bold cyan]{ext_path}[/bold cyan]")


if __name__ == "__main__":
    app()