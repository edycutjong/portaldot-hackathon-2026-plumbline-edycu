"""Parse the documented extrinsic surface out of the Portaldot mainnet docs (readthedocs HTML).

Each documented call on a `module-interface/extrinsics/<pallet>.html` page is a signature paragraph
followed by a bullet whose `interface` line names the call. Three spellings of that line occur:

    interface: balances.transferAll          plain    (most pages)
    interface: api.tx.multisig.asMulti       api-tx   (multisig)
    interface: - utility.batch               dash     (utility, vesting)

A signature is paired with the interface that follows it only when the names agree; anything else
is reported as unpaired, never guessed. Pages are read from a local cache filled by
`scripts/fetch_docs.py`; this module does no network I/O.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

DOCS_BASE = "https://portaldot-dev.readthedocs.io/en/latest/module-interface/extrinsics/"

INTERFACE = re.compile(
    r"^interface\s*:\s*(?P<prefix>-\s*|api\.tx\.)?(?P<pallet>[A-Za-z][A-Za-z0-9]*)\.(?P<call>[A-Za-z][A-Za-z0-9_]*)\s*$")
# args are empty or start with `name:`; this keeps complexity notes such as `O(1)` out
SIGNATURE = re.compile(r"^(?P<name>[A-Za-z][A-Za-z0-9_]*)\((?P<args>(?:[A-Za-z_][A-Za-z0-9_]*\s*:.*)?)\)$")
PAGE_LINK = re.compile(r'href="(?P<page>[A-Za-z][A-Za-z0-9]*)\.html"')
# a paragraph that starts like an interface line; one the INTERFACE pattern rejects is reported, not dropped
INTERFACE_LIKE = re.compile(r"^interface\s*:", re.IGNORECASE)

LAYOUTS = {None: "plain", "api.tx.": "api-tx", "-": "dash"}


@dataclass(frozen=True)
class DocCall:
    pallet: str               # as documented, e.g. "electionProviderMultiPhase"
    call: str                 # as documented, e.g. "transferKeepAlive"
    args: tuple[tuple[str, str], ...] | None  # None = no matching signature paragraph
    page: str
    url: str
    layout: str               # plain | api-tx | dash
    position: int             # 1-based order of the interface line on its page

    @property
    def key(self) -> str:
        return f"{self.pallet}.{self.call}"

    @property
    def source_ref(self) -> str:
        return f"{self.url} (interface {self.position})"


@dataclass
class PageResult:
    page: str
    url: str
    interfaces: int = 0
    signatures: int = 0
    raw_interface_labels: int = 0  # `<strong>interface</strong>` labels in the article body, inside a <p> or not
    unpaired: list[str] = field(default_factory=list)  # interface names with no matching signature
    unmatched_lines: list[str] = field(default_factory=list)  # interface-like paragraphs the pattern rejected
    calls: list[DocCall] = field(default_factory=list)

    @property
    def parsed(self) -> bool:
        return self.interfaces > 0

    @property
    def complete(self) -> bool:
        """Every interface label on the page became a call, and nothing interface-like was rejected."""
        return self.parsed and not self.unmatched_lines and self.raw_interface_labels == self.interfaces


class _ParagraphText(HTMLParser):
    """Collect the plain text of every <p> inside the page's article body, in document order."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.paragraphs: list[str] = []
        self.interface_labels = 0
        self._strong: list[str] | None = None
        self._body_tag: str | None = None  # tag name of the articleBody element while inside it
        self._body_depth = 0
        self._p_depth = 0
        self._buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        if self._body_tag is None:
            if dict(attrs).get("itemprop") == "articleBody":
                self._body_tag, self._body_depth = tag, 1
            return
        if tag == self._body_tag:
            self._body_depth += 1
        if tag == "strong":
            self._strong = []
        if tag == "p":
            if self._p_depth:  # an unclosed <p> is implicitly closed by the next one
                self._flush()
            self._buf, self._p_depth = [], 1

    def handle_endtag(self, tag):
        if self._body_tag is None:
            return
        if tag == "strong" and self._strong is not None:
            if "".join(self._strong).strip().lower() == "interface":
                self.interface_labels += 1
            self._strong = None
        if tag == "p" and self._p_depth:
            self._flush()
        if tag == self._body_tag:
            self._body_depth -= 1
            if self._body_depth == 0:
                if self._p_depth:
                    self._flush()
                self._body_tag = None

    def handle_data(self, data):
        if self._body_tag is None:
            return
        if self._strong is not None:
            self._strong.append(data)
        if self._p_depth:
            self._buf.append(data)

    def _flush(self):
        self.paragraphs.append(" ".join("".join(self._buf).split()))
        self._p_depth = 0


