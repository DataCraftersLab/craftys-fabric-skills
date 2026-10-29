"""
Power Query M parser, dependency-graph builder and query analyser.

A standalone Python port of the analysis layer in the "M Query Chain" Fabric app.
Stdlib only, no third-party dependencies, no Power BI Desktop, no gateway, no
Fabric access. Give it M text and it gives you:

  - **Queries**, however the paste is shaped: `// Name` labels, a section
    document, M inside TMDL, or one bare expression.
  - **Steps** — the `let` bindings in order, the operation each performs in Power
    Query's own vocabulary, what it reads, what reads it, and the columns it adds,
    drops, renames or retypes.
  - **The dependency graph** — which queries reference which, the layer each sits
    in, reference cycles, and what breaks if one changes.
  - **Sources** — the connector behind each query, the server or URL it names, and
    any native SQL, decoded out of `#(lf)` into readable text.
  - **Findings** — dead steps, row-by-row reads, chained models, inline data,
    where folding probably stops, and repeated connections to one source.

Its sibling is `tmdl_parser.py`, which does the same job for DAX in TMDL. The two
are deliberately separate: TMDL is a property tree read a line at a time, M is one
nested expression language where nothing useful can be decided a line at a time.

The rule that matters most is in `resolve_query`: **what a name means is decided
by lexical scope, not by matching text.** Nearly every query in a real model has a
step called `Source`; a model that also has a *query* called `Source` would, under
text matching, gain an edge from every query in the model to that one. Scope is
the only thing that gets this right, and shadowing is the normal case in M rather
than an edge case.

As a library:

    from m_parser import Model

    model = Model.from_paths(["queries.pq"])
    model.get("Non BbM Master").sources[0].native_query   # decoded SQL
    model.downstream("Non BbM Master")                    # blast radius
    model.get("Monthly Trend Final (Append)").all_source_kinds

As a CLI: `python m_parser.py --help`.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

__version__ = "1.0.0"


# ---------------------------------------------------------------------------
# Lexer
# ---------------------------------------------------------------------------
#
# Driven by the M language specification's lexical grammar. The parts that are
# easy to get wrong, and are therefore called out below, are all consequences of
# `#` meaning four unrelated things and `[` meaning four more.

# Reserved words. The `#…` names appear here as syntax and in INTRINSICS as
# values; INTRINSICS is consulted first.
#
# `optional` and `nullable` are contextual in the spec — they only bind inside a
# parameter list or a type — but treating them as keywords everywhere is safe,
# because M forbids them as identifiers anyway.
KEYWORDS = {
    "and", "as", "each", "else", "error", "false", "if", "in", "is", "let",
    "meta", "not", "null", "nullable", "optional", "or", "otherwise", "section",
    "shared", "then", "true", "try", "type",
    "#binary", "#date", "#datetime", "#datetimezone", "#duration", "#infinity",
    "#nan", "#sections", "#shared", "#table", "#time",
}

# Reserved words that are values or constructors rather than syntax.
INTRINSICS = {
    "#binary", "#date", "#datetime", "#datetimezone", "#duration", "#infinity",
    "#nan", "#sections", "#shared", "#table", "#time",
}

# Longest first — the scan tries these in order, so `>=` must be offered before
# `>`, `=>` before `=`, and `...` before `..`.
#
# `..` is the list-range operator (`{1..10}`) and has to beat a lone `.`, which M
# has no use for outside a number. `<>` is inequality, not two brackets.
#
# `...` earns its place from real files rather than from the grammar: it marks an
# open record type (`type [a = text, ...]`), and Power Query writes it as the
# *whole body* of a parameter query. Lexed as `..` then `.` it produced a
# stray-character error and the query was swallowed by its neighbour.
OPERATORS = [
    "...", "=>", "<=", ">=", "<>", "..", "??",
    "=", "<", ">", "+", "-", "*", "/", "&", "@",
]

PUNCTUATORS = ["(", ")", "[", "]", "{", "}", ",", ";", "?"]

# The `#(…)` escape sequences legal inside a text literal.
#
# This matters far more than it looks. Native SQL embedded in M arrives with every
# newline and tab written as `#(lf)` and `#(tab)` — single string literals carrying
# two hundred lines of SQL. Decoding them here is what makes those sources
# readable, and it is why `Token.value` is the decoded text, not the raw slice.
CONTROL_ESCAPES = {"cr": "\r", "lf": "\n", "tab": "\t"}

# M identifiers allow letters, digits, `_`, and — unusually — dots.
# `Table.AddColumn` is one identifier, not a member access, which is why nothing
# here treats `.` as an operator. ASCII only, matching the reference regexes.
_IDENT_START = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_")
_IDENT_PART = _IDENT_START | set("0123456789.")
_HEX = set("0123456789ABCDEFabcdef")
_DIGITS = set("0123456789")
_HEX_ESCAPE = re.compile(r"^[0-9A-Fa-f]{4}$|^[0-9A-Fa-f]{8}$")


def _ch(text: str, i: int) -> str:
    """`text[i]`, or the empty string past either end.

    JavaScript returns `undefined` for an out-of-range index and every comparison
    against it is false; Python raises. Every lookahead in the lexer goes through
    here so the ported control flow stays identical.
    """
    return text[i] if 0 <= i < len(text) else ""


def _decode_text(body: str) -> str:
    """Decode the body of a text literal.

    `""` is one literal quote. `#(…)` is an escape whose body is either a control
    name (`lf`, `cr`, `tab`), a `#` (the literal `#(` sequence), or one or more
    comma-separated hex codepoints (`#(0085)`, `#(2028,2029)`).

    Anything unrecognised is passed through verbatim rather than dropped: this
    runs on pasted text, and a mangled escape should show up as itself instead of
    silently vanishing from the SQL someone is trying to read.
    """
    out: list[str] = []
    i = 0
    while i < len(body):
        ch = body[i]

        if ch == '"' and _ch(body, i + 1) == '"':
            out.append('"')
            i += 2
            continue

        if ch == "#" and _ch(body, i + 1) == "(":
            close = body.find(")", i + 2)
            if close == -1:
                out.append(ch)
                i += 1
                continue
            inner = body[i + 2 : close]

            if inner == "#":
                out.append("#(")
                i = close + 1
                continue

            decoded: list[str | None] = []
            for part in inner.split(","):
                name = part.strip()
                if name in CONTROL_ESCAPES:
                    decoded.append(CONTROL_ESCAPES[name])
                elif _HEX_ESCAPE.match(name):
                    decoded.append(chr(int(name, 16)))
                else:
                    decoded.append(None)

            if all(d is not None for d in decoded):
                out.append("".join(d for d in decoded if d is not None))
                i = close + 1
                continue

            out.append(ch)
            i += 1
            continue

        out.append(ch)
        i += 1

    return "".join(out)


@dataclass
class Token:
    """One lexical token.

    `value` is the *decoded* form — identifiers unescaped, text literals with their
    `#(lf)` expanded — and `raw` is exactly what the source said. `quoted` is kept
    because it is the only way to tell `#"Changed Type"` from a bare `Source` after
    unescaping, and a name has to be rendered back in the spelling M requires.
    """

    kind: str
    value: str
    raw: str
    start: int
    end: int
    line: int
    quoted: bool = False


@dataclass
class LexError:
    message: str
    start: int
    line: int


@dataclass
class TokenizeResult:
    tokens: list[Token]
    errors: list[LexError]


def tokenize(source: str) -> TokenizeResult:
    """Tokenize M source. Never raises.

    Comments are emitted rather than dropped, because a `// Query Name` line is
    how the most common paste format labels its queries — a lexer that swallowed
    comments would throw away every query name.

    Unterminated strings and stray characters are recorded as errors and the scan
    continues, so one bad query in a paste of thirty still leaves the other
    twenty-nine readable.
    """
    tokens: list[Token] = []
    errors: list[LexError] = []

    # A BOM and non-breaking spaces both survive a copy out of a browser or a Word
    # document, and neither is whitespace to a naive scan.
    text = re.sub(r"^\ufeff", "", source).replace("\u00a0", " ")

    i = 0
    line = 1

    def push(kind: str, start: int, start_line: int, value: str, quoted: bool = False) -> None:
        tokens.append(
            Token(kind, value, text[start:i], start, end=i, line=start_line, quoted=quoted)
        )

    while i < len(text):
        ch = text[i]

        if ch in (" ", "\t", "\r", "\n"):
            if ch == "\n":
                line += 1
            i += 1
            continue

        start = i
        start_line = line

        # Comments. `//` runs to end of line; `/* */` does not nest in M, so the
        # first `*/` closes it however many `/*` came before.
        if ch == "/" and _ch(text, i + 1) == "/":
            nl = text.find("\n", i)
            stop = len(text) if nl == -1 else nl
            body = text[i + 2 : stop]
            i = stop
            push("comment", start, start_line, body.strip())
            continue

        if ch == "/" and _ch(text, i + 1) == "*":
            close = text.find("*/", i + 2)
            if close == -1:
                body = text[i + 2 :]
                line += text.count("\n", i)
                i = len(text)
                errors.append(LexError("Unterminated block comment.", start, start_line))
                push("comment", start, start_line, body.strip())
                continue
            body = text[i + 2 : close]
            line += text.count("\n", i, close + 2)
            i = close + 2
            push("comment", start, start_line, body.strip())
            continue

        # Text literal. `""` inside is an escaped quote, so the scan cannot stop at
        # the first `"` it meets.
        if ch == '"':
            j = i + 1
            terminated = False
            while j < len(text):
                if text[j] == '"':
                    if _ch(text, j + 1) == '"':
                        j += 2
                        continue
                    terminated = True
                    j += 1
                    break
                j += 1
            body = text[i + 1 : (j - 1 if terminated else j)]
            line += text.count("\n", i, j)
            i = j
            if not terminated:
                errors.append(LexError("Unterminated text literal.", start, start_line))
            push("text", start, start_line, _decode_text(body))
            continue

        # Everything beginning `#`, which is four unrelated things:
        #   #"quoted identifier"   an identifier
        #   #(lf)                  a text escape — illegal out here, but pasted
        #                          fragments contain it, so it is tolerated
        #   #date #table #shared   an intrinsic
        #   #sections              likewise
        if ch == "#":
            if _ch(text, i + 1) == '"':
                j = i + 2
                terminated = False
                while j < len(text):
                    if text[j] == '"':
                        if _ch(text, j + 1) == '"':
                            j += 2
                            continue
                        terminated = True
                        j += 1
                        break
                    # A quoted identifier cannot span lines. Stopping here means a
                    # missing closing quote costs one identifier rather than the
                    # rest of the file.
                    if text[j] == "\n":
                        break
                    j += 1
                body = text[i + 2 : (j - 1 if terminated else j)]
                line += text.count("\n", i, j)
                i = j
                if not terminated:
                    errors.append(LexError("Unterminated quoted identifier.", start, start_line))
                push("identifier", start, start_line, body.replace('""', '"'), True)
                continue

            j = i + 1
            while j < len(text) and text[j] in _IDENT_PART:
                j += 1
            word = text[i:j]
            i = j
            if word in INTRINSICS:
                push("intrinsic", start, start_line, word)
            elif word in KEYWORDS:
                push("keyword", start, start_line, word)
            else:
                # `#(lf)` out in the open, or a `#foo` M does not define. Recorded
                # and emitted as an identifier so the parser can step over it.
                errors.append(LexError(f"Unrecognised `{word or '#'}`.", start, start_line))
                push("identifier", start, start_line, word)
            continue

        # Numbers. `0x` hex, decimal, and an exponent whose sign is part of the
        # number rather than a following operator.
        if ch in _DIGITS or (ch == "." and _ch(text, i + 1) in _DIGITS):
            j = i
            if ch == "0" and _ch(text, i + 1) in ("x", "X"):
                j = i + 2
                while j < len(text) and text[j] in _HEX:
                    j += 1
            else:
                while j < len(text) and text[j] in _DIGITS:
                    j += 1
                # Only take the `.` when a digit follows it. `1..10` is a list
                # range, and consuming the first dot greedily lexed it as the two
                # numbers `1.` and `.10` with no operator between them — a
                # silently wrong parse of every `{1..12}` in a model.
                if _ch(text, j) == "." and _ch(text, j + 1) in _DIGITS:
                    j += 1
                    while j < len(text) and text[j] in _DIGITS:
                        j += 1
                if _ch(text, j) in ("e", "E"):
                    k = j + 1
                    if _ch(text, k) in ("+", "-"):
                        k += 1
                    if _ch(text, k) in _DIGITS:
                        k += 1
                        while k < len(text) and text[k] in _DIGITS:
                            k += 1
                        j = k
            raw = text[i:j]
            i = j
            push("number", start, start_line, raw)
            continue

        if ch in _IDENT_START:
            j = i
            while j < len(text) and text[j] in _IDENT_PART:
                j += 1
            # A trailing dot belongs to no identifier — `Table.` alone is a typo,
            # and keeping the dot would make the name fail every library lookup.
            while j > i + 1 and text[j - 1] == ".":
                j -= 1
            word = text[i:j]
            i = j
            push("keyword" if word in KEYWORDS else "identifier", start, start_line, word)
            continue

        op = next((c for c in OPERATORS if text.startswith(c, i)), None)
        if op is not None:
            i += len(op)
            push("operator", start, start_line, op)
            continue

        if ch in PUNCTUATORS:
            i += 1
            push("punctuator", start, start_line, ch)
            continue

        i += 1
        errors.append(LexError(f"Unexpected character `{ch}`.", start, start_line))

    tokens.append(Token("eof", "", "", len(text), len(text), line))
    return TokenizeResult(tokens, errors)


def without_comments(tokens: list[Token]) -> list[Token]:
    """The token stream with comments removed — what the parser consumes."""
    return [t for t in tokens if t.kind != "comment"]


# ---------------------------------------------------------------------------
# AST
# ---------------------------------------------------------------------------
#
# Deliberately shallow. This is not an evaluator — nothing here will ever run an
# M expression — so the tree only needs to be precise about the things that decide
# a reference's meaning:
#
#   - **What introduces a name.** `let` bindings, function parameters, and `each`
#     are the three binders, and every one of them can shadow a query name.
#   - **What looks like a reference but is not.** Record field names and field
#     access names are plain strings; a tree that stored them as identifier nodes
#     would make the resolver's job impossible to do correctly.
#   - **Where a node came from.** Every node carries source offsets so a step's M
#     text can be sliced back out verbatim rather than pretty-printed from the tree
#     and quietly changed.
#
# Anything the resolver does not need collapses into `Unparsed`, which keeps its
# source span. That is the escape hatch that lets one unfamiliar construct cost a
# subexpression instead of a whole query.
#
# `type` on each node is a plain class attribute rather than a dataclass field, so
# it reads like the reference implementation's discriminant without appearing in
# every constructor call.


@dataclass
class Span:
    start: int
    end: int
    line: int


@dataclass
class Identifier:
    """A name in expression position.

    The only node kind the resolver treats as a possible reference to a step, a
    query, or a library function. `recursive` is M's `@name` marker — "the
    enclosing scope's name" — which cannot resolve to a library function.
    """

    span: Span
    name: str
    quoted: bool
    recursive: bool
    type = "Identifier"


@dataclass
class Literal:
    """`kind` is one of number, text, logical, null, intrinsic.

    `intrinsic` covers `#date(…)`, `#table(…)`, `#duration(…)` and friends. For
    text, `value` is the decoded content — native SQL is readable straight off it.
    """

    span: Span
    kind: str
    value: str
    type = "Literal"


@dataclass
class ListExpr:
    span: Span
    items: list[Any]
    type = "List"


@dataclass
class RecordField:
    name: str
    quoted: bool
    value: Any
    span: Span


@dataclass
class RecordExpr:
    """A record literal, `[a = 1, b = 2]`.

    Field names are `str`, not `Identifier`. They are not references and never
    were — `[Delimiter = ","]` does not reference anything called `Delimiter` — and
    the single most likely way to get M dependency analysis wrong is to count them.
    Making the type unable to express it is cheaper than remembering.
    """

    span: Span
    fields: list[RecordField]
    type = "Record"


@dataclass
class FieldAccess:
    """`value[Field]`, `value[Field]?`, or a bare `[Field]` inside an `each`.

    `field` is a `str` for the same reason record field names are. When `target` is
    None the lookup is against the implicit `_`, which is what makes
    `each [DateCalcd]` work. `projection` is set for `[[a], [b]]`.
    """

    span: Span
    target: Any
    field: str
    quoted: bool
    optional: bool
    projection: list[str] | None = None
    type = "FieldAccess"


@dataclass
class ItemAccess:
    """`value{0}` or `value{[Key = "x"]}` — positional or key-based item access."""

    span: Span
    target: Any
    selector: Any
    optional: bool
    type = "ItemAccess"


@dataclass
class Invocation:
    span: Span
    callee: Any
    args: list[Any]
    type = "Invocation"


@dataclass
class Binding:
    name: str
    quoted: bool
    value: Any
    #: The whole `name = value` span, for slicing the step's source.
    span: Span
    #: Just the value, for showing the step's expression without its name.
    value_span: Span


@dataclass
class LetExpr:
    """`let name = expr, … in result`.

    `bindings` is in source order, which for a Power Query query is step order. All
    bindings are in scope in every binding's body — M's `let` is mutually recursive
    — so a step legitimately references one below it, and the resolver must not
    assume otherwise.
    """

    span: Span
    bindings: list[Binding]
    body: Any
    type = "Let"


@dataclass
class EachExpr:
    """`each expr` — sugar for `(_) => expr`, and the reason `_` is implicit."""

    span: Span
    body: Any
    type = "Each"


@dataclass
class Param:
    name: str
    optional: bool


@dataclass
class FunctionExpr:
    span: Span
    params: list[Param]
    body: Any
    type = "Function"


@dataclass
class IfExpr:
    span: Span
    condition: Any
    then: Any
    otherwise: Any
    type = "If"


@dataclass
class BinaryExpr:
    span: Span
    operator: str
    left: Any
    right: Any
    type = "Binary"


@dataclass
class UnaryExpr:
    span: Span
    operator: str
    operand: Any
    type = "Unary"


@dataclass
class TryExpr:
    span: Span
    body: Any
    #: `otherwise` handler, or None for a bare `try`.
    otherwise: Any
    type = "Try"


@dataclass
class TypeExpr:
    """A `type …` expression, kept as one node with its source text.

    Not a modelled type tree. Type expressions contain field names in bracket
    position (`type table [Month = _t]`) and never contain a reference the graph
    cares about, so parsing their interior would only create chances to invent
    dependencies. `nested` carries any genuinely embedded expressions so an `_t`
    defined in an enclosing `let` still resolves.
    """

    span: Span
    text: str
    nested: list[Any]
    type = "Type"


@dataclass
class MetaExpr:
    """`value meta record` — how a parameter query declares itself."""

    span: Span
    value: Any
    meta: Any
    type = "Meta"


@dataclass
class ParenExpr:
    span: Span
    inner: Any
    type = "Paren"


@dataclass
class ErrorExpr:
    """`error expr` — a raise, not a failure of this parser. See `Unparsed`."""

    span: Span
    value: Any
    type = "Error"


@dataclass
class FoundIdentifier:
    name: str
    quoted: bool
    span: Span


@dataclass
class Unparsed:
    """A span this parser could not read, kept verbatim.

    The point of having it: a paste is arbitrary text, and the alternative to a
    fallback node is a parser that either throws away a whole query or invents an
    interpretation of something it does not understand. Both are worse than saying
    "there is an expression here, this is its text, I did not read inside it".

    `identifiers` holds the identifier tokens found inside, so an unparsed region
    still contributes its references. A dependency edge that survives a parse
    failure is worth more than a clean tree.
    """

    span: Span
    text: str
    identifiers: list[FoundIdentifier]
    reason: str
    type = "Unparsed"


def children(node: Any) -> list[Any]:
    """Every child node, in source order where it matters."""
    kind = node.type
    if kind == "List":
        return list(node.items)
    if kind == "Record":
        return [f.value for f in node.fields if f.value is not None]
    if kind == "FieldAccess":
        return [node.target] if node.target is not None else []
    if kind == "ItemAccess":
        return [node.target, node.selector]
    if kind == "Invocation":
        return [node.callee, *node.args]
    if kind == "Let":
        return [b.value for b in node.bindings] + [node.body]
    if kind == "Each":
        return [node.body]
    if kind == "Function":
        return [node.body]
    if kind == "If":
        return [node.condition, node.then, node.otherwise]
    if kind == "Binary":
        return [node.left, node.right]
    if kind == "Unary":
        return [node.operand]
    if kind == "Try":
        return [node.body, node.otherwise] if node.otherwise is not None else [node.body]
    if kind == "Type":
        return list(node.nested)
    if kind == "Meta":
        return [node.value, node.meta]
    if kind == "Paren":
        return [node.inner]
    if kind == "Error":
        return [node.value]
    return []


def walk(node: Any, visit: Callable[[Any], Any]) -> None:
    """Depth-first walk. `visit` returning False skips the node's children."""
    if node is None:
        return
    if visit(node) is False:
        return
    for child in children(node):
        walk(child, visit)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------
