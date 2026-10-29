"""
The pipeline, end to end, on the fixtures. Plain unittest, no pytest, no plugins.

    python -m unittest discover -s tests/semantic-model-analyzer

What is worth asserting here is not "does it run" -- a build failure says that. It is the
handful of properties that could break quietly:

* the dataset is **stable**, so a golden-file diff of two runs is meaningful
* the two segments stay **independent**, so a Power-Query-only run cannot start leaning
  on a semantic model it was never given
* every finding **names a subject** and carries **help**, because a count with no subject
  is the failure mode this project spent the longest fixing
* the pages a run emits follow the **input**, so a missing segment is a missing page and
  never a page with nothing in it
"""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "plugins" / "semantic-model-analyzer" / "skills" / "semantic-model-analyzer"))

from sma import dataset, findings, inputs  # noqa: E402
from sma.render import build_pages  # noqa: E402

FIX = Path(__file__).parent / "fixtures"
TMDL = sorted(FIX.glob("*.tmdl"))
PQ = sorted(FIX.glob("*.pq"))


def build(paths):
    return dataset.build(inputs.resolve([str(p) for p in paths]))


class TestInputs(unittest.TestCase):
    def test_mode_follows_the_input(self):
        self.assertEqual(inputs.resolve([str(p) for p in TMDL])["mode"], inputs.MODE_TMDL)
        self.assertEqual(inputs.resolve([str(p) for p in PQ])["mode"], inputs.MODE_M)
        self.assertEqual(inputs.resolve([str(FIX)])["mode"], inputs.MODE_BOTH)

    def test_unreadable_input_says_what_it_looked_for(self):
        """An empty folder must not produce an empty report. Silence is the worst
        outcome here: the reader concludes the model is clean."""
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(inputs.InputError) as cm:
                inputs.resolve([d])
            self.assertIn("*.tmdl", str(cm.exception))

    def test_a_functions_file_says_functions_are_not_read(self):
        """`functions.tmdl` holds DAX user-defined functions, which the parser does not read
        yet. It was relayed with the M parser's "expected Power Query M" message."""
        text = "function 'Add Tax' =\n\t\t(amount : NUMERIC) => amount * 1.1\n"
        mode, _, note = inputs.classify_text(text, ".tmdl")
        self.assertIsNone(mode)
        self.assertIn("user-defined functions", note)

    def test_inspect_names_a_tmdl_file_as_tmdl(self):
        """The format column showed the M sniffer's guess, `expression`, for TMDL files."""
        from sma import cli
        import contextlib, io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(["inspect"] + [str(p) for p in TMDL]), 0)
        rows = [r for r in out.getvalue().splitlines() if r.startswith("  tmdl ")]
        self.assertEqual(len(rows), len(TMDL))
        self.assertTrue(all(r.rstrip().endswith(" tmdl") for r in rows), rows)

    def test_output_survives_a_cp1252_console(self):
        """A measure named with a Greek delta ended the run with exit 1 on a Windows console
        that encodes cp1252, halfway through the findings."""
        import os, subprocess
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "Sales.tmdl"
            path.write_text("table Sales\n\n\tmeasure 'ΔPY%' = 1\n", encoding="utf-8")
            env = dict(os.environ, PYTHONIOENCODING="cp1252",
                       PYTHONPATH=str(Path(__file__).resolve().parents[2] / "plugins" / "semantic-model-analyzer" / "skills" / "semantic-model-analyzer"))
            proc = subprocess.run([sys.executable, "-B", "-m", "sma", "list", str(path), "measures"],
                                  capture_output=True, env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("ΔPY%", proc.stdout.decode("utf-8"))


# Shapes live beside the fixtures, not inside them: `fixtures/` is one model to
# analyse, and sweeping eight routing samples into it would change every other test's
# input. Which is exactly what happened the first time.
SHAPES = Path(__file__).parent / "shapes"

#: Every shape someone actually hands over, and which segment it belongs to. The four
#: that used to be thrown away are marked, because the routing they broke on was not
#: obviously wrong -- it sniffed for `let` and `in` as substrings, which is right often
#: enough to look fine.
SHAPE_ROUTING = [
    ("advanced-editor.txt", inputs.MODE_M, "expression"),
    ("section-document.pq", inputs.MODE_M, "section"),
    ("labelled-dump.txt", inputs.MODE_M, "labelled"),
    ("helper-functions.m", inputs.MODE_M, "expression"),      # no `let` -> was skipped
    ("parameter-only.pq", inputs.MODE_M, "expression"),       # no `let` -> was skipped
    ("semantic-model-as.txt", inputs.MODE_TMDL, "expression"),  # `Completed` -> was M
    ("not-anything.txt", None, "expression"),
]


class TestShapes(unittest.TestCase):
    """The routing, over every shape of input.

    These are the tests the first version would have failed. It routed by extension and
    sniffed for `"let" in text and "in" in text` as substrings, so TMDL holding a measure
    called `Completed Orders` went to the M parser and came back empty, and any M with no
    `let` -- every bare function, every parameter -- was rejected outright.
    """

    def test_each_shape_goes_to_the_right_parser(self):
        for name, mode, fmt in SHAPE_ROUTING:
            with self.subTest(name):
                src = inputs.read_file(SHAPES / name)
                self.assertEqual(src.mode, mode)
                self.assertEqual(src.format, fmt)

    def test_an_unrecognised_shape_says_what_it_looked_like(self):
        """Silence is the worst outcome: the reader concludes the model is clean."""
        src = inputs.read_file(SHAPES / "not-anything.txt")
        self.assertIsNone(src.mode)
        self.assertTrue(src.note.strip())

    def test_extension_does_not_decide(self):
        """A `.txt` holds TMDL in one fixture and M in two others. If the extension were
        deciding, they could not all be right."""
        by_mode = {}
        for name in ("semantic-model-as.txt", "advanced-editor.txt", "labelled-dump.txt"):
            by_mode.setdefault(inputs.read_file(SHAPES / name).mode, []).append(name)
        self.assertEqual(sorted(by_mode), [inputs.MODE_M, inputs.MODE_TMDL])

    def test_the_whole_folder_resolves_to_both(self):
        res = inputs.resolve([str(SHAPES)])
        self.assertEqual(res["mode"], inputs.MODE_BOTH)
        self.assertEqual(len(res["tmdl"]), 1)
        self.assertEqual(len(res["m"]), 5)

    def test_a_section_document_keeps_all_three_object_kinds(self):
        """A parameter, a function and a table in one paste. Dropping any of them is the
        failure this whole segment was about."""
        report = dataset.build(inputs.resolve([str(SHAPES / "section-document.pq")]))
        kinds = {q["name"]: q["kind"] for q in report["m"]["queries"]}
        self.assertEqual(kinds.get("Currency Code"), "parameter")
        self.assertEqual(kinds.get("FnTitleCase"), "function")
        self.assertEqual(kinds.get("Orders"), "table")

    def test_functions_with_a_return_type_are_read(self):
        """`(x) as text => ...` -- the ascription hid the arrow from the paren-or-function
        lookahead, so the file came back with no queries at all."""
        for src in ("(x as number) as number => x * 2",
                    "() as table => #table({}, {})",
                    "(x) as nullable text => x"):
            with self.subTest(src):
                res = inputs.resolve([], [(src, "paste", "paste", None)])
                self.assertEqual(res["mode"], inputs.MODE_M)

    def test_the_coalesce_operator_does_not_empty_the_file(self):
        """`??` lexed as two `?` punctuators, the parse stopped, and the splitter refused
        the whole paste. One operator, no queries."""
        res = inputs.resolve([], [("let x = 1, y = x ?? 2 in y", "paste", "paste", None)])
        self.assertEqual(res["mode"], inputs.MODE_M)


class TestTextInput(unittest.TestCase):
    """A paste is an input in the same sense a folder is."""

    def test_text_alone_is_enough(self):
        res = inputs.resolve([], [("let a = 1 in a", "pasted text", "paste", None)])
        self.assertEqual(res["mode"], inputs.MODE_M)
        self.assertEqual(res["m"][0].origin, "paste")

    def test_a_declared_segment_wins_and_is_noted(self):
        """`--tmdl-text` is a statement, not a hint. Obeying it silently when the text
        reads as something else would hide a mistake worth surfacing."""
        src = inputs.make_source("let a = 1 in a", "pasted TMDL", "paste",
                                 declared=inputs.MODE_TMDL)
        self.assertEqual(src.mode, inputs.MODE_TMDL)
        self.assertIn("reads as m", src.note)

    def test_a_paste_is_traceable(self):
        """No filename, so the dataset has to describe it some other way or a run on a
        paste is a run nobody can reproduce."""
        res = inputs.resolve([], [("let a = 1 in a", "pasted text", "paste", None)])
        d = dataset.build(res)["input"]
        self.assertEqual(d["m"][0]["origin"], "paste")
        self.assertIsNone(d["m"][0]["path"])
        self.assertEqual(d["m"][0]["lines"], 1)
        self.assertTrue(d["m"][0]["digest"])
        self.assertTrue(d["digest"])

    def test_files_and_pastes_mix(self):
        res = inputs.resolve([str(p) for p in TMDL],
                             [("let a = 1 in a", "pasted M", "paste", None)])
        self.assertEqual(res["mode"], inputs.MODE_BOTH)
        self.assertEqual([s.origin for s in res["m"]], ["paste"])
        self.assertEqual({s.origin for s in res["tmdl"]}, {"file"})

    def test_the_same_text_from_a_file_and_a_paste_reads_the_same(self):
        """Provenance is the only difference. If the reading differed, one of the two
        paths would be doing something the other is not."""
        text = (SHAPES / "section-document.pq").read_text(encoding="utf-8")
        from_file = dataset.build(inputs.resolve([str(SHAPES / "section-document.pq")]))
        from_text = dataset.build(inputs.resolve([], [(text, "paste", "paste", None)]))
        self.assertEqual(json.dumps(from_file["m"]["queries"], sort_keys=True),
                         json.dumps(from_text["m"]["queries"], sort_keys=True))


class TestManySources(unittest.TestCase):
    """Several M sources, of different shapes, in one run.

    This is where the worst bug in the input path lived, and it was silent. A blob has
    exactly one detected format, so concatenating a section document with a labelled dump
    and three bare expressions was detected as `section`, and the section splitter read
    only the `shared` declarations. Five queries out of ten, no diagnostic, and a run that
    looked entirely successful.
    """

    def test_nothing_is_lost_when_the_shapes_differ(self):
        res = inputs.resolve([str(SHAPES)])
        per_source = 0
        for src in res["m"]:
            from sma.vendor import m_parser
            per_source += len(m_parser.split_document(src.text).queries)
        report = dataset.build(res)
        self.assertEqual(report["m"]["totals"]["queries"], per_source)
        self.assertGreater(per_source, 5, "the fixture should exercise more than one shape")

    def test_every_query_kind_survives(self):
        report = dataset.build(inputs.resolve([str(SHAPES)]))
        kinds = {q["kind"] for q in report["m"]["queries"]}
        self.assertEqual(kinds, {"table", "function", "parameter"})

    def test_a_placeholder_name_is_replaced_by_its_source(self):
        """The splitter calls an unlabelled bare expression `Query1`, which is a
        placeholder, not a name -- and three files of them all arrive called `Query1`."""
        report = dataset.build(inputs.resolve([str(SHAPES)]))
        names = [q["name"] for q in report["m"]["queries"]]
        self.assertNotIn("Query1", names)
        self.assertIn("advanced-editor", names)
        self.assertIn("helper-functions", names)
        self.assertEqual(len(names), len(set(names)), "names must be unique")

    def test_a_name_collision_is_reported_not_resolved_quietly(self):
        one = "let Source = 1 in Source"
        res = inputs.resolve([], [(one, "Orders", "paste", None),
                                  (one, "Orders", "paste", None)])
        text, notes = inputs.m_document(res["m"])
        self.assertTrue(any("Orders" in n for n in notes))
        self.assertIn("Orders (2)", text)

    def test_cross_source_references_resolve(self):
        """The whole reason the sources are re-emitted as one document rather than parsed
        one at a time: a query in one file reading a query in another."""
        res = inputs.resolve([], [
            ("let Source = Table.FromRows({{1}}, {\"A\"}) in Source", "Base", "paste", None),
            ("let Source = Base, Kept = Table.SelectRows(Source, each [A] > 0) in Kept",
             "Derived", "paste", None)])
        report = dataset.build(res)
        self.assertGreater(report["m"]["totals"]["links"], 0)
        derived = next(q for q in report["m"]["queries"] if q["name"] == "Derived")
        self.assertIn("Base", derived["dependsOn"])

    def test_native_sql_is_still_found_after_composition(self):
        """Re-emitting the queries must not damage a string literal. Recovering the SQL
        out of `#(lf)` escapes is the single biggest thing the parser does that the
        Power Query Editor does not."""
        report = dataset.build(inputs.resolve([str(SHAPES / "advanced-editor.txt")]))
        self.assertEqual(report["m"]["totals"]["withNativeSql"], 1)
        composed = dataset.build(inputs.resolve([str(SHAPES)]))
        self.assertEqual(composed["m"]["totals"]["withNativeSql"], 1)
        self.assertEqual(composed["m"]["sql"][0]["sql"],
                         report["m"]["sql"][0]["sql"])


class TestDataset(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = build([FIX])

    def test_schema_and_parts(self):
        self.assertEqual(self.report["schema"], dataset.SCHEMA)
        self.assertEqual(self.report["version"], dataset.VERSION)
        self.assertEqual(self.report["parts"], ["tmdl", "m"])

    def test_a_dataset_from_before_the_rename_still_loads(self):
        """report.json files written as semantic-model-analyser/report are the same shape, so
        diff and render keep reading them after the rename to Analyzer."""
        old = dict(self.report, schema="semantic-model-analyser/report")
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "old.json"
            path.write_text(json.dumps(old), encoding="utf-8")
            self.assertEqual(dataset.load(path)["parts"], ["tmdl", "m"])
            path.write_text(json.dumps(dict(old, schema="something-else/report")), encoding="utf-8")
            with self.assertRaises(ValueError):
                dataset.load(path)

    def test_analyze_is_accepted_as_a_spelling_of_analyse(self):
        from sma import cli
        import contextlib, io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(["analyze", str(FIX)]), 0)
        self.assertIn("rules fired", out.getvalue())

    def test_stable_across_runs(self):
        """Two builds of one input differ only in the timestamp. If anything else moves,
        a golden-file diff stops being able to tell a real change from set ordering.
        Line endings are normalised before comparing, so a checkout with CRLF on Windows
        and LF on Linux cannot make two correct runs disagree."""
        a, b = dataset.dumps(build([FIX])), dataset.dumps(build([FIX]))
        strip = lambda t: "\n".join(  # noqa: E731
            ln for ln in t.replace("\r\n", "\n").splitlines() if '"generatedAt"' not in ln)
        self.assertEqual(strip(a), strip(b))

    def test_segments_are_independent(self):
        m_only = build(PQ)
        self.assertNotIn("tmdl", m_only)
        self.assertNotIn("measures", m_only["m"])
        t_only = build([FIX / "sample-sales-analytics.tmdl"])
        self.assertNotIn("m", t_only)
        # A model that carries M gets a Power Query part read from its own partitions,
        # and that part still reads no TMDL field.
        carries_m = build([FIX / "star-schema.tmdl"])
        self.assertIn("m", carries_m)
        self.assertNotIn("measures", carries_m["m"])
        self.assertEqual(carries_m["input"]["derivedM"]["partitions"], 2)

    def test_m_part_identical_with_and_without_tmdl(self):
        """Handing over TMDL as well must not change what the M reading says. The two
        share a process, which is exactly how a dependency creeps in unnoticed."""
        alone = json.dumps(build(PQ)["m"], sort_keys=True)
        together = json.dumps(build([FIX])["m"], sort_keys=True)
        self.assertEqual(alone, together)

    def test_totals_match_the_lists_they_count(self):
        t = self.report["tmdl"]
        self.assertEqual(t["totals"]["measures"], len(t["measures"]))
        self.assertEqual(t["totals"]["columns"], len(t["columns"]))
        self.assertEqual(t["totals"]["tables"], len(t["tables"]))
        self.assertEqual(t["totals"]["unreferenced"], len(t["unreferenced"]))
        self.assertEqual(t["totals"]["relationships"], len(t["relationships"]))
        m = self.report["m"]
        self.assertEqual(m["totals"]["queries"], len(m["queries"]))
        self.assertEqual(m["totals"]["withNativeSql"], len(m["sql"]))
        self.assertEqual(m["totals"]["steps"], sum(len(q["steps"]) for q in m["queries"]))

    def test_measure_ids_are_unique_after_the_merge(self):
        ids = [m["id"] for m in self.report["tmdl"]["measures"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_every_declared_table_appears_in_the_bridge(self):
        bridge = {r["name"] for r in self.report["tmdl"]["bridge"]}
        for t in self.report["tmdl"]["tables"]:
            self.assertIn(t["name"], bridge)

    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "report.json"
            p.write_text(dataset.dumps(self.report), encoding="utf-8")
            self.assertEqual(dataset.load(p)["parts"], self.report["parts"])

    def test_load_refuses_a_version_it_cannot_read(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "report.json"
            bad = dict(self.report, version=dataset.VERSION + 1)
            p.write_text(dataset.dumps(bad), encoding="utf-8")
            with self.assertRaises(ValueError):
                dataset.load(p)


class TestFindings(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = build([FIX])
        cls.all = findings.worst_first(cls.report)

    def test_something_fired(self):
        self.assertGreater(len(self.all), 0)

    def test_error_only_for_a_parse_failure(self):
        """A modelling observation is `warning` or `info`: this has seen no report, no
        data and no meeting, so it is not entitled to the word `error`. `error` exists for
        one thing, text the parser could not read, and nothing else may carry it."""
        self.assertEqual({f["level"] for f in self.all} - {"error", "warning", "info"}, set())
        for f in self.all:
            if f["level"] == "error":
                self.assertIn(f["code"], findings.ALL_ERROR_CODES)
        self.assertEqual({f["level"] for f in self.all if f["code"] not in findings.ALL_ERROR_CODES}
                         - {"warning", "info"}, set())

    def test_every_finding_has_help(self):
        for f in self.all:
            self.assertIn(f["code"], self.report["help"], f["code"])

    def test_every_finding_names_a_subject_or_is_about_the_model(self):
        """A count is not a finding. A model-level finding may have an empty subject;
        anything else must say what it is about."""
        model_level = {"chained-model", "parse-recovered"}
        for f in self.all:
            if f["code"] in model_level:
                continue
            self.assertTrue((f.get("subject") or "").strip(),
                            "%s has no subject" % f["code"])

    def test_worst_first(self):
        order = [findings.LEVEL_ORDER[f["level"]] for f in self.all]
        self.assertEqual(order, sorted(order))

    def test_summary_agrees_with_the_list(self):
        s = findings.summarise(self.report)
        self.assertEqual(s["errors"], sum(1 for f in self.all if f["level"] == "error"))
        self.assertEqual(s["warnings"], sum(1 for f in self.all if f["level"] == "warning"))
        self.assertEqual(s["notes"], sum(1 for f in self.all if f["level"] == "info"))


class TestRender(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def render(self, paths, **kw):
        written = build_pages(build(paths), self.dir, **kw)
        return {p.name: p.read_text(encoding="utf-8") for p in written}

    def test_pages_follow_the_input(self):
        # Any TMDL input also gets the AI readiness checklist; Power Query alone does not.
        self.assertEqual(sorted(self.render([FIX])),
                         ["ai-readiness.html", "index.html", "power-query.html",
                          "references.html", "semantic-model.html"])
        # star-schema.tmdl carries two M partitions, so a TMDL-only run of the folder
        # reads that Power Query too. A model with no M in it gets no Power Query page.
        self.assertEqual(sorted(self.render(TMDL)),
                         ["ai-readiness.html", "index.html", "power-query.html",
                          "references.html", "semantic-model.html"])
        self.assertEqual(sorted(self.render([FIX / "sample-sales-analytics.tmdl"])),
                         ["ai-readiness.html", "index.html", "references.html",
                          "semantic-model.html"])
        self.assertEqual(sorted(self.render(PQ)),
                         ["index.html", "power-query.html", "references.html"])

    def test_no_link_points_at_a_page_that_was_not_written(self):
        """A segment with no input is not a page with nothing in it -- it does not exist,
        and nothing may link to it."""
        import re
        for paths in ([FIX], TMDL, PQ):
            for f in self.dir.glob("*.html"):
                f.unlink()
            out = self.render(paths)
            for name, html in out.items():
                for href in set(re.findall(r'href="([a-z-]+\.html)', html)):
                    self.assertIn(href, out, "%s links to missing %s" % (name, href))

    def test_no_network_requests(self):
        """The one property that makes these files worth sending: they open from
        file://, offline, in five years. One CDN reference ends that."""
        import re
        for html in self.render([FIX]).values():
            # Anything the page loads. A plain <a href> is a link the reader may follow
            # (the References page has four); it is not fetched when the page opens.
            for tag in re.findall(r"<(?!a\s)[a-z]+\b[^>]*>", html):
                for ref in re.findall(r'(?:src|href)\s*=\s*"([^"]+)"', tag):
                    self.assertFalse(ref.startswith(("http:", "https:", "//")), ref)
            for ref in re.findall(r'url\(\s*["\']?(https?:|//)', html):
                self.fail("CSS loads %s" % ref)

    def test_no_control_bytes(self):
        """Heredocs and editors have both silently written a NUL into these files
        before. It survives, renders, and is invisible to grep."""
        written = build_pages(build([FIX]), self.dir)
        for p in written:
            b = p.read_bytes()
            bad = sorted({c for c in b if c < 9 or c in (11, 12) or 14 <= c < 32})
            self.assertEqual(bad, [], "%s has control bytes %s" % (p.name, bad))

    def test_catalogue_adds_the_archive_and_nothing_else_does(self):
        plain = self.render([FIX])["semantic-model.html"]
        self.assertNotIn('id="archive"', plain)
        self.assertNotIn('id="later"', plain)
        for f in self.dir.glob("*.html"):
            f.unlink()
        full = self.render([FIX], catalogue=True)["semantic-model.html"]
        self.assertIn('id="archive"', full)
        self.assertIn('id="later"', full)
        self.assertGreater(len(full), len(plain))

    def test_the_rail_lists_every_page_and_no_others(self):
        out = self.render([FIX / "sample-sales-analytics.tmdl"])
        self.assertIn("semantic-model.html", out["index.html"])
        self.assertNotIn("power-query.html", out["index.html"])

    def test_a_second_render_in_one_process_repeats_itself(self):
        """The cards accumulate parts in a module list, so a second render could
        otherwise emit the first run's parts as well."""
        first = self.render([FIX])["semantic-model.html"]
        for f in self.dir.glob("*.html"):
            f.unlink()
        second = self.render([FIX])["semantic-model.html"]
        self.assertEqual(len(first), len(second))

    def test_the_card_numbering_guard_actually_fires(self):
        """The card codes are literals, guarded by an assertion in `part()`. A guard
        that has never failed is not a guard, so it is failed here on purpose."""
        from sma.render import cards
        cards.bind(build(TMDL))
        cards.reset_parts()
        with self.assertRaises(AssertionError) as cm:
            cards.part("part-t", "t", "{n}",
                       [("a", "h", "k", '<span class="dcv-title">T2 second</span>'),
                        ("b", "h", "k", '<span class="dcv-title">T1 first</span>')])
        self.assertIn("out of order", str(cm.exception))

    def test_numbers_on_the_page_come_from_the_dataset(self):
        report = build([FIX])
        html = build_pages(report, self.dir)[1].read_text(encoding="utf-8")
        self.assertIn(str(report["tmdl"]["totals"]["measures"]), html)
        self.assertIn(str(report["tmdl"]["totals"]["links"]), html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
