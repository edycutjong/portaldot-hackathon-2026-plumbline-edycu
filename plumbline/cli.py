"""`plumbline` command line. Subcommands are added as the engine modules land."""

import typer

from . import __version__

app = typer.Typer(add_completion=False, no_args_is_help=True,
                  help="Check Portaldot's documented calls against a live or stored runtime.")


@app.callback()
def main() -> None:
    """Keep `plumbline` a command group even while it has one subcommand."""


@app.command()
def version() -> None:
    """Print the plumbline version."""
    typer.echo(__version__)


if __name__ == "__main__":
    app()
