"""`plumbline` command line. Subcommands are added as the engine modules land."""

import json
from collections import Counter
from pathlib import Path

import typer

from . import __version__
from . import canary as canary_mod
from . import check as check_mod
from . import diff as diff_mod
from . import docs as docs_mod
from . import guide as guide_mod
from . import metadata as md
from . import plumb as plumb_mod
from . import produce
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


@app.command("plumb")
def plumb_cmd(profile: str = typer.Option(..., "--profile", "-p", help="stored:<blob> recorded by scripts/record_metadata.py"),
              runtime_record: Path = typer.Option(..., help="the blob's evidence record, e.g. evidence/metadata_1002.json"),
              out: Path = typer.Option(..., help="where to write the canonical ledger JSON (+ .blake2-256)"),
              guide_table: Path = typer.Option(Path("evidence/guide_calls.json")),
              guide_dir: Path = typer.Option(None, help="parse a clean guide clone instead of the committed table"),
              docs_table: Path = typer.Option(Path("evidence/docs_1002.json")),
              docs_cache: Path = typer.Option(None, help="parse fetched docs pages instead of the committed table"),
              db: Path = typer.Option(Path(".cache/ledger.db"))) -> None:
    """Build the drift ledger: one verdict per documented call, module or runtime API on one runtime."""
    code = produce.ledger(produce.Args(profile, runtime_record, out, guide_table, guide_dir, docs_table, docs_cache, db),
                          say=typer.echo, warn=lambda m: typer.echo(m, err=True))
    if code:
        raise typer.Exit(code)


REPO = Path(__file__).resolve().parents[1]
# offline targets: the stored metadata blob and its evidence record (paths relative to the repository)
STORED_TARGETS = {
    "mainnet-1002": ("fixtures/metadata_1002.scale", "evidence/metadata_1002.json"),
    "interim-sdk": ("fixtures/metadata_standin_sdk.scale", "evidence/metadata_standin_sdk.json"),
    "v3": ("fixtures/metadata_v3.scale", "evidence/metadata_v3.json"),
}


def _check_runtime(target: str, offline: bool) -> tuple[plumb_mod.Runtime, plumb_mod.Surface, str]:
    if target.startswith("stored:"):
        raise md.ProfileError("--target takes a chain profile (mainnet-1002 | v3 | interim-sdk), not stored:<file>")
    prof = md.get_profile(target)
    if offline:
        if target not in STORED_TARGETS:
            raise md.ProfileError(f"no stored metadata for {target} in this repository yet; "
                                  f"run without --offline to read it live")
        blob_rel, record_rel = STORED_TARGETS[target]
        blob = (REPO / blob_rel).read_bytes()
        if md.recorded_origin(blob) != target:
            raise md.ProfileError(f"{blob_rel} is not a recorded {target} blob")
        record = json.loads((REPO / record_rel).read_text())
        rt = plumb_mod.runtime_from_record(record, blob, prof.label, report_state=verdicts.CACHED)
        return rt, plumb_mod.surface_from_blob(blob, prof.preset), f"offline, stored {blob_rel}"
    substrate = md.connect(prof)
    try:
        block = md.pinned_head(substrate)
        blob = md.fetch_metadata_blob(substrate, block)
        info = md.runtime_info(prof, substrate, blob, block)
    except Exception as exc:  # RPC failure after a good connect
        raise md.ChainUnreachableError(f"profile {prof.name}: reading runtime failed: {exc}") from exc
    finally:
        substrate.close()
    rt = plumb_mod.runtime_from_record(info.to_dict(), blob, prof.label)
    return rt, plumb_mod.surface_from_blob(blob, prof.preset), f"live {prof.url} at {block}"


@app.command("check")
def check_cmd(path: Path = typer.Argument(..., help="the dApp directory to scan"),
              target: str = typer.Option(..., "--target", "-t", help="mainnet-1002 | v3 | interim-sdk"),
              offline: bool = typer.Option(False, "--offline", help="use the stored metadata blob, no network"),
              as_json: bool = typer.Option(False, "--json", help="print findings as canonical JSON")) -> None:
    """What in a dApp's code and contract artifacts will not work on the target runtime. Exit 1 on
    EXPECTED-REJECT, TYPE-TRAP, DRIFTED, ABSENT or ARG_DRIFT; 2 on a usage or chain error."""
    if not path.is_dir():
        typer.echo(f"error: {path} is not a directory", err=True)
        raise typer.Exit(2)
    stats: dict = {}
    try:
        rt, surface, mode = _check_runtime(target, offline)
        findings = check_mod.check(path, rt, surface, stats=stats)
    except (md.ProfileError, md.UnknownChainError, md.ChainUnreachableError, plumb_mod.LedgerError, OSError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2)
    standin = target == "interim-sdk"
    if as_json:
        out = {"target": rt.profile, "spec_name": rt.spec_name, "spec_version": rt.spec_version,
               "metadata_hash": rt.metadata_hash, "mode": mode, "label": rt.label,
               "files_scanned": stats["files"], "findings": [f.to_dict() for f in findings],
               "exit": check_mod.exit_code(findings)}
        if standin:
            out["warning"] = STANDIN_BANNER
        typer.echo(json.dumps(out, sort_keys=True, separators=(",", ":")))
    else:
        text = check_mod.render(findings, rt, mode, files=stats["files"], skipped=stats["skipped"])
        typer.echo((f"{STANDIN_BANNER}\n{text}{STANDIN_BANNER}" if standin else text).rstrip("\n"))
    raise typer.Exit(check_mod.exit_code(findings))


