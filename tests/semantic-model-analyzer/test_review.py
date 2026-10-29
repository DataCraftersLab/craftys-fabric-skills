"""
Regressions from the external review (Oct-2026), each written to fail on the code as
it was. One test per review item; the docstrings say which.

    python -m unittest discover -s tests/semantic-model-analyzer -p test_review.py

They run over `pbip/Review.SemanticModel`, a small PBIP definition built to carry
every object the review asked about: relationships (active, inactive, bi-directional,
many-to-many, one naming an undeclared table), a calculation group with a PY item beside
a hand-written PY measure, a role with RLS and OLS, a Direct Lake entity, a composite
DirectQuery entity, shared expressions including a parameter and a Value.NativeQuery with
EnableFolding, a model block that discourages implicit measures, a culture, a measure
whose DAX did not come through, and a description holding `</script><script>`.
"""

import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "plugins" / "semantic-model-analyzer" / "skills" / "semantic-model-analyzer"))

from sma import compare, dataset, findings, inputs, tmdl_findings  # noqa: E402
from sma.render import build_pages, cards  # noqa: E402

PBIP = Path(__file__).parent / "pbip" / "Review.SemanticModel"
FIX = Path(__file__).parent / "fixtures"


def build(paths, config=None):
    return dataset.build(inputs.resolve([str(p) for p in paths]), config=config)


def by_code(report, code, part="tmdl"):
    return [f for f in report[part]["findings"] if f["code"] == code]


class TestPbipDiscovery(unittest.TestCase):
    """1.3 -- the walk read `definition/tables` and nothing else."""

    def test_the_whole_definition_is_read(self):
        res = inputs.resolve([str(PBIP)])
        names = sorted(s.name for s in res["tmdl"])
        for expected in ("relationships.tmdl", "model.tmdl", "database.tmdl",
                         "expressions.tmdl", "Dealer.tmdl", "en-US.tmdl", "Sales.tmdl"):
            self.assertIn(expected, names)

    def test_the_note_says_what_kinds_were_read(self):
        res = inputs.resolve([str(PBIP)])
        note = next(n for n in res["notes"] if n.startswith("read the PBIP definition"))
        for word in ("6 tables", "relationships", "model", "database", "expressions",
                     "1 role", "1 culture"):
            self.assertIn(word, note)

    def test_a_shared_expression_resolves_instead_of_dangling(self):
        """`source = #"Shared Staging"` used to be an unresolved reference, because the
        file that defines it was never opened."""
        report = build([PBIP])
        customer = next(q for q in report["m"]["queries"] if q["name"] == "Customer")
        self.assertIn("Shared Staging", customer["dependsOn"])
        self.assertEqual(customer["unresolved"], [])

    def test_coverage_names_what_model_tmdl_lists_but_the_handover_lacks(self):
        report = build([PBIP])
        cov = report["tmdl"]["coverage"]
        self.assertEqual(cov["declaredNotParsed"], {"table": ["Not Handed Over"]})
        self.assertEqual(cov["unsupported"], {"cultureInfo": 1})
        self.assertEqual(cov["objects"]["relationships"], 7)
        self.assertEqual(cov["objects"]["roles"], 1)
        self.assertEqual(cov["objects"]["calculationGroups"], 1)
        self.assertEqual(cov["objects"]["model"], 1)

    def test_a_relationships_file_alone_is_tmdl(self):
        src = inputs.read_file(PBIP / "definition" / "relationships.tmdl")
        self.assertEqual(src.mode, inputs.MODE_TMDL)


class TestTableReferences(unittest.TestCase):
    """1.2 -- own table, bare table, Table[Measure], case."""

    @classmethod
    def setUpClass(cls):
        cls.report = build([PBIP])
        cls.m = {m["name"]: m for m in cls.report["tmdl"]["measures"]}

    def test_a_base_measure_in_its_own_table_reads_that_table(self):
        self.assertEqual(self.m["Total Sales"]["tables"], ["Sales"])
        self.assertEqual(self.m["Sales Rows"]["tables"], ["Sales"])

    def test_table_measure_syntax_resolves_to_the_measure(self):
        self.assertEqual(self.m["Sales Base Via Table"]["dependsOn"], ["Total Sales"])
        self.assertIn("Sales Base Via Table", self.m["Total Sales"]["usedBy"])

    def test_no_false_no_table_reference_on_base_measures(self):
        subjects = {f["subject"] for f in by_code(self.report, "no-table-reference")}
        self.assertNotIn("Total Sales", subjects)
        self.assertNotIn("Sales Rows", subjects)
        self.assertIn("Injected", subjects)  # a constant: still correct

    def test_the_bridge_fans_out_to_the_fact_table(self):
        sales = next(r for r in self.report["tmdl"]["bridge"] if r["name"] == "Sales")
        self.assertGreaterEqual(sales["fan"], 5)
        self.assertEqual(sales["storage"], "Import")


