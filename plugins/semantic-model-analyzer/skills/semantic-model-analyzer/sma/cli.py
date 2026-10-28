"""
The command line. Every stage is reachable on its own, and `report` runs the lot.

Stages exist separately because the cheap ones answer most questions. "How many measures
have no description" should cost a dataset read, not an HTML build.
"""

import argparse
import json
import sys
from pathlib import Path

from . import __version__, ask, dataset, findings, inputs, screen

LEVEL_MARK = {"error": "x", "warning": "!", "info": "-"}


def _config(args):
    path = getattr(args, "config", None)
    return dataset.load_config(path) if path else None


def gather(args, stop_on_credentials=True):
    """Everything handed over, whether it came from a path, a flag or a pipe.

    A paste out of the Power Query window is the common case, not the exotic one, so
    stdin and `--text` are inputs in the same sense a folder is. `--tmdl-text` and
    `--m-text` state the segment; `--text` and stdin let the sniff decide.
    """
    texts = []
    for value, declared, label in ((args.text, None, "pasted text"),
                                   (args.tmdl_text, inputs.MODE_TMDL, "pasted TMDL"),
                                   (args.m_text, inputs.MODE_M, "pasted M")):
        if value:
            texts.append((value, label, "paste", declared))
    if getattr(args, "stdin", False) or "-" in (args.paths or []):
        blob = sys.stdin.read()
        if blob.strip():
            texts.append((blob, "stdin", "stdin", None))
    paths = [p for p in (args.paths or []) if p != "-"]
    res = inputs.resolve(paths, texts)
    if res["screen"] and stop_on_credentials and not getattr(args, "no_credential_check", False):
        raise inputs.InputError(screen.refusal(res["screen"]))
    return res


def _out(path, text, quiet=False):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(text, encoding="utf-8")
    if not quiet:
        print("wrote %s  (%.0f KB)" % (path, Path(path).stat().st_size / 1024))


def cmd_inspect(args):
    """What would this run read, and which parts could it emit?

    Worth its own command because the answer is often the whole question: a folder that
    contains no TMDL, or a paste that reads as SQL, is better found here than three
    stages later.
    """
    res = gather(args, stop_on_credentials=False)
    print("mode      : %s" % inputs.describe(res))
    for kind in ("tmdl", "m"):
        for src in res[kind]:
            print("  %-5s %-46s %s" % (kind, src.describe()[:46], src.format or ""))
    for src in res["sources"]:
        if src.mode is None:
            print("  ----  %-46s %s" % (src.describe()[:46], src.note))
    for note in res["notes"]:
        print("note      : %s" % note)
    print("parts     : %s" % ", ".join(k for k in ("tmdl", "m") if res[k]))
    for line in screen.summary(res["screen"], len(res["tmdl"]) + len(res["m"])):
        print("screen    : %s" % line)
    return 0


def _coverage_line(report):
    """One line saying how much of the model was read, so a run on a partial handover
    says so before anyone reads the findings."""
    t = report.get("tmdl")
    if not t:
        return ""
    cov = t["coverage"]
    o = cov["objects"]
    bits = ["%d tables" % o["tables"], "%d measures" % o["measures"],
            "%d columns" % o["columns"], "%d relationships" % o["relationships"]]
    for key, label in (("roles", "roles"), ("calculationGroups", "calculation groups"),
                       ("hierarchies", "hierarchies"), ("expressions", "shared expressions"),
                       ("perspectives", "perspectives"), ("refreshPolicies", "refresh policies")):
        if o.get(key):
            bits.append("%d %s" % (o[key], label))
    line = "  read : " + ", ".join(bits)
    if o.get("model"):
        line += "; model block"
    if cov["unsupported"]:
        line += "; not analysed: " + ", ".join("%s x%d" % kv for kv in cov["unsupported"].items())
    for kind, names in cov["declaredNotParsed"].items():
        line += "\n  gap  : model.tmdl lists %d %s%s not in the handover: %s" % (
            len(names), kind, "" if len(names) == 1 else "s", ", ".join(names[:6]))
    return line


def cmd_dataset(args):
    res = gather(args)
    report = dataset.build(res, label=args.label, config=_config(args))
    _out(args.out, dataset.dumps(report), args.quiet)
    t = report.get("tmdl", {}).get("totals", {})
    m = report.get("m", {}).get("totals", {})
    if t:
        print("  tmdl : %d measures, %d columns, %d tables, %d links, %d relationships"
              % (t["measures"], t["columns"], t["tables"], t["links"], t["relationships"]))
        print(_coverage_line(report))
    if m:
        print("  m    : %d queries, %d steps, %d with native SQL"
              % (m["queries"], m["steps"], m["withNativeSql"]))
    return 0


