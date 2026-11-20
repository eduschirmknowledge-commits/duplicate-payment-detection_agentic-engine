from datetime import datetime
import os
from typing import Any, TypedDict
from database import APGovernanceDatabase
from langgraph.graph import END, START, StateGraph
import pandas as pd


class APBatchState(TypedDict, total=False):
  run_id: str
  source_path: str
  source_label: str
  input_df: pd.DataFrame
  records: list[dict[str, Any]]
  results_df: pd.DataFrame
  output_excel_path: str
  total_rows: int
  suppressed_rows: int
  evaluated_rows: int
  executive_summary: dict[str, Any]
  status_message: str


def build_ap_batch_graph(db: APGovernanceDatabase | None = None):
  governance_db = db or APGovernanceDatabase()

  def fetch_fbl1n_data(state: APBatchState) -> APBatchState:
    run_id = state.get("run_id") or datetime.now().strftime(
        "AP_RUN_%Y%m%d_%H%M%S"
    )
    source_path = (state.get("source_path") or "").strip()
    df = state.get("input_df")

    if df is None and state.get("records"):
      df = pd.DataFrame(state["records"])

    if df is None and source_path:
      ext = os.path.splitext(source_path)[1].lower()
      if ext in (".xlsx", ".xls"):
        df = pd.read_excel(source_path)
      elif ext == ".parquet":
        df = pd.read_parquet(source_path)
      else:
        df = pd.read_csv(source_path)

    if df is None:
      df = pd.DataFrame()

    source_label = (
        state.get("source_label")
        or (os.path.basename(source_path) if source_path else "")
        or "in_memory_fbl1n_batch"
    )

    return {
        "run_id": run_id,
        "source_label": source_label,
        "input_df": df,
        "total_rows": int(len(df)),
        "status_message": f"Fetched {len(df)} FBL1N rows for {run_id}.",
    }

  def stage_and_normalize(state: APBatchState) -> APBatchState:
    df = state.get("input_df")
    if df is None:
      df = pd.DataFrame()

    governance_db.load_fbl1n_staging(df)

    conn = governance_db._connect()
    open_df = pd.read_sql(
        "SELECT invoice_id, vendor_code FROM open_items", conn
    )
    resolved_df = pd.read_sql(
        "SELECT invoice_id, vendor_code FROM resolved_exceptions", conn
    )
    conn.close()

    resolved_set = set(
        zip(
            resolved_df["invoice_id"].astype(str).str.strip(),
            resolved_df["vendor_code"].astype(str).str.strip(),
        )
    )
    open_pairs = list(
        zip(
            open_df["invoice_id"].astype(str).str.strip(),
            open_df["vendor_code"].astype(str).str.strip(),
        )
    )
    suppressed_count = sum(1 for pair in open_pairs if pair in resolved_set)

    return {
        "suppressed_rows": int(suppressed_count),
        "status_message": (
            f"Staged {len(open_pairs)} open items ({suppressed_count}"
            " suppressed via resolved_exceptions)."
        ),
    }

  def evaluate_duplicates(state: APBatchState) -> APBatchState:
    results_df = governance_db.evaluate_duplicate_risk()
    if results_df is None or results_df.empty:
      results_df = pd.DataFrame()
    else:
      # Sort highest EUR exposure and highest probability first for offline review
      results_df = results_df.sort_values(
          by=["amount_eur", "probability_score"],
          ascending=[False, False],
      ).reset_index(drop=True)

    return {
        "results_df": results_df,
        "evaluated_rows": int(len(results_df)),
        "status_message": (
            f"Evaluated {len(results_df)} active items across L1-L15."
        ),
    }

  def build_executive_summary(state: APBatchState) -> APBatchState:
    df = state.get("results_df")
    total_rows = int(state.get("total_rows") or 0)
    suppressed_rows = int(state.get("suppressed_rows") or 0)

    if df is None or df.empty:
      summary = {
          "run_id": state.get("run_id", ""),
          "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
          "source_label": state.get("source_label", ""),
          "total_rows": total_rows,
          "suppressed_rows": suppressed_rows,
          "evaluated_rows": 0,
          "flagged_count": 0,
          "clear_count": 0,
          "mandatory_10k_count": 0,
          "mandatory_5k_count": 0,
          "total_flagged_eur": 0.0,
          "mandatory_review_eur": 0.0,
          "top_rules": [],
          "top_vendors_eur": [],
          "top_company_codes_eur": [],
      }
      return {
          "executive_summary": summary,
          "status_message": "Built empty executive summary.",
      }

    flagged_df = df[df["duplicate_status"] == "DUPLICATE FLAGGED"].copy()
    clear_df = df[df["duplicate_status"] != "DUPLICATE FLAGGED"].copy()

    mandatory_10k_df = flagged_df[flagged_df["amount_eur"] >= 10000.0]
    mandatory_5k_df = flagged_df[
        (flagged_df["amount_eur"] >= 5000.0) & (flagged_df["amount_eur"] < 10000.0)
    ]
    mandatory_any_df = flagged_df[flagged_df["amount_eur"] >= 5000.0]

    # Count rule hits across flagged items
    rule_counts: dict[str, int] = {}
    for raw_rules in flagged_df["matched_rules"].fillna(""):
      for code in [c.strip() for c in str(raw_rules).split(",") if c.strip()]:
        if code != "None":
          rule_counts[code] = rule_counts.get(code, 0) + 1

    top_rules = [
        {"rule_code": k, "flagged_items": v}
        for k, v in sorted(
            rule_counts.items(), key=lambda x: x[1], reverse=True
        )
    ]

    top_vendors = []
    if not flagged_df.empty:
      v_grp = (
          flagged_df.groupby(
              ["vendor_code", "vendor_name"], dropna=False
          )["amount_eur"]
          .agg(flagged_items="count", total_eur="sum")
          .reset_index()
          .sort_values("total_eur", ascending=False)
          .head(10)
      )
      top_vendors = v_grp.to_dict(orient="records")

    top_companies = []
    if not flagged_df.empty:
      c_grp = (
          flagged_df.groupby(["company_code"], dropna=False)["amount_eur"]
          .agg(flagged_items="count", total_eur="sum")
          .reset_index()
          .sort_values("total_eur", ascending=False)
          .head(10)
      )
      top_companies = c_grp.to_dict(orient="records")

    summary = {
        "run_id": state.get("run_id", ""),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source_label": state.get("source_label", ""),
        "total_rows": total_rows,
        "suppressed_rows": suppressed_rows,
        "evaluated_rows": int(len(df)),
        "flagged_count": int(len(flagged_df)),
        "clear_count": int(len(clear_df)),
        "mandatory_10k_count": int(len(mandatory_10k_df)),
        "mandatory_5k_count": int(len(mandatory_5k_df)),
        "total_flagged_eur": (
            round(float(flagged_df["amount_eur"].sum()), 2)
            if not flagged_df.empty
            else 0.0
        ),
        "mandatory_review_eur": (
            round(float(mandatory_any_df["amount_eur"].sum()), 2)
            if not mandatory_any_df.empty
            else 0.0
        ),
        "top_rules": top_rules,
        "top_vendors_eur": top_vendors,
        "top_company_codes_eur": top_companies,
    }

    return {
        "executive_summary": summary,
        "status_message": (
            f"Executive summary ready: {len(flagged_df)} flagged"
            f" ({len(mandatory_any_df)} >= EUR 5,000)."
        ),
    }

  def export_excel_report(state: APBatchState) -> APBatchState:
    run_id = state.get("run_id") or datetime.now().strftime(
        "AP_RUN_%Y%m%d_%H%M%S"
    )
    os.makedirs("reports", exist_ok=True)
    out_path = state.get("output_excel_path") or os.path.join(
        "reports", f"{run_id}_duplicate_governance_report.xlsx"
    )

    df = state.get("results_df")
    if df is None:
      df = pd.DataFrame()

    summary = state.get("executive_summary") or {}

    flagged_df = (
        df[df["duplicate_status"] == "DUPLICATE FLAGGED"].copy()
        if not df.empty
        else pd.DataFrame()
    )
    if not flagged_df.empty:
      flagged_df["reviewer_decision"] = ""
      flagged_df["reviewer_notes"] = ""

    high_eur_df = (
        flagged_df[flagged_df["amount_eur"] >= 5000.0].copy()
        if not flagged_df.empty
        else pd.DataFrame()
    )

    kpi_rows = [
        {"Metric": "Run ID", "Value": summary.get("run_id", run_id)},
        {"Metric": "Generated At", "Value": summary.get("generated_at", "")},
        {"Metric": "Source", "Value": summary.get("source_label", "")},
        {
            "Metric": "Total Input Rows",
            "Value": summary.get("total_rows", 0),
        },
        {
            "Metric": "Suppressed (Resolved Exceptions)",
            "Value": summary.get("suppressed_rows", 0),
        },
        {
            "Metric": "Evaluated Open Items",
            "Value": summary.get("evaluated_rows", 0),
        },
        {
            "Metric": "Total Flagged Duplicates",
            "Value": summary.get("flagged_count", 0),
        },
        {
            "Metric": "Mandatory Review (>= EUR 10,000)",
            "Value": summary.get("mandatory_10k_count", 0),
        },
        {
            "Metric": "Mandatory Review (EUR 5,000 - 9,999.99)",
            "Value": summary.get("mandatory_5k_count", 0),
        },
        {
            "Metric": "Total Flagged Exposure (EUR)",
            "Value": summary.get("total_flagged_eur", 0.0),
        },
        {
            "Metric": "Mandatory Review Exposure >= EUR 5,000 (EUR)",
            "Value": summary.get("mandatory_review_eur", 0.0),
        },
    ]
    kpi_df = pd.DataFrame(kpi_rows)
    rules_summary_df = pd.DataFrame(summary.get("top_rules") or [])
    vendors_summary_df = pd.DataFrame(summary.get("top_vendors_eur") or [])
    companies_summary_df = pd.DataFrame(
        summary.get("top_company_codes_eur") or []
    )
    audit_hist_df = governance_db.get_audit_history(limit=25)

    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
      kpi_df.to_excel(
          writer, sheet_name="Executive_Summary", index=False, startrow=0
      )
      start_r = len(kpi_df) + 3
      if not rules_summary_df.empty:
        rules_summary_df.to_excel(
            writer,
            sheet_name="Executive_Summary",
            index=False,
            startrow=start_r,
        )
        start_r += len(rules_summary_df) + 3
      if not companies_summary_df.empty:
        companies_summary_df.to_excel(
            writer,
            sheet_name="Executive_Summary",
            index=False,
            startrow=start_r,
        )
        start_r += len(companies_summary_df) + 3
      if not vendors_summary_df.empty:
        vendors_summary_df.to_excel(
            writer,
            sheet_name="Executive_Summary",
            index=False,
            startrow=start_r,
        )

      high_eur_df.to_excel(writer, sheet_name="High_EUR_Review", index=False)
      flagged_df.to_excel(
          writer, sheet_name="Flagged_Duplicates", index=False
      )
      df.to_excel(writer, sheet_name="All_Evaluated_Items", index=False)
      audit_hist_df.to_excel(writer, sheet_name="Audit_History", index=False)

    governance_db.record_audit_run(
        run_id=run_id,
        source_label=summary.get("source_label", ""),
        total_rows=summary.get("total_rows", 0),
        evaluated_rows=summary.get("evaluated_rows", 0),
        suppressed_rows=summary.get("suppressed_rows", 0),
        flagged_count=summary.get("flagged_count", 0),
        mandatory_review_5k_count=summary.get("mandatory_5k_count", 0),
        mandatory_review_10k_count=summary.get("mandatory_10k_count", 0),
        total_flagged_eur=summary.get("total_flagged_eur", 0.0),
        excel_path=out_path,
        notes=f"Exported to {out_path}",
    )

    return {
        "output_excel_path": out_path,
        "status_message": f"Excel report generated at {out_path}.",
    }

  workflow = StateGraph(APBatchState)
  workflow.add_node("fetch_fbl1n_data", fetch_fbl1n_data)
  workflow.add_node("stage_and_normalize", stage_and_normalize)
  workflow.add_node("evaluate_duplicates", evaluate_duplicates)
  workflow.add_node("build_executive_summary", build_executive_summary)
  workflow.add_node("export_excel_report", export_excel_report)

  workflow.add_edge(START, "fetch_fbl1n_data")
  workflow.add_edge("fetch_fbl1n_data", "stage_and_normalize")
  workflow.add_edge("stage_and_normalize", "evaluate_duplicates")
  workflow.add_edge("evaluate_duplicates", "build_executive_summary")
  workflow.add_edge("build_executive_summary", "export_excel_report")
  workflow.add_edge("export_excel_report", END)

  return workflow.compile()


def run_batch_pipeline(
    source_path: str | None = None,
    input_df: pd.DataFrame | None = None,
    records: list[dict[str, Any]] | None = None,
    output_excel_path: str | None = None,
    source_label: str | None = None,
    db: APGovernanceDatabase | None = None,
) -> APBatchState:
  app = build_ap_batch_graph(db=db)
  initial_state: APBatchState = {}
  if source_path:
    initial_state["source_path"] = source_path
  if input_df is not None:
    initial_state["input_df"] = input_df
  if records is not None:
    initial_state["records"] = records
  if output_excel_path:
    initial_state["output_excel_path"] = output_excel_path
  if source_label:
    initial_state["source_label"] = source_label
  return app.invoke(initial_state)