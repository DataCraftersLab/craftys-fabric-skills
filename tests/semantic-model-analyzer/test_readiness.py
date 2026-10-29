import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "plugins" / "semantic-model-analyzer" / "skills" / "semantic-model-analyzer"))

from sma import dataset, inputs  # noqa: E402

HERE = Path(__file__).parent
PBIP = HERE / "pbip" / "Review.SemanticModel"


def checks(paths):
    rep = dataset.build(inputs.resolve(paths))
    ai = rep["tmdl"]["aiReadiness"]
    return ai, {c["key"]: c for g in ai["groups"] for c in g["checks"]}


class TestReadiness(unittest.TestCase):
    def test_every_check_has_a_known_status(self):
        ai, by = checks([PBIP])
        self.assertEqual(set(c["status"] for c in by.values()) - {"pass", "fail", "na"}, set())
        s = ai["summary"]
        self.assertEqual(s["passed"] + s["failed"] + s["unknown"], s["total"])
        self.assertEqual([g["key"] for g in ai["groups"]], ["foundations", "descriptions", "prep"])

    def test_missing_files_are_not_a_fail(self):
        _, by = checks([HERE / "fixtures" / "sample-sales-analytics.tmdl"])
        self.assertEqual(by["verified"]["status"], "na")
        self.assertEqual(by["schema"]["status"], "na")
        self.assertEqual(by["instructions"]["status"], "na")

    def test_culture_file_is_read(self):
        _, by = checks([PBIP])
        self.assertNotEqual(by["lsdl"]["status"], "na")

    def test_custom_instructions_parse(self):
        from sma.readiness import culture_info

        class S:
            text = ('cultureInfo en-US\n\n\tlinguisticMetadata =\n\t\t\t{\n\t\t\t  "Version": "4.2.0",'
                    '\n\t\t\t  "Entities": {"a": {"Terms": [{"x": {"State": "Generated"}}, '
                    '{"y": {}}]}},\n\t\t\t  "CustomInstructions": "Use fiscal years."\n\t\t\t}\n')
        info = culture_info([S()])
        self.assertEqual(info["instructions"], "Use fiscal years.")
        self.assertEqual((info["entities"], info["terms"], info["authoredTerms"]), (1, 2, 1))


if __name__ == "__main__":
    unittest.main()
