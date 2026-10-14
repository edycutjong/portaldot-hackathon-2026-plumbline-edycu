"""`plumbline` command line. Subcommands are added as the engine modules land."""

import json
from pathlib import Path

import typer

from . import __version__
from . import docs as docs_mod
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
            record = {"profile": prof.name, "label": prof.label, "measured_as": prof.measured_as,
                      "metadata_version": md.metadata_version(blob),
                      "metadata_sha256": md.sha256_hex(blob), "metadata_bytes": len(blob),
                      "pallets": len(md.pallet_names(decoded)), "calls": len(md.call_surface(decoded))}
        else:
            substrate = md.connect(prof, url)
            block = md.pinned_head(substrate)
            blob = md.fetch_metadata_blob(substrate, block)
            record = md.runtime_info(prof, substrate, blob, block, endpoint=url or prof.url).to_dict()
            record["label"] = prof.label
            substrate.close()
    except (md.ProfileError, md.UnknownChainError, md.ChainUnreachableError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2)
    if as_json:
        typer.echo(json.dumps(record, indent=2, sort_keys=True))
    elif "spec_version" in record:
        typer.echo(f"{record['profile']}: {record['spec_name']}/{record['spec_version']} "
                   f"metadata V{record['metadata_version']} sha256 {record['metadata_sha256']} at {record['block_hash']}")
        typer.echo(f"  {record['label']}")
    else:
        typer.echo(f"{record['profile']}: metadata V{record['metadata_version']} sha256 {record['metadata_sha256']} "
                   f"({record['pallets']} pallets, {record['calls']} calls)")
        typer.echo(f"  {record['label']}")


@app.command()
def docs(cache: Path = typer.Option(Path(".cache/docs"), help="directory filled by scripts/fetch_docs.py"),
         profile: str = typer.Option(None, "--profile", "-p",
                                     help="also compare with a stored runtime, e.g. stored:fixtures/metadata_1002.scale")) -> None:
    """Parse the cached mainnet extrinsic docs; optionally compare them with a stored runtime's calls."""
    if not (cache / "index.html").exists():
        typer.echo(f"error: no cached docs in {cache}; run scripts/fetch_docs.py first", err=True)
        raise typer.Exit(2)
    results = docs_mod.parse_dir(cache)
    documented = docs_mod.documented_calls(results)
    for res in results:
        flags = []
        if res.unpaired:
            flags.append(f"unpaired {res.unpaired}")
        if res.unmatched_lines:
            flags.append(f"rejected interface lines {res.unmatched_lines}")
        if res.raw_interface_labels != res.interfaces:
            flags.append(f"{res.raw_interface_labels} interface labels but {res.interfaces} parsed")
        typer.echo(f"  {res.page:28} {res.interfaces:3}" + ("  " + "; ".join(flags) if flags else ""))
    duplicates = docs_mod.duplicate_calls(results)
    if duplicates:
        typer.echo(f"  duplicate calls: {sorted(duplicates)}")
    complete = sum(r.complete for r in results)
    typer.echo(f"parsed {sum(r.parsed for r in results)}/{len(results)} pages ({complete} complete), "
               f"{len(documented)} documented calls")
    if profile:
        try:
            prof = md.get_profile(profile)
        except md.ProfileError as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(2)
        if not prof.stored_file:
            typer.echo("error: --profile must be a stored:<file> profile", err=True)
            raise typer.Exit(2)
        decoded = md.decode_metadata(Path(prof.stored_file).read_bytes(), prof.preset)
        c = docs_mod.compare(documented, md.call_surface(decoded), md.pallet_names(decoded))["counts"]
        typer.echo(f"vs {prof.measured_as or prof.name} ({c['live_calls']} live calls): absent {c['absent']}, "
                   f"arg-mismatch {c['arg-mismatch']}, args-unknown {c['args-unknown']}, match {c['match']}, "
                   f"undocumented live {c['undocumented']}")
        typer.echo(f"  {prof.label}")
    if complete != len(results) or duplicates:
        raise typer.Exit(1)

if __name__ == "__main__":
    app()