#
# Recursive descent, following the operator precedence in the M language
# specification, from lowest binding to highest:
#
#     meta  →  or  →  and  →  relational (= <> < <= > >=)  →  as / is
#           →  additive (+ -)  →  multiplicative (* /)  →  unary (- not)
#           →  postfix (invoke, field access, item access)  →  primary
#
# `if`, `let`, `each`, `try`, `type`, `error` and `(x) => e` are all *expressions*
# in M, not statements, so each is entered at the top of `parse_expression` rather
# than at a statement level — there isn't one.
#
# **This parser never raises.** Any construct it cannot read becomes an `Unparsed`
# node carrying the source text and the identifiers inside it, and a diagnostic is
# recorded. The input is arbitrary pasted text; a parser that gave up on a document
# because query nineteen had a typo would be useless for the job.

#: The two-token sequence that begins a function: `(` … `)` `=>`.
ARROW = "=>"

_WS_RUN = re.compile(r"^[ \t]+$")


@dataclass
class ParseError:
    message: str
    line: int
    start: int


@dataclass
class ParseResult:
    ast: Any
    errors: list[ParseError]
    #: Index into the comment-free token stream just past the expression that was
    #: read — i.e. the first token this parse did *not* consume.
    #:
    #: The document splitter needs this. Splitting a paste into queries relies on
    #: candidate boundaries taken from `// Name` comments, and the only trustworthy
    #: check that a boundary was real is whether the slice it produced parsed to
    #: exactly one complete expression with nothing left over. Without this number
    #: the splitter would have to trust the formatting of the paste.
    next: int
    #: Length of the comment-free token stream, so `next` can be compared to it.
    total: int


class _Parser:
    def __init__(self, source: str, tokens: list[Token]) -> None:
        self.source = source
        self.tokens = tokens
        self.pos = 0
        self.errors: list[ParseError] = []

    # -- token access -------------------------------------------------------

    def peek(self, offset: int = 0) -> Token:
        return self.tokens[min(self.pos + offset, len(self.tokens) - 1)]

    def tok(self, index: int) -> Token | None:
        return self.tokens[index] if 0 <= index < len(self.tokens) else None

    def at(self, kind: str, value: str | None = None) -> bool:
        t = self.peek()
        return t.kind == kind and (value is None or t.value == value)

    def take(self) -> Token:
        t = self.peek()
        if self.pos < len(self.tokens) - 1:
            self.pos += 1
        return t

    def eat(self, kind: str, value: str | None = None) -> Token | None:
        if self.at(kind, value):
            return self.take()
        return None

    def error(self, message: str, token: Token | None = None) -> None:
        # One diagnostic per position. A parser recovering inside a loop can
        # otherwise report the same confusion on every pass and bury the first,
        # genuinely useful message under fifty duplicates.
        t = self.peek() if token is None else token
        if any(e.start == t.start for e in self.errors):
            return
        self.errors.append(ParseError(message, t.line, t.start))

    def span_from(self, start_token: Token, end_token: Token | None = None) -> Span:
        end = self.tokens[max(self.pos - 1, 0)] if end_token is None else end_token
        return Span(start_token.start, end.end, start_token.line)

    # -- names --------------------------------------------------------------

    def read_field_name(self) -> dict | None:
        """Read a *generalized identifier* — a field name inside `[ … ]`.

        M has two kinds of name and this is the one that surprises people: inside
        brackets, **a name may contain spaces without being quoted**. `[Graph Date]`,
        `[Daily Backlog]` and `[Incoming Backlog]` are all single field names, and
        they are everywhere in generated M.

        The lexer cannot know this, because whether spaces are part of a name
        depends on being inside brackets, and a lexer that tracked that would need
        the parser's context. So the name is reassembled here: consume adjacent
        identifier, keyword and number tokens as long as **only spaces or tabs
        separate them**, then take the name from the source span. Requiring
        whitespace-only separation is what stops it running past a `=`, a `,` or a
        `]`, and requiring no newline is what keeps it on one line, as the grammar
        demands.

        A `#"quoted"` name is returned as-is and never extended — quoting is the
        explicit form and already carries its own spaces.
        """
        first = self.peek()
        if first.kind not in ("identifier", "keyword", "number"):
            return None
        self.take()
        if first.quoted:
            return {"name": first.value, "quoted": True, "first": first, "last": first}

        last = first
        while True:
            nxt = self.peek()
            if nxt.kind not in ("identifier", "keyword", "number"):
                break
            if nxt.quoted:
                break
            if not _WS_RUN.match(self.source[last.end : nxt.start]):
                break
            self.take()
            last = nxt

        return {
            "name": self.source[first.start : last.end],
            "quoted": False,
            "first": first,
            "last": last,
        }

    def bracket_opens(self) -> str:
        """Decide what a `[` opens, by looking for the first `=` or `]` at depth one.

        `[a = 1]` is a record literal. `[Field]` is a field access — against the
        implicit `_` when nothing precedes it, which is what makes `each [Amount]`
        work. `[]` is the empty record. `[[A], [B]]` is a projection.

        They cannot be told apart from the `[` alone, and guessing wrong is not a
        cosmetic error: reading `[Graph Date]` as a record invents a field named
        "Graph Date" and loses a real field access, while reading
        `[ApiVersion = 15]` as an access invents a reference to something called
        `ApiVersion`.
        """
        k = self.pos + 1
        head = self.tok(k)
        if head is not None and head.kind == "punctuator" and head.value == "]":
            return "empty"
        if head is not None and head.kind == "punctuator" and head.value == "[":
            return "projection"

        depth = 1
        while k < len(self.tokens):
            t = self.tokens[k]
            if t.kind == "eof":
                break
            if t.kind == "punctuator":
                if t.value in ("[", "(", "{"):
                    depth += 1
                elif t.value in ("]", ")", "}"):
                    depth -= 1
                    if depth == 0:
                        return "access"
            elif t.kind == "operator" and t.value == "=" and depth == 1:
                return "record"
            elif t.kind == "operator" and t.value == "..." and depth == 1:
                # An open record type, `[a = text, ...]`, whose first field may
                # still be ahead of the `...` — but a bare `[...]` is a record
                # either way.
                return "record"
            k += 1
        return "record"

    # -- recovery -----------------------------------------------------------

    def unparsed(self, from_: int, to: int, reason: str) -> Unparsed:
        """Wrap a region as `Unparsed`, harvesting the identifiers inside it.

        The harvest is what makes recovery worth having: a region this parser could
        not read still yields the query names it mentions, so a dependency edge
        survives a construct the grammar missed.
        """
        chunk = self.tokens[from_:to]
        first = chunk[0] if chunk else self.peek()
        last = chunk[-1] if chunk else first
        return Unparsed(
            span=Span(first.start, last.end, first.line),
            text=self.source[first.start : last.end],
            identifiers=[
                FoundIdentifier(t.value, bool(t.quoted), Span(t.start, t.end, t.line))
                for t in chunk
                if t.kind == "identifier"
            ],
            reason=reason,
        )

    def recover_until(self, stops: list[str], reason: str) -> Unparsed:
        """Consume tokens until a delimiter at the current nesting depth.

        Depth-aware on purpose: a `,` inside a nested record must not be mistaken
        for the one that ends the outer argument, which is the failure mode of every
        "split on commas" approach to M.
        """
        from_ = self.pos
        depth = 0
        while not self.at("eof"):
            t = self.peek()
            if t.kind == "punctuator":
                if t.value in ("(", "[", "{"):
                    depth += 1
                elif t.value in (")", "]", "}"):
                    if depth == 0 and t.value in stops:
                        break
                    depth -= 1
                elif depth == 0 and t.value in stops:
                    break
            elif depth == 0 and t.kind == "keyword" and t.value in stops:
                break
            self.take()
        # Never return an empty region: a zero-width Unparsed in a loop that
        # expects forward progress is an infinite loop.
        if self.pos == from_:
            self.take()
        return self.unparsed(from_, self.pos, reason)

    # -- precedence ladder --------------------------------------------------

    def parse_expression(self) -> Any:
        return self.parse_meta()

    def parse_meta(self) -> Any:
        """`value meta record` — lowest precedence, and how parameter queries
        declare themselves."""
        left = self.parse_coalesce()
        while self.at("keyword", "meta"):
            start = left
            self.take()
            meta = self.parse_coalesce()
            left = MetaExpr(Span(start.span.start, meta.span.end, start.span.line), start, meta)
        return left

    def parse_coalesce(self) -> Any:
        """`a ?? b` — the left operand unless it is null.

        Right-associative, per the grammar: `a ?? b ?? c` is `a ?? (b ?? c)`. Sitting
        between `meta` and `or` is what the specification says, and it matters: with the
        levels swapped, `a ?? b or c` would group the wrong way.

        Missing this entirely cost whole files. The tokenizer split `??` into two `?`
        punctuators, the expression parser stopped at the first one, and the document
        splitter -- which requires a paste to parse end to end before it will call it a
        query -- returned nothing at all. One modern operator anywhere in a model and
        the answer was "no queries found".
        """
        left = self.parse_or()
        if self.at("operator", "??"):
            self.take()
            right = self.parse_coalesce()
            return self._binary("??", left, right)
        return left

    def parse_or(self) -> Any:
        left = self.parse_and()
        while self.at("keyword", "or"):
            self.take()
            left = self._binary("or", left, self.parse_and())
        return left

    def parse_and(self) -> Any:
        left = self.parse_relational()
        while self.at("keyword", "and"):
            self.take()
            left = self._binary("and", left, self.parse_relational())
        return left

    def parse_relational(self) -> Any:
        left = self.parse_as_is()
        while any(
            self.at("operator", op) for op in ("=", "<>", "<", "<=", ">", ">=")
        ):
            op = self.take().value
            left = self._binary(op, left, self.parse_as_is())
        return left

    def parse_as_is(self) -> Any:
        """`x as number`, `x is text`.

        The right side is a *type*, not an expression, so it goes through
        `parse_type_operand` — otherwise `x as table` would read `table` as an
        identifier reference to a query called "table".
        """
        left = self.parse_additive()
        while self.at("keyword", "as") or self.at("keyword", "is"):
            op = self.take().value
            left = self._binary(op, left, self.parse_type_operand())
        return left

    def parse_additive(self) -> Any:
        left = self.parse_multiplicative()
        while any(self.at("operator", op) for op in ("+", "-", "&")):
            op = self.take().value
            left = self._binary(op, left, self.parse_multiplicative())
        return left

    def parse_multiplicative(self) -> Any:
        left = self.parse_unary()
        while any(self.at("operator", op) for op in ("*", "/")):
            op = self.take().value
            left = self._binary(op, left, self.parse_unary())
        return left

    def parse_unary(self) -> Any:
        if self.at("operator", "-") or self.at("operator", "+") or self.at("keyword", "not"):
            t = self.take()
            operand = self.parse_unary()
            return UnaryExpr(Span(t.start, operand.span.end, t.line), t.value, operand)
        return self.parse_postfix()

    def parse_postfix(self) -> Any:
        """Invocation, field access and item access, left-associative and interleaved.

        `#"Filtered Rows"{0}[Content]` is item access then field access on the
        result, so this has to loop rather than handle one of each.
        """
        expr = self.parse_primary()

        while True:
            if self.at("punctuator", "("):
                open_token = self.take()
                args: list[Any] = []
                if not self.at("punctuator", ")"):
                    while True:
                        args.append(self.parse_argument())
                        if self.eat("punctuator", ","):
                            continue
                        break
                if not self.eat("punctuator", ")"):
                    self.error("Expected `)` to close the argument list.")
                expr = Invocation(
                    Span(expr.span.start, self.span_from(open_token).end, expr.span.line),
                    expr,
                    args,
                )
                continue

            if self.at("punctuator", "["):
                expr = self.parse_bracket_after(expr)
                continue

            if self.at("punctuator", "{"):
                open_token = self.take()
                selector = (
                    self.recover_until(["}"], "Empty item selector.")
                    if self.at("punctuator", "}")
                    else self.parse_expression()
                )
                if not self.eat("punctuator", "}"):
                    self.error("Expected `}` to close the item selector.")
                optional = self.eat("punctuator", "?") is not None
                expr = ItemAccess(
                    Span(expr.span.start, self.span_from(open_token).end, expr.span.line),
                    expr,
                    selector,
                    optional,
                )
                continue

            return expr

    def parse_argument(self) -> Any:
        """One argument in an invocation.

        On a parse failure this recovers to the next `,` or `)` rather than
        abandoning the call, so `Table.AddColumn(prev, "X", each <something odd>)`
        still yields a recognised `Table.AddColumn` with three arguments — which is
        what the operation label and the column-delta analysis read.
        """
        from_ = self.pos
        try:
            return self.parse_expression()
        except Exception:
            self.pos = from_
            self.error("Could not read this argument.")
            return self.recover_until([",", ")"], "Unreadable argument.")

    def parse_bracket_after(self, target: Any) -> Any:
        """`[` in postfix or primary position.

        `target` is None for a bare `[Field]`, which is a lookup against the
        implicit `_` — the form `each [Amount]` relies on. A record literal reached
        here is delegated to `parse_record`, since the two are only distinguishable
        by lookahead (`bracket_opens`).
        """
        kind = self.bracket_opens()
        if kind in ("record", "empty"):
            return self.parse_record()

        open_token = self.take()
        start_span = (
            target.span
            if target is not None
            else Span(open_token.start, open_token.end, open_token.line)
        )

        if kind == "projection":
            projection: list[str] = []
            while self.at("punctuator", "["):
                self.take()
                name = self.read_field_name()
                if name:
                    projection.append(name["name"])
                if not self.eat("punctuator", "]"):
                    self.error("Expected `]` in the projection.")
                if self.eat("punctuator", ","):
                    continue
                break
            if not self.eat("punctuator", "]"):
                self.error("Expected `]` to close the projection.")
            optional = self.eat("punctuator", "?") is not None
            return FieldAccess(
                Span(start_span.start, self.span_from(open_token).end, start_span.line),
                target,
                projection[0] if projection else "",
                False,
                optional,
                projection,
            )

        name = self.read_field_name()
        if not name:
            self.error("Expected a field name after `[`.")
            region = self.recover_until(["]"], "Unreadable field name.")
            self.eat("punctuator", "]")
            return FieldAccess(
                Span(start_span.start, self.span_from(open_token).end, start_span.line),
                target,
                region.text,
                False,
                False,
            )
        if not self.eat("punctuator", "]"):
            self.error("Expected `]` to close the field name.")
        optional = self.eat("punctuator", "?") is not None
        return FieldAccess(
            Span(start_span.start, self.span_from(open_token).end, start_span.line),
            target,
            name["name"],
            name["quoted"],
            optional,
        )

    def parse_primary(self) -> Any:
        t = self.peek()

        if t.kind == "keyword":
            if t.value == "let":
                return self.parse_let()
            if t.value == "each":
                return self.parse_each()
            if t.value == "if":
                return self.parse_if()
            if t.value == "try":
                return self.parse_try()
            if t.value == "type":
                return self.parse_type_expression()
            if t.value == "error":
                self.take()
                value = self.parse_expression()
                return ErrorExpr(Span(t.start, value.span.end, t.line), value)
            if t.value in ("true", "false"):
                self.take()
                return Literal(self.span_from(t), "logical", t.value)
            if t.value == "null":
                self.take()
                return Literal(self.span_from(t), "null", "null")
            if t.value == "not":
                self.take()
                operand = self.parse_unary()
                return UnaryExpr(Span(t.start, operand.span.end, t.line), "not", operand)
            if t.value == "section":
                # A `section` header reached in expression position means the caller
                # handed a whole section document to the expression parser. The
                # document splitter strips it; recovering here keeps that a
                # diagnostic rather than a crash.
                self.error("Unexpected `section` — this is a section document, not an expression.")
                return self.recover_until([";"], "Section header in expression position.")

        elif t.kind == "intrinsic":
            # `#table(…)`, `#date(…)`, `#duration(…)` — a value with an argument
            # list, so it is left for `parse_postfix` to invoke. `#shared` and
            # `#sections` take no arguments and simply stand alone.
            self.take()
            return Literal(self.span_from(t), "intrinsic", t.value)

        elif t.kind == "number":
            self.take()
            return Literal(self.span_from(t), "number", t.value)

        elif t.kind == "text":
            self.take()
            return Literal(self.span_from(t), "text", t.value)

        elif t.kind == "identifier":
            self.take()
            return Identifier(self.span_from(t), t.value, bool(t.quoted), False)

        elif t.kind == "operator":
            if t.value == "...":
                # A parameter query's whole body, as Power Query writes it:
                # `... meta [IsParameterQuery=true, …]`. Not in the grammar as an
                # expression, but it is what the file says, and treating it as a
                # value is what lets the analyser see the `meta` record and classify
                # the query as a parameter.
                self.take()
                return Literal(self.span_from(t), "intrinsic", "...")
            if t.value == "@":
                # `@Name` — an explicit recursive reference to the enclosing binding.
                self.take()
                name = self.eat("identifier")
                if not name:
                    self.error("Expected a name after `@`.")
                    return self.unparsed(self.pos - 1, self.pos, "Dangling `@`.")
                return Identifier(
                    Span(t.start, name.end, t.line), name.value, bool(name.quoted), True
                )

        elif t.kind == "punctuator":
            if t.value == "(":
                return self.parse_paren_or_function()
            if t.value == "{":
                return self.parse_list()
            # A `[` with nothing before it is either a record literal or a lookup
            # against the implicit `_`. `parse_bracket_after(None)` decides; routing
            # straight to `parse_record` here was the single biggest source of wrong
            # parses, because `each [Amount]` is far more common than a bare record.
            if t.value == "[":
                return self.parse_bracket_after(None)

        self.error(f"Unexpected `{t.raw or t.kind}`.")
        return self.recover_until(
            [",", ")", "]", "}", ";", "in", "then", "else"], "Unreadable expression."
        )

    def parse_paren_or_function(self) -> Any:
        """`(` … `)` is either a parenthesised expression or a function's parameter list.

        Which one is only knowable by looking past the matching `)` for a `=>`, so
        this scans ahead with depth tracking first. Guessing from the contents fails
        on `(x)` — a valid parenthesised identifier *and* a valid one-parameter list.
        """
        open_token = self.peek()

        depth = 0
        k = self.pos
        while k < len(self.tokens):
            t = self.tokens[k]
            if t.kind == "punctuator" and t.value in ("(", "[", "{"):
                depth += 1
            elif t.kind == "punctuator" and t.value in (")", "]", "}"):
                depth -= 1
                if depth == 0:
                    break
            elif t.kind == "eof":
                break
            k += 1
        # A return-type ascription sits between `)` and `=>`, so the token after the
        # matching `)` is then `as` and not the arrow. Testing only that one token sent
        # `(x as number) as number => x * 2` down the parenthesised branch, which then
        # stopped at the `=>` and reported no query at all -- even though the function
        # branch below has always handled the ascription. It never reached it.
        #
        # The grammar bounds a return type at `as nullable? primitive-type`, so this
        # looks past at most three tokens. Staying inside that bound is what keeps a
        # genuine type assertion on a parenthesised expression, `(a) as number`, falling
        # through to the paren branch: nothing is consumed here, only looked at.
        after_index = k + 1
        probe = self.tok(after_index)
        if probe is not None and probe.kind == "keyword" and probe.value == "as":
            j = after_index + 1
            nxt = self.tok(j)
            if nxt is not None and nxt.kind == "keyword" and nxt.value == "nullable":
                j += 1
                nxt = self.tok(j)
            if nxt is not None and (
                nxt.kind in ("identifier", "intrinsic")
                or (nxt.kind == "keyword" and nxt.value in ("type", "null"))
            ):
                after_index = j + 1
        after = self.tok(after_index)
        is_function = after is not None and after.kind == "operator" and after.value == ARROW

        if not is_function:
            self.take()
            inner = (
                self.recover_until([")"], "Empty parentheses.")
                if self.at("punctuator", ")")
                else self.parse_expression()
            )
            if not self.eat("punctuator", ")"):
                self.error("Expected `)`.")
            return ParenExpr(
                Span(open_token.start, self.span_from(open_token).end, open_token.line), inner
            )

        self.take()
        params: list[Param] = []
        while not self.at("punctuator", ")") and not self.at("eof"):
            optional = self.eat("keyword", "optional") is not None
            name = self.eat("identifier")
            if name:
                params.append(Param(name.value, optional))
                # `(Parameter1 as binary) =>` — the type ascription is not a
                # reference.
                if self.eat("keyword", "as"):
                    self.parse_type_operand()
            else:
                self.error("Expected a parameter name.")
                self.recover_until([",", ")"], "Unreadable parameter.")
            if self.eat("punctuator", ","):
                continue
            break
        if not self.eat("punctuator", ")"):
            self.error("Expected `)` to close the parameter list.")
        # A return-type ascription sits between `)` and `=>`.
        if self.eat("keyword", "as"):
            self.parse_type_operand()
        if not self.eat("operator", ARROW):
            self.error("Expected `=>` after the parameter list.")
        body = self.parse_expression()
        return FunctionExpr(Span(open_token.start, body.span.end, open_token.line), params, body)

    def parse_list(self) -> Any:
        open_token = self.take()
        items: list[Any] = []
        if not self.at("punctuator", "}"):
            while True:
                items.append(self.parse_list_item())
                if self.eat("punctuator", ","):
                    continue
                break
        if not self.eat("punctuator", "}"):
            self.error("Expected `}` to close the list.")
        return ListExpr(
            Span(open_token.start, self.span_from(open_token).end, open_token.line), items
        )

    def parse_list_item(self) -> Any:
        """A list item, or a `{1..10}` range whose two ends are both expressions."""
        left = self.parse_expression()
        if self.at("operator", ".."):
            self.take()
            return self._binary("..", left, self.parse_expression())
        return left

    def parse_record(self) -> Any:
        """A record literal, `[a = 1, b = 2]`, and the `[]` empty record.

        The field names captured here are strings by design. `[ApiVersion = 15]`
        must never contribute a reference to something named `ApiVersion`.
        """
        open_token = self.take()
        fields: list[RecordField] = []

        if not self.at("punctuator", "]"):
            while True:
                # `...` marks an open record type. It names no field, so it is
                # consumed and skipped rather than recorded as one.
                if self.eat("operator", "..."):
                    if self.eat("punctuator", ","):
                        continue
                    break

                name_token = self.read_field_name()
                if not name_token:
                    self.error("Expected a field name in the record.")
                    region = self.recover_until([",", "]"], "Unreadable record field.")
                    fields.append(RecordField(region.text, False, region, region.span))
                elif self.eat("operator", "="):
                    value = self.parse_record_value()
                    fields.append(
                        RecordField(
                            name_token["name"],
                            name_token["quoted"],
                            value,
                            Span(
                                name_token["first"].start,
                                value.span.end,
                                name_token["first"].line,
                            ),
                        )
                    )
                else:
                    # `[Name]` with no `=`: a record *type*'s field, or a malformed
                    # record. Either way the name is a name and there is no value.
                    fields.append(
                        RecordField(
                            name_token["name"],
                            name_token["quoted"],
                            None,
                            Span(
                                name_token["first"].start,
                                name_token["last"].end,
                                name_token["first"].line,
                            ),
                        )
                    )
                if self.eat("punctuator", ","):
                    continue
                break

        if not self.eat("punctuator", "]"):
            self.error("Expected `]` to close the record.")
        return RecordExpr(
            Span(open_token.start, self.span_from(open_token).end, open_token.line), fields
        )

    def parse_record_value(self) -> Any:
        from_ = self.pos
        try:
            return self.parse_expression()
        except Exception:
            self.pos = from_
            return self.recover_until([",", "]"], "Unreadable record value.")

    def parse_let(self) -> Any:
        """`let name = expr, … in result`.

        Two things a naive reading gets wrong. **A binding name can be quoted** —
        `#"Changed Type" = …` is the norm in generated M, not the exception. And **a
        missing comma must not end the `let`**: recovery steps to the next plausible
        binding start so one malformed step costs one step.
        """
        open_token = self.take()
        bindings: list[Binding] = []

        while True:
            if self.at("keyword", "in") or self.at("eof"):
                break

            name_token = self.peek()
            if name_token.kind != "identifier":
                self.error("Expected a step name in the `let`.")
                self.recover_until([",", "in"], "Unreadable step name.")
                if self.eat("punctuator", ","):
                    continue
                break
            self.take()

            if not self.eat("operator", "="):
                self.error(f"Expected `=` after the step name `{name_token.value}`.")
                region = self.recover_until([",", "in"], "Step with no `=`.")
                bindings.append(
                    Binding(
                        name_token.value,
                        bool(name_token.quoted),
                        region,
                        Span(name_token.start, region.span.end, name_token.line),
                        region.span,
                    )
                )
                if self.eat("punctuator", ","):
                    continue
                break

            value_start = self.peek()
            value = self.parse_binding_value()
            bindings.append(
                Binding(
                    name_token.value,
                    bool(name_token.quoted),
                    value,
                    Span(name_token.start, value.span.end, name_token.line),
                    Span(value_start.start, value.span.end, value_start.line),
                )
            )

            if self.eat("punctuator", ","):
                continue
            break

        if not self.eat("keyword", "in"):
            self.error("Expected `in` to close the `let`.")
        body = (
            self.unparsed(self.pos, self.pos + 1, "Missing `let` result.")
            if self.at("eof")
            else self.parse_expression()
        )

        return LetExpr(Span(open_token.start, body.span.end, open_token.line), bindings, body)

    def parse_binding_value(self) -> Any:
        from_ = self.pos
        try:
            return self.parse_expression()
        except Exception:
            self.pos = from_
            self.error("Could not read this step.")
            return self.recover_until([",", "in"], "Unreadable step.")

    def parse_each(self) -> Any:
        """`each expr` — sugar for `(_) => expr`. The resolver binds `_` for the body."""
        open_token = self.take()
        body = self.parse_expression()
        return EachExpr(Span(open_token.start, body.span.end, open_token.line), body)

    def parse_if(self) -> Any:
        open_token = self.take()
        condition = self.parse_expression()
        if not self.eat("keyword", "then"):
            self.error("Expected `then`.")
        then_branch = self.parse_expression()
        if not self.eat("keyword", "else"):
            self.error("Expected `else` — M has no one-armed `if`.")
        else_branch = self.parse_expression()
        return IfExpr(
            Span(open_token.start, else_branch.span.end, open_token.line),
            condition,
            then_branch,
            else_branch,
        )

    def parse_try(self) -> Any:
        open_token = self.take()
        body = self.parse_expression()
        otherwise = None
        if self.eat("keyword", "otherwise"):
            otherwise = self.parse_expression()
        end = (otherwise if otherwise is not None else body).span.end
        return TryExpr(Span(open_token.start, end, open_token.line), body, otherwise)

    def parse_type_expression(self) -> Any:
        """A `type …` expression, kept as source text plus any nested expressions.

        The interior is deliberately not modelled. `type table [#"Rounded PC" = _t]`
        has a field name in bracket position and a reference to `_t`, a step defined
        in the enclosing `let`. Reading the field names as references would invent
        dependencies; ignoring the interior entirely would lose `_t`. So the names
        are skipped and the values are kept.
        """
        open_token = self.take()
        nested: list[Any] = []

        if self.at("punctuator", "["):
            record = self.parse_record()
            if record.type == "Record":
                nested.extend(f.value for f in record.fields if f.value is not None)
        elif self.at("punctuator", "{"):
            lst = self.parse_list()
            if lst.type == "List":
                nested.extend(lst.items)
        elif self.at("punctuator", "("):
            nested.append(self.parse_paren_or_function())
        elif self.at("identifier") or self.at("keyword"):
            # `type table`, `type nullable text`, `type any`. The primitive name is
            # part of the type, not a reference to a query — stepping over it is the
            # whole point of having this branch.
            while self.at("keyword", "nullable") or self.at("keyword", "optional"):
                self.take()
            self.take()
            if self.at("punctuator", "["):
                record = self.parse_record()
                if record.type == "Record":
                    nested.extend(f.value for f in record.fields if f.value is not None)
            elif self.at("punctuator", "{"):
                lst = self.parse_list()
                if lst.type == "List":
                    nested.extend(lst.items)

        span = Span(open_token.start, self.span_from(open_token).end, open_token.line)
        return TypeExpr(span, self.source[span.start : span.end], nested)

    def parse_type_operand(self) -> Any:
        """The type on the right of `as` / `is`, or on a parameter.

        Accepts a bare primitive (`binary`, `number`, `nullable text`) as well as a
        full `type …`, because both spellings are legal there and the bare one is
        what generated M writes: `(Parameter1 as binary) =>`.
        """
        if self.at("keyword", "type"):
            return self.parse_type_expression()

        open_token = self.peek()
        while self.at("keyword", "nullable") or self.at("keyword", "optional"):
            self.take()
        if self.at("identifier") or self.at("keyword"):
            self.take()
        else:
            self.error("Expected a type.")

        nested: list[Any] = []
        if self.at("punctuator", "["):
            record = self.parse_record()
            if record.type == "Record":
                nested.extend(f.value for f in record.fields if f.value is not None)

        span = Span(open_token.start, self.span_from(open_token).end, open_token.line)
        return TypeExpr(span, self.source[span.start : span.end], nested)

    def _binary(self, operator: str, left: Any, right: Any) -> BinaryExpr:
        return BinaryExpr(
            Span(left.span.start, right.span.end, left.span.line), operator, left, right
        )


