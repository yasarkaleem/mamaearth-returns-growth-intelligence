"""Plot the verified tables exported by clean_and_eda.py using Matplotlib."""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("MPLCONFIGDIR", str(ROOT / ".mplconfig"))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter, PercentFormatter
import pandas as pd

from clean_and_eda import source_hashes


def main():
    generated = ROOT / "analysis/generated"
    if not (generated / "source_hashes.json").exists():
        raise SystemExit("Run python analysis/clean_and_eda.py before visualization.")
    if json.loads((generated / "source_hashes.json").read_text()) != source_hashes():
        raise SystemExit("Raw CSVs changed: rerun python analysis/clean_and_eda.py first.")
    payment = pd.read_csv(generated / "payment_summary.csv").sort_values("return_rate_pct", ascending=False)
    monthly = pd.read_csv(generated / "monthly_revenue.csv")
    output = ROOT / "visualizations"
    output.mkdir(exist_ok=True)
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11,
                         "axes.spines.top": False, "axes.spines.right": False,
                         "axes.spines.left": False, "axes.spines.bottom": False,
                         "axes.labelcolor": "#34453e", "text.color": "#172e23",
                         "xtick.color": "#34453e", "ytick.color": "#34453e",
                         "figure.facecolor": "#fafcf9", "axes.facecolor": "#fafcf9"})

    fig, ax = plt.subplots(figsize=(10, 6), layout="constrained")
    bars = ax.bar(payment["payment_method"], payment["return_rate_pct"], width=0.52,
                  color=["#a44a35" if item == "COD" else "#398565" for item in payment["payment_method"]])
    ax.bar_label(bars, labels=[f"{value:.1f}%" for value in payment["return_rate_pct"]],
                 padding=10, fontsize=14, fontweight="bold")
    cod = payment.loc[payment["payment_method"].eq("COD"), "return_rate_pct"].iloc[0]
    ax.set_title(f"COD leads returns at {cod:.1f}%", loc="left", fontsize=21, fontweight="bold", pad=30)
    ax.set_xlabel("Payment method", labelpad=14)
    ax.set_ylabel("Returned orders / orders", labelpad=14)
    ax.yaxis.set_major_formatter(PercentFormatter(xmax=100))
    ax.set_ylim(0, max(payment["return_rate_pct"]) * 1.3)
    ax.grid(axis="y", alpha=0.15)
    ax.set_axisbelow(True)
    labels = [f"{r.payment_method}\nn = {r.count}" for r in payment.itertuples()]
    ax.set_xticks(range(len(payment)), labels=labels)
    fig.savefig(output / "return_rate_by_payment.png", dpi=180, metadata={"Software": "Mamaearth capstone / Matplotlib"})
    plt.close(fig)

    dates = pd.to_datetime(monthly["year_month"] + "-01")
    revenue = monthly["corrected_revenue_inr"]
    peak_index = revenue.idxmax()
    peak_name = dates.loc[peak_index].month_name()
    fig, ax = plt.subplots(figsize=(10, 6), layout="constrained")
    ax.plot(range(len(monthly)), revenue, color="#276e51", linewidth=2.8, marker="o", markersize=7)
    ax.fill_between(range(len(monthly)), revenue, alpha=0.08, color="#276e51")
    for index, value in enumerate(revenue):
        ax.annotate(f"{value:,.2f}", (index, value), xytext=(0, 13), textcoords="offset points",
                    ha="center", fontsize=10, fontweight="bold" if index == peak_index else "normal")
    ax.set_title(f"{peak_name} is the peak after excluding bulk orders", loc="left",
                 fontsize=19, fontweight="bold", pad=30)
    ax.set_xticks(range(len(monthly)), dates.dt.strftime("%b %Y"))
    ax.set_xlabel("Order month", labelpad=14)
    ax.set_ylabel("Discounted booked order value (INR)", labelpad=14)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:,.0f}"))
    ax.set_ylim(0, revenue.max() * 1.25)
    ax.grid(axis="y", alpha=0.15)
    ax.set_axisbelow(True)
    fig.savefig(output / "monthly_revenue_trend.png", dpi=180, metadata={"Software": "Mamaearth capstone / Matplotlib"})
    plt.close(fig)
    print("Saved visualizations/return_rate_by_payment.png and visualizations/monthly_revenue_trend.png")


if __name__ == "__main__":
    main()
