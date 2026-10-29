"""The app layer: the finder's index, the views, and the links between them.

The browser half (keys, the drawer, one view at a time) is checked by validate_html.py;
these hold the generated half to what the script assumes about it.
"""

import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "plugins" / "semantic-model-analyzer" / "skills" / "semantic-model-analyzer"))

from sma import dataset, inputs  # noqa: E402
from sma.render import build_pages  # noqa: E402

HERE = Path(__file__).parent
PBIP = HERE / "pbip" / "Review.SemanticModel"
ISLAND = re.compile(r'<script type="application/json" id="sma-index">(.*?)</script>', re.S)


def render(paths):
    tmp = Path(tempfile.mkdtemp())
    report = dataset.build(inputs.resolve([str(p) for p in paths]))
    pages = {p.name: p.read_text(encoding="utf-8") for p in build_pages(report, tmp)}
    shutil.rmtree(tmp, ignore_errors=True)
    return report, pages


class TestIndex(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report, cls.pages = render([PBIP])
        cls.ix = json.loads(ISLAND.search(cls.pages["index.html"]).group(1))
        cls.by = {o["id"]: o for o in cls.ix["objects"]}

    def test_every_page_carries_the_same_index(self):
        islands = {ISLAND.search(h).group(1) for h in self.pages.values()}
        self.assertEqual(len(islands), 1)

    def test_every_measure_table_and_column_is_findable(self):
        t = self.report["tmdl"]
        for m in t["measures"]:
            self.assertIn("m:" + m["name"], self.by)
        for tb in t["tables"]:
            self.assertIn("t:" + tb["name"], self.by)
        for c in t["columns"]:
            self.assertIn("c:%s[%s]" % (c["table"], c["name"]), self.by)

    def test_every_link_lands_on_an_object(self):
        for o in self.ix["objects"]:
            for _, ids in o["l"]:
                for i in ids:
                    self.assertIn(i, self.by, "%s links to %s" % (o["id"], i))

    def test_findings_point_at_their_subject(self):
        named = [f for f in self.ix["findings"] if f["o"]]
        self.assertTrue(named)
        for i, f in enumerate(self.ix["findings"]):
            if f["o"]:
                self.assertIn(i, self.by[f["o"]].get("fi", []))
            self.assertIn(f["code"], self.ix["help"])

    def test_show_on_page_targets_exist(self):
        html_of = {"tmdl": self.pages["semantic-model.html"], "pq": self.pages["power-query.html"]}
        for kind, targets in self.ix["go"].items():
            for label, page, anchor, _ in targets:
                self.assertIn('id="%s"' % anchor, html_of[page], "%s -> %s" % (kind, anchor))

    def test_the_island_is_script_safe(self):
        for h in self.pages.values():
            self.assertNotIn("</script", ISLAND.search(h).group(1))


class TestViews(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _, cls.pages = render([PBIP])

    def test_one_tab_per_view_and_ids_are_unique(self):
        for name in ("semantic-model.html", "power-query.html"):
            h = self.pages[name]
            views = re.findall(r'class="sma-view dc-full" id="view-([\w-]+)"', h)
            tabs = re.findall(r'class="sma-tab" href="#([\w-]+)" data-view="\1"', h)
            self.assertEqual(views, tabs, name)
            self.assertIn("about", views)
            ids = re.findall(r'\sid="([^"]+)"', h)
            dupes = sorted({i for i in ids if ids.count(i) > 1})
            self.assertEqual(dupes, [], name)

    def test_cards_have_stable_ids(self):
        h = self.pages["semantic-model.html"]
        self.assertIn('id="card-t1"', h)
        self.assertIn('id="card-t14"', h)

    def test_method_notes_are_folded(self):
        h = self.pages["semantic-model.html"]
        self.assertGreater(h.count('class="sma-how"'), 5)

    def test_rail_links_to_other_pages_resolve(self):
        for name, h in self.pages.items():
            for page, anchor in re.findall(r'href="([a-z-]+\.html)#([\w-]+)"', h):
                self.assertIn('id="%s"' % anchor, self.pages[page], "%s: %s#%s" % (name, page, anchor))


class TestPowerQueryOnly(unittest.TestCase):
    def test_no_tmdl_objects_and_no_ai_page(self):
        _, pages = render([HERE / "fixtures" / "sample.pq"])
        self.assertNotIn("ai-readiness.html", pages)
        ix = json.loads(ISLAND.search(pages["index.html"]).group(1))
        self.assertEqual({o["k"] for o in ix["objects"]}, {"query"})
        self.assertNotIn("semantic-model.html", "".join(pages.values()).replace(
            'href="semantic-model.html', ""))


if __name__ == "__main__":
    unittest.main()