def parse_expression(source: str) -> ParseResult:
    """Parse one M expression. Never raises; unreadable regions become `Unparsed`."""
    result = tokenize(source)
    return parse_tokens(source, result.tokens, result.errors)


def parse_tokens(
    source: str, tokens: list[Token], lex_errors: list[LexError] | None = None
) -> ParseResult:
    """Parse from an already-tokenized source, so the splitter lexes only once."""
    lex_errors = lex_errors or []
    stream = without_comments(tokens)
    # One token means the stream is just the EOF sentinel: nothing to parse.
    if len(stream) <= 1:
        return ParseResult(
            ast=None,
            errors=[ParseError(e.message, e.line, e.start) for e in lex_errors],
            next=0,
            total=max(len(stream) - 1, 0),
        )

    parser = _Parser(source, stream)
    ast = parser.parse_expression()
    errors = [ParseError(e.message, e.line, e.start) for e in lex_errors] + parser.errors
    return ParseResult(ast=ast, errors=errors, next=parser.pos, total=len(stream) - 1)


# ---------------------------------------------------------------------------
# The M standard library
# ---------------------------------------------------------------------------
#
# Three separate jobs, kept together because they all key off a function name:
#
# 1. **Is this name a library function** rather than a reference to a query? The
#    resolver needs this to avoid reporting `Table.AddColumn` as an unresolved
#    dependency in every query in the model.
# 2. **What operation is this step**, in the words Power Query itself uses. A step
#    called `#"Changed Type"` is self-describing; one called `Source` or
#    `#"Added Custom"` is not, and neither is a hand-written step name.
# 3. **Is this a data source**, and if so what is it connecting to — which argument
#    holds the server, the DSN, the URL, the native query.
#
# Coverage is "what turns up in real models", not the whole library. An unknown
# function is not an error: it falls back to its own name, and `namespace_of` still
# classifies it. That matters because the library grows, connectors are added every
# month, and a tool that only understood a fixed list would rot.

# Library namespaces — the part before the dot.
#
# Used as the fallback test for "is this a library function". A dotted name whose
# namespace is on this list is library; a dotted name whose namespace is not is
# still *probably* library, since M has no user-defined namespaces, but keeping the
# list means `Sales.Amount` (someone's oddly-named query) is not silently
# classified away.
NAMESPACES = {
    # Core data types and their functions
    "Table", "List", "Record", "Text", "Number", "Date", "DateTime", "DateTimeZone",
    "Time", "Duration", "Logical", "Binary", "Value", "Type", "Function", "Guid",
    "Uri", "Character", "Expression", "Error", "Diagnostics", "Lines", "Precision",
    # Format and parse helpers
    "Csv", "Json", "Xml", "Html", "BinaryFormat", "TextEncoding", "BinaryEncoding",
    "Compression", "ByteOrder", "Combiner", "Splitter", "Comparer", "Replacer",
    "Percentage", "Currency",
    # Enumerations that read like namespaces
    "JoinKind", "JoinAlgorithm", "Occurrence", "RelativePosition", "ExtraValues",
    "MissingField", "QuoteStyle", "Order", "RoundingMode", "Day", "GroupKind",
    "CsvStyle", "TraceLevel", "ExcelStyle", "WebMethod", "Culture", "PctIn",
    "Int8", "Int16", "Int32", "Int64", "Single", "Double", "Decimal", "Byte",
    # Connectors
    "Sql", "Odbc", "OleDb", "Access", "Excel", "SharePoint", "Folder", "File",
    "Web", "Hdfs", "AzureStorage", "DataLake", "Lakehouse", "Fabric", "PowerBI",
    "PowerPlatform", "Dataverse", "AnalysisServices", "Cube", "Essbase", "Sap",
    "SapBusinessWarehouse", "SapHana", "Oracle", "MySQL", "PostgreSQL", "Teradata",
    "Sybase", "Informix", "Db2", "Snowflake", "Databricks", "GoogleBigQuery",
    "GoogleAnalytics", "AmazonRedshift", "Athena", "Exchange", "Salesforce",
    "Dynamics", "ActiveDirectory", "Odata", "OData", "Soda", "Kusto",
    "AzureDataExplorer", "DocumentDb", "CommonDataService", "Entity", "Sharepoint",
    "Mailchimp", "Github",
}

# Library functions with no dot in the name.
#
# Short, and worth being exact about: an undotted name that is *not* here and not
# in scope is a genuinely unresolved reference, which is a diagnostic worth
# showing. Bare type names are included because `x as number` and
# `type nullable text` both put them in identifier position.
BARE_LIBRARY = {
    "any", "anynonnull", "binary", "date", "datetime", "datetimezone", "duration",
    "function", "list", "logical", "none", "number", "record", "table", "text",
    "time", "type",
    "Byte", "Currency", "Decimal", "Double", "Guid", "Percentage", "Single",
}


def namespace_of(name: str) -> str:
    """`Table.AddColumn` -> `Table`. Empty string for an undotted name."""
    dot = name.find(".")
    return "" if dot == -1 else name[:dot]


def is_library_function(name: str) -> bool:
    """True when a name belongs to the M library rather than to the model.

    Checked *after* lexical scope and *after* the set of query names, never before:
    a query may legitimately be called `Date` or `Value`, and the model's own names
    have to win.
    """
    if name in BARE_LIBRARY:
        return True
    ns = namespace_of(name)
    return ns != "" and ns in NAMESPACES


@dataclass
class Operation:
    """What a step's operation does to the table.

    `shape` is deliberately coarse. The question a modeller asks of a step list is
    "where do my columns come from and where does this stop folding", and that
    needs to know whether a step adds columns or filters rows — not the exact
    semantics of `Table.TransformColumns`.

    `fold_break` is stated as a *reason* rather than a boolean because folding must
    be presented as an estimate. Whether a step folds truly depends on the
    connector, the arguments and the gateway; a tool that printed "does not fold"
    as a fact would be confidently wrong often enough to mislead. A reason invites
    the reader to judge.
    """

    label: str
    shape: str
    fold_break: str | None


# The functions that turn up in generated M, with the step name Power Query would
# have given them.
#
# Where the Editor's default step name is the giveaway ("Filtered Rows", "Changed
# Type", "Merged Queries"), that name is used verbatim: a modeller already reads
# those words and inventing new ones would be a worse label for being more precise.
OPERATIONS: dict[str, Operation] = {
    # Column shape
    "Table.AddColumn": Operation(
        "Added column",
        "addColumn",
        # A custom column body is arbitrary M evaluated per row. Some cases do fold
        # (a simple arithmetic projection over a SQL source), which is exactly why
        # this is a reason and not a verdict.
        "A custom column body is arbitrary M and usually cannot be translated to the source.",
    ),
    "Table.AddIndexColumn": Operation(
        "Added index", "addColumn", "An index column has no source-side equivalent."
    ),
    "Table.AddKey": Operation("Added key", "other", None),
    "Table.DuplicateColumn": Operation("Duplicated column", "addColumn", None),
    "Table.RemoveColumns": Operation("Removed columns", "removeColumn", None),
    "Table.SelectColumns": Operation("Removed other columns", "selectColumn", None),
    "Table.RenameColumns": Operation("Renamed columns", "renameColumn", None),
    "Table.ReorderColumns": Operation("Reordered columns", "other", None),
    "Table.TransformColumnTypes": Operation("Changed type", "retypeColumn", None),
    "Table.TransformColumns": Operation("Transformed columns", "retypeColumn", None),
    "Table.TransformColumnNames": Operation("Renamed columns", "renameColumn", None),
    "Table.DemoteHeaders": Operation(
        "Demoted headers", "shape", "Header promotion happens after the data is read."
    ),
    "Table.PromoteHeaders": Operation(
        "Promoted headers", "shape", "Header promotion happens after the data is read."
    ),
    # Rows
    "Table.SelectRows": Operation("Filtered rows", "filterRow", None),
    "Table.RemoveRows": Operation("Removed rows", "filterRow", None),
    "Table.RemoveFirstN": Operation("Removed top rows", "filterRow", None),
    "Table.RemoveLastN": Operation(
        "Removed bottom rows", "filterRow", "Counting from the end requires the whole result."
    ),
    "Table.FirstN": Operation("Kept first rows", "filterRow", None),
    "Table.LastN": Operation(
        "Kept last rows", "filterRow", "Counting from the end requires the whole result."
    ),
    "Table.Range": Operation("Kept range of rows", "filterRow", None),
    "Table.Distinct": Operation("Removed duplicates", "filterRow", None),
    "Table.Sort": Operation("Sorted rows", "sortRow", None),
    "Table.SelectRowsWithErrors": Operation(
        "Kept errors", "filterRow", "Error rows only exist after evaluation."
    ),
    "Table.RemoveRowsWithErrors": Operation(
        "Removed errors", "filterRow", "Error rows only exist after evaluation."
    ),
    "Table.ReplaceValue": Operation("Replaced value", "retypeColumn", None),
    "Table.ReplaceErrorValues": Operation(
        "Replaced errors", "retypeColumn", "Error values only exist after evaluation."
    ),
    "Table.FillDown": Operation(
        "Filled down", "retypeColumn", "Filling depends on row order in the result."
    ),
    "Table.FillUp": Operation(
        "Filled up", "retypeColumn", "Filling depends on row order in the result."
    ),
    # Grouping and aggregation
    "Table.Group": Operation("Grouped rows", "group", None),
    "Table.Pivot": Operation(
        "Pivoted column", "pivot", "Pivoting needs the distinct values first."
    ),
    "Table.Unpivot": Operation("Unpivoted columns", "pivot", None),
    "Table.UnpivotOtherColumns": Operation("Unpivoted other columns", "pivot", None),
    "Table.Transpose": Operation(
        "Transposed table", "shape", "Transposing needs the whole table in memory."
    ),
    # Combining
    "Table.NestedJoin": Operation("Merged queries", "join", None),
    "Table.Join": Operation("Merged queries", "join", None),
    "Table.FuzzyNestedJoin": Operation(
        "Merged queries (fuzzy)", "join", "Fuzzy matching has no source-side equivalent."
    ),
    "Table.Combine": Operation("Appended query", "append", None),
    "Table.ExpandTableColumn": Operation("Expanded column", "expand", None),
    "Table.ExpandListColumn": Operation(
        "Expanded list", "expand", "Expanding a list multiplies rows client-side."
    ),
    "Table.ExpandRecordColumn": Operation("Expanded record", "expand", None),
    # Buffering and diagnostics
    "Table.Buffer": Operation(
        "Buffered table",
        "other",
        "Buffering is an explicit instruction to stop folding and hold the result in memory.",
    ),
    "List.Buffer": Operation("Buffered list", "other", "Buffering holds the result in memory."),
    "Binary.Buffer": Operation("Buffered binary", "other", "Buffering holds the result in memory."),
    "Table.View": Operation("Custom view", "other", None),
    "Value.NativeQuery": Operation("Ran native query", "source", None),
    "Diagnostics.Trace": Operation("Traced", "other", None),
    # Construction — a table from nothing, which is where inline "Enter Data" lands
    "Table.FromRows": Operation(
        "Source", "source", "The rows are literals in the query; there is no source to fold to."
    ),
    "Table.FromRecords": Operation("Source", "source", "The rows are literals in the query."),
    "Table.FromList": Operation("Converted to table", "shape", None),
    "Table.FromColumns": Operation("Source", "source", "The columns are literals in the query."),
    "Table.FromValue": Operation("Converted to table", "shape", None),
    "Table.ToRows": Operation(
        "Converted to rows", "shape", "Converting away from a table ends folding."
    ),
    "Table.ToList": Operation(
        "Converted to list", "shape", "Converting away from a table ends folding."
    ),
    "Table.ToColumns": Operation(
        "Converted to columns", "shape", "Converting away from a table ends folding."
    ),
    "Table.Column": Operation("Read one column", "aggregate", None),
    "Table.ColumnNames": Operation("Read column names", "other", None),
    "Table.RowCount": Operation("Counted rows", "aggregate", None),
    "Table.Schema": Operation("Read schema", "other", None),
    # List aggregates — mostly seen inside a Table.Group or an added column
    "List.Sum": Operation("Summed", "aggregate", None),
    "List.Average": Operation("Averaged", "aggregate", None),
    "List.Min": Operation("Minimum", "aggregate", None),
    "List.Max": Operation("Maximum", "aggregate", None),
    "List.Count": Operation("Counted", "aggregate", None),
    "List.Distinct": Operation("Distinct list", "aggregate", None),
    "List.Dates": Operation(
        "Generated dates", "addColumn", "A generated date list is computed in the mashup engine."
    ),
    "List.Numbers": Operation(
        "Generated numbers", "addColumn", "A generated number list is computed in the mashup engine."
    ),
    "List.Generate": Operation(
        "Generated list", "addColumn", "A generated list is computed in the mashup engine."
    ),
    "List.Select": Operation("Filtered list", "filterRow", None),
    "List.Transform": Operation("Transformed list", "shape", None),
    "List.Combine": Operation("Combined lists", "append", None),
    "List.Accumulate": Operation(
        "Accumulated", "aggregate", "An accumulation is inherently sequential in the engine."
    ),
}

