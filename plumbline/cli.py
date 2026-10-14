"""`plumbline` command line. Subcommands are added as the engine modules land."""

import json

import typer

from . import __version__
from . import metadata as md

app = typer.Typer(add_completion=False, no_args_is_help=True,
                  help="Check Portaldot's documented calls against a live or stored runtime.")


@app.callback()
def main() -> None:
    """Keep `plumbline` a command group."""


@app.command()
def version() -> None:
    """Print the plumbline version."""
    typer.echo(__version__)


@app.command()
def runtime(profile: str = typer.Option(..., "--profile", "-p", help="mainnet-1002 | v3 | interim-sdk | stored:<file>"),
            url: str = typer.Option(None, help="override the profile endpoint"),
            as_json: bool = typer.Option(False, "--json", help="print the full record as JSON")) -> None:
    """Identify a chain's runtime: spec name/version, metadata version and hash (read-only)."""
    try:
        prof = md.get_profile(profile)
        if prof.stored_file:
            blob = open(prof.stored_file, "rb").read()
            decoded = md.decode_metadata(blob, prof.preset)
            record = {"profile": prof.name, "metadata_version": md.metadata_version(blob),
                      "metadata_sha256": md.sha256_hex(blob), "metadata_bytes": len(blob),
                      "pallets": len(md.pallet_names(decoded)), "calls": len(md.call_surface(decoded))}
        else:
            substrate = md.connect(prof, url)
            blob = md.fetch_metadata_blob(substrate)
            record = md.runtime_info(prof, substrate, blob, endpoint=url or prof.url).to_dict()
            substrate.close()
    except (md.ProfileError, md.UnknownChainError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2)
    if as_json:
        typer.echo(json.dumps(record, indent=2, sort_keys=True))
    elif "spec_version" in record:
        typer.echo(f"{record['profile']}: {record['spec_name']}/{record['spec_version']} "
                   f"metadata V{record['metadata_version']} sha256 {record['metadata_sha256']}")
    else:
        typer.echo(f"{record['profile']}: metadata V{record['metadata_version']} sha256 {record['metadata_sha256']} "
                   f"({record['pallets']} pallets, {record['calls']} calls)")


if __name__ == "__main__":
    app()
