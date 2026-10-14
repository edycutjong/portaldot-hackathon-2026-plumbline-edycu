"""`plumbline` command line. Subcommands are added as the engine modules land."""

import json
from collections import Counter
from pathlib import Path

import typer

from . import __version__
from . import diff as diff_mod
from . import docs as docs_mod
from . import guide as guide_mod
from . import metadata as md
from . import verdicts

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
            try:
                block = md.pinned_head(substrate)
                blob = md.fetch_metadata_blob(substrate, block)
                record = md.runtime_info(prof, substrate, blob, block, endpoint=url or prof.url).to_dict()
            except Exception as exc:  # RPC failure after a good connect
                raise md.ChainUnreachableError(f"profile {prof.name}: reading runtime failed: {exc}") from exc
            finally:
                substrate.close()
            record["label"] = prof.label
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

@app.command("guide")
def guide_cmd(guide_dir: Path = typer.Option(Path.home() / ".cache" / "plumbline-sources" / "guide",
                                             help="a checkout made by scripts/fetch_guide.py")) -> None:
    """Parse the V3 testnet guide (README tables, demo call sites by AST, FAQ modules) and count them."""
    if not all((guide_dir / part).exists() for part in ("README.md", "FAQ.md", "demo")):
        typer.echo(f"error: no guide checkout (README.md, FAQ.md, demo/) in {guide_dir}; "
                   "run scripts/fetch_guide.py first", err=True)
        raise typer.Exit(2)
    head, dirty = guide_mod.clone_state(guide_dir)
    res = guide_mod.parse_guide(guide_dir)
    for source in (verdicts.V3_GUIDE, verdicts.V3_DEMO, verdicts.V3_FAQ):
        sites = [s for s in res.sites if s.source == source]
        hows = ", ".join(f"{how} {n}" for how, n in sorted(Counter(s.how for s in sites).items()))
        typer.echo(f"  {source:9} {len(sites):3} sites ({hows})")
    unresolved = [s.ref for s in res.sites if not s.resolved]
    if unresolved:
        typer.echo(f"  unresolved: {', '.join(unresolved)}")
    if res.unaccounted:
        typer.echo(f"  references no site covers: {', '.join(res.unaccounted)}")
    pinned = head == guide_mod.GUIDE_COMMIT and not dirty
    typer.echo(f"guide at {head or 'unknown commit'}" + ("" if pinned else
               f" (NOT the pinned clean commit {guide_mod.GUIDE_COMMIT[:7]}; dirty: {dirty})"))
    if not pinned or unresolved or res.unaccounted:
        raise typer.Exit(1)


STANDIN_BANNER = "STAND-IN, NOT V3: test input only; no number from this output is a Portaldot finding"


def _load_blob(profile: str) -> tuple[md.Profile, bytes, str]:
    """(profile, metadata blob, one-line origin) for a stored or live profile. Live reads are pinned to the
    finalized head and are read-only."""
    prof = md.get_profile(profile)
    if prof.stored_file:
        return prof, Path(prof.stored_file).read_bytes(), prof.label
    substrate = md.connect(prof)
    try:
        block = md.pinned_head(substrate)
        blob = md.fetch_metadata_blob(substrate, block)
    except Exception as exc:  # RPC failure after a good connect
        raise md.ChainUnreachableError(f"profile {prof.name}: reading metadata failed: {exc}") from exc
    finally:
        substrate.close()
    return prof, blob, f"{prof.label}; live at {block}"


def _is_standin(prof: md.Profile) -> bool:
    return "interim-sdk" in (prof.name, prof.measured_as, prof.asserted_as)


@app.command("diff")
def diff_cmd(profile_a: str = typer.Argument(..., help="from: mainnet-1002 | v3 | interim-sdk | stored:<file>"),
             profile_b: str = typer.Argument(..., help="to: same choices"),
             as_json: bool = typer.Option(False, "--json", help="print canonical JSON rows instead of text"),
             kind: str = typer.Option(None, help="only rows of this kind: " + " | ".join(diff_mod.DIFF_KINDS))) -> None:
    """Everything that changes between two runtimes: pallets, calls, args, constants, events."""
    if kind is not None and kind not in diff_mod.DIFF_KINDS:
        typer.echo(f"error: --kind must be one of {', '.join(diff_mod.DIFF_KINDS)}", err=True)
        raise typer.Exit(2)
    try:
        sides = [_load_blob(p) for p in (profile_a, profile_b)]
    except (md.ProfileError, md.UnknownChainError, md.ChainUnreachableError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2)
    forms = []
    for prof, blob, _ in sides:
        version = md.metadata_version(blob)
        forms.append(diff_mod.normalise(md.decode_metadata(blob, prof.preset), version))
    rows = diff_mod.diff(*forms)
    shown = [r for r in rows if kind is None or r.kind == kind]
    standin = any(_is_standin(prof) for prof, _, _ in sides)
    header = [{"profile": prof.name, "label": origin, "metadata_version": md.metadata_version(blob),
               "metadata_sha256": md.sha256_hex(blob), "pallets": len(nf.pallets), "calls": nf.call_count()}
              for (prof, blob, origin), nf in zip(sides, forms)]
    if as_json:
        out = {"from": header[0], "to": header[1], "summary": diff_mod.summary(rows),
               "rows": [r.to_dict() for r in shown]}
        if standin:
            out["warning"] = STANDIN_BANNER
        typer.echo(json.dumps(out, sort_keys=True, separators=(",", ":")))
        return
    if standin:
        typer.echo(STANDIN_BANNER)
    for side, h in zip(("from", "to"), header):
        typer.echo(f"{side:4} {h['profile']}: metadata V{h['metadata_version']} sha256 {h['metadata_sha256'][:12]}… "
                   f"({h['pallets']} pallets, {h['calls']} calls)")
        typer.echo(f"     {h['label']}")
    for r in shown:
        where = f"{r.pallet}.{r.item.split(':', 1)[1]}" if r.item else r.pallet
        what = r.item.split(":", 1)[0] if r.item else "pallet"
        line = f"  {r.kind:13} {what:6} {where}"
        if r.kind == diff_mod.RENAMED:
            line += f" -> {r.after['call']}"
        elif r.kind == diff_mod.ARG_CHANGED:
            line += f"  {_args_text(r.before['args'])} -> {_args_text(r.after['args'])}"
        if r.note:
            line += f"  [{r.note}]"
        typer.echo(line)
    typer.echo("summary: " + ", ".join(f"{k} {n}" for k, n in diff_mod.summary(rows).items())
               + f" ({len(rows)} rows" + (f", {len(shown)} shown" if kind else "") + ")")
    if standin:
        typer.echo(STANDIN_BANNER)


def _args_text(args: list) -> str:
    return "(" + ", ".join(f"{n}: {t}" for n, t in args) + ")"


if __name__ == "__main__":
    app()