_CAMEL_BOUNDARY = re.compile(r"([a-z0-9])([A-Z])")


def _humanise(camel: str) -> str:
    """`SplitColumn` -> `Split column`; `FromRows` -> `From rows`."""
    spaced = _CAMEL_BOUNDARY.sub(r"\1 \2", camel).replace("_", " ")
    return spaced[:1].upper() + spaced[1:].lower()


def describe_operation(function_name: str | None) -> Operation:
    """Look up a step's operation, falling back to the function's own name."""
    if not function_name:
        return Operation("Step", "other", None)
    known = OPERATIONS.get(function_name)
    if known:
        return known

    ns = namespace_of(function_name)
    # Strip the namespace only when there is one. `namespace_of` returns "" for an
    # undotted name, and `name[len("") + 1:]` then drops the first character -- so every
    # custom function's step label lost its first letter. `FnCleanTable` read as
    # "N clean table" and `MyTransform` as "Y transform", on the step cards, in front of
    # whoever wrote the function.
    bare = function_name[len(ns) + 1 :] if ns else function_name
    return Operation(
        # `Table.SplitColumn` reads as "Split column" — better than either the raw
        # dotted name or a generic "Step", and it degrades gracefully as the library
        # grows rather than needing this table extended for every new function.
        _humanise(bare),
        "other",
        None,
    )


@dataclass
class Connector:
    """How to read a data-source function's arguments.

    `native_query_arg` is the point of this table. Native SQL embedded in M arrives
    with every newline written `#(lf)`, which the lexer already decodes — so knowing
    that `Odbc.Query`'s second argument is the query is all that stands between a
    4,000-character unreadable string and the SQL on screen.

    `tier` exists because of a wrong answer this got first time round.
    `Table.FromRows(Json.Document(Binary.Decompress(Binary.FromText("…"))))` is how
    Power Query writes an **Enter Data** table: the bytes are a literal in the query.
    It contains `Json.Document`, which is a real source function when handed a file
    — so every one of those inline tables was reported as a *file* source.

      - `connector` reaches outside the query: a server, a URL, a folder, a file.
      - `parser` reads a format out of a binary it was given. It says nothing about
        where the binary came from.
      - `inline` is data written into the query text.
    """

    label: str
    kind: str
    tier: str = "connector"
    #: Argument index holding the server, DSN, folder or URL.
    target_arg: int | None = None
    #: Argument index holding a second identifier, usually the database.
    database_arg: int | None = None
    #: Argument index holding a native query. `-1` means "look in the options
    #: record", not "argument minus one".
    native_query_arg: int | None = None
    #: True when the data is written into the query itself rather than fetched.
    inline: bool = False


CONNECTORS: dict[str, Connector] = {
    # Relational, via a driver
    "Sql.Database": Connector("SQL Server", "database", target_arg=0, database_arg=1, native_query_arg=-1),
    "Sql.Databases": Connector("SQL Server", "database", target_arg=0),
    "Odbc.Query": Connector("ODBC", "database", target_arg=0, native_query_arg=1),
    "Odbc.DataSource": Connector("ODBC", "database", target_arg=0, native_query_arg=-1),
    "OleDb.Query": Connector("OLE DB", "database", target_arg=0, native_query_arg=1),
    "OleDb.DataSource": Connector("OLE DB", "database", target_arg=0),
    "Oracle.Database": Connector("Oracle", "database", target_arg=0, native_query_arg=-1),
    "MySQL.Database": Connector("MySQL", "database", target_arg=0, database_arg=1, native_query_arg=-1),
    "PostgreSQL.Database": Connector("PostgreSQL", "database", target_arg=0, database_arg=1, native_query_arg=-1),
    "Teradata.Database": Connector("Teradata", "database", target_arg=0, native_query_arg=-1),
    "Db2.Database": Connector("Db2", "database", target_arg=0, database_arg=1, native_query_arg=-1),
    "Sybase.Database": Connector("Sybase", "database", target_arg=0, database_arg=1),
    "Snowflake.Databases": Connector("Snowflake", "cloud", target_arg=0, database_arg=1),
    "Databricks.Catalogs": Connector("Databricks", "cloud", target_arg=0),
    "AmazonRedshift.Database": Connector("Redshift", "cloud", target_arg=0, database_arg=1),
    "GoogleBigQuery.Database": Connector("BigQuery", "cloud"),
    "Kusto.Contents": Connector("Azure Data Explorer", "cloud", target_arg=0, database_arg=1, native_query_arg=2),
    "AzureDataExplorer.Contents": Connector("Azure Data Explorer", "cloud", target_arg=0, database_arg=1, native_query_arg=2),
    "Value.NativeQuery": Connector("Native query", "database", native_query_arg=1),
    # Files
    "Csv.Document": Connector("CSV", "file", tier="parser", target_arg=0),
    "Excel.Workbook": Connector("Excel workbook", "file", tier="parser", target_arg=0),
    "Excel.CurrentWorkbook": Connector("Current workbook", "file", tier="parser"),
    "Access.Database": Connector("Access", "file", target_arg=0),
    "Json.Document": Connector("JSON", "file", tier="parser", target_arg=0),
    "Xml.Tables": Connector("XML", "file", tier="parser", target_arg=0),
    "Xml.Document": Connector("XML", "file", tier="parser", target_arg=0),
    "File.Contents": Connector("File", "file", target_arg=0),
    "Folder.Files": Connector("Folder", "folder", target_arg=0),
    "Folder.Contents": Connector("Folder", "folder", target_arg=0),
    "Lines.FromBinary": Connector("Text lines", "file", tier="parser", target_arg=0),
    # SharePoint and OneDrive
    "SharePoint.Files": Connector("SharePoint files", "sharepoint", target_arg=0),
    "SharePoint.Contents": Connector("SharePoint folder", "sharepoint", target_arg=0),
    "SharePoint.Tables": Connector("SharePoint list", "sharepoint", target_arg=0),
    # Web and OData
    "Web.Contents": Connector("Web", "web", target_arg=0),
    "Web.BrowserContents": Connector("Web page", "web", target_arg=0),
    "Web.Page": Connector("Web page", "web", target_arg=0),
    "Html.Table": Connector("HTML table", "web", tier="parser", target_arg=0),
    "OData.Feed": Connector("OData", "web", target_arg=0),
    "Odata.Feed": Connector("OData", "web", target_arg=0),
    # Fabric and Power BI
    "Lakehouse.Contents": Connector("Lakehouse", "cloud"),
    "Fabric.Warehouse": Connector("Fabric warehouse", "cloud"),
    "DataLake.Contents": Connector("Data Lake", "cloud", target_arg=0),
    "AzureStorage.Blobs": Connector("Azure blobs", "cloud", target_arg=0),
    "AzureStorage.DataLake": Connector("ADLS Gen2", "cloud", target_arg=0),
    "PowerBI.Datasets": Connector("Power BI dataset", "model"),
    "PowerPlatform.Dataflows": Connector("Dataflow", "cloud"),
    "Dataverse.Contents": Connector("Dataverse", "service", target_arg=0),
    "CommonDataService.Database": Connector("Dataverse", "service", target_arg=0),
    # Another model — a chained import, which is worth naming loudly
    "AnalysisServices.Database": Connector(
        "Analysis Services", "model", target_arg=0, database_arg=1, native_query_arg=-1
    ),
    "AnalysisServices.Databases": Connector("Analysis Services", "model", target_arg=0),
    "Cube.Transform": Connector("Cube", "model"),
    # Services
    "Salesforce.Data": Connector("Salesforce", "service"),
    "Salesforce.Reports": Connector("Salesforce reports", "service"),
    "Exchange.Contents": Connector("Exchange", "service", target_arg=0),
    "GoogleAnalytics.Accounts": Connector("Google Analytics", "service"),
    "ActiveDirectory.Domains": Connector("Active Directory", "service", target_arg=0),
    # Data written into the query itself
    "Table.FromRows": Connector("Inline data", "inline", tier="inline", inline=True),
    "Table.FromRecords": Connector("Inline data", "inline", tier="inline", inline=True),
    "Table.FromColumns": Connector("Inline data", "inline", tier="inline", inline=True),
    "#table": Connector("Inline data", "inline", tier="inline", inline=True),
}


def describe_connector(function_name: str) -> Connector | None:
    return CONNECTORS.get(function_name)


def is_connector(function_name: str) -> bool:
    """Every function name this module recognises as a data source."""
    return function_name in CONNECTORS


# Record fields that can carry a native query, for connectors that take it as an
# option rather than a positional argument.
#
# `Sql.Database("srv", "db", [Query = "SELECT …"])` is the common spelling, and it
# is why `native_query_arg=-1` appears above.
NATIVE_QUERY_FIELDS = ["Query", "Command", "NativeQuery"]

#: Human label for a source kind, for a sources listing or a legend.
SOURCE_KIND_LABELS = {
    "database": "Database",
    "file": "File",
    "sharepoint": "SharePoint",
    "web": "Web",
    "cloud": "Cloud",
    "service": "Service",
    "model": "Model",
    "inline": "Inline data",
    "folder": "Folder",
}


# ---------------------------------------------------------------------------
# Splitting a paste into queries
# ---------------------------------------------------------------------------
#
# There is no single file format for "all the M in a model". Four shapes turn up in
# practice and all of them are accepted, detected from content:
#
# 1. **`// Query Name` above each expression.** What a tool that dumps every query
#    with a label produces.
# 2. **A section document** — `section Section1; shared Name = expr;`. The canonical
#    multi-query M format, and what a real `.pq` off the mashup is.
# 3. **TMDL** — `expression Name = …` for shared expressions, and
#    `partition X = m` / `source = …` for a table's own query. Where M lives inside
#    a PBIP semantic model.
# 4. **One bare expression**, `let … in …`, with no wrapper at all.
#
# The interesting one is (1), because a comment is not a reliable delimiter —
# comments also appear *inside* queries, and `let … in …` uses no brackets, so
# bracket depth cannot tell an interior comment from a query label.
#
# The trick is not to guess harder. Candidate boundaries are taken from the
# comments, then **each candidate is checked by parsing**: a boundary is real only
# if the slice it opens parses to exactly one complete expression with no tokens
# left over. When a slice has leftovers, the boundary that follows it was an
# interior comment, so the two slices are merged and the check runs again.
#
# That is deliberately not "assume a blank line separates queries". Relying on it
# would work right up until a paste came through a clipboard that reflowed the
# whitespace, which is the class of mistake the sibling TMDL parser was rewritten
# twice for.

_SECTION_RE = re.compile(r'^[ \t]*section\s+[#A-Za-z0-9_"]', re.M)
_SHARED_RE = re.compile(r'^[ \t]*shared\s+[#A-Za-z0-9_"]', re.M)
_PARTITION_LINE_RE = re.compile(r"^[ \t]*partition\s+.*=\s*[A-Za-z0-9_]+\s*$", re.M)
_EXPRESSION_LINE_RE = re.compile(r"^[ \t]*expression\s+[#'\"A-Za-z0-9_]", re.M)
_LEADING_SLASHES = re.compile(r"^/+\s*")
_DESCRIPTION_ATTR = re.compile(r'Description\s*=\s*"((?:[^"]|"")*)"')
_TABLE_RE = re.compile(r"^table\s+(?:'((?:[^']|'')*)'|(\S+))\s*$")
_EXPRESSION_RE = re.compile(r"^expression\s+(?:'((?:[^']|'')*)'|([^=]+?))\s*=\s*(.*)$")
_PARTITION_RE = re.compile(r"^partition\s+(?:'((?:[^']|'')*)'|(\S+))\s*=\s*(\S+)\s*$")
_QUERY_GROUP_RE = re.compile(r"^queryGroup\s*:\s*(.*)$")
_SOURCE_RE = re.compile(r"^source\s*=\s*(.*)$")
_TRAILING_PROPERTY_RE = re.compile(
    r"^(lineageTag|annotation|description|mode|dataView|state|type)\b"
)
_TMDL_DECLARATION_RE = re.compile(
    r"^(table|measure|column|partition|hierarchy|relationship|role|perspective"
    r"|expression|culture|model|database|queryGroup|refreshPolicy|calculationGroup"
    r"|annotation)\b"
)
_TMDL_PROPERTY_RE = re.compile(
    r"^(lineageTag|sourceLineageTag|formatString|displayFolder|isHidden|summarizeBy"
    r"|dataType|mode|dataView|state|type)\s*:"
)
_INDENT_RE = re.compile(r"^[ \t]*")
_DAX_TMDL_RE = re.compile(r"^\s*(measure|calculationGroup)\s", re.M)
_DAX_RE = re.compile(r"\bEVALUATE\b|\bSUMMARIZECOLUMNS\b|\bCALCULATE\s*\(", re.I)
_SQL_RE = re.compile(r"^\s*(SELECT|WITH)\b", re.I | re.M)

#: Tokens that can begin an M expression — used to validate a candidate boundary.
_EXPRESSION_KEYWORDS = {
    "let", "each", "if", "try", "type", "error", "true", "false", "null", "not",
}
_EXPRESSION_OPENERS = {"(", "[", "{"}
#: `...` is here because a parameter query's entire body is `... meta [ … ]`.
#: Without it the label above `Parameter1` failed this test, the boundary was
#: discarded, and the query was silently absorbed into the one before it — visible
#: only as a missing name in a list of thirty-five.
_EXPRESSION_PREFIXES = {"-", "+", "@", "..."}


@dataclass
class Diagnostic:
    level: str
    message: str


@dataclass
class RawQuery:
    name: str
    #: The query's source, verbatim. Offsets in the AST index into this.
    source: str
    ast: Any
    #: Character offset of `source` within the whole paste.
    offset: int
    #: 1-based first line within the whole paste.
    line: int
    errors: list[ParseError]
    #: A `///` or `//` description that sat above the query and was not its name.
    description: str
    #: Set when the container declared the query's group — `queryGroup` in TMDL.
    #: Absent for the labelled format, which is why a canvas groups by dependency
    #: layer rather than by group.
    group: str | None
    #: True when the container marked this query as loaded to the model. Only
    #: formats 2 and 3 can say; None means "not stated", which is not the same as
    #: False.
    load_enabled: bool | None


@dataclass
class DocumentResult:
    queries: list[RawQuery]
    format: str
    diagnostics: list[Diagnostic]


def _starts_expression(token: Token) -> bool:
    if token.kind in ("identifier", "number", "text", "intrinsic"):
        return True
    if token.kind == "keyword":
        return token.value in _EXPRESSION_KEYWORDS
    if token.kind == "punctuator":
        return token.value in _EXPRESSION_OPENERS
    if token.kind == "operator":
        return token.value in _EXPRESSION_PREFIXES
    return False


def _unquote(name: str) -> str:
    trimmed = name.strip()
    if trimmed.startswith('#"') and trimmed.endswith('"'):
        return trimmed[2:-1].replace('""', '"')
    if trimmed.startswith('"') and trimmed.endswith('"'):
        return trimmed[1:-1].replace('""', '"')
    return trimmed


def _line_at(text: str, offset: int) -> int:
    return 1 + text.count("\n", 0, min(offset, len(text)))


def _is_own_line(text: str, token: Token) -> bool:
    """True when only whitespace precedes the token on its line."""
    i = token.start - 1
    while i >= 0 and text[i] != "\n":
        if text[i] not in (" ", "\t", "\r"):
            return False
        i -= 1
    return True


def _count_label_candidates(text: str) -> int:
    tokens = tokenize(text).tokens
    count = 0
    for i, t in enumerate(tokens):
        if t.kind != "comment" or not _is_own_line(text, t):
            continue
        following = next((n for n in tokens[i + 1 :] if n.kind != "comment"), None)
        if following is not None and _starts_expression(following):
            count += 1
    return count


def detect_format(text: str) -> str:
    """Detect which of the four shapes a paste is."""
    if _SECTION_RE.search(text) or _SHARED_RE.search(text):
        return "section"
    # A `partition … = <kind>` line, or `expression Name =` at the start of a line,
    # both of which only occur in TMDL. `expression` is also an M identifier, so the
    # anchor and the `=` both matter.
    #
    # Any partition kind counts, not only `m`: a paste of a calculated table is
    # still TMDL, and recognising it is what lets the caller say "this is DAX in a
    # partition, you want the sibling parser" instead of failing generically.
    if _PARTITION_LINE_RE.search(text) or _EXPRESSION_LINE_RE.search(text):
        return "tmdl"
    # Two or more own-line comments each followed by something that starts an
    # expression. One is not enough — a single leading comment above a single `let`
    # is a documented query, not a labelled document.
    if _count_label_candidates(text) >= 2:
        return "labelled"
    return "expression"


def split_document(text: str) -> DocumentResult:
    diagnostics: list[Diagnostic] = []

    if not text.strip():
        return DocumentResult([], "expression", diagnostics)

    fmt = detect_format(text)
    if fmt == "section":
        queries = _split_section(text, diagnostics)
    elif fmt == "tmdl":
        queries = _split_tmdl(text, diagnostics)
    elif fmt == "labelled":
        queries = _split_labelled(text, diagnostics)
    else:
        queries = _split_single(text)

    if not queries:
        diagnostics.append(Diagnostic("warning", _describe_empty(text, fmt)))

    return DocumentResult(queries, fmt, diagnostics)


def _split_labelled(text: str, diagnostics: list[Diagnostic]) -> list[RawQuery]:
    """Format 1: `// Query Name` above each expression.

    Candidate boundaries come from own-line comments followed by an expression
    start. Each candidate is then *verified by parsing* — see the section header
    above for why that is the only honest way to do this. A slice with leftover
    tokens means the next candidate was an interior comment, so the slices merge and
    the parse runs again.
    """
    result = tokenize(text)
    tokens, lex_errors = result.tokens, result.errors

    candidates: list[dict] = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t.kind != "comment" or not _is_own_line(text, t):
            i += 1
            continue

        # Consecutive own-line comments are one label block: the *last* line names
        # the query (it is the one nearest the expression) and the rest document it.
        last = i
        while (
            last + 1 < len(tokens)
            and tokens[last + 1].kind == "comment"
            and _is_own_line(text, tokens[last + 1])
        ):
            last += 1
        following = tokens[last + 1] if last + 1 < len(tokens) else None
        if following is None or not _starts_expression(following):
            i = last + 1
            continue

        block = tokens[i : last + 1]
        candidates.append(
            {
                "comment_index": i,
                "start_index": last + 1,
                "name": _unquote(_LEADING_SLASHES.sub("", block[-1].value)),
                "description": " ".join(
                    _LEADING_SLASHES.sub("", c.value) for c in block[:-1]
                ).strip(),
            }
        )
        i = last + 1

    if not candidates:
        return _split_single(text)

    queries: list[RawQuery] = []
    merged = 0

    c = 0
    while c < len(candidates):
        candidate = candidates[c]

        # Try this candidate's slice against each following boundary in turn, taking
        # the first that parses clean to the end. Merging forward is what corrects a
        # comment that turned out to be interior.
        taken = -1
        best: dict | None = None

        for n in range(c + 1, len(candidates) + 1):
            end_token = (
                tokens[candidates[n]["comment_index"]].start if n < len(candidates) else len(text)
            )
            start = tokens[candidate["start_index"]].start
            source = text[start:end_token]
            attempt = _parse_slice(source, lex_errors, start)

            # Both conditions are needed, and the second one was learned the hard
            # way. `next == total` alone is satisfied by a *failed* parse too,
            # because error recovery consumes to the end of the slice — so
            # `let a = 1,` cut short by an interior comment looked like a complete
            # expression and the boundary was wrongly accepted. A slice is one
            # expression only when the parser reached the end of it **and** had
            # nothing to complain about.
            if not attempt.errors and attempt.next == attempt.total:
                taken = n
                best = {"ast": attempt.ast, "errors": attempt.errors, "source": source}
                break
            # The fallback is the *smallest* slice, not the last one tried: if no
            # merge ever parses clean, the honest answer is one query with a
            # diagnostic rather than a query that swallowed the rest of the document.
            if best is None:
                best = {"ast": attempt.ast, "errors": attempt.errors, "source": source}
                taken = n

        if best is None:
            c += 1
            continue

        if taken > c + 1:
            merged += taken - c - 1

        queries.append(
            RawQuery(
                name=candidate["name"] or f"Query{len(queries) + 1}",
                source=best["source"].rstrip(),
                ast=best["ast"],
                offset=tokens[candidate["start_index"]].start,
                line=tokens[candidate["start_index"]].line,
                errors=best["errors"],
                description=candidate["description"],
                group=None,
                load_enabled=None,
            )
        )

        c = taken

    if merged > 0:
        plural = "" if merged == 1 else "s"
        was = "was" if merged == 1 else "were"
        diagnostics.append(
            Diagnostic(
                "info",
                f"{merged} comment{plural} inside a query looked like a query label "
                f"and {was} folded back in.",
            )
        )

    return queries


