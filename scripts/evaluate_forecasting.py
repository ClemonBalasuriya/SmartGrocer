"""
Run the real forecasting pipeline (smartgrocer/forecasting.py, completely
unmodified) against the real Pack & Save evaluation database and produce
the model-comparison numbers for Step 19 of pprs-25-step-progress-plan.md.

Run this with the project's own venv (which already has statsmodels
pinned in requirements.txt), so SARIMA and both SARIMAX variants are
included alongside Seasonal Naive and Holt-Winters:
    venv\\Scripts\\python.exe scripts\\evaluate_forecasting.py

Expects pack_and_save_eval.db next to this script's data folder - run
build_eval_db.py first if it doesn't exist yet. Pass a folder as the first
argument to point at a different location.

Scope: the top-N best-selling products by item-line count (N=50 by
default - change TOP_N below for a bigger run; a few hundred SKUs should
still finish in well under an hour on an ordinary laptop. Running all
4,167 SKUs is unlikely to be worth the time - the long tail doesn't have
enough daily history for a seasonal model to mean anything, per Kolassa
2016, already cited in the literature review).
"""
import sys
import json
import csv
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from smartgrocer import db, forecasting  # noqa: E402

DATA_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "data"
DB_PATH = DATA_DIR / "pack_and_save_eval.db"
TOP_N = 50
HORIZON = 7
N_FOLDS = 3


def main():
    if not DB_PATH.exists():
        print(f"ERROR: {DB_PATH} not found - run build_eval_db.py first.")
        sys.exit(1)

    print(f"statsmodels available: {forecasting.STATSMODELS_AVAILABLE} "
          f"(SARIMA/SARIMAX will be {'included' if forecasting.STATSMODELS_AVAILABLE else 'SKIPPED - install statsmodels'})")

    conn = db.get_conn(str(DB_PATH))
    top_products = conn.execute(
        """SELECT p.id, p.name_en, COUNT(*) n
           FROM invoice_items ii JOIN products p ON p.id = ii.product_id
           GROUP BY p.id ORDER BY n DESC LIMIT ?""",
        (TOP_N,),
    ).fetchall()
    print(f"Evaluating top {len(top_products)} products by item-line count ...\n")

    all_scores = defaultdict(list)
    best_model_counts = defaultdict(int)
    per_product_rows = []
    evaluated = 0
    skipped_short_history = 0

    for i, row in enumerate(top_products, 1):
        pid, name, n_lines = row["id"], row["name_en"], row["n"]
        result = forecasting.select_best_model(conn, pid, horizon=HORIZON, n_folds=N_FOLDS)
        if not result["model_scores"]:
            skipped_short_history += 1
            continue
        evaluated += 1
        for model, score in result["model_scores"].items():
            all_scores[model].append(score)
        best_model_counts[result["best_model"]] += 1
        per_product_rows.append({
            "product_id": pid, "name": name, "item_lines": n_lines,
            **{f"nrmse_{k.replace(' ', '_').replace('(', '').replace(')', '')}": round(v, 4)
               for k, v in result["model_scores"].items()},
            "best_model": result["best_model"],
            "n_anomalies": len(result["anomalies"]),
        })
        print(f"  [{i}/{len(top_products)}] {name[:40]:40s}  best={result['best_model']}")

    print(f"\nEvaluated {evaluated} products with enough history "
          f"({skipped_short_history} skipped - too little history)")

    summary = {}
    for model, scores in all_scores.items():
        summary[model] = {
            "n_products_scored": len(scores),
            "mean_nrmse": round(sum(scores) / len(scores), 4),
            "median_nrmse": round(sorted(scores)[len(scores) // 2], 4),
            "times_best": best_model_counts.get(model, 0),
        }

    print("\n=== Model comparison (lower nRMSE = better) ===")
    for model, s in sorted(summary.items(), key=lambda kv: kv[1]["mean_nrmse"]):
        print(f"{model:28s}  mean_nRMSE={s['mean_nrmse']:.4f}  median_nRMSE={s['median_nrmse']:.4f}  "
              f"scored_on={s['n_products_scored']:3d}  won_for={s['times_best']:3d}")

    out = {
        "top_n": TOP_N, "horizon_days": HORIZON, "n_folds": N_FOLDS,
        "evaluated_products": evaluated, "skipped_short_history": skipped_short_history,
        "statsmodels_available": forecasting.STATSMODELS_AVAILABLE,
        "summary": summary,
    }
    (DATA_DIR / "eval_results.json").write_text(json.dumps(out, indent=2))
    if per_product_rows:
        with open(DATA_DIR / "eval_per_product.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(per_product_rows[0].keys()))
            w.writeheader()
            w.writerows(per_product_rows)
    print(f"\nSaved {DATA_DIR / 'eval_results.json'} and eval_per_product.csv")


if __name__ == "__main__":
    main()
