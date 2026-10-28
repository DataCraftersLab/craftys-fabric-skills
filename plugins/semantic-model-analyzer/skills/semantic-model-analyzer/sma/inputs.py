"""
What was handed over, and therefore which parts the run can emit.

**The segment is the input, not the toolchain.** A TMDL file read with the M parser
(because the M is inside a partition) is still a TMDL input. What decides a segment is
what someone had to supply, never which module did the reading. So this module answers one
question -- what am I looking at -- and everything downstream follows from its answer
rather than guessing for itself.

A run is handed TMDL, or M, or both, and emits only the parts its input can support. A
missing part is stated, not faked: a Power-Query-only run says why there is no semantic
model section rather than rendering an empty one.

Two things this module deliberately does not do
-----------------------------------------------

**It does not trust the extension.** The first version routed by suffix and sniffed for
`"let"` and `"in"` as substrings, which failed in both directions. TMDL holding a measure
called `Completed Orders` contains `let`, and `in` is in almost every English word, so a
semantic model saved as `.txt` was routed to the M parser, which found nothing, and the run
reported an empty model. In the other direction, every input with no `let` was rejected --
which is every bare function and every parameter query, i.e. exactly the `.pq` files people
write by hand.

The parsers themselves are the discriminator, and they turn out to be unambiguous. On
eleven shapes -- Advanced Editor paste, section document, labelled dump, annotated
function, bare function, parameter, TMDL as `.txt`, TMDL with an M partition, SQL, prose,
DAX -- each one is found by exactly one parser and yields nothing from the other. So
routing asks them instead of guessing, and the cheap format sniff only decides which to ask
first.

**It does not care whether the text came from a file.** A paste out of the Power Query
window is the common case, not the exotic one. An input is text with a name attached, and
where the text came from is provenance rather than a code path.
"""

import hashlib
import os
from pathlib import Path

from . import screen
from .vendor import m_parser
from .vendor.m_parser import Model as MModel
from .vendor.tmdl_parser import Model as TmdlModel

#: Used only to break a tie and to decide which parser to ask first. Never to decide.
TMDL_SUFFIXES = {".tmdl"}
M_SUFFIXES = {".pq", ".m", ".txt"}
READABLE_SUFFIXES = TMDL_SUFFIXES | M_SUFFIXES

#: A PBIP project keeps its TMDL under `definition`. Recognised so a reader can hand
#: over the folder they actually have rather than hunting for the one this wants. The
#: whole folder is read -- `tables/`, but also `relationships.tmdl`, `model.tmdl`,
#: `database.tmdl`, `expressions.tmdl`, `roles/`, `cultures/`, `perspectives/`. The
#: first version read `definition/tables` and nothing else, so a partition that pointed
#: at a shared expression degraded into an unresolved reference and nothing said why.
PBIP_DEFINITION_DIRS = ("definition", "Model.SemanticModel/definition")

#: The kinds of file a `definition` folder holds, by where they sit, for the note that
#: says what was read. Anything else under `definition` is listed as "other".
PBIP_KINDS = (
    ("tables", "table"), ("roles", "role"), ("cultures", "culture"),
    ("perspectives", "perspective"),
)
PBIP_ROOT_FILES = {"model.tmdl": "model", "database.tmdl": "database",
                   "relationships.tmdl": "relationships", "expressions.tmdl": "expressions"}

SKIP_DIRS = {".git", "__pycache__", "node_modules", ".vs", ".vscode", "dist", "build",
             ".pbi"}

MODE_TMDL = "tmdl"
MODE_M = "m"
MODE_BOTH = "both"

#: What a detected M format tells us on its own. `section` and `labelled` are positive
#: evidence of M; `tmdl` is positive evidence of TMDL; `expression` is the fallback the
#: parser returns for anything it could not place, so it settles nothing.
DECISIVE_FORMAT = {"section": MODE_M, "labelled": MODE_M, "tmdl": MODE_TMDL}