def _split_section(text: str, diagnostics: list[Diagnostic]) -> list[RawQuery]:
    """Format 2: a section document.

    `section Name; shared A = expr; B = expr;` — members are `;`-separated at the
    top level, and `shared` marks a member as visible outside the section, which for
    a Power Query document means loaded rather than internal.

    Splitting is depth-aware over tokens rather than by regex: a `;` inside a record
    or a nested list is not a member terminator, and M's `;` has no other use, so
    depth is sufficient here in a way it is not for the labelled format.
    """
    result = tokenize(text)
    lex_errors = result.errors
    stream = without_comments(result.tokens)
    queries: list[RawQuery] = []

    def st(index: int) -> Token | None:
        return stream[index] if 0 <= index < len(stream) else None

    i = 0

    # Step over the `section Name;` header if there is one.
    head = st(i)
    if head is not None and head.kind == "keyword" and head.value == "section":
        while i < len(stream) and not (
            stream[i].kind == "punctuator" and stream[i].value == ";"
        ):
            i += 1
        i += 1

    while i < len(stream) and stream[i].kind != "eof":
        # Attributes: `[Description = "…"]` before a member. Recorded as the
        # description rather than skipped, since it is the only documentation a
        # section document carries.
        description = ""
        if stream[i].kind == "punctuator" and stream[i].value == "[":
            from_ = i
            depth = 0
            while i < len(stream):
                t = stream[i]
                if t.kind == "punctuator" and t.value == "[":
                    depth += 1
                elif t.kind == "punctuator" and t.value == "]":
                    depth -= 1
                    if depth == 0:
                        i += 1
                        break
                i += 1
            attr = text[stream[from_].start : stream[min(i, len(stream) - 1)].start]
            found = _DESCRIPTION_ATTR.search(attr)
            description = (found.group(1) if found else "").replace('""', '"').strip()

        current = st(i)
        is_shared = current is not None and current.kind == "keyword" and current.value == "shared"
        if is_shared:
            i += 1

        name_token = st(i)
        if name_token is None or name_token.kind != "identifier":
            # Not a member start. Step to the next `;` so one malformed member costs
            # one member.
            while i < len(stream) and not (
                stream[i].kind == "punctuator" and stream[i].value == ";"
            ):
                i += 1
            i += 1
            continue
        i += 1

        equals = st(i)
        if not (equals is not None and equals.kind == "operator" and equals.value == "="):
            while i < len(stream) and not (
                stream[i].kind == "punctuator" and stream[i].value == ";"
            ):
                i += 1
            i += 1
            continue
        i += 1

        value_start = i
        depth = 0
        while i < len(stream) and stream[i].kind != "eof":
            t = stream[i]
            if t.kind == "punctuator" and t.value in ("(", "[", "{"):
                depth += 1
            elif t.kind == "punctuator" and t.value in (")", "]", "}"):
                depth -= 1
            elif t.kind == "punctuator" and t.value == ";" and depth == 0:
                break
            i += 1

        head_token = st(value_start)
        tail_token = st(i)
        start = head_token.start if head_token is not None else 0
        end = tail_token.start if tail_token is not None else len(text)
        source = text[start:end].strip()
        parsed = _parse_slice(source, lex_errors, start)

        queries.append(
            RawQuery(
                name=_unquote(name_token.value),
                source=source,
                ast=parsed.ast,
                offset=start,
                line=_line_at(text, start),
                errors=parsed.errors,
                description=description,
                group=None,
                # `shared` is genuinely what "loaded" means in a section document, so
                # this is a fact rather than a guess — unlike the labelled format,
                # which can only say None.
                load_enabled=is_shared,
            )
        )

        i += 1  # step over the `;`

    if queries:
        shared = sum(1 for q in queries if q.load_enabled)
        diagnostics.append(
            Diagnostic(
                "info",
                f"Read as a section document: {shared} shared, "
                f"{len(queries) - shared} internal.",
            )
        )

    return queries


def _indent_of(line: str) -> int:
    return len(_INDENT_RE.match(line).group(0))


def _is_tmdl_declaration(trimmed: str) -> bool:
    return bool(_TMDL_DECLARATION_RE.match(trimmed)) or trimmed.startswith("///")


def _is_tmdl_boundary(trimmed: str) -> bool:
    return _is_tmdl_declaration(trimmed) or bool(_TMDL_PROPERTY_RE.match(trimmed))


def _split_tmdl(text: str, diagnostics: list[Diagnostic]) -> list[RawQuery]:
    """Format 3: M inside TMDL.

    Two homes for it. `expression Name = …` is a shared expression — a parameter or
    a reusable query. `partition X = m` with a `source = …` beneath it is a table's
    own query, and the table name is the query name because that is what a modeller
    calls it.

    This is line-oriented, unlike everything else here, because the *TMDL* around
    the M is line-oriented. The M body is still handed to the expression parser.
    """
    lines = re.split(r"\r?\n", re.sub(r"^\ufeff", "", text))
    queries: list[RawQuery] = []
    lex_errors = tokenize(text).errors

    # Offset of the start of each line, so spans point into the whole paste.
    line_offsets: list[int] = []
    acc = 0
    for line in lines:
        line_offsets.append(acc)
        acc += len(line) + 1

    current_table: str | None = None
    pending_description: list[str] = []
    i = 0

    while i < len(lines):
        raw = lines[i]
        trimmed = raw.strip()

        if trimmed.startswith("///"):
            pending_description.append(re.sub(r"^///\s?", "", trimmed))
            i += 1
            continue

        table = _TABLE_RE.match(trimmed)
        if table:
            current_table = (table.group(1) or table.group(2) or "").replace("''", "'")
            pending_description = []
            i += 1
            continue

        # `expression Name = body` — the body may be inline or on following lines.
        expression = _EXPRESSION_RE.match(trimmed)
        if expression:
            name = (expression.group(1) or expression.group(2) or "").replace("''", "'").strip()
            body = _read_tmdl_body(lines, i, expression.group(3), _indent_of(raw))
            start = line_offsets[i]
            parsed = _parse_slice(body["text"], lex_errors, start)
            queries.append(
                RawQuery(
                    name=_unquote(name),
                    source=body["text"],
                    ast=parsed.ast,
                    offset=start,
                    line=i + 1,
                    errors=parsed.errors,
                    description=" ".join(pending_description).strip(),
                    group=body["query_group"],
                    load_enabled=None,
                )
            )
            pending_description = []
            i = body["next"]
            continue

        # `partition X = m` then `source = …`. The kind after the `=` is what says
        # the body is M — `calculated` means it is DAX and belongs to the sibling
        # parser.
        partition = _PARTITION_RE.match(trimmed)
        if partition:
            kind = partition.group(3)
            partition_name = (partition.group(1) or partition.group(2) or "").replace("''", "'")
            found = _read_partition_source(lines, i + 1, _indent_of(raw))
            if kind in ("m", "query") and found["text"].strip():
                start = line_offsets[min(found["start_line"], len(lines) - 1)]
                parsed = _parse_slice(found["text"], lex_errors, start)
                queries.append(
                    RawQuery(
                        # The table name, not the partition name: a single-partition
                        # table is the norm and "Sales" is what a modeller calls that
                        # query, not "Sales-a1b2c3".
                        name=current_table if current_table is not None else _unquote(partition_name),
                        source=found["text"],
                        ast=parsed.ast,
                        offset=start,
                        line=found["start_line"] + 1,
                        errors=parsed.errors,
                        description=" ".join(pending_description).strip(),
                        group=found["query_group"],
                        load_enabled=True,
                    )
                )
            pending_description = []
            i = found["next"]
            continue

        pending_description = []
        i += 1

    if queries:
        grouped = sum(1 for q in queries if q.group)
        plural = "y" if len(queries) == 1 else "ies"
        extra = f", {grouped} with a query group" if grouped else ""
        diagnostics.append(
            Diagnostic("info", f"Read as TMDL: {len(queries)} quer{plural}{extra}.")
        )

    return queries


def _read_tmdl_body(lines: list[str], decl_line: int, inline: str, decl_indent: int) -> dict:
    """Read a TMDL `expression`'s body — inline, fenced with ```, or indented.

    The indent test is the fallback and not the primary rule: it only decides how
    far an *already-identified* body extends, never whether a line is a body at all.
    A line that announces itself as a TMDL declaration or property ends the body
    regardless of how it is indented, which is the sibling parser's hard-won rule
    applied here.
    """
    i = decl_line + 1
    head = inline.strip()
    if head.startswith("```"):
        block: list[str] = []
        rest = head[3:]
        if rest.strip():
            block.append(rest)
        while i < len(lines) and not lines[i].strip().startswith("```"):
            block.append(lines[i])
            i += 1
        i += 1
        after = _read_trailing_properties(lines, i, decl_indent)
        return {
            "text": "\n".join(block).strip(),
            "next": after["next"],
            "query_group": after["query_group"],
        }

    block = [head] if head else []
    while i < len(lines):
        trimmed = lines[i].strip()
        if not trimmed:
            block.append("")
            i += 1
            continue
        if _is_tmdl_boundary(trimmed):
            break
        if head and _indent_of(lines[i]) <= decl_indent:
            break
        block.append(lines[i])
        i += 1

    after = _read_trailing_properties(lines, i, decl_indent)
    return {
        "text": "\n".join(block).strip(),
        "next": after["next"],
        "query_group": after["query_group"],
    }


def _read_trailing_properties(lines: list[str], start: int, decl_indent: int) -> dict:
    i = start
    query_group: str | None = None
    while i < len(lines):
        trimmed = lines[i].strip()
        if not trimmed:
            i += 1
            continue
        group = _QUERY_GROUP_RE.match(trimmed)
        if group:
            query_group = _unquote(group.group(1)) or None
            i += 1
            continue
        if _TRAILING_PROPERTY_RE.match(trimmed):
            i += 1
            continue
        if _is_tmdl_declaration(trimmed) or _indent_of(lines[i]) <= decl_indent:
            break
        i += 1
    return {"next": i, "query_group": query_group}


def _read_partition_source(lines: list[str], start: int, partition_indent: int) -> dict:
    """Read the `source = …` under a partition, plus the partition's `queryGroup`."""
    i = start
    query_group: str | None = None
    text = ""
    start_line = start

    while i < len(lines):
        trimmed = lines[i].strip()
        if not trimmed:
            i += 1
            continue

        group = _QUERY_GROUP_RE.match(trimmed)
        if group:
            query_group = _unquote(group.group(1)) or None
            i += 1
            continue

        source = _SOURCE_RE.match(trimmed)
        if source:
            start_line = i
            body = _read_tmdl_body(lines, i, source.group(1), _indent_of(lines[i]))
            text = body["text"]
            i = body["next"]
            if not query_group:
                query_group = body["query_group"]
            continue

        if _is_tmdl_declaration(trimmed):
            break
        if _indent_of(lines[i]) <= partition_indent:
            break
        i += 1

    return {"text": text, "start_line": start_line, "next": i, "query_group": query_group}


def _split_single(text: str) -> list[RawQuery]:
    """Format 4: one bare expression. Named from a leading comment if there is one."""
    result = tokenize(text)
    tokens, lex_errors = result.tokens, result.errors
    leading: list[str] = []
    first_index = 0
    while (
        first_index < len(tokens)
        and tokens[first_index].kind == "comment"
        and _is_own_line(text, tokens[first_index])
    ):
        leading.append(_LEADING_SLASHES.sub("", tokens[first_index].value))
        first_index += 1

    first = tokens[first_index] if first_index < len(tokens) else None
    if first is None or first.kind == "eof":
        return []

    source = text[first.start :].strip()
    parsed = _parse_slice(source, lex_errors, first.start)

    # Tokens left over means the text is not one M expression, so it is not a query.
    # This is the gate that stops a paste of DAX, SQL or TMDL becoming a query called
    # "Query1" with a mangled body — `EVALUATE SUMMARIZECOLUMNS (…)` parses as the
    # identifier `EVALUATE` and then stops, and reporting that as a successfully-read
    # query would be the worst possible answer. Falling through to `_describe_empty`
    # instead lets the caller say what it thinks the paste is.
    if parsed.next != parsed.total:
        return []

    return [
        RawQuery(
            name=_unquote(leading[-1]) if leading else "Query1",
            source=source,
            ast=parsed.ast,
            offset=first.start,
            line=first.line,
            errors=parsed.errors,
            description=" ".join(leading[:-1]).strip(),
            group=None,
            load_enabled=None,
        )
    ]


def _parse_slice(source: str, document_lex_errors: list[LexError], offset: int) -> ParseResult:
    """Parse one slice, re-tokenizing it.

    Re-tokenizing rather than reusing a sub-range of the document's tokens keeps
    every AST offset relative to the slice's own `source` string, which is what a
    step's text is sliced out of. Lex errors from the whole document are filtered
    down to the ones that fall inside this slice, so a bad string literal in query
    three is not reported against query nine.
    """
    result = tokenize(source)
    own = result.errors
    outer = [
        LexError(e.message, e.start - offset, e.line)
        for e in document_lex_errors
        if offset <= e.start < offset + len(source)
        and not any(o.start == e.start - offset for o in own)
    ]
    return parse_tokens(source, result.tokens, own + outer)


def _describe_empty(text: str, fmt: str) -> str:
    if fmt == "tmdl":
        return (
            "This looks like TMDL but has no `expression` or `partition … = m` block. "
            "Power Query lives in a table’s M partition or in a shared expression."
        )
    if _DAX_TMDL_RE.search(text):
        return (
            "This is DAX in TMDL, not Power Query. The measure dependency graph is the "
            "sibling app, TMDL: Measure Chain."
        )
    if _DAX_RE.search(text):
        return "This looks like DAX, not M. Paste Power Query — an expression starting `let` — instead."
    if _SQL_RE.search(text):
        return "This looks like SQL. Paste the Power Query that wraps it — the M expression, starting `let`."
    return "No queries found. Expected Power Query M, such as `let Source = Sql.Database(…) in Source`."


# ---------------------------------------------------------------------------
# Scope-aware reference resolution
# ---------------------------------------------------------------------------
#
# This is the code that decides what the whole analysis is telling you, so the
# rule it implements is worth stating plainly. An identifier resolves in this
# order:
#
#     1. an enclosing lambda or `each` parameter
#     2. an enclosing `let` binding, innermost block first
#     3. a query name
#     4. the M standard library
#     5. unresolved
#
# Only a hit at (3) is a dependency between queries. Only a hit at (2) *in the
# query's own top-level `let`* is a step reference. Nothing else wires anything up.
#
# **Why order matters more than it looks.** Almost every query in a real model has
# a step called `Source` and one called `#"Changed Type"`. If a model also has a
# query called `Changed Type` — and models like that exist — then a text-matching
# approach draws an edge from every query in the model to that one. The reverse
# mistake is just as easy: a step named after the query it reads
# (`Source = #"Non BbM Master"`) has to resolve the right-hand side to the query
# while resolving its own name to the step.
#
# Shadowing is the normal case in M, not an edge case, and it can only be got right
# with a scope stack.


@dataclass
class Reference:
    name: str
    #: One of step, query, local, library, unresolved.
    kind: str
    span: Span
    #: The top-level step this reference appeared inside, or None when it sits in
    #: the query's `in` clause or outside any step.
    step: str | None
    #: How many `each` / lambda bodies enclose it.
    #:
    #: Non-zero on a `query` or `step` reference is the row-by-row smell: the
    #: referenced table is re-evaluated per row of the outer table.
    lambda_depth: int
    #: True when the reference is the callee of an invocation rather than a value.
    is_call: bool


@dataclass
class CallSite:
    #: Dotted function name, e.g. `Table.AddColumn`. Empty when the callee is not a
    #: plain name.
    name: str
    args: list[Any]
    span: Span
    step: str | None
    lambda_depth: int


@dataclass
class ResolveResult:
    references: list[Reference]
    calls: list[CallSite]
    #: The top-level `let`'s bindings, in source order — the query's steps. Empty
    #: for a query that is not a `let` at all: a parameter, a one-line expression,
    #: or a function whose body is not a `let`.
    step_names: list[str]
    #: Name of the step the `in` clause returns, or None when the result is an
    #: expression rather than a plain step reference. Not always the last step,
    #: which is the whole reason to record it — a query returning an earlier step
    #: leaves the ones after it dead.
    result_step: str | None
    #: True when the query's body is a function definition.
    is_function: bool
    parameters: list[str]


@dataclass
class _Frame:
    kind: str
    names: set[str]


@dataclass
class _Spine:
    steps_let: Any
    result_step: str | None
    is_function: bool
    parameters: list[str]


def _as_function(node: Any) -> Any:
    """The function inside, looking through parentheses. None when it is not one."""
    current = node
    for _ in range(4):
        if current is None:
            return None
        if current.type == "Paren":
            current = current.inner
            continue
        return current if current.type == "Function" else None
    return None


def _unwrap_function(node: Any) -> Any:
    current = node
    for _ in range(4):
        if current is None:
            return None
        if current.type == "Paren":
            current = current.inner
            continue
        if current.type == "Function":
            current = current.body
            continue
        return current
    return current


def _unwrap_spine(ast: Any) -> _Spine:
    """Find the `let` whose bindings are the query's steps.

    A query is not always a bare `let`. A Combine Files transform is
    `let Source = (Parameter1 as binary) => let … in … in Source` — a function whose
    *own* body is the interesting `let`, wrapped in a one-step `let` that just names
    it. And a query can be `(x) => let … in …` directly.

    The rule: unwrap parentheses and function bodies, and take the first `let`
    found. Where a `let` has exactly one binding whose value is a function, look
    through it too — that single binding is a naming convention, not a step list
    anyone wants to read.
    """
    node = ast
    is_function = False
    parameters: list[str] = []

    for _ in range(8):
        if node is None:
            break
        if node.type == "Paren":
            node = node.inner
            continue
        if node.type == "Meta":
            node = node.value
            continue
        if node.type == "Function":
            is_function = True
            if not parameters:
                parameters = [p.name for p in node.params]
            node = node.body
            continue
        if node.type == "Let":
            only = node.bindings[0] if len(node.bindings) == 1 else None
            # Only a single binding whose value is a *function* is a wrapper.
            #
            # Requiring the function is what keeps `let Source = let ratio = … in …`
            # — a one-step query whose step happens to use a nested `let` —
            # reporting its own step rather than the helper variables inside it.
            # Without the test, any single-step query with a nested `let` lost its
            # step list.
            fn = _as_function(only.value) if only is not None else None
            inner = _unwrap_function(fn) if fn is not None else None
            if fn is not None and inner is not None and inner.type == "Let":
                is_function = True
                if not parameters:
                    parameters = [p.name for p in fn.params]
                node = inner
                continue
            return _Spine(
                steps_let=node,
                result_step=node.body.name if node.body.type == "Identifier" else None,
                is_function=is_function,
                parameters=parameters,
            )
        break

    return _Spine(None, None, is_function, parameters)


def _callee_name(callee: Any) -> str:
    """The name being invoked, for a plain call.

    Empty string when the callee is not a bare name — `#"Transform File"([Content])`
    gives `Transform File`, but `(f)(x)` or `Record.Field(r)("a")` give nothing,
    because there is no library function to look up and pretending otherwise would
    mislabel the step.
    """
    if callee.type == "Identifier":
        return callee.name
    if callee.type == "Literal" and callee.kind == "intrinsic":
        return callee.value
    if callee.type == "Paren":
        return _callee_name(callee.inner)
    return ""