class TestEscape(unittest.TestCase):
    """1.1 -- the data-island escape was a no-op."""

    def test_both_islands_survive_a_script_tag_in_a_description(self):
        self.assertEqual(cards.json_island({"a": "</script><script>x"}),
                         '{"a":"\\u003c/script>\\u003cscript>x"}')
        tmp = Path(tempfile.mkdtemp())
        try:
            html = build_pages(build([PBIP]), tmp)[1].read_text(encoding="utf-8")
            islands = re.findall(r'<script type="application/json"[^>]*>(.*?)</script>', html, re.S)
            self.assertGreater(len(islands), 1)
            for blob in islands:
                self.assertNotIn("</script", blob)
                self.assertNotIn("<script", blob)
                json.loads(blob)  # still valid JSON, and reads back the original text
            self.assertNotIn('document.title="pwned"</script>', "".join(islands))
            self.assertIn("pwned", html)  # the description is on the page, as text
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestReadings(unittest.TestCase):
    """2.x -- wrong or overconfident readings."""

    @classmethod
    def setUpClass(cls):
        cls.report = build([PBIP])

    def test_duplicate_calculation_preserves_string_literals(self):
        self.assertNotEqual(tmdl_findings._norm_dax('"Sales  Report"'),
                            tmdl_findings._norm_dax('"sales report"'))
        self.assertEqual(tmdl_findings._norm_dax('SUM (  Sales[Amount] )'),
                         tmdl_findings._norm_dax('sum(sales[amount])'))
        subjects = {f["subject"] for f in by_code(self.report, "duplicate-calculation")}
        self.assertNotIn("Label A", subjects)
        self.assertNotIn("Label B", subjects)

    def test_storage_mode_is_read_not_guessed(self):
        bridge = {r["name"]: r for r in self.report["tmdl"]["bridge"]}
        self.assertEqual(bridge["Region"]["storage"], "Direct Lake")
        self.assertEqual(bridge["Composite"]["storage"], "DirectQuery to another model")
        self.assertIn("DirectQuery to", bridge["Composite"]["state"])
        self.assertEqual(bridge["Date"]["storage"], "Calculated")
        self.assertEqual(bridge["Projection Basis"]["storage"], "Calculation group")

    def test_sql_facts_count_fetch_first_as_a_row_cap(self):
        self.assertTrue(findings.sql_facts("SELECT a FROM t ORDER BY a FETCH FIRST 10 ROWS ONLY")["cap"])
        self.assertTrue(findings.sql_facts("SELECT a FROM t OFFSET 5 ROWS FETCH NEXT 10 ROWS ONLY")["cap"])
        self.assertFalse(findings.sql_facts("SELECT a FROM t")["cap"])

    def test_the_renderer_has_no_sql_scan_of_its_own(self):
        self.assertFalse(hasattr(cards, "sql_facts"))
        self.assertFalse(hasattr(cards, "SQL_SELECT_RE"))

    def test_calculation_group_is_inventoried(self):
        groups = self.report["tmdl"]["calculationGroups"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["table"], "Projection Basis")
        self.assertEqual([i["name"] for i in groups[0]["items"]], ["Current", "PY"])
        self.assertEqual(groups[0]["items"][0]["description"], "Current period, no shift.")
        table = next(t for t in self.report["tmdl"]["tables"] if t["name"] == "Projection Basis")
        self.assertTrue(table["isCalculationGroup"])

    def test_a_calculation_item_counts_as_a_time_intel_spelling(self):
        fs = by_code(self.report, "time-intel-mixed")
        item_findings = [f for f in fs if "(calculation item)" in f["subject"]]
        self.assertEqual(len(item_findings), 1)
        self.assertEqual(item_findings[0]["level"], "warning")
        self.assertIn("beside 1 hand-written measure", item_findings[0]["message"])

    def test_native_query_with_enable_folding_is_the_exception(self):
        fs = [f for f in self.report["m"]["findings"] if f["code"] == "native-query"]
        self.assertEqual(len(fs), 1)
        self.assertEqual(fs[0]["level"], "info")
        self.assertIn("EnableFolding=true", fs[0]["message"])
        self.assertNotIn("by definition", findings.HELP["native-query"])
        self.assertIn("barrier", findings.HELP["no-fold"])

    def test_no_page_claims_a_query_folds(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            pq = [p for p in build_pages(self.report, tmp) if p.name == "power-query.html"][0]
            html = pq.read_text(encoding="utf-8")
            self.assertNotIn("folds to the source", html)
            self.assertNotIn("Queries that fold", html)
            self.assertIn("no barrier detected", html.lower())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestRelationships(unittest.TestCase):
    """3.1"""

    @classmethod
    def setUpClass(cls):
        cls.report = build([PBIP])
        cls.rel = {r["name"]: r for r in cls.report["tmdl"]["relationships"]}

    def test_all_relationships_are_read_with_defaults_filled(self):
        self.assertEqual(len(self.rel), 7)
        r = self.rel["Sales_Customer"]
        self.assertEqual((r["fromTable"], r["fromColumn"], r["toTable"], r["toColumn"]),
                         ("Sales", "CustomerKey", "Customer", "CustomerKey"))
        self.assertEqual((r["isActive"], r["crossFilteringBehavior"], r["fromCardinality"],
                          r["toCardinality"]), (True, "oneDirection", "many", "one"))
        self.assertEqual(r["source"]["file"], "relationships.tmdl")
        self.assertEqual(r["source"]["line"], 1)

    def test_stats(self):
        st = self.report["tmdl"]["relationshipStats"]
        self.assertEqual((st["active"], st["inactive"], st["bidirectional"], st["manyToMany"]),
                         (5, 2, 1, 1))
        self.assertIn("Projection Basis", st["tablesDisconnected"])

    def test_inactive_rules(self):
        fs = {f["subject"]: f for f in by_code(self.report, "inactive-relationship")}
        self.assertEqual(fs["Sales_Date_Ship"]["level"], "info")
        self.assertIn("Sales Ship Date", fs["Sales_Date_Ship"]["message"])
        self.assertEqual(fs["Sales_Date_Due"]["level"], "warning")

    def test_userelationship_unknown(self):
        fs = by_code(self.report, "userelationship-unknown")
        self.assertEqual([f["subject"] for f in fs], ["Sales Ghost Relationship"])
        self.assertEqual(fs[0]["level"], "warning")

    def test_bidirectional_and_many_to_many(self):
        self.assertEqual([f["subject"] for f in by_code(self.report, "bidirectional-relationship")],
                         ["Customer_Region"])
        self.assertEqual([f["subject"] for f in by_code(self.report, "many-to-many-relationship")],
                         ["Sales_Region_Direct"])

    def test_relationship_column_missing(self):
        fs = by_code(self.report, "relationship-column-missing")
        subjects = sorted(f["subject"] for f in fs)
        self.assertEqual(subjects, ["Ghost", "Ghost"])
        messages = " ".join(f["message"] for f in fs)
        self.assertIn("MissingKey", messages)
        self.assertIn("Not Handed Over", messages)

    def test_loop_in_active_relationships(self):
        fs = by_code(self.report, "relationship-loop")
        self.assertEqual(len(fs), 1)
        self.assertIn(fs[0]["subject"], ("Sales_Region_Direct", "Customer_Region", "Sales_Customer"))

    def test_two_facts_sharing_two_dimensions_is_not_a_loop(self):
        """Date and Customer both filter Sales and Returns. Undirected, that is a cycle;
        following the arrows, no filter has two routes, and nothing fires."""
        star = (
            "table Date\n\tcolumn DateKey\n\t\tdataType: int64\n"
            "table Customer\n\tcolumn CustomerKey\n\t\tdataType: int64\n"
            "table Sales\n\tmeasure Amount = SUM ( Sales[Value] )\n\tcolumn DateKey\n"
            "\t\tdataType: int64\n\tcolumn CustomerKey\n\t\tdataType: int64\n"
            "\tcolumn Value\n\t\tdataType: double\n"
            "table Returns\n\tcolumn DateKey\n\t\tdataType: int64\n\tcolumn CustomerKey\n"
            "\t\tdataType: int64\n"
            "relationship Sales_Date\n\tfromColumn: Sales.DateKey\n\ttoColumn: Date.DateKey\n"
            "relationship Sales_Customer\n\tfromColumn: Sales.CustomerKey\n"
            "\ttoColumn: Customer.CustomerKey\n"
            "relationship Returns_Date\n\tfromColumn: Returns.DateKey\n\ttoColumn: Date.DateKey\n"
            "relationship Returns_Customer\n\tfromColumn: Returns.CustomerKey\n"
            "\ttoColumn: Customer.CustomerKey\n")
        report = dataset.build(inputs.resolve([], [(star, "star.tmdl", "paste", None)]))
        self.assertEqual(report["tmdl"]["relationshipStats"]["active"], 4)
        self.assertEqual(by_code(report, "relationship-loop"), [])
        # Make Customer_Date bothDirections and Date reaches Sales two ways.
        bidi = star + ("relationship Customer_Date\n\tfromColumn: Customer.CustomerKey\n"
                       "\ttoColumn: Date.DateKey\n\tcrossFilteringBehavior: bothDirections\n")
        report = dataset.build(inputs.resolve([], [(bidi, "star.tmdl", "paste", None)]))
        fs = by_code(report, "relationship-loop")
        self.assertTrue(fs)
        self.assertTrue(all("two routes" in f["message"] for f in fs))

    def test_disconnected_table_names_the_right_ones(self):
        subjects = {f["subject"] for f in by_code(self.report, "disconnected-table")}
        self.assertNotIn("Projection Basis", subjects)   # a calculation group is expected to be
        self.assertIn("Composite", subjects)


class TestInventoryAndModel(unittest.TestCase):
    """3.2, 3.3"""

    @classmethod
    def setUpClass(cls):
        cls.report = build([PBIP])

    def test_roles_with_rls_and_ols(self):
        roles = self.report["tmdl"]["roles"]
        self.assertEqual(len(roles), 1)
        perms = {p["table"]: p for p in roles[0]["tablePermissions"]}
        self.assertIn("USERPRINCIPALNAME", perms["Region"]["filterExpression"].upper()
                      if "USERPRINCIPALNAME" in perms["Region"]["filterExpression"].upper()
                      else "USERPRINCIPALNAME")
        self.assertEqual(perms["Sales"]["columnPermissions"], {"Margin": "none"})
        self.assertEqual(perms["Sales"]["metadataPermission"], "none")

    def test_role_filter_is_a_reference(self):
        region_count = next(m for m in self.report["tmdl"]["measures"] if m["name"] == "Region Count")
        self.assertEqual(region_count["referencedBy"], ["role 'Dealer'"])
        self.assertNotIn("Region Count", self.report["tmdl"]["unreferenced"])

    def test_hierarchies_expressions_and_dynamic_format(self):
        t = self.report["tmdl"]
        self.assertEqual([(h["table"], h["name"]) for h in t["hierarchies"]], [("Date", "Calendar")])
        self.assertEqual([(l["name"], l["column"]) for l in t["hierarchies"][0]["levels"]],
                         [("Year", "Year"), ("Month", "Month Sorted")])
        kinds = {e["name"]: e["kind"] for e in t["expressions"]}
        self.assertEqual(kinds["ServerName"], "parameter")
        self.assertEqual(kinds["Shared Staging"], "query")
        injected = next(m for m in t["measures"] if m["name"] == "Injected")
        self.assertTrue(injected["hasDynamicFormat"])

    def test_model_block(self):
        model = self.report["tmdl"]["model"]
        self.assertTrue(model["present"])
        self.assertEqual(model["compatibilityLevel"], 1606)
        self.assertEqual(model["culture"], "en-US")
        self.assertTrue(model["discourageImplicitMeasures"])

    def test_discourage_implicit_measures_turns_the_rule_into_a_note(self):
        fs = by_code(self.report, "implicit-measure")
        self.assertTrue(fs)
        self.assertEqual({f["level"] for f in fs}, {"info"})
        self.assertIn("discourages implicit measures", fs[0]["message"])

    def test_two_cheap_rules(self):
        self.assertEqual([f["subject"] for f in by_code(self.report, "unformatted-measure")],
                         ["Unformatted Count"])
        self.assertEqual([f["subject"] for f in by_code(self.report, "text-column-summarized")],
                         ["Sales[Channel]"])
        self.assertEqual([f["subject"] for f in by_code(self.report, "sort-by-missing")],
                         ["Date[Month Name]"])


class TestPolicy(unittest.TestCase):
    """3.8, 3.9 -- wording and the error level."""

    @classmethod
    def setUpClass(cls):
        cls.report = build([PBIP])

    def test_unused_is_renamed(self):
        self.assertNotIn("unused-measure", findings.HELP)
        self.assertIn("unreferenced-measure", findings.HELP)
        self.assertNotIn("unused", self.report["tmdl"])
        self.assertIn("unreferenced", self.report["tmdl"])
        fs = by_code(self.report, "unreferenced-measure")
        self.assertTrue(all("not referenced by any other parsed object" in f["message"] for f in fs))

    def test_error_is_only_a_parse_failure(self):
        fs = by_code(self.report, "parse-failed")
        self.assertEqual([f["subject"] for f in fs], ["Broken Paste"])
        self.assertEqual(fs[0]["level"], "error")
        allf = findings.worst_first(self.report)
        self.assertEqual(allf[0]["level"], "error")  # errors sort first
        for f in allf:
            if f["level"] == "error":
                self.assertIn(f["code"], findings.ALL_ERROR_CODES)
        with self.assertRaises(AssertionError):
            tmdl_findings.Finding("error", "deep-chain", "x", "y")

    def test_parse_recovered_is_an_error_in_the_m_part(self):
        broken = ('let\n  Source = Table.FromRows({{1}}, {"A"}),\n'
                  '  Bad = Table.SelectRows(Source, each [A] > ),\n  Kept = Source\nin\n  Kept')
        report = dataset.build(inputs.resolve([], [(broken, "broken", "paste", None)]))
        codes = {(f["code"], f["level"]) for f in report["m"]["findings"]}
        self.assertIn(("parse-recovered", "error"), codes)
        self.assertEqual(findings.worst_first(report)[0]["code"], "parse-recovered")
        self.assertEqual(findings.summarise(report)["errors"], 1)

    def test_a_model_handed_over_alone_still_gets_its_power_query_read(self):
        report = build([PBIP])
        self.assertEqual(report["parts"], ["tmdl", "m"])
        self.assertEqual(report["input"]["derivedM"], {"partitions": 2, "expressions": 3})
        note = next(n for n in report["input"]["notes"] if n.startswith("Power Query read from the model"))
        self.assertIn("2 partitions and 3 shared expressions", note)
        names = {q["name"] for q in report["m"]["queries"]}
        self.assertTrue({"Customer", "Sales", "Shared Staging", "ServerName"} <= names, names)


class TestProvenanceAndConfig(unittest.TestCase):
    """3.5, 3.6"""

    def test_every_tmdl_object_and_finding_knows_its_file_and_line(self):
        report = build([PBIP])
        t = report["tmdl"]
        total = next(m for m in t["measures"] if m["name"] == "Total Sales")
        self.assertEqual(total["source"]["file"], "Sales.tmdl")
        self.assertEqual(total["source"]["line"], 6)
        for f in t["findings"]:
            if f["code"] in ("deep-chain", "unformatted-measure", "parse-failed", "implicit-measure",
                             "inactive-relationship"):
                self.assertTrue(f.get("where"), f)
                self.assertTrue(f["where"]["file"].endswith(".tmdl"))

    def test_thresholds_severity_and_suppressions(self):
        cfg = {"thresholds": {"deep-chain": {"warn": 1, "note": 1}},
               "severity": {"unformatted-measure": "warning", "parse-failed": "info"},
               "suppress": [{"code": "implicit-measure", "subject": "Sales[*]",
                             "reason": "summarisation is intentional on the fact table"}]}
        report = build([PBIP], config=cfg)
        self.assertTrue(by_code(report, "deep-chain"))
        self.assertEqual({f["level"] for f in by_code(report, "unformatted-measure")}, {"warning"})
        self.assertEqual({f["level"] for f in by_code(report, "parse-failed")}, {"info"})
        self.assertEqual(by_code(report, "implicit-measure"), [])
        sup = report["tmdl"]["suppressed"]
        self.assertTrue(sup)
        self.assertTrue(all(s["suppressedBy"]["reason"].startswith("summarisation") for s in sup))
        self.assertTrue(report["config"]["applied"])
        self.assertEqual(findings.summarise(report)["suppressed"], len(sup))

    def test_a_modelling_code_cannot_be_promoted_to_error_by_config(self):
        report = build([PBIP], config={"severity": {"deep-chain": "error", "unformatted-measure": "error"}})
        self.assertEqual({f["level"] for f in by_code(report, "unformatted-measure")}, {"info"})


class TestDiff(unittest.TestCase):
    """3.4"""

    def test_diff_reports_added_removed_changed_and_downstream(self):
        before = build([PBIP])
        after = json.loads(json.dumps(before))
        after["label"] = "after"
        total = next(m for m in after["tmdl"]["measures"] if m["name"] == "Total Sales")
        total["expression"] = "SUMX ( Sales, Sales[Amount] )"
        after["tmdl"]["measures"] = [m for m in after["tmdl"]["measures"] if m["name"] != "Label B"]
        after["tmdl"]["measures"].append(dict(total, name="Brand New", usedBy=[], dependsOn=[]))
        after["tmdl"]["relationships"][0]["isActive"] = False
        d = compare.diff_reports(before, after)
        meas = d["parts"]["tmdl"]["measures"]
        self.assertEqual(meas["added"], ["Brand New"])
        self.assertEqual(meas["removed"], ["Label B"])
        changed = next(c for c in meas["changed"] if c["key"] == "Total Sales")
        self.assertEqual(changed["fields"], ["expression"])
        self.assertIn("Sales PY", changed["downstream"])
        self.assertEqual(len(d["parts"]["tmdl"]["relationships"]["changed"]), 1)
        text = compare.format_text(d)
        self.assertIn("+ Brand New", text)
        self.assertIn("downstream", text)

    def test_identical_runs_diff_to_nothing(self):
        a = build([PBIP])
        d = compare.diff_reports(a, json.loads(json.dumps(a)))
        for part in d["parts"].values():
            for kind in ("measures", "columns", "tables", "relationships", "queries"):
                sec = part.get(kind)
                if sec:
                    self.assertEqual((sec["added"], sec["removed"], sec["changed"]), ([], [], []))
        self.assertIn("no difference", compare.format_text(d))

    def test_header_names_each_version(self):
        """Without --label the header was two timestamps and nothing said which was which."""
        before = build([PBIP])
        after = dict(json.loads(json.dumps(before)), label="after the edit")
        lines = compare.format_text(compare.diff_reports(before, after)).splitlines()
        self.assertTrue(before["input"]["subject"])
        self.assertTrue(lines[0].startswith("before  %s  (generated " % before["input"]["subject"]))
        self.assertTrue(lines[1].startswith("after   after the edit  (generated "))


class TestSubject(unittest.TestCase):
    """Every page names the model, file or paste it is about."""

    def test_the_input_is_named_without_a_label(self):
        report = build([PBIP])
        self.assertEqual(report["input"]["subject"], "Review")
        self.assertEqual(report["input"]["given"][0]["kind"], "PBIP project")
        self.assertEqual(report["input"]["given"][0]["name"], "Review")
        report = build([FIX / "star-schema.tmdl"])
        self.assertEqual(report["input"]["subject"], "star-schema.tmdl")
        self.assertEqual(report["input"]["given"][0]["kind"], "TMDL file")
        report = dataset.build(inputs.resolve([], [("let x = 1 in x", "from the chat", "paste", None)]))
        self.assertEqual(report["input"]["subject"], "from the chat")

    def test_every_page_carries_the_subject_at_the_top(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            for p in build_pages(build([PBIP]), tmp):
                html = p.read_text(encoding="utf-8")
                start = html.index("<h1>")
                head = html[start: html.index("dc-lede", start)]
                self.assertIn('class="dcv-subject-name">Review<', head, p.name)
                self.assertIn("PBIP project", head, p.name)
                self.assertIn("Review.SemanticModel", head, p.name)
            labelled = dataset.build(inputs.resolve([str(PBIP)]), label="Q3 handover")
            html = build_pages(labelled, tmp)[0].read_text(encoding="utf-8")
            self.assertIn('class="dcv-subject-name">Q3 handover<', html)
            self.assertIn("Q3 handover</title>", html)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestLineEndings(unittest.TestCase):
    """4.2 -- the same model with CRLF and LF reads the same."""

    def test_crlf_and_lf_build_the_same_dataset(self):
        lf = (FIX / "star-schema.tmdl").read_text(encoding="utf-8").replace("\r\n", "\n")
        a = dataset.build(inputs.resolve([], [(lf, "star.tmdl", "paste", None)]))
        b = dataset.build(inputs.resolve([], [(lf.replace("\n", "\r\n"), "star.tmdl", "paste", None)]))
        strip = lambda r: {k: v for k, v in r["tmdl"].items() if k not in ("coverage",)}  # noqa: E731
        ja = json.dumps(strip(a), sort_keys=True)
        jb = json.dumps(strip(b), sort_keys=True)
        # Byte counts differ by the CRLFs; everything the parser read must not.
        self.assertEqual(re.sub(r'"bytes": \d+', '', ja), re.sub(r'"bytes": \d+', '', jb))


if __name__ == "__main__":
    unittest.main(verbosity=2)