def cmd_analyse(args):
    report = (dataset.load(args.report) if args.report
              else dataset.build(gather(args), label=args.label, config=_config(args)))
    summary = findings.summarise(report)
    head = "%d worth acting on, %d worth knowing, %d rules fired" % (
        summary["warnings"], summary["notes"], len(summary["byCode"]))
    if summary["errors"]:
        head = "%d could not be read, " % summary["errors"] + head
    if summary["suppressed"]:
        head += ", %d suppressed by the rules file" % summary["suppressed"]
    print(head)
    for part, s in summary["byPart"].items():
        print("  %-5s %3d findings  (%d error, %d warning, %d note)"
              % (part, s["total"], s["errors"], s["warnings"], s["notes"]))
    cov = _coverage_line(report)
    if cov:
        print(cov)
    print()
    shown = 0
    for f in findings.worst_first(report, level=None if args.all else "warning"):
        if args.limit and shown >= args.limit:
            print("  ... %d more" % (len(findings.worst_first(
                report, level=None if args.all else "warning")) - shown))
            break
        where = f.get("where") or {}
        loc = ("  @%s:%s" % (where.get("file"), where.get("line"))) if where else ""
        print("  %s [%-26s] %-34s %s%s"
              % (LEVEL_MARK.get(f["level"], " "), f["code"],
                 (f.get("subject") or "")[:34], f["message"][:76], loc))
        shown += 1
    if args.json:
        _out(args.json, json.dumps(
            {"summary": summary, "findings": findings.worst_first(report)},
            indent=2, ensure_ascii=False) + "\n", args.quiet)
    return 0


def cmd_render(args):
    from .render import build_pages
    report = (dataset.load(args.report) if args.report
              else dataset.build(gather(args), label=args.label, config=_config(args)))
    written = build_pages(report, args.out, catalogue=args.catalogue)
    for path in written:
        print("wrote %s  (%.0f KB)" % (path, Path(path).stat().st_size / 1024))
    return 0


def cmd_report(args):
    """Everything: dataset to disk, findings to the terminal, pages if asked."""
    res = gather(args)
    report = dataset.build(res, label=args.label, config=_config(args))
    out = Path(args.out)
    _out(out / "report.json", dataset.dumps(report), args.quiet)
    summary = findings.summarise(report)
    print("  %s  %d worth acting on, %d worth knowing%s"
          % (inputs.describe(res), summary["warnings"], summary["notes"],
             ", %d could not be read" % summary["errors"] if summary["errors"] else ""))
    cov = _coverage_line(report)
    if cov:
        print(cov)
    if args.html:
        from .render import build_pages
        for path in build_pages(report, out, catalogue=args.catalogue):
            print("wrote %s  (%.0f KB)" % (path, Path(path).stat().st_size / 1024))
    return 0


def _report_for(args):
    """A dataset from --report, or from parsing what was handed over."""
    if getattr(args, "report", None):
        return dataset.load(args.report)
    return dataset.build(gather(args), label=args.label, config=_config(args))


def cmd_list(args):
    """Objects of one kind, filtered and projected. The answer to most "which" questions."""
    report = _report_for(args)
    rows, fields, total = ask.list_cmd(report, args.kind, args.where, args.match,
                                       args.fields.split(",") if args.fields else None,
                                       0 if args.all else args.limit)
    if args.count:
        print(total)
        return 0
    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return 0
    print(ask.format_rows(rows, fields, wide=args.wide))
    if total > len(rows):
        print("\n%d of %d; --all for every row, --limit N for more" % (len(rows), total))
    elif rows:
        print("\n%d %s" % (total, args.kind))
    return 0


def cmd_explain(args):
    """Everything the dataset holds about one named thing."""
    report = _report_for(args)
    blocks = ask.explain(report, args.name)
    if args.json:
        print(json.dumps(blocks, indent=2, ensure_ascii=False))
        return 0 if blocks else 1
    print(ask.format_explain(blocks, args.name, wide=args.wide))
    return 0 if blocks else 1