def resolve_query(ast: Any, query_names: set[str]) -> ResolveResult:
    """Walk a query's AST and classify every identifier in it.

    `query_names` is every query in the paste, including this one — a query that
    references itself is legal M and worth surfacing as recursion rather than
    hiding.
    """
    references: list[Reference] = []
    calls: list[CallSite] = []
    frames: list[_Frame] = []

    state = {
        "current_step": None,
        "lambda_depth": 0,
        # Set once, when the query's step-bearing `let` is entered.
        "steps_frame_entered": False,
        # Lambda nesting already in force where the step list begins.
        #
        # `lambda_depth` on a reference is reported *relative to this*, and the
        # reason is a false positive that shipped once: a custom-function query is
        # `let f = (x) => let Source = …, #"Change Type" = …(Source) in … in f`, so
        # its steps live one lambda deep by construction. Measured absolutely, every
        # step of every custom function looked like the row-by-row pattern. Measured
        # relative to where the steps start, only a lambda *inside* a step counts —
        # which is what the pattern actually is.
        "steps_base_lambda_depth": 0,
    }

    spine = _unwrap_spine(ast)

    def classify(name: str) -> str:
        for frame in reversed(frames):
            if name not in frame.names:
                continue
            return "step" if frame.kind == "steps" else "local"
        # The model wins over the library, deliberately. A query named `Date` or
        # `Value` is unusual but legal, and if someone has one, their query is what
        # they meant.
        if name in query_names:
            return "query"
        if is_library_function(name):
            return "library"
        return "unresolved"

    def record(name: str, span: Span, is_call: bool) -> None:
        references.append(
            Reference(
                name=name,
                kind=classify(name),
                span=span,
                step=state["current_step"],
                lambda_depth=max(
                    0, state["lambda_depth"] - state["steps_base_lambda_depth"]
                ),
                is_call=is_call,
            )
        )

    def visit(node: Any, as_callee: bool = False) -> None:
        if node is None:
            return
        kind = node.type

        if kind == "Identifier":
            record(node.name, node.span, as_callee)
            return

        if kind == "Let":
            # Every binding is visible in every other binding's body: M's `let` is
            # mutually recursive, so a step may reference one declared below it. A
            # resolver that only exposed earlier bindings would misclassify those as
            # unresolved.
            is_steps_frame = not state["steps_frame_entered"] and node is spine.steps_let
            if is_steps_frame:
                state["steps_frame_entered"] = True
                state["steps_base_lambda_depth"] = state["lambda_depth"]

            frames.append(
                _Frame("steps" if is_steps_frame else "let", {b.name for b in node.bindings})
            )

            for binding in node.bindings:
                # Only the top-level `let` names steps. A binding inside a nested
                # `let` belongs to whichever step encloses it, which is what keeps
                # the step attribution of a reference correct however deep it sits.
                previous = state["current_step"]
                if is_steps_frame:
                    state["current_step"] = binding.name
                visit(binding.value)
                state["current_step"] = previous

            previous_step = state["current_step"]
            # The `in` clause belongs to no step.
            if is_steps_frame:
                state["current_step"] = None
            visit(node.body)
            state["current_step"] = previous_step

            frames.pop()
            return

        if kind == "Function":
            frames.append(_Frame("lambda", {p.name for p in node.params}))
            state["lambda_depth"] += 1
            visit(node.body)
            state["lambda_depth"] -= 1
            frames.pop()
            return

        if kind == "Each":
            # `each` binds `_`, and `[Field]` with no target is a lookup on it. Both
            # are handled by binding the name rather than by special-casing the
            # bracket, which is why `FieldAccess.target` may be None.
            frames.append(_Frame("lambda", {"_"}))
            state["lambda_depth"] += 1
            visit(node.body)
            state["lambda_depth"] -= 1
            frames.pop()
            return

        if kind == "Invocation":
            visit(node.callee, True)
            calls.append(
                CallSite(
                    name=_callee_name(node.callee),
                    args=node.args,
                    span=node.span,
                    step=state["current_step"],
                    lambda_depth=state["lambda_depth"],
                )
            )
            for arg in node.args:
                visit(arg)
            return

        if kind == "Record":
            # Field *names* are skipped on purpose. `[ApiVersion = 15]` references
            # nothing called `ApiVersion`, and `[Delimiter = ","]` references nothing
            # called `Delimiter`. The AST stores them as strings so this cannot be
            # got wrong by accident.
            for f in node.fields:
                visit(f.value)
            return

        if kind == "FieldAccess":
            # Same reasoning: `field` is a name, not a reference. Only the target is
            # an expression.
            visit(node.target)
            return

        if kind == "ItemAccess":
            visit(node.target)
            visit(node.selector)
            return

        if kind == "Type":
            # A type's field names are names; its embedded expressions are not.
            # `type table [#"Rounded PC" = _t]` must yield `_t` and nothing else.
            for child in node.nested:
                visit(child)
            return

        if kind == "Unparsed":
            # A region the parser could not read still contributes its identifiers,
            # classified through the scope stack as it stands here. A dependency that
            # survives a parse failure is worth more than a clean tree.
            for found in node.identifiers:
                record(found.name, found.span, False)
            return

        if kind == "List":
            for item in node.items:
                visit(item)
            return

        if kind == "If":
            visit(node.condition)
            visit(node.then)
            visit(node.otherwise)
            return

        if kind == "Binary":
            visit(node.left)
            visit(node.right)
            return

        if kind == "Unary":
            visit(node.operand)
            return

        if kind == "Try":
            visit(node.body)
            visit(node.otherwise)
            return

        if kind == "Meta":
            visit(node.value)
            visit(node.meta)
            return

        if kind == "Paren":
            visit(node.inner)
            return

        if kind == "Error":
            visit(node.value)
            return

        # Literal, and anything else with no children.
        return

    visit(ast)

    return ResolveResult(
        references=references,
        calls=calls,
        step_names=[b.name for b in spine.steps_let.bindings] if spine.steps_let else [],
        result_step=spine.result_step,
        is_function=spine.is_function,
        parameters=spine.parameters,
    )


# ---------------------------------------------------------------------------
# What a query is
# ---------------------------------------------------------------------------


@dataclass
class ColumnDelta:
    """Columns a step adds, drops, renames or retypes, as far as the M states it."""

    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    renamed: list[dict] = field(default_factory=list)
    retyped: list[dict] = field(default_factory=list)
    #: Columns explicitly kept, where the step names them rather than the drops.
    kept: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "added": list(self.added),
            "removed": list(self.removed),
            "renamed": [dict(r) for r in self.renamed],
            "retyped": [dict(r) for r in self.retyped],
            "kept": list(self.kept),
        }


@dataclass
class Step:
    """One `name = expression` in a query's top-level `let`."""

    name: str
    #: Position in declaration order, 0-based.
    index: int
    #: The step's M, verbatim. Sliced from the source, never re-printed.
    expression: str
    #: 1-based line within the query's own source.
    line: int
    #: The operation label, in Power Query's own vocabulary where it has one.
    label: str
    shape: str
    #: The outermost library function this step calls, or None.
    function_name: str | None
    #: Steps in this query that this step reads.
    reads: list[str]
    #: Steps in this query that read this one.
    read_by: list[str]
    #: Other queries this step references.
    queries: list[str]
    #: True when the step is not reachable from the query's result.
    #:
    #: Dead steps are ordinary in hand-edited M — an experiment left behind, a
    #: filter someone routed around — and nothing in the Power Query Editor tells
    #: you, because it draws the step list as a line whatever the references say.
    dead: bool
    columns: ColumnDelta
    #: Why this step probably will not fold, or None when it probably will.
    fold_break: str | None
    #: Queries referenced from inside an `each` or lambda in this step — the
    #: row-by-row pattern, where the referenced query is re-evaluated for every row
    #: of the table being transformed.
    row_by_row: list[str]

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "index": self.index,
            "expression": self.expression,
            "line": self.line,
            "label": self.label,
            "shape": self.shape,
            "functionName": self.function_name,
            "reads": list(self.reads),
            "readBy": list(self.read_by),
            "queries": list(self.queries),
            "dead": self.dead,
            "columns": self.columns.to_dict(),
            "foldBreak": self.fold_break,
            "rowByRow": list(self.row_by_row),
        }


@dataclass
class SourceRef:
    """A data source a query reaches, read off the connector call."""

    function_name: str
    label: str
    kind: str
    #: Whether the call reaches outside the query, reads a format, or is a literal.
    tier: str
    #: Server, DSN, folder or URL, when the call states it as a literal.
    target: str | None
    database: str | None
    #: The native query, with `#(lf)` and `#(tab)` decoded.
    #:
    #: Decoding is done by the lexer, so this is readable SQL rather than the
    #: single-line escaped form the M literal holds.
    native_query: str | None
    #: True when the data is written into the query rather than fetched.
    inline: bool
    #: The step the connector was called in.
    step: str | None
    line: int

    def to_dict(self) -> dict:
        return {
            "functionName": self.function_name,
            "label": self.label,
            "kind": self.kind,
            "tier": self.tier,
            "target": self.target,
            "database": self.database,
            "nativeQuery": self.native_query,
            "inline": self.inline,
            "step": self.step,
            "line": self.line,
        }


@dataclass
class Finding:
    """Something worth telling the reader about a query.

    `level` is never `error`. A finding is an observation about the model, and this
    has no standing to call a modeller's decision an error. Parse failures are separate
    and live in `errors`.
    """

    level: str
    #: Stable machine code, for filtering and for tests.
    code: str
    message: str
    #: The step it concerns, when it concerns one.
    step: str | None

    def to_dict(self) -> dict:
        return {
            "level": self.level,
            "code": self.code,
            "message": self.message,
            "step": self.step,
        }


@dataclass
class Query:
    """A query, analysed, and with its place in the dependency graph resolved.

    The graph fields (`id`, `used_by`, `layer`, `all_source_kinds`, `source_kind`,
    `in_cycle`) are filled in by `build_model`; everything above them comes from
    `analyse_query` and is true of the query on its own.
    """

    name: str
    kind: str
    description: str
    #: Query group, when the container stated one. None for a labelled paste.
    group: str | None
    #: Whether the container said this query loads to the model. None = not stated.
    load_enabled: bool | None
    #: The query's whole M, verbatim.
    source: str
    #: 1-based line of the query within the whole paste.
    line: int
    steps: list[Step]
    #: Name of the step the `in` clause returns, or None when it returns an
    #: expression.
    result_step: str | None
    #: Parameter names, for a function query.
    parameters: list[str]
    #: Queries this one references, deduplicated, excluding itself.
    depends_on: list[str]
    #: True when the query references its own name.
    recursive: bool
    sources: list[SourceRef]
    findings: list[Finding]
    errors: list[ParseError]
    #: Names referenced that are neither a step, a query in the paste, nor library.
    unresolved: list[str]
    #: First step on the path to the result that probably will not fold, or None.
    #:
    #: An estimate, and labelled as one everywhere it is shown. Real folding depends
    #: on the connector, the argument values and the gateway; a tool that printed a
    #: verdict would be confidently wrong often enough to be worse than silent.
    fold_break_step: str | None

    #: Slug, unique across the paste.
    id: str = ""
    #: Queries that reference this one.
    used_by: list[str] = field(default_factory=list)
    #: Longest distance from a query that depends on nothing.
    #:
    #: Layer 0 is the sources, and a query sits one past the deepest thing it reads,
    #: so every edge points rightward. Longest path rather than shortest: with
    #: shortest, a query reading both a source and a deep chain would land beside
    #: the source and its own input would sit to its right.
    layer: int = 0
    #: The source systems this query reaches through its whole upstream chain, not
    #: only its own `let`. A query that only references other queries has no
    #: connector of its own and would otherwise look sourceless.
    all_source_kinds: list[str] = field(default_factory=list)
    #: Primary source kind — the one a canvas would colour by.
    source_kind: str | None = None
    #: True when this query is part of a reference cycle.
    in_cycle: bool = False

    def brief(self) -> dict:
        return {
            "name": self.name,
            "id": self.id,
            "kind": self.kind,
            "layer": self.layer,
            "steps": len(self.steps),
            "dependsOn": list(self.depends_on),
            "usedBy": list(self.used_by),
            "sourceKind": self.source_kind,
        }

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "id": self.id,
            "kind": self.kind,
            "description": self.description,
            "group": self.group,
            "loadEnabled": self.load_enabled,
            "source": self.source,
            "line": self.line,
            "steps": [s.to_dict() for s in self.steps],
            "resultStep": self.result_step,
            "parameters": list(self.parameters),
            "dependsOn": list(self.depends_on),
            "usedBy": list(self.used_by),
            "recursive": self.recursive,
            "sources": [s.to_dict() for s in self.sources],
            "findings": [f.to_dict() for f in self.findings],
            "errors": [
                {"message": e.message, "line": e.line, "start": e.start} for e in self.errors
            ],
            "unresolved": list(self.unresolved),
            "foldBreakStep": self.fold_break_step,
            "layer": self.layer,
            "allSourceKinds": list(self.all_source_kinds),
            "sourceKind": self.source_kind,
            "inCycle": self.in_cycle,
        }


# ---------------------------------------------------------------------------
# Analysing one query
# ---------------------------------------------------------------------------
#
# Everything here reads the resolved reference list rather than the text, so a step
# named `Source` that happens to match a query name cannot be misattributed.
#
# The analyses divide into two kinds and the code keeps them apart:
#
#   - **Facts.** The step list, what reads what, which steps are unreachable, which
#     connector was called with which arguments, what a native query says. All read
#     directly off the syntax.
#   - **Estimates.** Whether a step folds, and whether a pattern is a performance
#     problem. These are heuristics, they are labelled as such, and their reasons
#     are carried rather than reduced to a boolean — a modeller can judge a reason
#     and cannot judge a verdict.

_TABLE_OR_LIST_CALL = re.compile(r"\b(Table|List)\.[A-Za-z]")
_NOW_CALL = re.compile(r"\bDateTime\.(LocalNow|FixedLocalNow)\b|\bDate\.From\b")


def _arg(args: list[Any], index: int) -> Any:
    """`args[index]`, or None past the end — JavaScript's out-of-range read."""
    return args[index] if 0 <= index < len(args) else None


def _unwrap(node: Any) -> Any:
    """Look through parentheses to the expression that matters."""
    current = node
    for _ in range(8):
        if current is None:
            return None
        if current.type == "Paren":
            current = current.inner
            continue
        return current
    return current


def _text_of(node: Any) -> str | None:
    """A text literal's decoded value, or None when the node is not one."""
    inner = _unwrap(node)
    if inner is not None and inner.type == "Literal" and inner.kind == "text":
        return inner.value
    return None


def _text_list_of(node: Any) -> list[str]:
    """A list of text literals, e.g. `{"A", "B"}`. Non-literal items are skipped."""
    inner = _unwrap(node)
    if inner is None:
        return []
    if inner.type == "Literal" and inner.kind == "text":
        return [inner.value]
    if inner.type != "List":
        return []
    return [v for v in (_text_of(item) for item in inner.items) if v is not None]


def _pair_list_of(node: Any, source: str) -> list[dict]:
    """A list of pairs, `{{"old", "new"}, …}` — how renames and type changes are written.

    The second element is frequently *not* a text literal: in
    `Table.TransformColumnTypes` it is a type expression (`type number`,
    `Int64.Type`), so its source text is taken instead. That is why this returns
    strings rather than nodes.
    """
    inner = _unwrap(node)
    if inner is None or inner.type != "List":
        return []

    pairs: list[dict] = []
    for item in inner.items:
        pair = _unwrap(item)
        if pair is None or pair.type != "List" or not pair.items:
            continue
        first = _text_of(pair.items[0])
        if first is None:
            continue
        second_node = _arg(pair.items, 1)
        if second_node is None:
            second = ""
        else:
            second = _text_of(second_node)
            if second is None:
                second = source[second_node.span.start : second_node.span.end].strip()
        pairs.append({"first": first, "second": second})
    return pairs


def _aggregate_names(node: Any) -> list[str]:
    """Column names a `Table.Group` aggregation list introduces.

    `{{"Incoming Backlog", each List.Sum([Daily Backlog]), type number}}` — the first
    element of each inner list names the new column. Same shape as a rename list,
    different meaning, so it gets its own reader rather than sharing one.
    """
    inner = _unwrap(node)
    if inner is None or inner.type != "List":
        return []
    out: list[str] = []
    for item in inner.items:
        spec = _unwrap(item)
        if spec is None or spec.type != "List":
            continue
        name = _text_of(_arg(spec.items, 0))
        if name is not None:
            out.append(name)
    return out


def _column_delta(call: CallSite | None, source: str) -> ColumnDelta:
    """What a step's outermost library call does to the columns.

    Read off the arguments, which means it only reports what the M literally states.
    `Table.RemoveColumns(prev, Table.ColumnNames(other))` yields nothing rather than
    a guess, and that is the right answer — the column list is not knowable without
    evaluating the query.
    """
    delta = ColumnDelta()
    if call is None:
        return delta
    args = call.args

    if call.name in ("Table.AddColumn", "Table.AddIndexColumn"):
        name = _text_of(_arg(args, 1))
        if name:
            delta.added.append(name)

    elif call.name == "Table.DuplicateColumn":
        name = _text_of(_arg(args, 2))
        if name:
            delta.added.append(name)

    elif call.name == "Table.RemoveColumns":
        delta.removed.extend(_text_list_of(_arg(args, 1)))

    elif call.name == "Table.SelectColumns":
        delta.kept.extend(_text_list_of(_arg(args, 1)))

    elif call.name == "Table.RenameColumns":
        delta.renamed.extend(
            {"from": p["first"], "to": p["second"]} for p in _pair_list_of(_arg(args, 1), source)
        )

    elif call.name in ("Table.TransformColumnTypes", "Table.TransformColumns"):
        delta.retyped.extend(
            {"name": p["first"], "type": p["second"]}
            for p in _pair_list_of(_arg(args, 1), source)
        )

    elif call.name == "Table.Group":
        delta.kept.extend(_text_list_of(_arg(args, 1)))
        delta.added.extend(_aggregate_names(_arg(args, 2)))

    elif call.name in ("Table.NestedJoin", "Table.FuzzyNestedJoin"):
        # The fifth argument names the nested table column the join adds.
        name = _text_of(_arg(args, 4))
        if name:
            delta.added.append(name)

    elif call.name in ("Table.ExpandTableColumn", "Table.ExpandRecordColumn"):
        column = _text_of(_arg(args, 1))
        if column:
            delta.removed.append(column)
        # Argument 3 is the new names, argument 2 the source field names. The new
        # names win where given, since those are the columns that appear.
        renamed = _text_list_of(_arg(args, 3))
        delta.added.extend(renamed if renamed else _text_list_of(_arg(args, 2)))

    elif call.name == "Table.ExpandListColumn":
        # Expands in place: the column stays, the rows multiply.
        pass

    elif call.name == "Table.UnpivotOtherColumns":
        delta.kept.extend(_text_list_of(_arg(args, 1)))
        attribute = _text_of(_arg(args, 2))
        value = _text_of(_arg(args, 3))
        if attribute:
            delta.added.append(attribute)
        if value:
            delta.added.append(value)

    elif call.name == "Table.Unpivot":
        delta.removed.extend(_text_list_of(_arg(args, 1)))
        attribute = _text_of(_arg(args, 2))
        value = _text_of(_arg(args, 3))
        if attribute:
            delta.added.append(attribute)
        if value:
            delta.added.append(value)

    elif call.name == "Table.Pivot":
        value = _text_of(_arg(args, 2))
        if value:
            delta.removed.append(value)

    return delta


def _read_source(call: CallSite, line: int) -> SourceRef | None:
    """Read a connector call into a `SourceRef`.

    `native_query_arg == -1` means "the query is a field in the options record",
    which is how `Sql.Database(server, db, [Query = "…"])` is written. Both
    spellings are checked, because both are common and a tool that only found the
    positional one would show nothing for every SQL Server model.
    """
    connector = describe_connector(call.name)
    if connector is None:
        return None

    target = _text_of(_arg(call.args, connector.target_arg)) if connector.target_arg is not None else None
    database = (
        _text_of(_arg(call.args, connector.database_arg))
        if connector.database_arg is not None
        else None
    )

    native_query: str | None = None
    if connector.native_query_arg is not None:
        if connector.native_query_arg >= 0:
            native_query = _text_of(_arg(call.args, connector.native_query_arg))
        else:
            # Search every argument's options record — the position of the options
            # record differs by connector and is not worth tabulating.
            for arg in call.args:
                record = _unwrap(arg)
                if record is None or record.type != "Record":
                    continue
                for f in record.fields:
                    if f.name not in NATIVE_QUERY_FIELDS:
                        continue
                    value = _text_of(f.value)
                    if value:
                        native_query = value

    return SourceRef(
        function_name=call.name,
        label=connector.label,
        kind=connector.kind,
        tier=connector.tier,
        target=target,
        database=database,
        native_query=native_query,
        inline=connector.inline,
        step=call.step,
        line=line,
    )


def _line_of(source: str, offset: int) -> int:
    """1-based line of an offset within a string."""
    return 1 + source.count("\n", 0, min(offset, len(source)))


def _defining_call(calls: list[CallSite], step: str) -> CallSite | None:
    """The call that defines what a step *is*.

    Not simply the first call in the step: `Table.AddColumn(prev, "X", each
    List.Sum(…))` contains `List.Sum` too, and labelling that step "Summed" would be
    wrong. The defining call is the outermost one — the smallest span that encloses
    the others — and it is never inside a lambda, which is what excludes the `each`
    body.
    """
    own = [c for c in calls if c.step == step and c.lambda_depth == 0 and c.name]
    if not own:
        return None
    widest = own[0]
    for candidate in own[1:]:
        if candidate.span.end - candidate.span.start > widest.span.end - widest.span.start:
            widest = candidate
    return widest


def _is_parameter_query(ast: Any) -> bool:
    body = _unwrap(ast)
    if body is None or body.type != "Meta":
        return False
    record = _unwrap(body.meta)
    if record is None or record.type != "Record":
        return False
    for f in record.fields:
        if f.name != "IsParameterQuery":
            continue
        value = _unwrap(f.value)
        if value is not None and value.type == "Literal" and value.value != "false":
            return True
    return False