CANARY_RECORDS = {"v3": Path("evidence/canary_v3.json"), "interim-sdk": Path("evidence/canary_interim.json")}


@app.command("canary")
def canary_cmd(profile: str = typer.Option(..., "--profile", "-p", help="v3 (funded seed file) | interim-sdk (dev keys, local chain only)"),
               out: Path = typer.Option(None, help="the receipts record (default evidence/canary_<profile>.json)"),
               proof: Path = typer.Option(Path("docs/PROOF.md"), help="the proof table generated from the records"),
               url: str = typer.Option(None, help="override the profile endpoint (loopback only for dev keys)"),
               replace: bool = typer.Option(False, "--replace", help="overwrite an existing receipts record")) -> None:
    """Send the canary calls, keep each receipt, read each back from its block.
    v3: System.remark_with_event, Balances.transfer_keep_alive (to itself), Utility.batch_all.
    interim-sdk: Balances.transfer_keep_alive, System.remark_with_event, Revive.map_account (labelled not V3)."""
    out = out or CANARY_RECORDS.get(profile, Path(f"evidence/canary_{profile}.json"))
    existing = [p for p in (out, proof) if p.exists()]
    if existing and not replace:  # the proof table is guarded too: --out elsewhere must not overwrite PROOF.md
        typer.echo(f"error: {', '.join(map(str, existing))} exists; pass --replace to overwrite with a new run",
                   err=True)
        raise typer.Exit(2)
    try:
        record = canary_mod.run(profile, url)
    except canary_mod.CanaryError as exc:
        if exc.partial and exc.partial["receipts"]:  # receipts already on chain are kept, never dropped
            partial = out.with_name(f"{out.stem}.{exc.partial['run_id']}.partial.json")
            partial.write_text(json.dumps(exc.partial | {"incomplete": str(exc)}, indent=2, sort_keys=True) + "\n")
            typer.echo(f"error: {exc} ({len(exc.partial['receipts'])} receipts kept in {partial})", err=True)
            raise typer.Exit(1)
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2)
    except (md.ProfileError, md.UnknownChainError, md.ChainUnreachableError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    proof.parent.mkdir(parents=True, exist_ok=True)
    if out == CANARY_RECORDS.get(profile):  # the canonical records feed one proof table: V3 section, then interim
        records = {p: json.loads(path.read_text()) if path.exists() else None for p, path in CANARY_RECORDS.items()}
        proof.write_text(canary_mod.proof_document(records["v3"], records["interim-sdk"]))
    else:
        proof.write_text(canary_mod.proof_markdown(record))
    for r in record["receipts"]:
        outcome = ("success" if r["success"] else "outcome not read back" if r["success"] is None
                   else f"failed {r['dispatch_error']}")
        typer.echo(f"  {r['pallet']}.{r['call']:22} {str(r['extrinsic_id'] or '-'):>9} {r['tx_hash']}  {outcome}  "
                   f"read-back {'ok' if r['readback_ok'] else 'FAILED'}")
    typer.echo(f"{record['label']}: {len(record['receipts'])} receipts -> {out}, {proof}")
    if not all(r["readback_ok"] for r in record["receipts"]):  # a receipt is kept either way
        raise typer.Exit(1)


@app.command("readback")
def readback_cmd(record_file: Path = typer.Argument(..., help="a receipts record written by plumbline canary"),
                 url: str = typer.Option(None, help="override the record's endpoint")) -> None:
    """Re-read every receipt of a canary record from the chain: block by hash, extrinsic by index, its hash,
    decoded call and dispatch outcome. Exit 1 on any mismatch."""
    try:
        record = json.loads(record_file.read_text())
        rows = canary_mod.verify(record, url)
    except (OSError, ValueError, KeyError, TypeError, md.UnknownChainError, md.ChainUnreachableError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2)
    for row in rows:
        typer.echo(f"  {str(row['extrinsic_id'] or '-'):>9} {row['call']:32} decoded {row['decoded']}  block {row['block_number']}  "
                   f"success {row['success_on_chain']}  {'MATCH' if row['match'] else 'MISMATCH'}")
    matched = sum(r["match"] for r in rows)
    typer.echo(f"{record.get('label', '')}: {matched}/{len(rows)} receipts match the chain")
    if matched != len(rows):
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