def cmd_diff(args):
    """Two datasets, compared in model terms."""
    from . import compare
    before, after = dataset.load(args.before), dataset.load(args.after)
    d = compare.diff_reports(before, after)
    if args.json:
        _out(args.json, json.dumps(d, indent=2, ensure_ascii=False) + "\n", args.quiet)
    print(compare.format_text(d))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="sma", description="Semantic Model Analyzer " + chr(0x2014)
        + " read a Power BI model as text and say what is in it.")
    ap.add_argument("--version", action="version", version="%(prog)s " + __version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def shared(p, paths="required"):
        # Paths are optional on every command now, because text may be the whole input.
        # The "at least one of these" check happens after parsing -- argparse cannot
        # express it, and the error is more useful when it can name all the ways in.
        if paths:
            p.add_argument("paths", nargs="*", default=[],
                           help="TMDL files, .pq/.m/.txt files, a folder, a PBIP "
                                "project, or - for stdin")
        p.add_argument("--label", default="", help="a name for this run, shown on output")
        p.add_argument("-q", "--quiet", action="store_true")
        # A paste is a first-class input. `--text` sniffs which segment it is; the two
        # named flags state it, for the case where the sniff would have to guess.
        p.add_argument("--text", metavar="M_OR_TMDL",
                       help="analyse this text directly, deciding the segment from it")
        p.add_argument("--tmdl-text", metavar="TMDL",
                       help="analyse this text as a semantic model")
        p.add_argument("--m-text", metavar="M",
                       help="analyse this text as Power Query M")
        p.add_argument("--stdin", action="store_true",
                       help="read the text from stdin (or pass - as a path)")
        p.add_argument("--config", metavar="RULES.json",
                       help="thresholds, severity overrides and documented suppressions")
        p.add_argument("--no-credential-check", action="store_true",
                       help="skip the credential screen; only after the user has looked at "
                            "every line it listed and confirmed none is a credential")

    p = sub.add_parser("inspect", help="say what would be read, and stop")
    shared(p)
    p.set_defaults(fn=cmd_inspect)

    p = sub.add_parser("dataset", help="parse and write report.json")
    shared(p)
    p.add_argument("-o", "--out", default="report.json")
    p.set_defaults(fn=cmd_dataset)

    p = sub.add_parser("analyse", aliases=["analyze"], help="run the rules and list what they found")
    shared(p, paths="optional")
    p.add_argument("--report", help="read an existing report.json instead of parsing")
    p.add_argument("--all", action="store_true", help="notes as well as warnings")
    p.add_argument("--limit", type=int, default=40)
    p.add_argument("--json", help="also write the findings to this file")
    p.set_defaults(fn=cmd_analyse)

    p = sub.add_parser("render", help="write the HTML pages")
    shared(p, paths="optional")
    p.add_argument("--report", help="read an existing report.json instead of parsing")
    p.add_argument("-o", "--out", default=".")
    p.add_argument("--catalogue", action="store_true",
                   help="every candidate pattern, including the archive and the backlog")
    p.set_defaults(fn=cmd_render)

    p = sub.add_parser("report", help="dataset + findings, and HTML with --html")
    shared(p)
    p.add_argument("-o", "--out", default="model-report")
    p.add_argument("--html", action="store_true")
    p.add_argument("--catalogue", action="store_true")
    p.set_defaults(fn=cmd_report)

    p = sub.add_parser("list", help="objects of one kind, filtered: measures, columns, "
                                    "tables, relationships, queries, steps, sql, findings, ...")
    shared(p, paths="optional")
    p.add_argument("kind", choices=sorted(ask.KINDS), help="what to list")
    p.add_argument("--report", help="read an existing report.json instead of parsing")
    p.add_argument("--where", action="append", default=[], metavar="FIELD=VALUE",
                   help="keep rows where FIELD=VALUE, FIELD!=VALUE, FIELD~text (contains), "
                        "FIELD>N or FIELD<N; a.b reaches into nested fields; repeatable")
    p.add_argument("--match", metavar="TEXT", help="keep rows whose text contains TEXT anywhere")
    p.add_argument("--fields", metavar="A,B,C", help="columns to show, comma-separated")
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--all", action="store_true", help="no row limit")
    p.add_argument("--count", action="store_true", help="print only how many rows match")
    p.add_argument("--wide", action="store_true", help="do not shorten long cells")
    p.add_argument("--json", action="store_true", help="rows as JSON instead of a table")
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("explain", help="everything known about one measure, table, "
                                       "Table[Column], relationship, role, expression or query")
    shared(p, paths="optional")
    p.add_argument("name", help="the object's name; a column as Table[Column]")
    p.add_argument("--report", help="read an existing report.json instead of parsing")
    p.add_argument("--wide", action="store_true", help="do not shorten long text")
    p.add_argument("--json", action="store_true", help="the blocks as JSON")
    p.set_defaults(fn=cmd_explain)

    p = sub.add_parser("diff", help="compare two report.json files in model terms")
    p.add_argument("before", help="the older report.json")
    p.add_argument("after", help="the newer report.json")
    p.add_argument("--json", help="also write the comparison to this file")
    p.add_argument("-q", "--quiet", action="store_true")
    p.set_defaults(fn=cmd_diff)

    args = ap.parse_args(argv)
    # `paths` is optional once --report is given, so the check lives here rather than in
    # argparse, which cannot express "one of these two".
    gave_text = any(getattr(args, a, None) for a in ("text", "tmdl_text", "m_text")) \
        or getattr(args, "stdin", False)
    if (args.cmd != "diff" and getattr(args, "report", None) is None
            and not getattr(args, "paths", None) and not gave_text):
        ap.error("nothing to read. Give paths, or --text / --tmdl-text / --m-text, or "
                 "--stdin, or --report pointing at a report.json")
    try:
        return args.fn(args)
    except inputs.InputError as exc:
        print("input error: %s" % exc, file=sys.stderr)
        return 2
    except (ValueError, AssertionError, OSError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1
