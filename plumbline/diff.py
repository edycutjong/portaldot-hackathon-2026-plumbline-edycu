"""Runtime diff: what changes for an app moving from one runtime to another.

`normalise()` maps decoded metadata of either generation to one form, so a V13 runtime (mainnet spec 1002)
and a V14+ runtime (modern, self-describing) can be compared item by item:

  pallet -> calls      {call: ((arg name, type name), ...)}
            events     {event: (type name, ...)}            V13 events carry no field names, so only types
            constants  {constant: (type name, value hex)}   value = the SCALE bytes as stored in metadata
            errors     (names; listed, not diffed — the diff kinds have no error kind)

Type names: V13 metadata spells each type as a Rust path string (`T::Balance`, `Compact<BalanceOf<T>>`);
V14+ gives each field a `typeName` and a type id in the portable registry. Both are reduced the same
way: a qualified path `<X as Trait>::Assoc` becomes `X::Assoc`, then `T::` / `I::` prefixes are dropped
and all whitespace is removed; a V14 field whose registry type is compact is wrapped in `Compact<…>` when
its `typeName` does not say so. Two runtimes from different metadata generations still spell many types
differently (`LookupSource` vs `AccountIdLookupOf<T>`), so an arg row whose argument names are equal is
marked `names_equal`: its types differ, which may be spelling only or a real change (u32 -> u64); the
diff cannot tell, and never hides the row. Known limit: aliases are not resolved, so a type alias whose
width differs between the runtimes under the same name (e.g. an Assets `Balance`) yields no row.

`diff()` emits rows of the architecture's kinds: added / removed (pallets and calls), renamed (a call,
only through an explicit row in `plumbline.renames` whose two sides match the two runtimes — never by
similarity), arg-changed, const-changed (added, removed, retyped or new value), event-changed (added,
removed, or field types changed). Rows are sorted, so the output is deterministic.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from . import verdicts as v
from .renames import RENAMES, Rename

ADDED, REMOVED, RENAMED = "added", "removed", "renamed"
ARG_CHANGED, CONST_CHANGED, EVENT_CHANGED = "arg-changed", "const-changed", "event-changed"
DIFF_KINDS = (ADDED, REMOVED, RENAMED, ARG_CHANGED, CONST_CHANGED, EVENT_CHANGED)  # = diff_row.kind CHECK


@dataclass
class Pallet:
    calls: dict[str, tuple[tuple[str, str], ...]] = field(default_factory=dict)
    events: dict[str, tuple[str, ...]] = field(default_factory=dict)
    constants: dict[str, tuple[str, str]] = field(default_factory=dict)
    errors: tuple[str, ...] = ()


@dataclass
class NormalForm:
    metadata_version: int
    pallets: dict[str, Pallet]

    def call_count(self) -> int:
        return sum(len(p.calls) for p in self.pallets.values())


@dataclass(frozen=True)
class DiffRow:
    pallet: str
    item: str          # '' = the pallet itself; 'call:<name>', 'const:<name>', 'event:<name>'
    kind: str          # one of DIFF_KINDS
    before: object     # JSON-able; None when the item does not exist on the `from` side
    after: object      # JSON-able; None when the item does not exist on the `to` side
    note: str = ""

    def to_dict(self) -> dict:
        return {"pallet": self.pallet, "item": self.item, "kind": self.kind, "before": self.before,
                "after": self.after, "note": self.note}


# ---------------------------------------------------------------- normalise

# `<X as Trait>::` / `<X as Trait<I>>::` (one level of generics inside the trait) -> `X::`
_QUALIFIED = re.compile(r"<\s*([\w:]+(?:<[^<>]*>)?)\s+as\s+[\w:]+(?:<[^<>]*>)?\s*>::")
_PREFIX = re.compile(r"\b[TI]::")


def type_name(text: str | None) -> str:
    """One spelling of a type path: qualified paths unwrapped, `T::`/`I::` dropped, whitespace removed."""
    unwrapped, previous = text or "", None
    while unwrapped != previous:  # innermost first: `<<T as A>::B as C>::D` takes two passes
        previous, unwrapped = unwrapped, _QUALIFIED.sub(lambda m: m.group(1) + "::", unwrapped)
    return re.sub(r"\s+", "", _PREFIX.sub("", unwrapped))


class _Registry:
    """Type names for V14+ portable-registry ids (only what the normal form needs)."""

    def __init__(self, metadata):
        types = metadata.portable_registry.value["types"]
        self.types = {t["id"]: t["type"] for t in types}

    def is_compact(self, type_id: int) -> bool:
        return "compact" in self.types[type_id]["def"]

    def name(self, type_id: int, depth: int = 0) -> str:
        t = self.types[type_id]
        d = t["def"]
        if depth > 8:
            return f"#{type_id}"
        if "primitive" in d:
            return d["primitive"]
        if "compact" in d:
            return f"Compact<{self.name(d['compact']['type'], depth + 1)}>"
        if "sequence" in d:
            return f"Vec<{self.name(d['sequence']['type'], depth + 1)}>"
        if "array" in d:
            return f"[{self.name(d['array']['type'], depth + 1)};{d['array']['len']}]"
        if "tuple" in d:
            return "(" + ",".join(self.name(x, depth + 1) for x in d["tuple"]) + ")"
        if "bitsequence" in d:
            return "BitVec"
        path = t.get("path") or []
        base = path[-1] if path else f"#{type_id}"
        params = [p for p in t.get("params") or [] if p.get("type") is not None]
        if params:
            base += "<" + ",".join(self.name(p["type"], depth + 1) for p in params) + ">"
        return base

    def field_type(self, fld: dict) -> str:
        """A field's type as its `typeName` spells it, Compact made explicit; else the registry's name."""
        written = fld.get("typeName")
        if not written:
            return type_name(self.name(fld["type"]))
        name = type_name(written)
        if self.is_compact(fld["type"]) and not name.startswith("Compact<"):
            name = f"Compact<{name}>"
        return name


def _const_hex(const) -> str:
    return const.value_object["value"].get_used_bytes().hex()


def normalise(metadata, metadata_version: int) -> NormalForm:
    """Decoded metadata (`metadata.decode_metadata`) of V13 or V14+ -> the normal form."""
    pallets: dict[str, Pallet] = {}
    if metadata_version >= 14:
        reg = _Registry(metadata)
        for p in metadata.pallets:
            pallets[p.name] = Pallet(
                calls={c.name: tuple((a.value["name"], reg.field_type(a.value)) for a in c.args)
                       for c in p.calls or []},
                events={e.name: tuple(reg.field_type(f) for f in e.value["fields"]) for e in p.events or []},
                constants={k.name: (type_name(reg.name(k.value_object["type"].value)), _const_hex(k))
                           for k in p.constants or []},
                errors=tuple(e.name for e in p.errors or []))
    else:
        for p in metadata.pallets:
            pallets[p.name] = Pallet(
                calls={c.name: tuple((a.name, type_name(str(a.type))) for a in c.args) for c in p.calls or []},
                events={e.name: tuple(type_name(str(x)) for x in e.args or []) for e in p.events or []},
                constants={k.name: (type_name(k.value["type"]), _const_hex(k)) for k in p.constants or []},
                errors=tuple(e.name for e in p.errors or []))
    return NormalForm(metadata_version, dict(sorted(pallets.items())))


# ---------------------------------------------------------------- diff

def _args(args) -> list[list[str]]:
    return [list(a) for a in args]


def applicable_renames(a: NormalForm, b: NormalForm, renames: tuple[Rename, ...] = RENAMES) -> dict:
    """{(pallet, from_call): (to_call, rename row)} for every explicit call rename whose old side exists only
    in `a` and new side only in `b`. A rename row pairs two names (documented ↔ live) with evidence; it is
    used in whichever direction the two runtimes show, and only when that direction is unambiguous."""
    found: dict[tuple[str, str], tuple[str, Rename]] = {}
    for r in renames:
        if r.module or r.kind == v.NONE or not r.live_call:
            continue
        pa, pb = a.pallets.get(r.pallet), b.pallets.get(r.pallet)
        if pa is None or pb is None:
            continue
        for old, new in ((r.live_call, r.doc_call), (r.doc_call, r.live_call)):
            if old in pa.calls and old not in pb.calls and new in pb.calls and new not in pa.calls:
                found.setdefault((r.pallet, old), (new, r))
    return found


def diff(a: NormalForm, b: NormalForm, renames: tuple[Rename, ...] = RENAMES) -> list[DiffRow]:
    rows: list[DiffRow] = []
    for name in sorted(set(a.pallets) - set(b.pallets)):
        rows.append(DiffRow(name, "", REMOVED, {"calls": len(a.pallets[name].calls)}, None))
    for name in sorted(set(b.pallets) - set(a.pallets)):
        rows.append(DiffRow(name, "", ADDED, None, {"calls": len(b.pallets[name].calls)}))
    renamed = applicable_renames(a, b, renames)
    for name in sorted(set(a.pallets) & set(b.pallets)):
        pa, pb = a.pallets[name], b.pallets[name]
        renamed_to = {old: new for (pallet, old), (new, _) in renamed.items() if pallet == name}
        for call in sorted(set(pa.calls) | set(pb.calls)):
            item = f"call:{call}"
            if call in renamed_to:
                new = renamed_to[call]
                r = renamed[(name, call)][1]
                same = [n for n, _ in pa.calls[call]] == [n for n, _ in pb.calls[new]]
                rows.append(DiffRow(name, item, RENAMED, {"call": call, "args": _args(pa.calls[call])},
                                    {"call": new, "args": _args(pb.calls[new])},
                                    f"explicit rename row ({r.profile}: {r.pallet}.{r.doc_call} <-> {r.live_call})"
                                    + ("" if same else "; argument names differ too")))
            elif call in renamed_to.values():
                continue  # the new side of a rename row: reported on the row above
            elif call not in pb.calls:
                rows.append(DiffRow(name, item, REMOVED, {"args": _args(pa.calls[call])}, None))
            elif call not in pa.calls:
                rows.append(DiffRow(name, item, ADDED, None, {"args": _args(pb.calls[call])}))
            elif pa.calls[call] != pb.calls[call]:
                names_equal = [n for n, _ in pa.calls[call]] == [n for n, _ in pb.calls[call]]
                rows.append(DiffRow(name, item, ARG_CHANGED, {"args": _args(pa.calls[call])},
                                    {"args": _args(pb.calls[call])}, "names_equal" if names_equal else ""))
        for const in sorted(set(pa.constants) | set(pb.constants)):
            before = pa.constants.get(const)
            after = pb.constants.get(const)
            if before != after:
                rows.append(DiffRow(name, f"const:{const}", CONST_CHANGED,
                                    None if before is None else {"type": before[0], "value": before[1]},
                                    None if after is None else {"type": after[0], "value": after[1]},
                                    _const_note(before, after)))
        for event in sorted(set(pa.events) | set(pb.events)):
            before = pa.events.get(event)
            after = pb.events.get(event)
            if before != after:
                note = "added" if before is None else "removed" if after is None else \
                    "field types differ (spelling or real)"
                rows.append(DiffRow(name, f"event:{event}", EVENT_CHANGED,
                                    None if before is None else {"fields": list(before)},
                                    None if after is None else {"fields": list(after)}, note))
    return rows


def _const_note(before, after) -> str:
    if before is None:
        return "added"
    if after is None:
        return "removed"
    parts = []
    if before[0] != after[0]:
        parts.append("type")
    if before[1] != after[1]:
        parts.append("value")
    return " and ".join(parts) + " changed"


def summary(rows: list[DiffRow]) -> dict[str, int]:
    """Row counts per kind (and per item class), plus how many arg-changed rows keep their argument names
    (types differ: spelling or real) and how many const-changed rows differ in type only (V14 constants carry
    no `typeName`, so a V13 alias such as `Balance` meets its resolved primitive `u128`)."""
    out: dict[str, int] = {}
    for r in rows:
        cls = r.item.split(":", 1)[0] if r.item else "pallet"
        key = f"{r.kind} {cls}" if r.kind in (ADDED, REMOVED) else r.kind
        out[key] = out.get(key, 0) + 1
    out["arg-changed (names equal)"] = sum(1 for r in rows if r.kind == ARG_CHANGED and r.note == "names_equal")
    out["const-changed (type only)"] = sum(1 for r in rows if r.kind == CONST_CHANGED and r.note == "type changed")
    return dict(sorted(out.items()))


def store(conn, from_runtime: int, to_runtime: int, rows: list[DiffRow]) -> int:
    """Write diff rows into a ledger's `diff_row` table (both runtimes must already be in it)."""
    for r in rows:
        conn.execute("INSERT INTO diff_row (from_runtime, to_runtime, pallet, item, kind, before_json, after_json) "
                     "VALUES (?,?,?,?,?,?,?)",
                     (from_runtime, to_runtime, r.pallet, r.item, r.kind,
                      json.dumps(r.before, sort_keys=True), json.dumps(r.after, sort_keys=True)))
    conn.commit()
    return len(rows)
