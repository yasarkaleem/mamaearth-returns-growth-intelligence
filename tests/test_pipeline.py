"""Acceptance and failure-path tests; never make a network request."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from analysis.clean_and_eda import analyze, source_hashes
from narrator.generate_narrative import (check_grounded_numbers, check_numeric_accuracy,
                                        generate_scr_narrative, generate_scr_narrative_offline,
                                        request_gemini)
from sql.build_seed import generate_seed
from sql.run_sql import run, statements


class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.analysis = analyze(verbose=False, export=False)
        cls.findings = cls.analysis["findings"]
        cls.sql = run(database=":memory:", verbose=False)

    def test_immutable_source_hashes(self):
        expected = json.loads((ROOT / "data/source_manifest.json").read_text())
        self.assertEqual(source_hashes(), expected["sha256"])

    def test_seed_is_generated_from_csvs(self):
        self.assertEqual(generate_seed(), (ROOT / "sql/seed_data.sql").read_text())

    def test_seed_nulls_foreign_keys_and_rerun(self):
        with sqlite3.connect(":memory:") as connection:
            connection.executescript((ROOT / "sql/schema.sql").read_text())
            for _ in range(2):
                connection.executescript((ROOT / "sql/seed_data.sql").read_text())
                self.assertEqual(connection.execute("SELECT COUNT(*), COUNT(rating) FROM orders").fetchone(), (180, 165))
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM orders WHERE typeof(rating)='text'").fetchone()[0], 0)
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("UPDATE orders SET customer_id='MISSING' WHERE order_id='O0001'")

    def test_sql_report_outputs(self):
        rows = [item["rows"] for item in self.sql]
        self.assertEqual(rows[0], [(180, "99860.20", "554.78")])
        self.assertEqual(rows[1], [(180, 165, 15)])
        self.assertEqual(rows[2], [("C045", "Vihaan")])
        self.assertEqual(rows[2], rows[3])
        self.assertEqual(rows[4], [("Jaipur", 19, 8, 42.1), ("Lucknow", 49, 15, 30.6), ("Bangalore", 33, 8, 24.2)])
        self.assertEqual(rows[5], [("C043", "Reyansh", "12920.00"), ("C026", "Isha", "8371.60"),
                                   ("C008", "Meera", "4564.60"), ("C011", "Arjun", "4111.00"), ("C042", "Sanya", "3785.00")])
        self.assertEqual(rows[6], rows[5][2:5])
        self.assertEqual(rows[7], [("Haircare", 54, "44956.10"), ("Skincare", 60, "27346.00"),
                                   ("Babycare", 30, "16805.00"), ("PersonalCare", 36, "10753.10")])
        self.assertEqual(len(rows[8]), 10)
        self.assertEqual(rows[9], [("Ad",), ("Organic",), ("Referral",), ("Social",)])
        self.assertEqual(rows[10], [("Gold", 28), ("Silver", 17)])

    def test_report_comments_match_executed_results(self):
        connection = sqlite3.connect(":memory:")
        connection.executescript((ROOT / "sql/schema.sql").read_text())
        connection.executescript((ROOT / "sql/seed_data.sql").read_text())
        for statement in statements((ROOT / "sql/reports.sql").read_text()):
            cursor = connection.execute(statement)
            if cursor.description:
                for row in cursor.fetchall():
                    self.assertIn("-- " + " | ".join(map(str, row)), statement)
        connection.close()

    def test_cross_layer_reconciliation(self):
        raw_sql = float(self.sql[0]["rows"][0][1])
        self.assertEqual(raw_sql, self.findings["raw_total_revenue_inr"])
        cleaned = self.findings["cleaned_total_revenue_inr"]
        self.assertEqual(round(raw_sql - cleaned, 2), self.findings["duplicate_reconciliation_delta_inr"])
        self.assertEqual(round(self.analysis["dropped"]["order_value"].sum(), 2), 2501.90)

    def test_cleaned_counts_segments_outliers(self):
        self.assertEqual(len(self.analysis["orders"]), 175)
        self.assertEqual(self.analysis["payment"]["count"].to_dict(), {"CARD": 68, "COD": 54, "UPI": 53})
        self.assertEqual(self.analysis["segments"].loc[("COD", 1), "count"], 32)
        self.assertEqual(self.analysis["segments"].loc[("COD", 2), "count"], 22)
        self.assertEqual(int(self.analysis["orders"]["is_outlier"].sum()), 2)

    def test_findings_file_is_reproducible(self):
        saved = json.loads((ROOT / "narrator/findings.json").read_text())
        self.assertEqual(self.findings, saved)

    def test_offline_deterministic_and_keyless(self):
        with patch.dict(os.environ, {}, clear=True), patch("narrator.generate_narrative.request_gemini") as api:
            first = generate_scr_narrative(self.findings)
            second = generate_scr_narrative(self.findings)
            api.assert_not_called()
        self.assertEqual(first, second)
        self.assertEqual(first["mode"], "offline")
        self.assertTrue(all(check_numeric_accuracy(first["narrative"], self.findings, verbose=False).values()))

    def test_api_error_falls_back(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "fake-test-key"}), patch(
            "narrator.generate_narrative.request_gemini",
            return_value={"status": "error", "narrative": None, "message": "quota exceeded"},
        ):
            result = generate_scr_narrative(self.findings)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["mode"], "offline")
        self.assertIn("fallback_reason", result)

    def test_template_uses_arguments_not_seed_constants(self):
        changed = copy.deepcopy(self.findings)
        changed["cleaned_total_revenue_inr"] = 12345.67
        changed["true_peak_month"] = {"month": "2026-04", "revenue_inr": 1234.56}
        result = generate_scr_narrative_offline(changed)
        self.assertEqual(result["status"], "success")
        self.assertIn("12,345.67", result["narrative"])
        self.assertIn("April", result["narrative"])
        self.assertNotIn("97,358.30", result["narrative"])

    def test_missing_or_extra_figures_rejected(self):
        text = generate_scr_narrative_offline(self.findings)["narrative"]
        with self.assertRaises(AssertionError):
            check_numeric_accuracy(text.replace("44.4", "44.5"), self.findings, verbose=False)
        with self.assertRaises(AssertionError):
            check_grounded_numbers(text + " Guaranteed savings: 99999.", self.findings)

    def test_saved_sample_numeric_accuracy(self):
        text = (ROOT / "narrator/sample_output.txt").read_text()
        check_numeric_accuracy(text, self.findings, verbose=False)
        check_grounded_numbers(text, self.findings)

    def test_invalid_findings_return_structured_error(self):
        result = generate_scr_narrative_offline({})
        self.assertEqual(result["status"], "error")
        self.assertIsNone(result["narrative"])
        self.assertIn("message", result)


class GeminiContractTests(unittest.TestCase):
    """Optional SDK tests mock the network boundary, using real SDK config types."""
    def setUp(self):
        try:
            from google import genai  # noqa: F401
        except ImportError:
            self.skipTest("Install requirements-gemini.txt for optional SDK contract tests")
        self.findings = json.loads((ROOT / "narrator/findings.json").read_text())

    def test_locked_parameters_system_instruction_and_token_count(self):
        response = MagicMock()
        response.text = generate_scr_narrative_offline(self.findings)["narrative"]
        response.usage_metadata.total_token_count = 321
        with patch("google.genai.Client") as client_factory:
            client = client_factory.return_value.__enter__.return_value
            client.models.generate_content.return_value = response
            result = request_gemini(self.findings, "fake-test-key")
            arguments = client.models.generate_content.call_args.kwargs
            self.assertEqual(arguments["config"].temperature, 0.0)
            self.assertGreaterEqual(arguments["config"].max_output_tokens, 300)
            self.assertIn("senior data analyst", arguments["config"].system_instruction)
            self.assertIn(json.dumps(self.findings), arguments["contents"])
            self.assertGreaterEqual(client_factory.call_args.kwargs["http_options"].timeout, 10000)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["tokens"], 321)

    def test_client_exception_is_structured(self):
        with patch("google.genai.Client", side_effect=TimeoutError("test timeout")):
            result = request_gemini(self.findings, "fake-test-key")
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["message"], "test timeout")
        self.assertIsNone(result["narrative"])

    def test_online_hallucinated_number_is_rejected(self):
        response = MagicMock()
        response.text = generate_scr_narrative_offline(self.findings)["narrative"] + " Save 99999."
        with patch("google.genai.Client") as factory:
            factory.return_value.__enter__.return_value.models.generate_content.return_value = response
            result = request_gemini(self.findings, "fake-test-key")
        self.assertEqual(result["status"], "error")


if __name__ == "__main__":
    unittest.main()
