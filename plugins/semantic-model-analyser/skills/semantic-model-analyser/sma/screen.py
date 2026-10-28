"""Credential-looking text in the input, found before anything copies it out.

A connection string with a password in it would otherwise reach report.json and every
page, and the pages are made to be sent. So every command that parses stops on a match,
and the screen reports where, never what: a source, a line, and the kind of thing it
looks like. The value itself is never printed, stored or returned.

It is a net, not a proof. A clean screen means none of these shapes was found.
"""
import os
from pathlib import Path
import re

#: (kind, pattern). Case-insensitive. Each one wants a value after the name, so a column
#: called Password or a step that reads a token from a parameter does not match.
PATTERNS = [
    ("password in a connection string",
     re.compile(r"(?i)\b(?:password|pwd)\s*=\s*(?:\"\"[^\"]+\"\"|'[^']{3,}'|\"[^\"]{3,}\"|[^\s;\"',)\]]{3,})")),
    ("account or access key",
     re.compile(r"(?i)\b(?:accountkey|sharedaccesskey)\s*=\s*[A-Za-z0-9+/=]{20,}")),
    ("shared access signature",
     re.compile(r"(?i)(?:\bsharedaccesssignature\s*=|[?&]sig=[A-Za-z0-9%+/=]{20,})")),
    ("client secret", re.compile(r"(?i)\bclient_secret[\"']?\s*[=:]\s*[\"']?[^\s\"'&;]{8,}")),
    ("bearer token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}")),
    # Base64 nearly always carries a digit, + or /, which keeps "Basic Configuration" out.
    ("basic authentication",
     re.compile(r"(?i)(?:\bbasic\s+(?=[A-Za-z0-9+/]*[0-9+/])[A-Za-z0-9+/]{12,}={0,2}"
                r"|Text\.ToBinary\(\s*\"[^\"\s:]+:[^\"\s]{3,}\"\s*\))")),
    ("API key",
     re.compile(r"(?i)\b(?:x-api-key|api[_-]?key|subscription-key)[\"']?\s*[=:]\s*[\"']?[A-Za-z0-9._~+/=-]{12,}")),
    ("token in a URL",
     re.compile(r"(?i)[?&](?:access_token|token|code|key|apikey|client_secret)=[^&\"\s]{16,}")),
    ("token or secret literal",
     re.compile(r"(?i)\b(?:access_token|auth_token|token|secret|apikey)[\"']?\s*[=:]\s*\"[A-Za-z0-9._~+/=-]{16,}\"")),
    ("vendor token",
     re.compile(r"\b(?:(?:ghp|gho|ghu|ghs|github_pat)_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,}"
                r"|AKIA[0-9A-Z]{16}|xox[abpr]-[A-Za-z0-9-]{10,})")),
    ("JSON web token", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.")),
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
]


def _labels(sources):
    """A name per source that tells two files apart: the file name, or, where two share
    one, the path below the folder they have in common."""
    names = [s.describe() for s in sources]
    clash = [s for s, n in zip(sources, names) if names.count(n) > 1 and s.path]
    root = os.path.commonpath([str(Path(s.path).resolve().parent) for s in clash]) if len(clash) > 1 else None
    out = {}
    for s, n in zip(sources, names):
        if root and s in clash:
            n = Path(os.path.relpath(Path(s.path).resolve(), root)).as_posix()
        out[id(s)] = n
    return out


def scan(sources):
    """Every match as (source label, line number, kind), in input order. Never the text."""
    labels = _labels(sources)
    hits = []
    for src in sources:
        for number, line in enumerate((src.text or "").splitlines(), 1):
            for kind, rx in PATTERNS:
                if rx.search(line):
                    hits.append((labels[id(src)], number, kind))
                    break
    return hits


def summary(hits, searched):
    """The lines `inspect` prints after `screen    : `, one per match."""
    if not hits:
        return ["clean (%d source%s searched)" % (searched, "" if searched == 1 else "s")]
    return ["possible credential at %s:%d (%s)" % h for h in hits]


def refusal(hits):
    """The input error every parsing command raises on a match. Every match is listed, so
    the user can look at each one before deciding."""
    where = "\n".join("  %s:%d  %s" % h for h in hits)
    return ("possible credential in the input, so nothing was parsed or written:\n%s\n"
            "Remove it from the source (and rotate it if it is real). If the user has looked at "
            "every line listed and none is a credential, run again with --no-credential-check."
            % where)