class InputError(Exception):
    """Nothing readable was found. The message says what was looked for."""


class Source:
    """One piece of input: text, a name, and what it turned out to be.

    A file and a paste differ only in `origin` and `path`. Everything downstream reads
    `text`, so neither is a special case.
    """

    __slots__ = ("name", "origin", "path", "text", "mode", "format", "note")

    def __init__(self, name, text, origin="text", path=None):
        self.name = name
        self.text = text
        self.origin = origin
        self.path = path
        self.mode = None
        self.format = None
        self.note = ""

    @property
    def lines(self):
        return self.text.count("\n") + 1 if self.text else 0

    @property
    def digest(self):
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()[:16]

    def describe(self):
        if self.origin == "file":
            return self.name
        return "%s (%s, %d line%s)" % (self.name, self.origin, self.lines,
                                       "" if self.lines == 1 else "s")

    def to_dict(self):
        """Provenance for the dataset. A paste has no path, so it is described instead."""
        return {"name": self.name, "origin": self.origin,
                "path": str(self.path) if self.path else None,
                "format": self.format, "lines": self.lines,
                "bytes": len(self.text.encode("utf-8")), "digest": self.digest}


def _tmdl_yield(text):
    """How much the TMDL parser found. Everything the parser models counts, not only
    measures: a relationships file, a role file or `model.tmdl` holds no measure and is
    still part of a semantic model."""
    try:
        d = TmdlModel.from_text(text).dump()
    except Exception:
        return 0
    # A culture file holds nothing the parser models, and is still TMDL: the parser
    # recorded it as unsupported, which is a reading too. Without this a PBIP's
    # `cultures/` folder was skipped as "nothing readable" and never counted.
    return (len(d["measures"]) + len(d["columns"]) + len(d["tableRows"])
            + len(d["relationships"]) + len(d["roles"]) + len(d["perspectives"])
            + len(d["expressions"]) + (1 if d.get("model") else 0)
            + sum((d.get("unsupported") or {}).values()))


def _m_yield(text):
    """How many queries the M parser found, and what it said if it found none."""
    try:
        model = MModel.from_text(text)
    except Exception:
        return 0, ""
    why = model.diagnostics[0].message if (not model.queries and model.diagnostics) else ""
    return len(model.queries), why


def classify_text(text, suffix=""):
    """What this text is: (mode, detected format, note).

    `mode` is None when neither parser found anything, and the note then carries the M
    parser's own guess at what the paste is -- it already recognises SQL, DAX and prose,
    and saying "this looks like SQL" is worth more than "not recognised".
    """
    if not text or not text.strip():
        return None, None, "empty"

    fmt = m_parser.detect_format(text)
    tmdl_first = suffix.lower() in TMDL_SUFFIXES

    # A `.tmdl` file that the TMDL parser can read is TMDL, whatever the M sniffer
    # makes of its text. A calculation-group file opening with a dozen `///` doc
    # comment lines looked like a labelled M document to the sniffer, and the whole
    # model was read as Power Query -- three "could not be read" errors and no model.
    if tmdl_first and _tmdl_yield(text):
        return MODE_TMDL, fmt, ""

    decisive = DECISIVE_FORMAT.get(fmt)
    if decisive is not None:
        return decisive, fmt, ""

    # `expression` settles nothing, so ask both parsers. Extension decides only the
    # order, which costs nothing when the first one answers.
    n_queries, why = _m_yield(text)
    if n_queries:
        return MODE_M, fmt, ""
    if not tmdl_first and _tmdl_yield(text):
        return MODE_TMDL, fmt, ""
    return None, fmt, why or "no measures, columns, tables or queries found in it"


def read_file(path):
    """One file as a Source. `utf-8-sig` because an export can carry a BOM, and
    `newline=""` because how a file ends its lines is something to be immune to rather
    than something to normalise away."""
    p = Path(path)
    with p.open(encoding="utf-8-sig", newline="", errors="replace") as fh:
        text = fh.read()
    src = Source(p.name, text, origin="file", path=str(p.resolve()))
    src.mode, src.format, src.note = classify_text(text, p.suffix)
    return src


