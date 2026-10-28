"""The landing page: lineage, highlights, the health checklist and the references page."""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sma import dataset, inputs  # noqa: E402
from sma.render import build_pages, overview  # noqa: E402

HERE = Path(__file__).parent
PBIP = HERE / "pbip" / "Review.SemanticModel"
GUID = "08000aac-8c6c-4697-bd48-22b6d3719fd9"

AUTO_DATE_TMDL = """table Sales
\tcolumn Amount
\t\tdataType: double
\t\tsourceColumn: Amount
\tcolumn OrderDate
\t\tdataType: dateTime
\t\tsourceColumn: OrderDate
\tmeasure Total = SUM ( Sales[Amount] )

table LocalDateTable_%s
\tisHidden
\tcolumn Date
\t\tdataType: dateTime
\t\tisHidden
\t\tsourceColumn: [Date]
\tpartition LocalDateTable_%s = calculated
\t\tmode: import
\t\tsource = Calendar(Date(2020,1,1), Date(2020,12,31))

relationship r1
\tfromColumn: Sales.OrderDate
\ttoColumn: LocalDateTable_%s.Date
""" % (GUID, GUID, GUID)


def build(paths=None, text=None):
    if text:
        return dataset.build(inputs.resolve([], [(text, "auto.tmdl", "paste", None)]))
    return dataset.build(inputs.resolve([str(p) for p in paths]))


class TestLineage(unittest.TestCase):
    def test_edges_join_real_nodes_and_measures_come_last(self):
        g = overview.lineage(build([PBIP]))
        ids = {n["id"] for n in g["nodes"]}
        for a, b in g["edges"]:
            self.assertIn(a, ids)
            self.assertIn(b, ids)
        kinds = {n["kind"] for n in g["nodes"]}
        self.assertTrue({"source", "table", "measures"} <= kinds)
        self.assertTrue(all(n["tone"] for n in g["nodes"]))

    def test_sources_feed_something(self):
        g = overview.lineage(build([PBIP]))
        feeding = {a for a, _ in g["edges"]}
        for n in g["nodes"]:
            if n["kind"] == "source":
                self.assertIn(n["id"], feeding, n["label"])

    def test_auto_date_tables_collapse_into_one_flagged_node(self):
        g = overview.lineage(build(text=AUTO_DATE_TMDL))
        self.assertEqual(g["autoDate"], 1)
        auto = [n for n in g["nodes"] if n["id"] == "auto"]
        self.assertEqual(len(auto), 1)
        self.assertTrue(auto[0]["flag"])
        self.assertFalse(any("LocalDateTable" in n["label"] for n in g["nodes"]))

    def test_unused_table_is_flagged(self):
        g = overview.lineage(build([PBIP]))
        flagged = {n["label"] for n in overview.flagged(g)}
        self.assertIn("Composite", flagged)


class TestMeasureLineage(unittest.TestCase):
    def test_measures_read_tables_and_each_other(self):
        rep = build([PBIP])
        g = overview.measure_lineage(rep)
        ids = {n["id"] for n in g["nodes"]}
        for a, b in g["edges"]:
            self.assertIn(a, ids)
            self.assertIn(b, ids)
            self.assertTrue(b.startswith("m:"))
        measures = [n for n in g["nodes"] if n["kind"] == "measure"]
        self.assertEqual(len(measures), len(rep["tmdl"]["measures"]))
        self.assertTrue(any(a.startswith("m:") for a, _ in g["edges"]), "no measure-to-measure edge")
        self.assertEqual(g["shown"], g["total"])

    def test_a_large_model_is_capped_with_dependencies_kept(self):
        rep = build([PBIP])
        g = overview.measure_lineage(rep, cap=2)
        self.assertLess(g["shown"], g["total"])
        kept = {n["id"][2:] for n in g["nodes"] if n["kind"] == "measure"}
        by = {m["name"]: m for m in rep["tmdl"]["measures"]}
        for name in kept:
            for d in by[name].get("dependsOn") or []:
                self.assertIn(d, kept, "%s kept without %s" % (name, d))

    def test_no_measures_no_graph(self):
        self.assertIsNone(overview.measure_lineage(build([HERE / "fixtures" / "sample.pq"])))


class TestHighlights(unittest.TestCase):
    def test_auto_date_leads_and_the_list_is_short(self):
        rep = build(text=AUTO_DATE_TMDL)
        pages = {"tmdl": {"file": "semantic-model.html"}, "ai": {"file": "ai-readiness.html"}}
        items = overview.highlights(rep, pages, overview.lineage(rep))
        self.assertLessEqual(len(items), 4)
        self.assertEqual(items[0][0], "bad")
        self.assertIn("auto date/time", items[0][2])

    def test_checklist_statuses(self):
        rep = build([PBIP])
        rows = overview.checklist(rep, [("Checked", 3, 4, 0.5)], [], overview.lineage(rep))
        self.assertTrue(rows)
        self.assertEqual({r[4] for r in rows} - {"pass", "warn", "fail"}, set())


class TestLandingPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = Path(tempfile.mkdtemp())
        cls.pages = {p.name: p.read_text(encoding="utf-8")
                     for p in build_pages(build([PBIP]), tmp)}
        shutil.rmtree(tmp, ignore_errors=True)

    def test_landing_carries_the_highlights_and_the_lineage(self):
        h = self.pages["index.html"]
        for anchor in ("glance", "highlights", "health", "lineage", "mlineage", "first", "jump"):
            self.assertIn('id="%s"' % anchor, h)
        self.assertIn('id="lineage-data"', h)
        self.assertIn('id="mlineage-data"', h)
        start = h.index('id="glance"')
        self.assertLess(start, h.index('id="lineage"'))
        self.assertLess(h.index('id="lineage"'), h.index('id="fed"'))

    def test_power_query_only_rail_has_no_dead_landing_link(self):
        tmp = Path(tempfile.mkdtemp())
        pages = {p.name: p.read_text(encoding="utf-8")
                 for p in build_pages(build([HERE / "fixtures" / "sample.pq"]), tmp)}
        shutil.rmtree(tmp, ignore_errors=True)
        self.assertNotIn('id="mlineage"', pages["index.html"])
        import re
        for name, h in pages.items():
            for anchor in re.findall(r'href="index\.html#([\w-]+)"', h):
                self.assertIn('id="%s"' % anchor, pages["index.html"], "%s -> #%s" % (name, anchor))

    def test_references_page_is_last_and_linked_everywhere(self):
        self.assertIn("references.html", self.pages)
        refs = self.pages["references.html"]
        self.assertNotIn("Made with", refs)
        for url in ("https://datacrafters.io", "https://www.linkedin.com/in/khaledchowdhury/",
                    "https://www.linkedin.com/company/datacraftersio",
                    "https://fabricdataaiplatfrom.substack.com/",
                    "https://data-mozart.com/about/", "https://learn.microsoft.com/power-bi/"):
            self.assertIn(url, refs)
        for name, h in self.pages.items():
            self.assertIn('href="references.html"', h, name)


if __name__ == "__main__":
    unittest.main()
