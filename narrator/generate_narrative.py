"""Gemini SCR narrator with a deterministic, standard-library-only fallback."""
import argparse
import calendar
from decimal import Decimal
import json
import os
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
SYSTEM_INSTRUCTION = (
    "You are a senior data analyst writing for Mamaearth's regional ops and finance heads. "
    "Write approximately 250 words in exactly three labeled sections: Situation, Complication, Resolution. "
    "Every number in your output must come from the supplied findings and appear with the same value. "
    "No invented statistics, derived ratios, targets, savings, counts, or quantified forecasts. "
    "Include cleaned total revenue, duplicate reconciliation delta, COD return rate, highest-risk "
    "segment rate and tier, and the true peak month (spell out its English month name) and revenue. "
    "Use ordinary decimal numbers, optionally with commas and currency symbols, not spelled-out "
    "numbers or abbreviations such as lakh or k. Do not number headings or recommendations. "
    "Revenue here is discounted booked order value including returned orders, not realized revenue "
    "or profit. Outlier exclusion is a sensitivity analysis. Associations are not causal. "
    "Recommend operational investigation and measured experiments; do not claim verified margin loss."
)


def month_name(year_month):
    return calendar.month_name[int(year_month.split("-")[1])]


def check_numeric_accuracy(narrative, findings, verbose=True):
    """Check all five required figure substrings, accepting trailing decimal zeroes."""
    normalized = narrative.replace(",", "")

    def contains(value):
        # 97358.30 and 97358.3 are equivalent, but 197358.30 is not a match.
        text = format(Decimal(str(value)).normalize(), "f")
        suffix = r"0*" if "." in text else r"(?:\.0+)?"
        return bool(re.search(r"(?<![\d.])" + re.escape(text) + suffix + r"(?!\d|\.\d)", normalized))

    peak = findings["true_peak_month"]
    checks = {
        "cleaned total revenue": contains(findings["cleaned_total_revenue_inr"]),
        "COD return rate": contains(findings["return_rate_by_payment"]["COD"]),
        "highest-risk segment return rate": contains(findings["highest_risk_segment"]["return_rate_pct"]),
        "duplicate reconciliation delta": contains(findings["duplicate_reconciliation_delta_inr"]),
        "true peak month and revenue": contains(peak["revenue_inr"]) and month_name(peak["month"]).lower() in normalized.lower(),
    }
    for label, passed in checks.items():
        if verbose:
            print(f"{'PASS' if passed else 'FAIL'} — {label}")
    if not all(checks.values()):
        raise AssertionError("Narrative missing required figures: " + ", ".join(k for k, v in checks.items() if not v))
    return checks


def check_grounded_numbers(narrative, findings):
    """Reject extra numeric literals beyond those supplied in findings (including date parts)."""
    allowed = set()

    def visit(value):
        if isinstance(value, dict):
            for child in value.values():
                visit(child)
        elif isinstance(value, (int, float)):
            allowed.add(Decimal(str(value)))
        elif isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}", value):
            allowed.update(Decimal(part) for part in value.split("-"))

    visit(findings)
    # Hyphens in Tier-2 and YYYY-MM are separators, not negative signs.
    literals = re.findall(r"\d+(?:\.\d+)?", narrative.replace(",", ""))
    unknown = [item for item in literals if Decimal(item) not in allowed]
    if unknown:
        raise AssertionError("Narrative contains unsupported numbers: " + ", ".join(unknown))
    for heading in ("Situation", "Complication", "Resolution"):
        if not re.search(rf"(?mi)^\s*(?:#+\s*)?(?:\*\*)?{heading}\b", narrative):
            raise AssertionError(f"Missing SCR heading: {heading}")


