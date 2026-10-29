"""
HTML rendering. Reads a dataset, writes self-contained pages.

One page per segment plus an overview that connects them, because a run is separate: the
skill is handed TMDL, or M, or both, and emits only the parts its input can support. A
single scroll carrying both is a page no real run produces.

Every page inlines its own theme, script and logo and makes **zero network requests**, so
it opens from `file://`, survives being emailed, and still works in five years. That is
asserted by `tests/validate_html.py`, not assumed.
"""

from pathlib import Path

ASSETS = Path(__file__).parent / "assets"


def asset(name):
    return (ASSETS / name).read_text(encoding="utf-8")


def asset_bytes(name):
    return (ASSETS / name).read_bytes()


def build_pages(report, out_dir, catalogue=False):
    """Write the pages this dataset can support. Returns the paths written.

    `catalogue=True` adds the archive and the not-yet-drawn backlog -- the full
    candidate set, for choosing what a report should contain. The default is the report:
    the patterns that survived that choice.
    """
    from . import cards

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    cards.bind(report)
    cards.set_catalogue(catalogue)
    cards.reset_parts()
    written = []
    for key, html in cards.pages(report):
        path = out / cards.PAGES[key]["file"]
        path.write_text(html, encoding="utf-8")
        written.append(path)
    return written