def make_source(text, name, origin="text", declared=None):
    """One paste as a Source. `declared` forces the segment when the caller already knows
    it -- `--tmdl-text` is a statement, not a hint, and overriding the sniff is the whole
    point of naming the flag."""
    src = Source(name, text, origin=origin)
    src.mode, src.format, src.note = classify_text(text)
    if declared and src.mode != declared:
        # Say so rather than silently obeying either one. A caller who declared M and
        # handed over TMDL has made a mistake worth surfacing.
        if src.mode is None:
            src.note = "declared %s; nothing recognised in it (%s)" % (declared, src.note)
        else:
            src.note = "declared %s, but it reads as %s -- treated as %s" % (
                declared, src.mode, declared)
        src.mode = declared
    return src


def _definition_root(p):
    """The PBIP `definition` folder under `p`, if `p` is a project, a `.SemanticModel`
    folder, or the definition folder itself."""
    if p.name == "definition" and any(p.rglob("*.tmdl")):
        return p
    for rel in PBIP_DEFINITION_DIRS:
        cand = p / rel
        if cand.is_dir() and any(cand.rglob("*.tmdl")):
            return cand
    # A project folder: `Report.pbip` beside `Name.SemanticModel/definition`.
    for child in sorted(p.iterdir()) if p.is_dir() else []:
        if child.is_dir() and child.name.endswith(".SemanticModel"):
            cand = child / "definition"
            if cand.is_dir() and any(cand.rglob("*.tmdl")):
                return cand
    return None


def describe_definition(files, root):
    """What a PBIP definition folder turned out to hold, by kind, for the input note."""
    counts = {}
    for f in files:
        rel = f.relative_to(root)
        kind = "other"
        if len(rel.parts) == 1:
            kind = PBIP_ROOT_FILES.get(rel.name, "other")
        else:
            kind = dict(PBIP_KINDS).get(rel.parts[0], "other")
        counts[kind] = counts.get(kind, 0) + 1
    order = ["table", "relationships", "model", "database", "expressions", "role",
             "perspective", "culture", "other"]
    bits = []
    for kind in order:
        n = counts.get(kind)
        if not n:
            continue
        if kind in ("relationships", "model", "database", "expressions"):
            bits.append(kind)
        else:
            bits.append("%d %s%s" % (n, kind, "" if n == 1 else "s"))
    return ", ".join(bits)


def walk(root):
    """Files worth reading under a folder. A PBIP project is read whole: every
    `.tmdl` under `definition`, and the note says what kinds were found."""
    p = Path(root)
    notes = []
    definition = _definition_root(p)
    if definition is not None:
        files = sorted(f for f in definition.rglob("*.tmdl")
                       if not any(part in SKIP_DIRS for part in f.relative_to(definition).parts))
        rel = definition.relative_to(p) if definition != p else Path(".")
        notes.append("read the PBIP definition%s: %s" % (
            "" if str(rel) == "." else " at %s" % rel.as_posix(),
            describe_definition(files, definition)))
        return files, notes
    found = []
    for dirpath, dirnames, filenames in os.walk(p):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in sorted(filenames):
            if Path(fn).suffix.lower() in READABLE_SUFFIXES:
                found.append(Path(dirpath) / fn)
    return found, notes


def collect(paths=(), texts=()):
    """Everything handed over, as Sources, in a stable order.

    `texts` is a list of (text, name, origin, declared) for pastes and stdin.
    """
    sources, notes, seen = [], [], set()

    for text, name, origin, declared in texts:
        sources.append(make_source(text, name, origin, declared))

    for raw in paths:
        p = Path(raw)
        if not p.exists():
            raise InputError("no such path: %s" % raw)
        files, walk_notes = ([p], []) if p.is_file() else walk(p)
        notes.extend(walk_notes)
        for fp in files:
            key = str(Path(fp).resolve())
            if key in seen:
                continue
            seen.add(key)
            try:
                sources.append(read_file(fp))
            except OSError as exc:
                notes.append("could not read %s: %s" % (Path(fp).name, exc))

    for s in sources:
        if s.mode is None:
            notes.append("skipped %s: %s" % (s.describe(), s.note))
        elif s.note:
            notes.append("%s: %s" % (s.describe(), s.note))
    return sources, notes