def generate_scr_narrative_offline(findings: dict) -> dict:
    """No SDK, key, network, clock, random values, or hand-entered report numbers."""
    try:
        rates = findings["return_rate_by_payment"]
        risk = findings["highest_risk_segment"]
        peak = findings["true_peak_month"]
        inflated = findings["outlier_inflated_month"]
        narrative = f"""Situation
Mamaearth's cleaned discounted booked order value is ₹{findings['cleaned_total_revenue_inr']:,.2f}, compared with the raw total of ₹{findings['raw_total_revenue_inr']:,.2f}. Removing duplicate submissions explains the entire ₹{findings['duplicate_reconciliation_delta_inr']:,.2f} difference. These values include returned orders and therefore measure booked demand, not realized revenue or profit. The reconciled dataset provides a consistent basis for regional ops and finance to investigate return exposure.

Complication
COD has an observed return rate of {rates['COD']:.1f}%, compared with {rates['CARD']:.1f}% for CARD and {rates['UPI']:.1f}% for UPI. The highest-risk segment is {risk['payment_method']} in Tier-{risk['city_tier']} cities, at {risk['return_rate_pct']:.1f}%. This concentration makes a blanket payment policy less informative than a focused investigation. {month_name(inflated['month'])}'s apparent revenue of ₹{inflated['apparent_revenue_inr']:,.2f} falls to ₹{inflated['corrected_revenue_inr']:,.2f} when quantity outliers are excluded. {month_name(peak['month'])} is the peak in this sensitivity view, at ₹{peak['revenue_inr']:,.2f}. Bulk orders remain in the cleaned total because their size alone does not establish an error.

Resolution
Prioritize review of return reasons, delivery experience and order confirmation in the highest-risk payment and city segment. Test confirmation or prepaid incentives with a comparison group, tracking conversion alongside returns before expanding any intervention. Preserve customer choice until the tradeoffs are measured. Repair double-submit handling and reconcile duplicate records before finance reporting. Validate bulk orders with operations, and present both the full and outlier-corrected monthly views when planning demand. These observational findings guide investigation; they do not prove payment method causes returns. Join refund, fulfillment and product-cost records before estimating margin loss or promising savings."""
        check_numeric_accuracy(narrative, findings, verbose=False)
        check_grounded_numbers(narrative, findings)
        return {"status": "success", "narrative": narrative, "tokens": 0, "mode": "offline"}
    except Exception as err:
        return {"status": "error", "narrative": None, "message": str(err), "mode": "offline"}


def request_gemini(findings: dict, api_key: str) -> dict:
    """Keep the API error contract separate from the caller's offline fallback."""
    try:
        # Lazy import: the offline path works without installing google-genai.
        from google import genai
        from google.genai import types

        prompt = "Write the SCR narrative from these verified findings:\n" + json.dumps(findings, ensure_ascii=False)
        with genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=30000)) as client:
            response = client.models.generate_content(
                model=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"),
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_INSTRUCTION,
                    # Factual reporting needs minimal sampling variation, not creative variety.
                    # Temperature zero reduces variation; it does not guarantee identical API text.
                    temperature=0.0,
                    max_output_tokens=2048,
                    thinking_config=types.ThinkingConfig(thinking_budget=0),
                ),
            )
        narrative = response.text
        if not narrative or not narrative.strip():
            raise ValueError("Gemini returned no narrative text")
        check_numeric_accuracy(narrative, findings, verbose=False)
        check_grounded_numbers(narrative, findings)
        usage = getattr(response, "usage_metadata", None)
        return {"status": "success", "narrative": narrative,
                "tokens": getattr(usage, "total_token_count", None), "mode": "online"}
    except Exception as err:
        return {"status": "error", "narrative": None, "message": str(err), "mode": "online"}


def generate_scr_narrative(findings: dict) -> dict:
    """Prefer a configured Gemini free-tier key; transparently fall back on any error."""
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        return generate_scr_narrative_offline(findings)
    result = request_gemini(findings, key)
    if result["status"] == "success":
        return result
    fallback = generate_scr_narrative_offline(findings)
    # Do not persist API errors: third-party exception text can include request details.
    fallback["fallback_reason"] = "Gemini request or narrative validation failed; used deterministic offline output."
    return fallback


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="Force offline even if a key is configured")
    parser.add_argument("--check-sample", action="store_true", help="Validate saved sample without calling Gemini")
    args = parser.parse_args()
    findings = json.loads((ROOT / "narrator/findings.json").read_text())
    sample = ROOT / "narrator/sample_output.txt"
    if args.check_sample:
        narrative = sample.read_text()
        check_numeric_accuracy(narrative, findings)
        check_grounded_numbers(narrative, findings)
        return
    result = (generate_scr_narrative_offline(findings) if args.offline
              else generate_scr_narrative(findings))
    if result["status"] != "success":
        raise SystemExit("Narration failed: " + result["message"])
    print(f"Narrator mode: {result['mode']}; tokens: {result['tokens']}")
    if "fallback_reason" in result:
        print(result["fallback_reason"])
    print("\n" + result["narrative"] + "\n")
    check_numeric_accuracy(result["narrative"], findings)
    # Save actual text returned by the selected path; never pass offline text off as Gemini.
    sample.write_text(result["narrative"] + "\n", encoding="utf-8")
    provenance = {key: value for key, value in result.items() if key != "narrative"}
    (ROOT / "narrator/sample_metadata.json").write_text(json.dumps(provenance, indent=2) + "\n")
    print("Saved narrator/sample_output.txt and narrator/sample_metadata.json")


if __name__ == "__main__":
    main()
