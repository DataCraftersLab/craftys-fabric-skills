"""
`list` and `explain`: questions about a model answered from the dataset.

    python -m unittest discover -s tests/semantic-model-analyzer -p test_ask.py
"""

import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "plugins" / "semantic-model-analyzer" / "skills" / "semantic-model-analyzer"))

from sma import ask, cli, dataset, inputs  # noqa: E402

PBIP = Path(__file__).parent / "pbip" / "Review.SemanticModel"
FIX = Path(__file__).parent / "fixtures"


def run_cli(*argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cli.main(list(argv))
    return rc, buf.getvalue()


class TestList(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = dataset.build(inputs.resolve([str(PBIP)]))

    def test_every_kind_answers_without_error(self):
        for kind in ask.KINDS:
            rows, fields, total = ask.list_cmd(self.report, kind)
            self.assertIsInstance(rows, list, kind)
            self.assertTrue(fields, kind)
            self.assertEqual(total, len(rows), kind)

    def test_where_filters_compose(self):
        rows, _, _ = ask.list_cmd(self.report, "measures", where=["table=Sales", "formatString="])
        names = {r["name"] for r in rows}
        self.assertIn("Unformatted Count", names)
        self.assertNotIn("Total Sales", names)
        rows, _, _ = ask.list_cmd(self.report, "relationships", where=["isActive=false"])
        self.assertEqual(sorted(r["name"] for r in rows), ["Sales_Date_Due", "Sales_Date_Ship"])
        rows, _, _ = ask.list_cmd(self.report, "measures", where=["usedBy>0"])
        self.assertIn("Total Sales", {r["name"] for r in rows})
        rows, _, _ = ask.list_cmd(self.report, "columns", where=["dataType=string", "summarizeBy!=none"])
        self.assertEqual([(r["table"], r["name"]) for r in rows], [("Sales", "Channel")])
        rows, _, _ = ask.list_cmd(self.report, "findings", where=["level=error"])
        self.assertEqual([r["code"] for r in rows], ["parse-failed"])
        rows, _, _ = ask.list_cmd(self.report, "measures", where=["source.file=Sales.tmdl", "name~sales"])
        self.assertTrue(rows and all("sales" in r["name"].lower() for r in rows))

    def test_match_searches_the_whole_row(self):
        rows, _, _ = ask.list_cmd(self.report, "measures", match="USERELATIONSHIP")
        self.assertEqual(sorted(r["name"] for r in rows), ["Sales Ghost Relationship", "Sales Ship Date"])

    def test_derived_kinds(self):
        rows, _, _ = ask.list_cmd(self.report, "functions")
        self.assertIn("SUM", [r["name"] for r in rows])
        self.assertTrue(all("measures" in r for r in rows))
        rows, _, _ = ask.list_cmd(self.report, "steps", where=["query=Shared Staging"])
        self.assertTrue(rows)
        rows, _, _ = ask.list_cmd(self.report, "unreferenced")
        self.assertIn("Unformatted Count", {r["name"] for r in rows})
        rows, _, _ = ask.list_cmd(self.report, "partitions", where=["storage=Direct Lake"])
        self.assertEqual([r["name"] for r in rows], ["Region"])

    def test_bad_filter_and_bad_kind_say_so(self):
        with self.assertRaises(ValueError):
            ask.list_cmd(self.report, "measures", where=["nonsense"])
        with self.assertRaises(ValueError):
            ask.rows_of(self.report, "widgets")

    def test_cli_text_json_and_count(self):
        rc, out = run_cli("list", str(PBIP), "relationships", "--where", "isActive=false", "-q")
        self.assertEqual(rc, 0)
        self.assertIn("Sales_Date_Ship", out)
        self.assertIn("2 relationships", out)
        rc, out = run_cli("list", str(PBIP), "measures", "--json", "--where", "name=Total Sales", "-q")
        self.assertEqual(json.loads(out)[0]["name"], "Total Sales")
        rc, out = run_cli("list", str(PBIP), "measures", "--count", "-q")
        self.assertEqual(out.strip(), "12")

    def test_list_reads_an_existing_report(self):
        import tempfile
        tmp = Path(tempfile.mkdtemp()) / "report.json"
        tmp.write_text(dataset.dumps(self.report), encoding="utf-8")
        rc, out = run_cli("list", "tables", "--report", str(tmp), "-q")
        self.assertEqual(rc, 0)
        self.assertIn("Projection Basis", out)


class TestExplain(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = dataset.build(inputs.resolve([str(PBIP)]))

    def test_a_measure(self):
        blocks = ask.explain(self.report, "total sales")
        self.assertEqual([b["kind"] for b in blocks], ["measure"])
        b = blocks[0]
        self.assertEqual(b["name"], "Total Sales")
        self.assertIn("Sales PY", b["usedBy"])
        self.assertIn("Sales Base Via Table", b["downstream"])
        self.assertEqual(b["source"]["file"], "Sales.tmdl")
        self.assertIn("SUM", b["dax"].upper())
        self.assertEqual(b["anatomy"]["chain"], 0)
        text = ask.format_explain(blocks, "total sales")
        self.assertIn("MEASURE  Total Sales", text)
        self.assertIn("DAX:", text)

    def test_a_table_with_everything_attached(self):
        b = ask.explain(self.report, "Sales")
        kinds = [x["kind"] for x in b]
        self.assertIn("table", kinds)
        self.assertIn("query", kinds)  # the partition's M was read as a query of the same name
        tb = next(x for x in b if x["kind"] == "table")
        self.assertEqual(tb["storage"], "Import")
        self.assertIn("Total Sales", tb["measures"])
        self.assertTrue(any(c["name"] == "Amount" for c in tb["columns"]))
        self.assertEqual(len(tb["relationships"]), 6)  # five real ones and Ghost
        self.assertTrue(any(f["code"] == "parse-failed" for f in tb["findings"]) is False)  # findings on the table itself only
        rf = [x for x in tb["roleFilters"]]
        self.assertTrue(any(x["role"] == "Dealer" for x in rf))

    def test_a_column_a_relationship_a_role_and_a_query(self):
        col = ask.explain(self.report, "Sales[Amount]")
        self.assertEqual(col[0]["kind"], "column")
        self.assertIn("Total Sales", col[0]["measuresReadingIt"])
        self.assertTrue(any(f["code"] == "implicit-measure" for f in col[0]["findings"]))
        rel = ask.explain(self.report, "Sales_Date_Ship")
        self.assertEqual(rel[0]["kind"], "relationship")
        self.assertEqual(rel[0]["usedByMeasures"], ["Sales Ship Date"])
        role = ask.explain(self.report, "Dealer")
        self.assertEqual(role[0]["kind"], "role")
        self.assertTrue(role[0]["tablePermissions"])
        q = ask.explain(self.report, "Shared Staging")
        kinds = {x["kind"] for x in q}
        self.assertEqual(kinds, {"expression", "query"})
        qb = next(x for x in q if x["kind"] == "query")
        self.assertTrue(qb["nativeSql"])
        self.assertTrue(any(f["code"] == "native-query" for f in qb["findings"]))

    def test_unknown_name_is_a_clear_answer(self):
        self.assertEqual(ask.explain(self.report, "No Such Thing"), [])
        rc, out = run_cli("explain", str(PBIP), "No Such Thing", "-q")
        self.assertEqual(rc, 1)
        self.assertIn("nothing called", out)

    def test_cli_json(self):
        rc, out = run_cli("explain", str(PBIP), "Region Count", "--json", "-q")
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)[0]["referencedBy"], ["role 'Dealer'"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