def _read_body(page_html: str) -> _ParagraphText:
    parser = _ParagraphText()
    parser.feed(page_html)
    parser.close()
    return parser


def paragraphs(page_html: str) -> list[str]:
    return _read_body(page_html).paragraphs


OPEN, CLOSE = "<([{", ">)]}"


def split_args(arg_text: str) -> tuple[tuple[str, str], ...]:
    """`dest: MultiAddress , value: Compact<u128>` -> (("dest", "MultiAddress"), ("value", "Compact<u128>"))."""
    parts, depth, start = [], 0, 0
    for i, ch in enumerate(arg_text):
        depth += (ch in OPEN) - (ch in CLOSE)
        if ch == "," and depth == 0:
            parts.append(arg_text[start:i])
            start = i + 1
    parts.append(arg_text[start:])
    out = []
    for part in parts:
        name, _, typ = part.partition(":")
        if name.strip():  # an empty slot (`a: u8,,`) is not an argument
            out.append((name.strip(), " ".join(typ.split())))
    return tuple(out)


def parse_page(page_html: str, page: str, url: str | None = None) -> PageResult:
    url = url or f"{DOCS_BASE}{page}.html"
    body = _read_body(page_html)
    result = PageResult(page=page, url=url, raw_interface_labels=body.interface_labels)
    last_sig: tuple[str, str] | None = None
    for text in body.paragraphs:
        sig = SIGNATURE.match(text)
        if sig:
            last_sig = (sig["name"], sig["args"])
            result.signatures += 1
            continue
        iface = INTERFACE.match(text)
        if not iface:
            if INTERFACE_LIKE.match(text):
                result.unmatched_lines.append(text)
            continue
        result.interfaces += 1
        call = iface["call"]
        prefix = iface["prefix"].strip() if iface["prefix"] else None
        args = split_args(last_sig[1]) if last_sig and last_sig[0] == call else None
        if args is None:
            result.unpaired.append(call)
        result.calls.append(DocCall(pallet=iface["pallet"], call=call, args=args, page=page, url=url,
                                    layout=LAYOUTS[prefix], position=result.interfaces))
        last_sig = None
    return result


def index_pages(index_html: str) -> list[str]:
    """Extrinsic page names linked from the extrinsics index, in link order, without duplicates."""
    seen: list[str] = []
    for m in PAGE_LINK.finditer(index_html):
        if m["page"] not in seen and m["page"] != "index":
            seen.append(m["page"])
    return seen


def parse_dir(cache_dir: Path) -> list[PageResult]:
    """Parse every page the cached index links to. A linked page missing from the cache fails loud."""
    cache_dir = Path(cache_dir)
    pages = index_pages((cache_dir / "index.html").read_text(encoding="utf-8"))
    results = []
    for page in pages:
        path = cache_dir / f"{page}.html"
        if not path.exists():
            raise FileNotFoundError(f"page {page} is linked from the index but not cached: {path}")
        results.append(parse_page(path.read_text(encoding="utf-8"), page))
    return results


def documented_calls(results: list[PageResult]) -> dict[str, DocCall]:
    """First occurrence of each `pallet.call`; see `duplicate_calls` for the ones this collapses."""
    calls: dict[str, DocCall] = {}
    for res in results:
        for call in res.calls:
            calls.setdefault(call.key, call)
    return calls


def duplicate_calls(results: list[PageResult]) -> dict[str, list[str]]:
    """`pallet.call` documented more than once -> the source refs of every occurrence."""
    refs: dict[str, list[str]] = {}
    for res in results:
        for call in res.calls:
            refs.setdefault(call.key, []).append(call.source_ref)
    return {key: where for key, where in refs.items() if len(where) > 1}


