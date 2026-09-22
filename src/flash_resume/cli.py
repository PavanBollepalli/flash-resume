"""Command-line entry point for Flash Resume."""

import typer

from flash_resume import __version__


app = typer.Typer(
    name="fs",
    help="Create ATS-targeted, layout-verified resumes.",
    no_args_is_help=True,
    add_completion=False,
)


@app.callback()
def main(version: bool = False) -> None:
    """Flash Resume command-line application."""
    if version:
        typer.echo(f"Flash Resume {__version__}")
        raise typer.Exit()


if __name__ == "__main__":
    app()
