"""
The credential screen: every parsing command stops on credential-looking input, and says
where without saying what.

    python -m unittest discover -s tests/semantic-model-analyzer -p test_screen.py
"""

import io
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "plugins" / "semantic-model-analyzer" / "skills" / "semantic-model-analyzer"))

from sma import cli, inputs, screen  # noqa: E402

HERE = Path(__file__).parent

# Built at run time so no file in the skill carries a credential-shaped literal.
FAKE = "Zq7" * 8
CASES = {
    "password in a connection string":
        'Odbc.DataSource("dsn=Finance;uid=svc;' + "pw" + "d=" + FAKE + '")',
    "account or access key": '"DefaultEndpointsProtocol=https;Account' + "Key=" + FAKE + '"',
    "shared access signature": '"https://x.blob.core.windows.net/c?sv=2024&' + "si" + "g=" + FAKE + '"',
    "client secret": '[client_' + 'secret = "' + FAKE + '"]',
    "bearer token": '[Headers=[Authorization="Bea' + 'rer ' + FAKE + '"]]',
    "API key": '"https://api.example.com/v1/rates?api_' + "key=" + FAKE + '"',
    "token or secret literal": 'Token = "' + FAKE + '"',
    "JSON web token": '"' + "ey" + "J" + FAKE + ".ey" + "J" + FAKE + '.sig"',
    "private key": '"-----BEGIN ' + 'RSA PRIVATE KEY-----"',
}

# Shapes the round-2 review found passing the first version of the screen.
MORE = {
    "password in a connection string": [
        '"Server=s;Database=d;User Id=u;Pass' + 'word=""' + FAKE + '"";"',
        "'Driver=x;PW" + "D=" + "'" + FAKE + "''",
        'Odbc.DataSource("dsn=x", [Pass' + 'word="two ' + FAKE + ' words"])'],
    "account or access key": ['"Endpoint=sb://x.servicebus.windows.net/;SharedAccess' + "Key=" + FAKE + '"'],
    "basic authentication": [
        '[Headers=[Authorization="Ba' + 'sic ' + FAKE + '9=="]]',
        'Binary.ToText(Text.To' + 'Binary("svc:' + FAKE + '"), BinaryEncoding.Base64)'],
    "API key": ['[Headers=[#"Ocp-Apim-Subscription' + '-Key"="' + FAKE + '"]]'],
    "token in a URL": ['"https://api.example.com/v1/x?access_' + "token=" + FAKE + '"',
                       '"https://api.example.com/v1/x?co' + "de=" + FAKE + '&y=1"'],
    "vendor token": ['"gh' + 'p_' + FAKE + '"', '"s' + 'k-' + FAKE + '"'],
}


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = cli.main(list(argv))
    return rc, out.getvalue(), err.getvalue()


def query(expr):
    return "let\n    Source = %s,\n    Kept = Source\nin\n    Kept\n" % expr


class TestPatterns(unittest.TestCase):
    def test_each_shape_is_found_with_its_kind_and_line(self):
        for kind, expr in CASES.items():
            src = inputs.make_source(query(expr), "q.pq", "paste", inputs.MODE_M)
            self.assertEqual(screen.scan([src]), [(src.describe(), 2, kind)], kind)

    def test_shapes_found_in_review(self):
        for kind, exprs in MORE.items():
            for expr in exprs:
                src = inputs.make_source(query(expr), "q.pq", "paste", inputs.MODE_M)
                self.assertEqual([h[2] for h in screen.scan([src])], [kind], expr)

    def test_two_files_with_one_name_are_told_apart(self):
        root = Path(tempfile.mkdtemp())
        try:
            for d in ("east", "west"):
                (root / d).mkdir()
                (root / d / "sales.pq").write_text(query(CASES["bearer token"]), encoding="utf-8")
            res = inputs.resolve([str(root)])
            self.assertEqual(sorted(h[0] for h in res["screen"]), ["east/sales.pq", "west/sales.pq"])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_every_match_is_listed(self):
        many = "".join(query(CASES["bearer token"]) for _ in range(30))
        hits = screen.scan([inputs.make_source(many, "q.pq", "paste", inputs.MODE_M)])
        self.assertEqual(len(hits), 30)
        self.assertEqual(screen.refusal(hits).count("bearer token"), 30)
        self.assertNotIn("more", screen.refusal(hits))

    def test_names_without_values_are_not_credentials(self):
        clean = [
            "column Password\n    dataType: string\n",
            'Source = Sql.Database(ServerName, "Warehouse")',
            "Token = Json.Document(Web.Contents(TokenUrl))[access_token]",
            'measure \'Password Resets\' = COUNTROWS ( Resets )',
            "/// Basic Configuration of the Sales table",
            'Source = Web.Contents(BaseUrl, [Headers=[Authorization="Bearer " & AccessToken]])',
        ]
        for text in clean:
            self.assertEqual(screen.scan([inputs.make_source(text, "x", "paste")]), [], text)

    def test_every_bundled_fixture_is_clean(self):
        for folder in ("fixtures", "shapes", "pbip", "parsers/fixtures"):
            res = inputs.resolve([str(HERE / folder)])
            self.assertEqual(res["screen"], [], folder)


class TestCommands(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.pq = self.tmp / "finance.pq"
        self.pq.write_text("section Section1;\n\nshared Journal = " + query(CASES[
            "password in a connection string"]).rstrip() + ";\n", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_parsing_commands_stop_and_never_print_the_value(self):
        out_dir = self.tmp / "report"
        for argv in (["analyse", str(self.pq)], ["dataset", str(self.pq), "-o", str(self.tmp / "r.json")],
                     ["render", str(self.pq), "-o", str(out_dir)], ["list", str(self.pq), "queries"],
                     ["explain", str(self.pq), "Journal"]):
            rc, out, err = run_cli(*argv)
            self.assertEqual(rc, 2, argv)
            self.assertIn("finance.pq:4", err)
            self.assertNotIn(FAKE, out + err)
        self.assertFalse(out_dir.exists())
        self.assertFalse((self.tmp / "r.json").exists())

    def test_inspect_reports_the_match_and_carries_on(self):
        rc, out, _ = run_cli("inspect", str(self.pq))
        self.assertEqual(rc, 0)
        self.assertIn("screen    : possible credential at finance.pq:4 (password in a connection string)", out)
        self.assertNotIn(FAKE, out)

    def test_a_paste_is_screened_too(self):
        rc, _, err = run_cli("analyse", "--m-text", query(CASES["bearer token"]))
        self.assertEqual(rc, 2)
        self.assertIn("bearer token", err)
        self.assertNotIn(FAKE, err)

    def test_the_override_runs_once_the_user_has_checked(self):
        rc, out, _ = run_cli("analyse", str(self.pq), "--no-credential-check")
        self.assertEqual(rc, 0)
        self.assertIn("rules fired", out)

    def test_a_clean_input_says_so(self):
        rc, out, _ = run_cli("inspect", str(HERE / "pbip" / "Review.SemanticModel"))
        self.assertEqual(rc, 0)
        self.assertIn("screen    : clean (12 sources searched)", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
