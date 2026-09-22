"""Command-line entry point for Flash Resume."""

from __future__ import annotations

from typing import Annotated

import typer

from flash_resume import __version__


app = typer.Typer(
    name="fs",
    help="Create ATS-targeted, layout-verified resumes.",
    no_args_is_help=True,
    add_completion=False,
)


def version_callback(value: bool) -> None:
    """Print the installed version when requested."""
    if value:
        typer.echo(f"Flash Resume {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            "--v",
            callback=version_callback,
            is_eager=True,
            help="Show the Flash Resume version and exit.",
        ),
    ] = False,
) -> None:
    """Flash Resume command-line application."""


if __name__ == "__main__":
    app()