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
    unpaired: list[str] = field(default_factory=list)  # interface names with no matching signature
    calls: list[DocCall] = field(default_factory=list)

    @property
    def parsed(self) -> bool:
        return self.interfaces > 0


class _ParagraphText(HTMLParser):
    """Collect the plain text of every <p> inside the page's article body, in document order."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.paragraphs: list[str] = []
        self._in_body = False
        self._body_depth = 0
        self._p_depth = 0
        self._buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if not self._in_body:
            if attrs.get("itemprop") == "articleBody":
                self._in_body, self._body_depth = True, 1
            return
        if tag == "div":
            self._body_depth += 1
        if tag == "p":
            if self._p_depth == 0:
                self._buf = []
            self._p_depth += 1

    def handle_endtag(self, tag):
        if not self._in_body:
            return
        if tag == "p" and self._p_depth:
            self._p_depth -= 1
            if self._p_depth == 0:
                self.paragraphs.append(" ".join("".join(self._buf).split()))
        if tag == "div":
            self._body_depth -= 1
            if self._body_depth == 0:
                self._in_body = False

    def handle_data(self, data):
        if self._in_body and self._p_depth:
            self._buf.append(data)


def paragraphs(page_html: str) -> list[str]:
    parser = _ParagraphText()
    parser.feed(page_html)
    parser.close()
    return parser.paragraphs


def split_args(arg_text: str) -> tuple[tuple[str, str], ...]:
    """`dest: MultiAddress , value: Compact<u128>` -> (("dest", "MultiAddress"), ("value", "Compact<u128>"))."""
    parts, depth, cur = [], 0, []
    for ch in arg_text:
        if ch in "<([":
            depth += 1
        elif ch in ">)]":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    if "".join(cur).strip():
        parts.append("".join(cur))
    out = []
    for part in parts:
        name, _, typ = part.partition(":")
        out.append((name.strip(), " ".join(typ.split())))
    return tuple(out)


def parse_page(page_html: str, page: str, url: str | None = None) -> PageResult:
    url = url or f"{DOCS_BASE}{page}.html"
    result = PageResult(page=page, url=url)
    last_sig: tuple[str, str] | None = None
    for text in paragraphs(page_html):
        sig = SIGNATURE.match(text)
        if sig:
            last_sig = (sig["name"], sig["args"])
            result.signatures += 1
            continue
        iface = INTERFACE.match(text)
        if not iface:
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
    calls: dict[str, DocCall] = {}
    for res in results:
        for call in res.calls:
            calls.setdefault(call.key, call)
    return calls


def snake(name: str) -> str:
    """Documented camelCase -> metadata snake_case: transferKeepAlive -> transfer_keep_alive."""
    name = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name)
    name = re.sub(r"(?<=[A-Za-z])(?=[0-9])", "_", name)
    return name.lower()


# Comparison classes. These are raw set comparisons; ledger verdicts are assigned elsewhere.
MATCH, ARG_MISMATCH, CALL_ABSENT, PALLET_ABSENT = "match", "arg-mismatch", "call-absent", "pallet-absent"


def compare(docs: dict[str, DocCall], live: dict[str, list[tuple[str, str]]]) -> dict:
    """Compare documented calls with a runtime call surface (`Pallet.call` -> [(arg, type)]).

    Argument comparison is by name and order only; type spellings differ between the docs and V13
    metadata (MultiAddress vs LookupSource) without that being drift.
    """
    live_pallets = {key.split(".")[0].lower(): key.split(".")[0] for key in live}
    rows, matched_live = [], set()
    for key in sorted(docs):
        doc = docs[key]
        pallet = live_pallets.get(doc.pallet.lower())
        live_key = f"{pallet}.{snake(doc.call)}" if pallet else None
        live_args = None
        if not pallet:
            cls = PALLET_ABSENT
        elif live_key not in live:
            cls = CALL_ABSENT
        else:
            matched_live.add(live_key)
            live_args = live[live_key]
            doc_names = [name for name, _ in (doc.args or ())]
            cls = MATCH if doc_names == [name for name, _ in live_args] else ARG_MISMATCH
        rows.append({
            "doc_call": key, "live_call": live_key if cls in (MATCH, ARG_MISMATCH) else None, "class": cls,
            "page": doc.page, "source_ref": doc.source_ref,
            "doc_args": [list(a) for a in doc.args] if doc.args is not None else None,
            "live_args": [list(a) for a in live_args] if live_args is not None else None,
        })
    documented_pallets = {live_pallets[d.pallet.lower()] for d in docs.values() if d.pallet.lower() in live_pallets}
    undocumented = sorted(set(live) - matched_live)
    counts = {cls: sum(1 for r in rows if r["class"] == cls) for cls in (MATCH, ARG_MISMATCH, CALL_ABSENT, PALLET_ABSENT)}
    return {
        "counts": counts | {
            "documented": len(rows),
            "absent": counts[CALL_ABSENT] + counts[PALLET_ABSENT],
            "live_calls": len(live),
            "undocumented": len(undocumented),
            "undocumented_in_documented_pallets": sum(1 for k in undocumented if k.split(".")[0] in documented_pallets),
        },
        "pallets_without_docs_page": sorted({k.split(".")[0] for k in undocumented} - documented_pallets),
        "rows": rows,
        "undocumented": undocumented,
    }
