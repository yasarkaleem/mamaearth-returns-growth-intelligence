"""Independent pandas pipeline over the original CSVs; exports verified findings."""
import hashlib
import itertools
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
NATURAL_KEY = ["customer_id", "product_id", "order_date", "quantity", "discount_pct",
               "payment_method", "rating", "returned"]


def source_hashes():
    return {name: hashlib.sha256((ROOT / "data" / f"{name}.csv").read_bytes()).hexdigest()
            for name in ("customers", "products", "orders")}


def value_orders(frame, products):
    result = frame.merge(products, on="product_id", how="left", validate="many_to_one")
    assert result["price"].notna().all(), "Unmatched product"
    return result["quantity"] * result["price"] * (1 - result["discount_pct"].fillna(0) / 100)


def strength(value):
    magnitude = abs(value)
    if magnitude < 0.2:
        return "negligible"
    if magnitude < 0.4:
        return "weak"
    if magnitude < 0.7:
        return "moderate"
    return "strong"


def analyze(verbose=True, export=True):
    def show(*args):
        if verbose:
            print(*args)

    customers = pd.read_csv(ROOT / "data/customers.csv")
    products = pd.read_csv(ROOT / "data/products.csv")
    orders = pd.read_csv(ROOT / "data/orders.csv")
    show("Task 1 — Raw orders.shape:", orders.shape)
    show("Customers:", customers.shape, "Products:", products.shape)
    show(orders.head().to_string(index=False))
    show("Column types:\n" + orders.dtypes.to_string())
    assert orders.shape == (180, 9)

    # Independently computed from all raw rows; no SQL database is required.
    raw_revenue = round(float(value_orders(orders, products).sum()), 2)
    show("\nTask 2 — Raw payment methods:", orders["payment_method"].unique())
    assert orders["payment_method"].nunique() == 7
    orders["payment_method"] = orders["payment_method"].str.strip().str.upper()
    payment_counts = orders["payment_method"].value_counts().sort_index()
    show("Standardized methods:", orders["payment_method"].unique())
    show("Counts before deduplication:\n" + payment_counts.to_string())
    assert payment_counts.to_dict() == {"CARD": 70, "COD": 55, "UPI": 55}

    duplicate_mask = orders.duplicated(subset=NATURAL_KEY, keep="first")
    dropped = orders.loc[duplicate_mask].copy()
    clean = orders.loc[~duplicate_mask].copy()
    show("\nTask 3 — Duplicates dropped:", len(dropped), dropped["order_id"].tolist())
    show("orders_clean.shape:", clean.shape)
    assert dropped["order_id"].tolist() == [f"O{i:04d}" for i in range(176, 181)]
    assert clean.shape == (175, 9)

    missing = clean[["discount_pct", "rating"]].isna().sum()
    median = float(clean["rating"].median())
    show("\nTask 4 — Missing values before imputation:", missing.to_dict())
    show("Rating median before imputation:", median)
    assert missing.to_dict() == {"discount_pct": 12, "rating": 15}
    assert median == 3.0
    before_imputation_revenue = round(float(value_orders(clean, products).sum()), 2)
    clean["discount_pct"] = clean["discount_pct"].fillna(0)
    clean["rating"] = clean["rating"].fillna(median)
    remaining_nulls = clean[["discount_pct", "rating"]].isnull().sum()
    show("Missing values after imputation:", remaining_nulls.to_dict())
    assert remaining_nulls.eq(0).all()

    merged = clean.merge(products, on="product_id", how="left", validate="many_to_one")
    merged = merged.merge(customers, on="customer_id", how="left", validate="many_to_one")
    assert len(merged) == len(clean) and merged[["price", "name"]].notna().all().all()
    merged["order_value"] = merged["quantity"] * merged["price"] * (1 - merged["discount_pct"] / 100)
    cleaned_revenue = round(float(merged["order_value"].sum()), 2)
    dropped["order_value"] = value_orders(dropped.reset_index(drop=True), products).to_numpy()
    duplicate_value = round(float(dropped["order_value"].sum()), 2)
    delta = round(raw_revenue - cleaned_revenue, 2)
    assert raw_revenue == 99860.20 and cleaned_revenue == 97358.30
    assert delta == duplicate_value == 2501.90
    assert before_imputation_revenue == cleaned_revenue
    show(f"\nTask 5 — Cleaned revenue: INR {cleaned_revenue:.2f} across {len(merged)} rows")
    show("Independent value of dropped rows:\n" + dropped[["order_id", "order_value"]].to_string(index=False))
    show(f"Reconciliation: raw order value of ₹{raw_revenue:,.2f} minus cleaned order value "
         f"of ₹{cleaned_revenue:,.2f} equals ₹{delta:,.2f}. The {len(dropped)} duplicate "
         f"rows removed in Task 3 independently sum to ₹{duplicate_value:,.2f}, exactly "
         "explaining this delta. Discount imputation matches SQL COALESCE at zero, and "
         "rating is absent from the revenue formula; neither imputation changes total order value.")

    q1, q3 = merged["quantity"].quantile([0.25, 0.75])
    iqr = q3 - q1
    lower, upper = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    merged["is_outlier"] = ~merged["quantity"].between(lower, upper)
    outliers = merged.loc[merged["is_outlier"], ["order_id", "order_date", "quantity", "order_value"]]
    show(f"\nTask 6 — Q1={q1}, Q3={q3}, IQR={iqr}, lower={lower}, upper={upper}")
    show("Outliers (flagged, retained in the main dataset):\n" + outliers.to_string(index=False))
    assert (q1, q3, iqr, lower, upper) == (1.0, 2.0, 1.0, -0.5, 3.5)
    assert outliers["order_id"].tolist() == ["O0011", "O0098"]

    show("\nTask 7 — Hypothesis: COD has a higher observed return rate than CARD and UPI.")
    payment = merged.groupby("payment_method")["returned"].agg(["count", "mean"])
    payment["return_rate_pct"] = (payment["mean"] * 100).round(1)
    show(payment.to_string())
    rates = payment["return_rate_pct"].to_dict()
    assert rates == {"CARD": 14.7, "COD": 44.4, "UPI": 18.9}
    assert payment.loc["COD", "mean"] > payment.drop("COD")["mean"].max()
    show("Confirmed (descriptive sample comparison; no causal or significance claim).")

    segments = merged.groupby(["payment_method", "city_tier"])["returned"].agg(["count", "mean"])
    segments["return_rate_pct"] = (segments["mean"] * 100).round(1)
    highest = segments["mean"].idxmax()
    highest_rate = float(segments.loc[highest, "return_rate_pct"])
    show("\nTask 8 — Payment × city tier:\n" + segments.to_string())
    show(f"Highest-risk segment: {highest[0]} + Tier-{highest[1]} cities at {highest_rate:.1f}%.")
    for tier in (1, 2):
        row = segments.loc[("COD", tier)]
        show(f"COD Tier-{tier}: {int(row['count'])} orders; {row['return_rate_pct']:.1f}% returned.")
    show("The blended COD rate hides higher observed risk in Tier-2 cities.")
    assert highest == ("COD", 2) and highest_rate == 54.5

    correlations = merged[["rating", "returned", "discount_pct", "quantity"]].corr()
    show("\nTask 9 — Pearson correlations (including retained outliers and imputed ratings):")
    show(correlations.to_string(float_format=lambda x: f"{x:.4f}"))
    show("Bands by |r|: [0, 0.2) negligible; [0.2, 0.4) weak; [0.4, 0.7) moderate; [0.7, 1] strong.")
    for left, right in itertools.combinations(correlations.columns, 2):
        value = float(correlations.loc[left, right])
        band = strength(value)
        show(f"{left} vs {right}: r={value:.4f} → {band}")
        assert band == "negligible"
    show(f"Hypothesis 'higher discounts reduce returns': Busted as evidence in this sample "
         f"(r={correlations.loc['discount_pct', 'returned']:.4f}, negligible). "
         "This observational correlation does not establish or disprove a causal effect.")

    merged["order_date"] = pd.to_datetime(merged["order_date"], format="%Y-%m-%d")
    merged["year_month"] = merged["order_date"].dt.strftime("%Y-%m")
    monthly = pd.DataFrame({
        "including_outliers_inr": merged.groupby("year_month")["order_value"].sum(),
        "corrected_revenue_inr": merged.loc[~merged["is_outlier"]].groupby("year_month")["order_value"].sum(),
    }).round(2)
    peak = monthly["corrected_revenue_inr"].idxmax()
    apparent_peak = monthly["including_outliers_inr"].idxmax()
    peak_name = pd.Timestamp(f"{peak}-01").month_name()
    apparent_name = pd.Timestamp(f"{apparent_peak}-01").month_name()
    show("\nTask 10 — Both monthly series (INR):\n" + monthly.to_string(float_format=lambda x: f"{x:.2f}"))
    details = "; ".join(f"{r.order_id} on {r.order_date}" for r in outliers.itertuples())
    show(f"{apparent_name}'s apparent lead is an artifact of the two bulk orders landing "
         f"in {apparent_name} ({details}). {peak_name} is the genuine peak month once "
         f"they are excluded: ₹{monthly.loc[peak, 'corrected_revenue_inr']:,.2f}. "
         "Bulk orders may be legitimate; exclusion is a sensitivity view, not a deletion.")
    assert monthly["corrected_revenue_inr"].tolist() == [11637.10, 13195.50, 20318.90, 9495.30, 13151.10, 11615.40]
    assert monthly.loc["2026-01", "including_outliers_inr"] == 29582.10
    assert peak == "2026-03"

    findings = {
        "cleaned_total_revenue_inr": cleaned_revenue,
        "raw_total_revenue_inr": raw_revenue,
        "duplicate_reconciliation_delta_inr": duplicate_value,
        "return_rate_by_payment": {key: rates[key] for key in ("COD", "CARD", "UPI")},
        "highest_risk_segment": {"payment_method": highest[0], "city_tier": int(highest[1]),
                                 "return_rate_pct": highest_rate},
        "true_peak_month": {"month": peak, "revenue_inr": float(monthly.loc[peak, "corrected_revenue_inr"])},
        "outlier_inflated_month": {"month": apparent_peak,
                                  "apparent_revenue_inr": float(monthly.loc[apparent_peak, "including_outliers_inr"]),
                                  "corrected_revenue_inr": float(monthly.loc[apparent_peak, "corrected_revenue_inr"])},
    }
    # Additional source-data limitation: some orders predate recorded signup dates.
    pre_signup = int((merged["order_date"] < pd.to_datetime(merged["signup_date"])).sum())
    show(f"\nData-quality caveat: {pre_signup} orders predate signup; retained as supplied. "
         "Do not infer acquisition chronology from this synthetic dataset.")
    show("Revenue means discounted booked order value, including returned orders; "
         "costs, refunds and shipping are unavailable, so margin loss is not measured.")

    if export:
        output = ROOT / "analysis/generated"
        output.mkdir(exist_ok=True)
        merged.to_csv(output / "orders_clean.csv", index=False, float_format="%.6f", date_format="%Y-%m-%d")
        payment.to_csv(output / "payment_summary.csv", float_format="%.6f")
        monthly.to_csv(output / "monthly_revenue.csv", float_format="%.2f")
        (output / "source_hashes.json").write_text(json.dumps(source_hashes(), indent=2) + "\n")
        (ROOT / "narrator/findings.json").write_text(json.dumps(findings, indent=2, allow_nan=False) + "\n")
        show("\nExported analysis/generated tables and narrator/findings.json from computed values.")
    return {"findings": findings, "orders": merged, "payment": payment, "segments": segments,
            "monthly": monthly, "correlations": correlations, "dropped": dropped}


if __name__ == "__main__":
    analyze()
