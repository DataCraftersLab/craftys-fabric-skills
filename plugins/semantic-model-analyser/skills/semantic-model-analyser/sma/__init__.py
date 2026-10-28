"""
Semantic Model Analyser -- read a Power BI model as text, and say what is in it.

Four stages, and each one can be stopped at:

    parse      the vendored parsers read TMDL and Power Query M
    dataset    one report.json holding everything either of them found
    analyse    rules over that dataset, each naming a specific subject
    render     self-contained HTML, only when asked for
    diff       two datasets compared in model terms

No Power BI Desktop, no XMLA connection, no gateway, no Fabric access, and no
third-party packages. Text in, facts out. Every stage runs anywhere Python 3.9 runs.

What it reads
-------------
Tables, columns, measures, partitions, relationships, roles, perspectives, calculation
groups, hierarchies, refresh policies, shared expressions and the model block -- the
whole `definition` of a PBIP project, or the same objects inline in a single-file
export. The Power Query inside partitions and shared expressions is read as M.

What it will not claim
----------------------
**No row counts, no timings, no memory.** Nothing here has seen the data. A "heaviest"
measure is heavy in *syntax*, and the output keeps saying so.

**No report usage.** A measure is *unreferenced* when no other parsed object reads it;
whether a visual does is not knowable from the definition.

**No verdicts.** A finding is an observation at `warning` or `info`. `error` is carried
by one kind of finding only: text the parser could not read.
"""

__version__ = "0.4.0"
__all__ = ["compare", "dataset", "findings", "inputs"]