def snake(name: str) -> str:
    """Documented camelCase -> metadata snake_case: transferKeepAlive -> transfer_keep_alive.

    An underscore goes before an upper-case letter that follows a lower-case letter or digit, and
    before the first digit of a run that follows a letter (asMultiThreshold1 -> as_multi_threshold_1).
    """
    out: list[str] = []
    for i, ch in enumerate(name):
        prev = name[i - 1] if i else ""
        if (ch.isupper() and (prev.islower() or prev.isdigit())) or (ch.isdigit() and prev.isalpha()):
            out.append("_")
        out.append(ch.lower())
    return "".join(out)


# Comparison classes. These are raw set comparisons; ledger verdicts are assigned elsewhere.
MATCH, ARG_MISMATCH, ARGS_UNKNOWN = "match", "arg-mismatch", "args-unknown"
CALL_ABSENT, PALLET_ABSENT = "call-absent", "pallet-absent"
CLASSES = (MATCH, ARG_MISMATCH, ARGS_UNKNOWN, CALL_ABSENT, PALLET_ABSENT)


def compare(docs: dict[str, DocCall], live: dict[str, list[tuple[str, str]]],
            live_pallets: list[str] | None = None) -> dict:
    """Compare documented calls with a runtime call surface (`Pallet.call` -> [(arg, type)]).

    `live_pallets` is the runtime's full pallet list (pallets with no calls included); without it,
    pallets are inferred from `live`, which only holds pallets that have calls.

    Argument comparison is by name and order only; type spellings differ between the docs and V13
    metadata (MultiAddress vs LookupSource) without that being drift. A documented call with no
    signature is `args-unknown`, never compared as if it had no arguments. An arg-mismatch where the
    names differ only by a leading underscore (`remark` vs `_remark`) is flagged `underscore_only`.
    """
    pallet_names = live_pallets if live_pallets is not None else [key.split(".")[0] for key in live]
    by_lower = {name.lower(): name for name in pallet_names}
    rows, matched_live = [], set()
    for key in sorted(docs):
        doc = docs[key]
        pallet = by_lower.get(doc.pallet.lower())
        live_key = f"{pallet}.{snake(doc.call)}" if pallet else None
        live_args = None
        underscore_only = False
        if not pallet:
            cls = PALLET_ABSENT
        elif live_key not in live:
            cls = CALL_ABSENT
        else:
            matched_live.add(live_key)
            live_args = live[live_key]
            if doc.args is None:
                cls = ARGS_UNKNOWN
            else:
                doc_names = [name for name, _ in doc.args]
                live_names = [name for name, _ in live_args]
                cls = MATCH if doc_names == live_names else ARG_MISMATCH
                underscore_only = cls == ARG_MISMATCH and [n.lstrip("_") for n in doc_names] == [
                    n.lstrip("_") for n in live_names]
        row = {
            "doc_call": key, "live_call": live_key if cls in (MATCH, ARG_MISMATCH, ARGS_UNKNOWN) else None,
            "class": cls, "page": doc.page, "source_ref": doc.source_ref,
            "doc_args": [list(a) for a in doc.args] if doc.args is not None else None,
            "live_args": [list(a) for a in live_args] if live_args is not None else None,
        }
        if cls == ARG_MISMATCH:
            row["underscore_only"] = underscore_only
        rows.append(row)
    documented_pallets = {by_lower[d.pallet.lower()] for d in docs.values() if d.pallet.lower() in by_lower}
    undocumented = sorted(set(live) - matched_live)
    counts = {cls: sum(1 for r in rows if r["class"] == cls) for cls in CLASSES}
    return {
        "counts": counts | {
            "documented": len(rows),
            "absent": counts[CALL_ABSENT] + counts[PALLET_ABSENT],
            "arg-mismatch-underscore-only": sum(1 for r in rows if r.get("underscore_only")),
            "live_calls": len(live),
            "undocumented": len(undocumented),
            "undocumented_in_documented_pallets": sum(1 for k in undocumented if k.split(".")[0] in documented_pallets),
        },
        "pallets_without_docs_page": sorted({k.split(".")[0] for k in undocumented} - documented_pallets),
        "rows": rows,
        "undocumented": undocumented,
    }