def resolve(paths=(), texts=()):
    """The whole routing decision, in one object.

    mode          which parts the run may emit
    tmdl / m      the Sources feeding each, in a stable order
    sources       every Source, including the ones nothing was made of
    notes         what was skipped, what was declared, and any layout recognised
    """
    sources, notes = collect(paths, texts)
    key = lambda s: (s.origin != "file", (s.path or s.name).lower())  # noqa: E731
    tmdl = sorted([s for s in sources if s.mode == MODE_TMDL], key=key)
    m = sorted([s for s in sources if s.mode == MODE_M], key=key)

    if tmdl and m:
        mode = MODE_BOTH
    elif tmdl:
        mode = MODE_TMDL
    elif m:
        mode = MODE_M
    else:
        given = ", ".join([str(x) for x in paths]
                          + ["%s (%s)" % (n, o) for _, n, o, _ in texts]) or "nothing"
        raise InputError(
            "found nothing readable in %s.\n"
            "Expected a semantic model as TMDL, or Power Query M -- as files (*.tmdl, "
            "*.pq, *.m, *.txt), a folder, a PBIP project, or text on stdin.\n"
            "M may be a whole section document, a labelled dump, one Advanced Editor "
            "paste, a bare function or a single parameter.%s"
            % (given, ("\nWhat was looked at: " + "; ".join(notes)) if notes else ""))
    # Only what will be parsed is screened: a skipped file is never copied anywhere.
    return {"mode": mode, "tmdl": tmdl, "m": m, "sources": sources, "notes": notes,
            "given": describe_given(paths, texts), "screen": screen.scan(tmdl + m)}


def describe_given(paths=(), texts=()):
    """What the user handed over, as the reader should see it named: a PBIP project by
    its project name, a file by its file name, a paste by the name it was given.

    A page that does not say which model it is about is a page nobody can file. The
    run label was optional and usually absent, so the name comes from the input.
    """
    out = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            root = _definition_root(p)
            if root is not None:
                owner = root.parent if root.name == "definition" else root
                if owner.name.endswith(".SemanticModel"):
                    name = owner.name[: -len(".SemanticModel")]
                elif owner == p and not p.name.endswith(".SemanticModel"):
                    name = p.name
                else:
                    name = owner.name
                out.append({"kind": "PBIP project", "name": name, "path": str(p)})
            else:
                out.append({"kind": "folder", "name": p.name, "path": str(p)})
        elif p.suffix.lower() in TMDL_SUFFIXES:
            out.append({"kind": "TMDL file", "name": p.name, "path": str(p)})
        elif p.suffix.lower() in (".pq", ".m"):
            out.append({"kind": "Power Query file", "name": p.name, "path": str(p)})
        else:
            out.append({"kind": "file", "name": p.name, "path": str(p)})
    for text, name, origin, declared in texts:
        out.append({"kind": origin or "paste", "name": name, "path": None})
    return out


def subject_name(given):
    """One name for the run: the single input, up to three joined, or a count."""
    names = [g["name"] for g in given if g.get("name")]
    if not names:
        return ""
    if len(names) <= 3:
        return " + ".join(names)
    return "%d inputs" % len(names)


def text_of(sources):
    """One blob, for TMDL.

    Concatenated rather than parsed per file, because cross-references only resolve when
    every definition is in the same parse -- a measure in one file reading a measure in
    another. TMDL composes under concatenation: two files of `table` blocks are a valid
    third file of `table` blocks.
    """
    return "\n\n".join(src.text for src in sources)