def _classify_kind(ast: Any, is_function: bool, steps: list[Step], result_step: str | None) -> str:
    """Which query kind this is.

    `parameter` is decided by the `meta [IsParameterQuery = true]` record Power
    Query writes, which is a fact rather than a guess. The rest fall out of the
    namespace of the result step's function, and default to `table` when there are
    steps at all — because a query with steps is overwhelmingly a table, and being
    wrong here only mislabels a badge.
    """
    if _is_parameter_query(ast):
        return "parameter"
    if is_function:
        return "function"

    if result_step:
        result = next((s for s in steps if s.name == result_step), None)
    else:
        result = steps[-1] if steps else None
    fn = result.function_name if result is not None else None
    if fn:
        if fn.startswith("Table.") or fn == "#table":
            return "table"
        if fn.startswith("List."):
            return "list"
        if fn.startswith("Record."):
            return "record"
    if steps:
        return "table"

    body = _unwrap(ast)
    if body is not None:
        if body.type == "List":
            return "list"
        if body.type == "Record":
            return "record"
        if body.type == "Literal":
            return "scalar"
    return "unknown"


def _reachable(steps: list[Step], result_step: str | None, roots_from_body: list[str]) -> set[str]:
    """Steps reachable from the query's result, walking `reads` backwards.

    When the `in` clause is an expression rather than a plain step name, the roots
    are every step that expression references — which is why the resolver records
    references with a null step for exactly that case.
    """
    by_name = {s.name: s for s in steps}
    seen: set[str] = set()
    queue = [result_step] if result_step else list(roots_from_body)

    # No stated result at all: treat every step as live rather than every step as
    # dead. Reporting a whole query as dead code because its `in` clause could not
    # be read would be a confident wrong answer.
    if not queue:
        return {s.name for s in steps}

    while queue:
        name = queue.pop()
        if name in seen:
            continue
        seen.add(name)
        step = by_name.get(name)
        if step is None:
            continue
        for read in step.reads:
            if read not in seen:
                queue.append(read)
    return seen


def _describe_calless_step(refs: list[Reference], step_name: str) -> Operation:
    """Label a step that calls no function.

    Three cases, all worth distinguishing in a step list:

      - `Source = #"Other Query"` — a reference to another query. The opening step of
        every derived query in every model, so it must not read as "Step".
      - `x = #"Previous Step"` — an alias, usually left behind by an edit.
      - anything else — a literal or a computed value.
    """
    queries = [r for r in refs if r.kind == "query"]
    if len(queries) == 1:
        return Operation(f"Reference to {queries[0].name}", "reference", None)
    if len(queries) > 1:
        return Operation("Reference to queries", "reference", None)
    steps = [r for r in refs if r.kind == "step" and r.name != step_name]
    if len(steps) == 1:
        return Operation(f"Alias of {steps[0].name}", "reference", None)
    return Operation("Value", "other", None)


def _fold_break_for(
    base: str | None, shape: str, call: CallSite | None, source: str
) -> str | None:
    """Refine a library function's generic folding reason with what the arguments say.

    `Table.AddColumn` carries "a custom column body is arbitrary M", but the
    distinction that actually matters is whether the body is a simple projection over
    the row or a call into another table. Saying which one it is turns a boilerplate
    warning into a usable one.
    """
    if call is None:
        return base

    if shape == "addColumn" and call.name == "Table.AddColumn":
        body = _arg(call.args, 2)
        if body is None:
            return base
        text = source[body.span.start : body.span.end]
        # A body that calls into a table is a different order of problem from one
        # that does arithmetic on the current row.
        if _TABLE_OR_LIST_CALL.search(text):
            return "The column body queries another table, so the whole outer table must be read first."
        if _NOW_CALL.search(text):
            return "The column body depends on the current time, which the source cannot evaluate."
        return base

    return base


def _unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _list_names(names: list[str]) -> str:
    """`a`, `b` and `c` — for a message, not for display in a table."""
    quoted = [f"`{n}`" for n in names]
    if len(quoted) == 1:
        return quoted[0]
    if len(quoted) == 2:
        return f"{quoted[0]} and {quoted[1]}"
    return f"{', '.join(quoted[:-1])} and {quoted[-1]}"


def _step_let_bindings(ast: Any, step_names: list[str]) -> dict[str, Binding]:
    """The step-bearing `let`'s bindings by name, so a step's source can be sliced.

    Re-derived here rather than returned from the resolver because the resolver's
    job is references and this is spans; keeping the two apart means neither has to
    carry the other's concerns. `step_names` from the resolver is the authority on
    *which* `let` is the step list, so the two cannot disagree.
    """
    out: dict[str, Binding] = {}
    if not step_names:
        return out

    # A separator that cannot occur in a step name. A space could: a model with a
    # step called `A B` and one with steps `A` and `B` would produce the same key.
    wanted = "\u0000".join(step_names)
    found = False

    def visit(node: Any) -> None:
        nonlocal found
        if node is None or found:
            return
        if node.type == "Let" and "\u0000".join(b.name for b in node.bindings) == wanted:
            for binding in node.bindings:
                out[binding.name] = binding
            found = True
            return
        if node.type == "Paren":
            visit(node.inner)
        elif node.type == "Meta":
            visit(node.value)
        elif node.type == "Function":
            visit(node.body)
        elif node.type == "Let":
            for binding in node.bindings:
                visit(binding.value)
            visit(node.body)

    visit(ast)
    return out


def analyse_query(raw: RawQuery, query_names: set[str]) -> Query:
    """Turn a parsed query into the facts worth showing."""
    resolved = resolve_query(raw.ast, query_names)
    references, calls = resolved.references, resolved.calls

    step_names = resolved.step_names
    step_set = set(step_names)

    # Steps, in declaration order. `reads` is restricted to *this query's* steps: a
    # nested `let`'s binding resolves to `local` and never appears here, so a helper
    # variable inside one step cannot masquerade as a step edge.
    bindings = _step_let_bindings(raw.ast, step_names)
    steps: list[Step] = []
    for index, name in enumerate(step_names):
        own = [r for r in references if r.step == name]
        call = _defining_call(calls, name)
        binding = bindings.get(name)
        operation = (
            describe_operation(call.name)
            if call is not None
            # A step with no function call at all is not "Step" — it is one of three
            # recognisable things, and the first is the most common step in any
            # model: `Source = #"Other Query"`, which every referencing query opens
            # with. Labelling those 30-odd steps "Step" wasted the one column a
            # reader scans.
            else _describe_calless_step(own, name)
        )

        reads = _unique(
            r.name
            for r in own
            if r.kind == "step" and r.name != name and r.name in step_set
        )
        queries = _unique(r.name for r in own if r.kind == "query" and r.name != raw.name)
        # Invoking a custom function per row is excluded, because that *is* the
        # pattern rather than a misuse of it: `each #"Transform File"([Content])` is
        # what the Combine Files experience generates and what every folder import
        # looks like. Flagging it made the finding noise instead of a signal. The
        # pattern that matters is a table referenced as a *value* inside the lambda,
        # where the whole table is re-read for each row.
        row_by_row = _unique(
            r.name
            for r in own
            if r.lambda_depth > 0
            and r.kind in ("query", "step")
            and r.name != name
            and not r.is_call
        )

        steps.append(
            Step(
                name=name,
                index=index,
                expression=(
                    raw.source[binding.value_span.start : binding.value_span.end].strip()
                    if binding is not None
                    else ""
                ),
                line=_line_of(raw.source, binding.span.start) if binding is not None else 1,
                label=operation.label,
                shape=operation.shape,
                function_name=call.name if call is not None else None,
                reads=reads,
                read_by=[],
                queries=queries,
                dead=False,
                columns=_column_delta(call, raw.source),
                fold_break=_fold_break_for(
                    operation.fold_break, operation.shape, call, raw.source
                ),
                row_by_row=row_by_row,
            )
        )

    by_name = {s.name: s for s in steps}
    for step in steps:
        for read in step.reads:
            target = by_name.get(read)
            if target is not None:
                target.read_by.append(step.name)

    roots_from_body = _unique(
        r.name for r in references if r.step is None and r.kind == "step"
    )
    live = _reachable(steps, resolved.result_step, roots_from_body)
    for step in steps:
        step.dead = step.name not in live

    sources = [
        s
        for s in (_read_source(call, _line_of(raw.source, call.span.start)) for call in calls)
        if s is not None
    ]

    depends_on = _unique(
        r.name for r in references if r.kind == "query" and r.name != raw.name
    )
    recursive = any(r.kind == "query" and r.name == raw.name for r in references)
    unresolved = _unique(r.name for r in references if r.kind == "unresolved")

    kind = _classify_kind(raw.ast, resolved.is_function, steps, resolved.result_step)

    # The break is the first step *on the path to the result* that cannot fold — a
    # dead step's folding is irrelevant to the query's cost.
    fold_break_step = next(
        (s.name for s in steps if not s.dead and s.fold_break is not None), None
    )
    if fold_break_step is None and any(s.inline for s in sources):
        fold_break_step = steps[0].name if steps else None

    findings = _collect_findings(
        raw=raw,
        steps=steps,
        sources=sources,
        unresolved=unresolved,
        recursive=recursive,
        fold_break_step=fold_break_step,
    )

    return Query(
        name=raw.name,
        kind=kind,
        description=raw.description,
        group=raw.group,
        load_enabled=raw.load_enabled,
        source=raw.source,
        line=raw.line,
        steps=steps,
        result_step=resolved.result_step,
        parameters=resolved.parameters,
        depends_on=depends_on,
        recursive=recursive,
        sources=sources,
        findings=findings,
        errors=raw.errors,
        unresolved=unresolved,
        fold_break_step=fold_break_step,
    )


def _collect_findings(
    raw: RawQuery,
    steps: list[Step],
    sources: list[SourceRef],
    unresolved: list[str],
    recursive: bool,
    fold_break_step: str | None,
) -> list[Finding]:
    """The observations worth showing about one query.

    Ordered by how much they should change what someone does: a correctness or
    performance surprise first, then facts about the shape of the query.
    """
    findings: list[Finding] = []

    for step in steps:
        if not step.row_by_row or step.dead:
            continue
        findings.append(
            Finding(
                "warning",
                "row-by-row",
                f"Reads {_list_names(step.row_by_row)} from inside an `each`, so it is "
                "re-evaluated for every row rather than once.",
                step.name,
            )
        )

    dead = [s for s in steps if s.dead]
    if dead:
        message = (
            f"`{dead[0].name}` is never reached from the query's result."
            if len(dead) == 1
            else f"{len(dead)} steps are never reached from the query's result: "
            f"{_list_names([s.name for s in dead])}."
        )
        findings.append(Finding("warning", "dead-step", message, dead[0].name))

    if unresolved:
        one = len(unresolved) == 1
        findings.append(
            Finding(
                "warning",
                "unresolved-reference",
                # The likeliest cause by far, and worth saying, because the graph
                # will be missing an edge and the reader needs to know it is the
                # paste and not the model.
                f"References {_list_names(unresolved)}, which {'is' if one else 'are'} not a "
                f"query in this paste, a step, or an M function. Paste the missing "
                f"quer{'y' if one else 'ies'} to complete the graph.",
                None,
            )
        )

    if recursive:
        findings.append(
            Finding(
                "info",
                "recursive",
                "References itself. Recursion is legal in M but rare outside a function.",
                None,
            )
        )

    for source in (s for s in sources if s.inline):
        findings.append(
            Finding(
                "info",
                "inline-data",
                'The data is written into the query itself rather than fetched — an '
                '"Enter Data" table. It can only be changed by editing the query.',
                source.step,
            )
        )

    for source in (s for s in sources if s.kind == "model"):
        extra = f", {source.database}" if source.database else ""
        findings.append(
            Finding(
                "warning",
                "chained-model",
                f"Imports from another semantic model ({source.label}{extra}). A chained "
                "import refreshes only after its upstream model does.",
                source.step,
            )
        )

    for source in (s for s in sources if s.native_query):
        lines = len((source.native_query or "").split("\n"))
        findings.append(
            Finding(
                "info",
                "native-query",
                f"Carries a {lines}-line native query against "
                f"{source.target if source.target else source.label}. Folding stops at the "
                "source, which is doing the work.",
                source.step,
            )
        )

    if fold_break_step:
        step = next((s for s in steps if s.name == fold_break_step), None)
        if step is not None and step.fold_break:
            findings.append(
                Finding(
                    "info",
                    "no-fold",
                    f"Folding probably stops at `{fold_break_step}`. {step.fold_break}",
                    fold_break_step,
                )
            )

    if raw.errors:
        plural = "" if len(raw.errors) == 1 else "s"
        findings.append(
            Finding(
                "warning",
                "parse-recovered",
                f"{len(raw.errors)} part{plural} of this query could not be read; the rest "
                "was parsed. References inside the unread part are still counted.",
                None,
            )
        )

    return findings


# ---------------------------------------------------------------------------
# The query dependency graph
# ---------------------------------------------------------------------------
#
# A query's band is a **dependency layer**, not a folder. That is the one place
# this deliberately diverges from its TMDL sibling, and the reason is that a paste
# of M carries no folder: Power Query's query groups live in the mashup metadata,
# not in the query text, so three of the four accepted paste formats cannot state
# one. Layers are derived, so they work on every paste.
#
# Layer 0 is every query that references no other query — the sources. A query then
# sits one past the **deepest** thing it reads. Longest path rather than shortest is
# load-bearing: with shortest, a query reading both a raw source and the end of a
# long chain would land in layer 1, with its own input somewhere to its right, and
# every edge in the picture would stop meaning "flows onward".

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")
_SLUG_EDGES = re.compile(r"(^-|-$)")

# Order source kinds so a palette is stable across pastes. Positional assignment
# means an unstable order would recolour everything when a query was added.
KIND_ORDER = [
    "database", "cloud", "sharepoint", "file", "folder", "web", "service", "model",
    "inline",
]


def slugify(name: str) -> str:
    return _SLUG_EDGES.sub("", _SLUG_STRIP.sub("-", name.lower().replace("%", " pct")))


def unique_slugs(names: list[str]) -> dict[str, str]:
    """Unique ids for lookups.

    Slugs collide once punctuation is stripped — `Prior Month Rev Forecast CSV` and
    `Prior Month Rev Forecast CSV (CreatedDate)` differ only by a parenthesised
    suffix. A name that slugifies to nothing still gets a usable id.
    """
    counts: dict[str, int] = {}
    ids: dict[str, str] = {}
    for index, name in enumerate(names):
        base = slugify(name) or f"query-{index + 1}"
        n = counts.get(base, 0) + 1
        counts[base] = n
        ids[name] = base if n == 1 else f"{base}-{n}"
    return ids


def order_kinds(kinds: Iterable[str]) -> list[str]:
    return sorted(kinds, key=lambda k: KIND_ORDER.index(k) if k in KIND_ORDER else len(KIND_ORDER))


def _find_cycles(queries: list[Query]) -> set[str]:
    """Every query that sits on a reference cycle, found by Tarjan's algorithm."""
    by_name = {q.name: q for q in queries}
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    cyclic: set[str] = set()
    counter = 0

    def connect(name: str) -> None:
        nonlocal counter
        index[name] = counter
        low[name] = counter
        counter += 1
        stack.append(name)
        on_stack.add(name)

        query = by_name.get(name)
        for dependency in (query.depends_on if query else []):
            if dependency not in by_name:
                continue
            if dependency not in index:
                connect(dependency)
                low[name] = min(low[name], low[dependency])
            elif dependency in on_stack:
                low[name] = min(low[name], index[dependency])

        if low[name] == index[name]:
            component: list[str] = []
            while True:
                member = stack.pop()
                on_stack.discard(member)
                component.append(member)
                if member == name:
                    break
            # A single node is only a cycle if it references itself.
            head = by_name.get(component[0])
            is_cycle = len(component) > 1 or (
                head is not None and component[0] in head.depends_on
            )
            if is_cycle:
                cyclic.update(component)

    for query in queries:
        if query.name not in index:
            connect(query.name)
    return cyclic


def _compute_layers(queries: list[Query], cycles: set[str]) -> dict[str, int]:
    """Longest-path layering, with cycles collapsed.

    A reference cycle has no longest path — the recursion would not terminate — so
    members of a cycle are pinned to the deepest layer reachable from *outside* the
    cycle and the internal edges are ignored. Cycles are illegal in Power Query and
    would fail to refresh, but they turn up in a paste that is missing a query, and
    a tool that hung on one would be useless exactly when it was needed.
    """
    by_name = {q.name: q for q in queries}
    layers: dict[str, int] = {}
    visiting: set[str] = set()

    def depth(name: str) -> int:
        cached = layers.get(name)
        if cached is not None:
            return cached
        if name in visiting:
            return 0

        query = by_name.get(name)
        if query is None:
            return 0

        visiting.add(name)
        deepest = -1
        for dependency in query.depends_on:
            if dependency not in by_name:
                continue
            # Skip the edge that closes a cycle rather than the whole node: the
            # query keeps whatever depth its non-cyclic inputs give it.
            if name in cycles and dependency in cycles:
                continue
            deepest = max(deepest, depth(dependency))
        visiting.discard(name)

        result = deepest + 1
        layers[name] = result
        return result

    for query in queries:
        depth(query.name)
    return layers


def _resolve_source_kinds(queries: list[Query]) -> dict[str, list[str]]:
    """Source kinds reached through the whole upstream chain, not only a query's own `let`.

    A query that just references two other queries has no connector of its own, and
    would otherwise show no source at all — which is wrong in the way that matters
    most, because the whole downstream side of a real model is queries like that.
    """
    by_name = {q.name: q for q in queries}
    cache: dict[str, list[str]] = {}
    state = {"hit_cycle": False}

    def resolve(name: str, seen: set[str]) -> list[str]:
        cached = cache.get(name)
        if cached:
            return cached
        if name in seen:
            # A cycle. Return nothing *and* say a cycle was hit, so the partial
            # answer this produces is not frozen into the cache — a cached partial
            # would make the result depend on which query the outer loop happened to
            # start from.
            state["hit_cycle"] = True
            return []
        seen.add(name)

        query = by_name.get(name)
        if query is None:
            return []

        # Parser-tier calls are excluded: `Json.Document` over a literal binary says
        # nothing about where any data came from, and counting it put a phantom
        # `file` source on every Enter Data table.
        kinds = {s.kind for s in query.sources if s.tier != "parser"}
        for dependency in query.depends_on:
            kinds.update(resolve(dependency, seen))

        result = order_kinds(kinds)
        if not state["hit_cycle"]:
            cache[name] = result
        return result

    out: dict[str, list[str]] = {}
    for query in queries:
        state["hit_cycle"] = False
        out[query.name] = resolve(query.name, set())
    return out


def _primary_kind(query: Query, inherited: list[str]) -> str | None:
    """The one kind a query would be coloured by.

    Tier order, then `KIND_ORDER` within a tier. A real connector wins; failing that,
    inline data; failing that, a format parser; failing that, whatever the upstream
    chain reaches.

    The tier ordering is what stops an Enter Data table — written as
    `Table.FromRows(Json.Document(Binary.Decompress(…)))` — being read as a JSON
    *file* source, which is what happened when the only test was "not inline".
    """
    for tier in ("connector", "inline", "parser"):
        matching = [s.kind for s in query.sources if s.tier == tier]
        if matching:
            return order_kinds(matching)[0]
    return inherited[0] if inherited else None


def _model_findings(queries: list[Query]) -> list[Finding]:
    """Observations about the model as a whole, which no single query can see.

    The duplicate-source one earns its place on a real model alone: six separate
    queries opening the same SharePoint site with `SharePoint.Files`, each of which
    is its own round trip on every refresh. Nothing in the Power Query Editor puts
    those six side by side.
    """
    findings: list[Finding] = []

    by_target: dict[str, dict] = {}
    for query in queries:
        for source in query.sources:
            if source.inline or not source.target:
                continue
            key = f"{source.function_name}::{source.target}"
            entry = by_target.setdefault(key, {"label": source.target, "queries": []})
            if query.name not in entry["queries"]:
                entry["queries"].append(query.name)

    for entry in by_target.values():
        if len(entry["queries"]) < 3:
            continue
        findings.append(
            Finding(
                "info",
                "duplicate-source",
                f"{len(entry['queries'])} queries connect to {entry['label']} separately. "
                "Each is its own round trip on refresh; one query others reference "
                "would replace them.",
                entry["label"],
            )
        )

    cyclic = [q for q in queries if q.in_cycle]
    if cyclic:
        findings.append(
            Finding(
                "warning",
                "unresolved-reference",
                f"{len(cyclic)} queries form a reference cycle "
                f"({', '.join(q.name for q in cyclic)}). Power Query cannot refresh a "
                "cycle — most often this means a query in the paste is not the one the "
                "model actually has.",
                None,
            )
        )

    return findings


def build_model(text: str) -> tuple[list[Query], str, list[Diagnostic], list[Finding]]:
    """Parse a paste and resolve every query's place in the graph.

    Returns the queries, the detected format, the splitter's diagnostics and the
    model-level findings. `Model` wraps this; call that unless you want the pieces.
    """
    document = split_document(text)
    if not document.queries:
        return [], document.format, document.diagnostics, []

    # Every query name is needed before any query can be analysed: whether an
    # identifier is a query reference depends on the whole set, so the resolver
    # cannot run one query at a time in isolation.
    names = {q.name for q in document.queries}
    queries = [analyse_query(q, names) for q in document.queries]

    ids = unique_slugs([q.name for q in queries])
    cycles = _find_cycles(queries)
    layer_of = _compute_layers(queries, cycles)

    used_by: dict[str, list[str]] = {q.name: [] for q in queries}
    for query in queries:
        for dependency in query.depends_on:
            if dependency in used_by:
                used_by[dependency].append(query.name)

    for query in queries:
        query.id = ids[query.name]
        query.used_by = used_by.get(query.name, [])
        query.layer = layer_of.get(query.name, 0)
        query.in_cycle = query.name in cycles

    source_kinds_of = _resolve_source_kinds(queries)
    for query in queries:
        query.all_source_kinds = source_kinds_of.get(query.name, [])
        query.source_kind = _primary_kind(query, query.all_source_kinds)

    _prune_scalar_row_by_row(queries)

    return queries, document.format, document.diagnostics, _model_findings(queries)


#: Query kinds that are a single value. Reading one inside a lambda costs nothing: it is a
#: constant, not a table being re-read per row.
SCALAR_KINDS = frozenset({"parameter", "scalar"})


def _prune_scalar_row_by_row(queries: list[Query]) -> None:
    """Drop `row-by-row` where the thing read inside the `each` is a scalar.

    The rule means one thing: a *table* referenced as a value inside a lambda, so the whole
    table is re-read for every row. `each [Date] >= #"Report Start"` is not that. A
    parameter is one value, evaluated once, and flagging it is a false positive of exactly
    the kind this project deletes rules for -- a warning a modeller cannot act on teaches
    them to ignore the next one.

    This runs here rather than in `analyse_query` because it needs the *other* queries'
    kinds, and those are only known once every query has been analysed. Per-row calls to a
    custom function are already excluded upstream, by `is_call`.
    """
    scalars = {q.name for q in queries if q.kind in SCALAR_KINDS}
    if not scalars:
        return
    for query in queries:
        touched = False
        for step in query.steps:
            if not step.row_by_row:
                continue
            kept = [name for name in step.row_by_row if name not in scalars]
            if len(kept) != len(step.row_by_row):
                step.row_by_row = kept
                touched = True
        if not touched:
            continue
        # Rebuilt rather than edited: the message names the references, so a pruned
        # message has to be regenerated or it would state something that is no longer
        # true. Row-by-row leads the list, which is the order `_collect_findings` sets.
        rest = [f for f in query.findings if f.code != "row-by-row"]
        query.findings = [
            Finding(
                "warning",
                "row-by-row",
                f"Reads {_list_names(step.row_by_row)} from inside an `each`, so it is "
                "re-evaluated for every row rather than once.",
                step.name,
            )
            for step in query.steps
            if step.row_by_row and not step.dead
        ] + rest


# ---------------------------------------------------------------------------
# The model
# ---------------------------------------------------------------------------


class Model:
    """A parsed paste of M, and the questions worth asking of it."""

    def __init__(self, text: str) -> None:
        queries, fmt, diagnostics, model_findings = build_model(text)
        self.queries = queries
        self.format = fmt
        self.diagnostics = diagnostics
        self.model_findings = model_findings
        self.by_name = {q.name: q for q in queries}
        self.by_id = {q.id: q for q in queries}

    @classmethod
    def from_text(cls, text: str) -> "Model":
        return cls(text)

    @classmethod
    def from_paths(cls, paths: Iterable[str | Path]) -> "Model":
        """Read `.pq`, `.m` and `.tmdl` files, or directories of them.

        Multiple files are concatenated and parsed as one blob, which is what a paste
        of a whole model looks like — and is required for cross-query references to
        resolve.
        """
        files: list[Path] = []
        for entry in paths:
            path = Path(entry)
            if path.is_dir():
                found = sorted(
                    p
                    for p in path.rglob("*")
                    if p.is_file() and p.suffix.lower() in (".pq", ".m", ".tmdl", ".txt")
                )
                files.extend(found)
            elif path.is_file():
                files.append(path)
            else:
                raise FileNotFoundError(f"No such file or directory: {path}")

        if not files:
            raise FileNotFoundError("No .pq, .m or .tmdl files found in the given paths.")

        parts = []
        for f in files:
            # Read verbatim: `newline=""` keeps CRLF intact, because how the file
            # ends its lines is one of the things this is meant to be immune to and
            # normalising it here would hide a regression rather than prevent one.
            with f.open(encoding="utf-8-sig", newline="") as handle:
                parts.append(handle.read())
        return cls("\n\n".join(parts))

    def __len__(self) -> int:
        return len(self.queries)

    def __iter__(self):
        return iter(self.queries)

    # -- lookups ------------------------------------------------------------

    def get(self, name: str) -> Query | None:
        """One query by exact name, then by case-insensitive name, then by id."""
        found = self.by_name.get(name)
        if found is not None:
            return found
        low = name.lower()
        for query in self.queries:
            if query.name.lower() == low or query.id == low:
                return query
        return None

    def find(self, needle: str) -> list[Query]:
        low = needle.lower()
        return [q for q in self.queries if low in q.name.lower()]

    def search(self, needle: str) -> list[Query]:
        """Queries matching on name, description, a step name, or a source.

        Searching the decoded SQL is the point of decoding it: a table name that
        appears only inside a native query is otherwise unfindable.
        """
        q = needle.strip().lower()
        if not q:
            return list(self.queries)
        out: list[Query] = []
        for item in self.queries:
            if (
                q in item.name.lower()
                or q in item.description.lower()
                or any(q in s.name.lower() for s in item.steps)
                or any(
                    q in s.label.lower()
                    or q in (s.target or "").lower()
                    or q in (s.native_query or "").lower()
                    for s in item.sources
                )
            ):
                out.append(item)
        return out

    # -- graph --------------------------------------------------------------

    @property
    def layers(self) -> list[int]:
        return sorted({q.layer for q in self.queries})

    @property
    def link_count(self) -> int:
        return sum(len(q.depends_on) for q in self.queries)

    @property
    def step_count(self) -> int:
        return sum(len(q.steps) for q in self.queries)

    @property
    def source_kinds(self) -> list[str]:
        return order_kinds({k for q in self.queries for k in q.all_source_kinds})

    def upstream(self, name: str) -> list[str]:
        """Every query this one reads, transitively. Cycle-safe."""
        return self._walk(name, lambda q: q.depends_on)

    def downstream(self, name: str) -> list[str]:
        """Every query that would break if this one changed. Cycle-safe."""
        return self._walk(name, lambda q: q.used_by)

    def _walk(self, name: str, edges: Callable[[Query], list[str]]) -> list[str]:
        root = self.get(name)
        if root is None:
            return []
        seen: set[str] = set()
        out: list[str] = []
        queue = list(edges(root))
        while queue:
            current = queue.pop(0)
            if current in seen or current == root.name:
                continue
            seen.add(current)
            out.append(current)
            found = self.by_name.get(current)
            if found is not None:
                queue.extend(edges(found))
        return out

    def focus(self, name: str) -> list[Query]:
        """One query and its direct neighbours — one hop, not the closure.

        One hop because the closure still drags in most of a real graph through
        shared ancestors, which is the opposite of narrowing.
        """
        root = self.get(name)
        if root is None:
            return []
        keep = {root.name, *root.depends_on, *root.used_by}
        return [q for q in self.queries if q.name in keep]

    # -- listings -----------------------------------------------------------

    def sources(self) -> list[tuple[Query, SourceRef]]:
        return [(q, s) for q in self.queries for s in q.sources]

    def findings(self) -> list[tuple[Query | None, Finding]]:
        """Every finding, model-level first."""
        out: list[tuple[Query | None, Finding]] = [(None, f) for f in self.model_findings]
        for query in self.queries:
            out.extend((query, f) for f in query.findings)
        return out

    def dead_steps(self) -> list[tuple[Query, Step]]:
        return [(q, s) for q in self.queries for s in q.steps if s.dead]

    def unresolved(self) -> list[tuple[Query, str]]:
        return [(q, name) for q in self.queries for name in q.unresolved]

    def operations(self) -> list[tuple[str, int]]:
        """Every library function the model's steps call, most-used first."""
        counts: dict[str, int] = {}
        for query in self.queries:
            for step in query.steps:
                if step.function_name:
                    counts[step.function_name] = counts.get(step.function_name, 0) + 1
        return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))

    # -- shaped output ------------------------------------------------------

    def overview(self) -> dict:
        return {
            "format": self.format,
            "queries": len(self.queries),
            "steps": self.step_count,
            "links": self.link_count,
            "layers": self.layers,
            "sourceKinds": self.source_kinds,
            "kinds": {
                kind: sum(1 for q in self.queries if q.kind == kind)
                for kind in sorted({q.kind for q in self.queries})
            },
            "parseErrors": sum(len(q.errors) for q in self.queries),
            "unresolved": sum(len(q.unresolved) for q in self.queries),
            "deadSteps": len(self.dead_steps()),
            "cycles": [q.name for q in self.queries if q.in_cycle],
            "diagnostics": [{"level": d.level, "message": d.message} for d in self.diagnostics],
            "modelFindings": [f.to_dict() for f in self.model_findings],
            "mostUsed": [
                {"name": q.name, "usedBy": len(q.used_by)}
                for q in sorted(self.queries, key=lambda q: (-len(q.used_by), q.name))
                if q.used_by
            ][:10],
        }

    def dump(self) -> dict:
        return {
            "version": __version__,
            "format": self.format,
            "queries": [q.to_dict() for q in self.queries],
            "layers": self.layers,
            "linkCount": self.link_count,
            "stepCount": self.step_count,
            "sourceKinds": self.source_kinds,
            "diagnostics": [{"level": d.level, "message": d.message} for d in self.diagnostics],
            "modelFindings": [f.to_dict() for f in self.model_findings],
        }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _did_you_mean(model: Model, name: str) -> str:
    near = model.find(name)
    if near:
        return f"No query named {name!r}. Did you mean:\n" + "\n".join(
            f"  {q.name}" for q in near[:10]
        )
    return f"No query named {name!r}."


def _fmt_overview(model: Model) -> str:
    o = model.overview()
    out = [
        f"{o['queries']} queries | {o['steps']} steps | {o['links']} links | "
        f"{len(o['layers'])} layers | read as {o['format']}"
    ]
    kinds = " | ".join(f"{n} {k}" for k, n in o["kinds"].items())
    if kinds:
        out.append(kinds)
    if o["sourceKinds"]:
        out.append("sources: " + ", ".join(SOURCE_KIND_LABELS.get(k, k) for k in o["sourceKinds"]))

    problems = []
    if o["parseErrors"]:
        problems.append(f"{o['parseErrors']} parse errors")
    if o["unresolved"]:
        problems.append(f"{o['unresolved']} unresolved references")
    if o["deadSteps"]:
        problems.append(f"{o['deadSteps']} dead steps")
    if o["cycles"]:
        problems.append(f"{len(o['cycles'])} queries in a cycle")
    if problems:
        out.append(" | ".join(problems))

    for d in o["diagnostics"]:
        out.append(f"[{d['level']}] {d['message']}")
    for f in o["modelFindings"]:
        out.append(f"[{f['level']}] {f['message']}")

    out.append("")
    out.append("By layer:")
    for layer in o["layers"]:
        members = [q.name for q in model.queries if q.layer == layer]
        out.append(f"  {layer}  {', '.join(members)}")

    if o["mostUsed"]:
        out.append("")
        out.append("Most referenced:")
        for entry in o["mostUsed"]:
            out.append(f"  {entry['usedBy']:>4}  {entry['name']}")
    return "\n".join(out)


def _fmt_query(model: Model, name: str) -> str:
    query = model.get(name)
    if query is None:
        return _did_you_mean(model, name)

    out = [
        query.name,
        f"  kind    {query.kind}" + (f" ({', '.join(query.parameters)})" if query.parameters else ""),
        f"  layer   {query.layer}",
    ]
    if query.group:
        out.append(f"  group   {query.group}")
    if query.load_enabled is not None:
        out.append(f"  loaded  {'yes' if query.load_enabled else 'no'}")
    if query.source_kind:
        out.append(
            f"  source  {SOURCE_KIND_LABELS.get(query.source_kind, query.source_kind)}"
            + (
                f"  (chain reaches {', '.join(query.all_source_kinds)})"
                if len(query.all_source_kinds) > 1
                else ""
            )
        )
    if query.description:
        out.append(f"  note    {query.description}")

    out.append("")
    out.append(f"{len(query.steps)} steps"
               + (f", result is `{query.result_step}`" if query.result_step else ", result is an expression"))
    for step in query.steps:
        flags = []
        if step.dead:
            flags.append("DEAD")
        if step.fold_break:
            flags.append("fold break")
        suffix = f"  [{', '.join(flags)}]" if flags else ""
        out.append(f"  {step.index:>3}  {step.name}  —  {step.label}{suffix}")

    for label, names in (("Depends on", query.depends_on), ("Used by", query.used_by)):
        out.append("")
        out.append(f"{label} ({len(names)}):")
        out.extend(f"  {n}" for n in names) if names else out.append("  none")

    if query.sources:
        out.append("")
        out.append("Sources:")
        for source in query.sources:
            target = f" — {source.target}" if source.target else ""
            db = f" / {source.database}" if source.database else ""
            sql = (
                f"  ({len(source.native_query.split(chr(10)))}-line native query)"
                if source.native_query
                else ""
            )
            out.append(f"  {source.label}{target}{db}{sql}")

    if query.findings:
        out.append("")
        out.append("Findings:")
        for finding in query.findings:
            where = f" [{finding.step}]" if finding.step else ""
            out.append(f"  [{finding.level}]{where} {finding.message}")

    return "\n".join(out)


def _fmt_steps(model: Model, name: str) -> str:
    query = model.get(name)
    if query is None:
        return _did_you_mean(model, name)

    out = [f"{query.name} — {len(query.steps)} steps"]
    for step in query.steps:
        out.append("")
        head = f"{step.index:>3}  {step.name}  —  {step.label}"
        if step.dead:
            head += "   [DEAD: nothing reaches this from the result]"
        out.append(head)
        if step.reads:
            out.append(f"       reads: {', '.join(step.reads)}")
        if step.queries:
            out.append(f"       queries: {', '.join(step.queries)}")
        cols = step.columns
        if cols.added:
            out.append(f"       + {', '.join(cols.added)}")
        if cols.removed:
            out.append(f"       - {', '.join(cols.removed)}")
        if cols.kept:
            out.append(f"       kept: {', '.join(cols.kept)}")
        for rename in cols.renamed:
            out.append(f"       {rename['from']} -> {rename['to']}")
        for retype in cols.retyped:
            out.append(f"       {retype['name']} as {retype['type']}")
        if step.fold_break:
            out.append(f"       fold: {step.fold_break}")
        if step.row_by_row:
            out.append(f"       row-by-row: {', '.join(step.row_by_row)}")
    return "\n".join(out)


def _fmt_impact(model: Model, name: str) -> str:
    query = model.get(name)
    if query is None:
        return _did_you_mean(model, name)
    down = model.downstream(query.name)
    up = model.upstream(query.name)
    out = [
        query.name,
        f"  {len(down)} queries break if this changes",
        f"  {len(query.used_by)} of them reference it directly",
        f"  it reads {len(up)} queries, {len(query.depends_on)} directly",
        "",
    ]
    if down:
        out.append("Breaks:")
        for n in down:
            found = model.by_name.get(n)
            direct = "*" if n in query.used_by else " "
            out.append(f"  {direct} L{found.layer if found else '?'}  {n}")
    else:
        out.append("Nothing references it.")
    if up:
        out.append("")
        out.append("Reads, transitively:")
        out.extend(f"  {n}" for n in up)
    return "\n".join(out)


def _fmt_sources(model: Model) -> str:
    rows = model.sources()
    if not rows:
        return "No connector calls found."
    out = [f"{len(rows)} source calls"]
    by_kind: dict[str, list[tuple[Query, SourceRef]]] = {}
    for query, source in rows:
        by_kind.setdefault(source.kind, []).append((query, source))
    for kind in order_kinds(by_kind):
        out.append("")
        out.append(f"{SOURCE_KIND_LABELS.get(kind, kind)}:")
        for query, source in by_kind[kind]:
            target = f" — {source.target}" if source.target else ""
            db = f" / {source.database}" if source.database else ""
            sql = (
                f"  ({len(source.native_query.split(chr(10)))}-line query)"
                if source.native_query
                else ""
            )
            out.append(f"  {query.name}: {source.label}{target}{db}{sql}")
    return "\n".join(out)


def _fmt_sql(model: Model, name: str) -> str:
    query = model.get(name)
    if query is None:
        return _did_you_mean(model, name)
    carrying = [s for s in query.sources if s.native_query]
    if not carrying:
        return f"{query.name} carries no native query."
    out = []
    for source in carrying:
        out.append(f"-- {query.name} / {source.step or 'query'} / {source.target or source.label}")
        out.append(source.native_query or "")
        out.append("")
    return "\n".join(out).rstrip()


def _fmt_findings(model: Model) -> str:
    rows = model.findings()
    if not rows:
        return "Nothing to report."
    out = [f"{len(rows)} findings"]
    for query, finding in rows:
        where = query.name if query else "model"
        step = f" [{finding.step}]" if finding.step else ""
        out.append("")
        out.append(f"[{finding.level}] {where}{step}")
        out.append(f"  {finding.message}")
    return "\n".join(out)


def _fmt_layers(model: Model) -> str:
    out = []
    for layer in model.layers:
        members = [q for q in model.queries if q.layer == layer]
        out.append(f"Layer {layer}  ({len(members)})")
        for query in members:
            kind = f" [{query.source_kind}]" if query.source_kind else ""
            deps = f"  <- {', '.join(query.depends_on)}" if query.depends_on else ""
            out.append(f"  {query.name}{kind}{deps}")
        out.append("")
    return "\n".join(out).rstrip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="m_parser",
        description="Parse Power Query M and answer questions about its queries, "
        "steps, sources and dependencies.",
    )
    parser.add_argument("paths", nargs="+", help=".pq / .m / .tmdl file(s), or a directory")
    parser.add_argument("--query", metavar="NAME", help="one query: steps, sources, findings")
    parser.add_argument("--steps", metavar="NAME", help="the step chain, with column changes")
    parser.add_argument("--impact", metavar="NAME", help="what breaks if NAME changes")
    parser.add_argument("--sql", metavar="NAME", help="the decoded native query NAME carries")
    parser.add_argument("--sources", action="store_true", help="every connector call in the model")
    parser.add_argument("--findings", action="store_true", help="everything worth reporting")
    parser.add_argument("--layers", action="store_true", help="the graph, layer by layer")
    parser.add_argument("--find", metavar="TEXT", help="queries matching name, step or SQL")
    parser.add_argument("--dead", action="store_true", help="steps nothing reaches")
    parser.add_argument("--operations", action="store_true", help="functions the steps call")
    parser.add_argument("--dump", action="store_true", help="everything, as canonical JSON")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)

    # Windows defaults stdout to cp1252, which cannot encode the em dashes and
    # curly quotes in these messages, and defaults its newline to CRLF. Both are
    # pinned rather than left to the console.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", newline="\n")

    try:
        model = Model.from_paths(args.paths)
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 1

    if args.dump:
        print(json.dumps(model.dump(), indent=2, ensure_ascii=False, sort_keys=True))
        return 0

    if args.query:
        found = model.get(args.query)
        payload = found.to_dict() if found else None
        text = _fmt_query(model, args.query)
    elif args.steps:
        found = model.get(args.steps)
        payload = [s.to_dict() for s in found.steps] if found else None
        text = _fmt_steps(model, args.steps)
    elif args.impact:
        payload = {
            "query": args.impact,
            "downstream": model.downstream(args.impact),
            "upstream": model.upstream(args.impact),
        }
        text = _fmt_impact(model, args.impact)
    elif args.sql:
        found = model.get(args.sql)
        payload = (
            [s.to_dict() for s in found.sources if s.native_query] if found else None
        )
        text = _fmt_sql(model, args.sql)
    elif args.sources:
        payload = [
            {"query": q.name, **s.to_dict()} for q, s in model.sources()
        ]
        text = _fmt_sources(model)
    elif args.findings:
        payload = [
            {"query": q.name if q else None, **f.to_dict()} for q, f in model.findings()
        ]
        text = _fmt_findings(model)
    elif args.layers:
        payload = [q.brief() for q in model.queries]
        text = _fmt_layers(model)
    elif args.find:
        hits = model.search(args.find)
        payload = [q.brief() for q in hits]
        text = "\n".join(f"L{q.layer}  {q.name}" for q in hits) or "No match."
    elif args.dead:
        rows = model.dead_steps()
        payload = [{"query": q.name, **s.to_dict()} for q, s in rows]
        text = "\n".join(f"{q.name}: {s.name}  ({s.label})" for q, s in rows) or "None."
    elif args.operations:
        rows = model.operations()
        payload = [{"name": n, "uses": c} for n, c in rows]
        text = "\n".join(f"{c:>4}  {n}" for n, c in rows) or "None."
    else:
        payload = model.overview()
        text = _fmt_overview(model)

    print(json.dumps(payload, indent=2, ensure_ascii=False) if args.json else text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