class LineMap:
    """Which source a line of the concatenated TMDL came from.

    `text_of` joins sources with one blank line, so source k+1 starts at
    `start_k + lines_k + 1`. The parser reports 1-based lines into the joined text;
    this turns them back into (source name, line in that source), which is what a
    finding needs to say where it came from.
    """

    def __init__(self, sources):
        self.starts = []
        line = 1
        for src in sources:
            self.starts.append((line, src.name, src.path))
            line += src.lines + 1

    def locate(self, line):
        """(file name, path, local line) for a 1-based line in the joined text, or None."""
        if not line or line < 1 or not self.starts:
            return None
        hit = self.starts[0]
        for start in self.starts:
            if start[0] <= line:
                hit = start
            else:
                break
        return {"file": hit[1], "path": hit[2], "line": line - hit[0] + 1}


#: What `_split_single` calls a bare expression it found no label above. Not a name
#: anyone chose, so it is replaced with something that identifies the source.
DEFAULT_QUERY_NAME = "Query1"


def _source_stem(src):
    """A usable query name from a source. `orders.pq` -> `orders`."""
    stem = Path(src.name).stem if src.origin == "file" else src.name
    return stem.strip() or src.name


def m_document(sources):
    """One *labelled* document, for M, however many files and pastes there were.

    Returns (text, notes).

    Concatenating the raw text does not work here, and the way it fails is silent. A blob
    has exactly one detected format, so a folder holding a section document, a labelled
    dump, two bare expressions and a parameter is detected as `section` -- and the
    section splitter then reads only the `shared` declarations. Everything else is
    dropped with no diagnostic at all. On the shapes fixture that was five queries out of
    ten, and the run looked entirely successful.

    So each source is split by its *own* format first, and the queries are re-emitted as
    one labelled document -- the one format that composes, because a label belongs to a
    query rather than to a file. The second parse is the authoritative one: cross-file
    references only resolve when every definition is in the same parse.

    A `//` comment at column 0 inside a query body cannot split this wrongly. The
    labelled splitter decides boundaries by parsing rather than by matching comments,
    which is the rule that already tells a query label from a comment inside a query.
    """
    parts, notes, used = [], [], {}

    for src in sources:
        raws = m_parser.split_document(src.text).queries
        for raw in raws:
            # An unlabelled bare expression is called `Query1` by the splitter, which is
            # a placeholder rather than a name -- and three files of them all arrive
            # called `Query1`. The file it came from is better information.
            name = raw.name
            if name == DEFAULT_QUERY_NAME and len(raws) == 1:
                name = _source_stem(src)
            elif name == DEFAULT_QUERY_NAME:
                name = "%s %d" % (_source_stem(src), raws.index(raw) + 1)

            # Two sources may genuinely define the same name. Both are kept, because
            # dropping one would be the silent loss this function exists to prevent, and
            # the collision is reported rather than resolved quietly.
            if name in used:
                used[name] += 1
                unique = "%s (%d)" % (name, used[name])
                notes.append("two queries are called %r; the one from %s is listed as %r"
                             % (name, src.describe(), unique))
                name = unique
            else:
                used[name] = 1

            # A name with a newline in it would end the label and swallow the query, so
            # it is flattened rather than trusted.
            label = " ".join(name.split())
            parts.append("// %s\n%s" % (label, raw.source.strip()))

    return "\n\n".join(parts), notes


def describe(res):
    bits = []
    for kind, label in ((MODE_TMDL, "TMDL"), (MODE_M, "Power Query")):
        got = res[kind]
        if not got:
            continue
        files = sum(1 for s in got if s.origin == "file")
        pasted = len(got) - files
        parts = []
        if files:
            parts.append("%d file%s" % (files, "" if files == 1 else "s"))
        if pasted:
            parts.append("%d paste%s" % (pasted, "" if pasted == 1 else "s"))
        bits.append("%s %s" % (label, " + ".join(parts)))
    return "%s (%s)" % (res["mode"], ", ".join(bits))
