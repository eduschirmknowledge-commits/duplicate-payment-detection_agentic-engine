from datetime import datetime
import os
import re
from typing import Any, TypedDict
from database import APGovernanceDatabase
from dummy_dataset_generator import DummyExcelDatasetGenerator
from dummy_excel_rules import DummyExcelRulesLoader
from langgraph.graph import END, START, StateGraph
import pandas as pd


class DummyBatchState(TypedDict, total=False):
  run_id: str
  data_excel_path: str
  rules_excel_path: str
  source_label: str
  input_df: pd.DataFrame
  results_df: pd.DataFrame
  output_excel_path: str
  total_rows: int
  suppressed_rows: int
  evaluated_rows: int
  synced_rules_count: int
  executive_summary: dict[str, Any]
  status_message: str


def _strip_affixes_keep_number(val: Any) -> str:
  """Extracts invoice number without INB/INV/RE/DUP prefixes or suffixes.
  Sequential series like INB-002 vs INB-003 stay '2' vs '3' (not equal).
  """
  text = str(val or "").strip().upper()
  if not text:
    return ""
  cleaned = re.sub(r"^(INB|INV|RE|DUP|BILL)[-_:\s]*", "", text)
  cleaned = re.sub(r"[-_:\s]*(INB|INV|RE|DUP|COPY)$", "", cleaned)
  cleaned = re.sub(r"[^A-Z0-9]", "", cleaned)
  if cleaned.isdigit():
    return cleaned.lstrip("0") or "0"
  return cleaned


def _parse_date(val: Any) -> datetime | None:
  if val is None or (isinstance(val, float) and pd.isna(val)):
    return None
  s = str(val).strip()[:10]
  for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%m/%d/%Y"):
    try:
      return datetime.strptime(s, fmt)
    except ValueError:
      continue
  return None


def _classify_paid_status(raw_status: Any, payment_block: Any) -> str:
  s = str(raw_status or "").strip().upper()
  blk = str(payment_block or "").strip().upper()
  if any(k in s for k in ("PAID", "CLEARED", "SETTLED", "CLOSED")):
    return "Paid / Cleared"
  if blk and ("OPEN" not in s and "UNPAID" not in s):
    return f"Blocked ({blk}) / Unpaid"
  return "Unpaid / Open"


def enrich_findings_with_basis_and_peers(
    results_df: pd.DataFrame, input_df: pd.DataFrame | None = None
) -> pd.DataFrame:
  """Computes accurate duplicate_status, `duplicate_of_invoices`,
  `matched_payment_status`, and `matching_basis` (which exact fields matched).
  """
  if results_df is None or results_df.empty:
    return results_df

  df = results_df.copy()

  # Index input rows by invoice_id for rich peer attributes
  peer_records: list[dict[str, Any]] = []
  if input_df is not None and not input_df.empty:
    for _, r in input_df.iterrows():
      inv_id = str(r.get("invoice_id") or "").strip()
      if not inv_id:
        continue
      amt_raw = r.get("net_amount", r.get("gross_amount", 0.0))
      try:
        amt_val = float(amt_raw)
      except (TypeError, ValueError):
        amt_val = 0.0

      peer_records.append({
          "invoice_id": inv_id,
          "invoice_id_u": inv_id.upper(),
          "inv_core": _strip_affixes_keep_number(inv_id),
          "vendor_code": str(r.get("vendor_code") or "").strip().upper(),
          "vendor_name": str(r.get("vendor_name") or "").strip().upper(),
          "payment_system": str(r.get("payment_system") or "").strip().upper(),
          "iban": str(r.get("ibanco") or r.get("iban") or "").strip().upper(),
          "vat_id": str(r.get("vat_id") or "").strip().upper(),
          "amount": amt_val,
          "inv_date": _parse_date(r.get("invoice_date")),
          "post_date": _parse_date(r.get("posting_date")),
          "paid_status": _classify_paid_status(
              r.get("clearing_status"), r.get("payment_block")
          ),
      })

  # Map each row in results_df to its peer record if available
  by_inv: dict[str, dict[str, Any]] = {
      p["invoice_id_u"]: p for p in peer_records
  }

  new_statuses: list[str] = []
  dup_of_col: list[str] = []
  paid_col: list[str] = []
  basis_col: list[str] = []

  for _, row in df.iterrows():
    inv_id = str(row.get("invoice_id") or "").strip()
    inv_u = inv_id.upper()
    cur = by_inv.get(inv_u)

    cur_core = cur["inv_core"] if cur else _strip_affixes_keep_number(inv_id)
    cur_vendor = (
        cur["vendor_code"]
        if cur
        else str(row.get("vendor_code") or "").strip().upper()
    )
    cur_pay_sys = (
        cur["payment_system"]
        if cur
        else str(row.get("payment_system") or "").strip().upper()
    )
    cur_iban = cur["iban"] if cur else ""
    cur_vat = cur["vat_id"] if cur else ""
    cur_inv_dt = cur["inv_date"] if cur else _parse_date(row.get("invoice_date"))
    cur_post_dt = (
        cur["post_date"] if cur else _parse_date(row.get("posting_date"))
    )

    try:
      cur_amt = (
          cur["amount"]
          if cur
          else float(row.get("amount_eur") or row.get("net_amount") or 0.0)
      )
    except (TypeError, ValueError):
      cur_amt = 0.0

    best_matches: list[tuple[str, str, list[str]]] = []

    for peer in peer_records:
      if peer["invoice_id_u"] == inv_u:
        continue

      reasons: list[str] = []

      # 1. Exact same core number after stripping INB/INV/RE affixes (never sequential series)
      same_core = bool(
          cur_core and peer["inv_core"] and cur_core == peer["inv_core"]
      )
      if same_core:
        reasons.append(f"Invoice Core ({cur_core}) + Morph Affix Match")

      # 2. Vendor match
      same_vendor = bool(
          cur_vendor and peer["vendor_code"] and cur_vendor == peer["vendor_code"]
      )

      # 3. Amount match (exact or within 1%)
      amt_match = False
      if cur_amt > 0 and peer["amount"] > 0:
        diff_pct = abs(cur_amt - peer["amount"]) / max(cur_amt, peer["amount"])
        if diff_pct <= 1e-6:
          amt_match = True
          reasons.append("Exact Net Amount Match")
        elif diff_pct <= 0.01:
          amt_match = True
          reasons.append("Net Amount Within 1% Tolerance")

      # 4. Invoice date proximity (+-3 days)
      inv_date_close = False
      if cur_inv_dt and peer["inv_date"]:
        if abs((cur_inv_dt - peer["inv_date"]).days) <= 3:
          inv_date_close = True
          reasons.append("Invoice Date Within +-3 Days")

      # 5. Posting date <= 7 days catalyst
      if cur_post_dt and peer["post_date"]:
        if abs((cur_post_dt - peer["post_date"]).days) <= 7:
          reasons.append("Posting Date Within <=7 Days")

      # 6. IBAN / VAT / Payment system signals
      if cur_iban and peer["iban"] and cur_iban == peer["iban"] and not same_vendor:
        reasons.append("Shared IBAN Across Vendors")
      elif cur_iban and peer["iban"] and cur_iban != peer["iban"] and same_vendor and amt_match:
        reasons.append("Vendor IBAN Change")

      if cur_vat and peer["vat_id"] and cur_vat == peer["vat_id"] and not same_vendor:
        reasons.append("Shared VAT ID")

      if cur_pay_sys and peer["payment_system"] and cur_pay_sys != peer["payment_system"]:
        reasons.append("Cross-Payment-System")

      # Require strong corroboration so casual coincidences aren't over-flagged
      is_strong_dup = (
          (same_core and (amt_match or inv_date_close or same_vendor))
          or (same_vendor and amt_match and (inv_date_close or same_core))
          or (
              bool(cur_iban and peer["iban"] and cur_iban == peer["iban"])
              and amt_match
              and inv_date_close
          )
      )

      if is_strong_dup and reasons:
        best_matches.append((peer["invoice_id"], peer["paid_status"], reasons))

    if best_matches:
      new_statuses.append("DUPLICATE FLAGGED")
      dup_of_col.append(", ".join(m[0] for m in best_matches[:4]))

      p_stats = sorted({m[1] for m in best_matches})
      paid_col.append(
          p_stats[0] if len(p_stats) == 1 else "Mixed (" + " / ".join(p_stats) + ")"
      )

      all_reasons: list[str] = []
      for _, _, r_list in best_matches:
        for r_item in r_list:
          if r_item not in all_reasons:
            all_reasons.append(r_item)
      basis_col.append(" + ".join(all_reasons))
    else:
      new_statuses.append("NOT DUPLICATE")
      dup_of_col.append("—")
      paid_col.append("N/A")
      basis_col.append("No duplicate match (distinct series/signals)")

  df["duplicate_status"] = new_statuses
  df["duplicate_of_invoices"] = dup_of_col
  df["matched_payment_status"] = paid_col
  df["matching_basis"] = basis_col
  return df


def build_dummy_batch_graph(db: APGovernanceDatabase | None = None):
  governance_db = db or APGovernanceDatabase()

  def fetch_dummy_excel(state: DummyBatchState) -> DummyBatchState:
    run_id = state.get("run_id") or datetime.now().strftime(
        "DUMMY_RUN_%Y%m%d_%H%M%S"
    )
    data_path = state.get("data_excel_path") or "data/dummy_ap_dataset.xlsx"

    if not os.path.exists(data_path):
      DummyExcelDatasetGenerator(output_path=data_path).generate()

    df = pd.read_excel(data_path, sheet_name=0)
    return {
        "run_id": run_id,
        "data_excel_path": data_path,
        "source_label": os.path.basename(data_path),
        "input_df": df,
        "total_rows": int(len(df)),
        "status_message": (
            f"Ingested {len(df)} rows from dummy Excel '{data_path}'."
        ),
    }

  def ingest_excel_rules_and_stage(state: DummyBatchState) -> DummyBatchState:
    rules_path = state.get("rules_excel_path") or "data/dummy_rules.xlsx"
    rules_loader = DummyExcelRulesLoader(rules_excel_path=rules_path)
    synced_rules = rules_loader.sync_rules_to_db(
        governance_db, rules_excel_path=rules_path
    )

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
    suppressed = sum(1 for pair in open_pairs if pair in resolved_set)

    return {
        "rules_excel_path": rules_path,
        "synced_rules_count": int(synced_rules),
        "suppressed_rows": int(suppressed),
        "status_message": (
            f"Synced {synced_rules} Excel rules and staged {len(open_pairs)}"
            f" open items ({suppressed} suppressed)."
        ),
    }

  def evaluate_duplicates(state: DummyBatchState) -> DummyBatchState:
    results_df = governance_db.evaluate_duplicate_risk()
    if results_df is None or results_df.empty:
      # Fallback directly from staged open_items / input_df if needed
      results_df = (
          state.get("input_df").copy()
          if state.get("input_df") is not None
          else pd.DataFrame()
      )
      if not results_df.empty and "amount_eur" not in results_df.columns:
        results_df["amount_eur"] = pd.to_numeric(
            results_df.get("net_amount", 0.0), errors="coerce"
        ).fillna(0.0)

    if results_df is not None and not results_df.empty:
      results_df = enrich_findings_with_basis_and_peers(
          results_df, input_df=state.get("input_df")
      )
      results_df = results_df.sort_values(
          by=["amount_eur"], ascending=[False]
      ).reset_index(drop=True)
    else:
      results_df = pd.DataFrame()

    return {
        "results_df": results_df,
        "evaluated_rows": int(len(results_df)),
        "status_message": (
            f"Evaluated {len(results_df)} items with matching basis & peer"
            " status."
        ),
    }

  def build_executive_summary(state: DummyBatchState) -> DummyBatchState:
    df = state.get("results_df")
    total_rows = int(state.get("total_rows") or 0)
    suppressed_rows = int(state.get("suppressed_rows") or 0)

    if df is None or df.empty:
      return {
          "executive_summary": {
              "run_id": state.get("run_id", ""),
              "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
              "source_label": state.get("source_label", ""),
              "total_rows": total_rows,
              "suppressed_rows": suppressed_rows,
              "evaluated_rows": 0,
              "flagged_count": 0,
              "matched_paid_count": 0,
              "matched_unpaid_count": 0,
              "mandatory_5k_count": 0,
              "mandatory_10k_count": 0,
              "total_flagged_eur": 0.0,
          },
          "status_message": "Built empty summary.",
      }

    flagged_df = df[df["duplicate_status"] == "DUPLICATE FLAGGED"].copy()
    m10k = flagged_df[flagged_df["amount_eur"] >= 10000.0]
    m5k = flagged_df[
        (flagged_df["amount_eur"] >= 5000.0) & (flagged_df["amount_eur"] < 10000.0)
    ]

    paid_cnt = 0
    unpaid_cnt = 0
    if not flagged_df.empty and "matched_payment_status" in flagged_df.columns:
      for st_val in flagged_df["matched_payment_status"].astype(str):
        u = st_val.upper()
        if "PAID" in u or "CLEARED" in u:
          paid_cnt += 1
        if "UNPAID" in u or "OPEN" in u:
          unpaid_cnt += 1

    summary = {
        "run_id": state.get("run_id", ""),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source_label": state.get("source_label", ""),
        "total_rows": total_rows,
        "suppressed_rows": suppressed_rows,
        "evaluated_rows": int(len(df)),
        "flagged_count": int(len(flagged_df)),
        "matched_paid_count": int(paid_cnt),
        "matched_unpaid_count": int(unpaid_cnt),
        "mandatory_5k_count": int(len(m5k)),
        "mandatory_10k_count": int(len(m10k)),
        "total_flagged_eur": (
            round(float(flagged_df["amount_eur"].sum()), 2)
            if not flagged_df.empty
            else 0.0
        ),
    }
    return {
        "executive_summary": summary,
        "status_message": (
            f"Summary built: {len(flagged_df)} flagged out of {len(df)}"
            " evaluated."
        ),
    }

  def export_excel_report(state: DummyBatchState) -> DummyBatchState:
    run_id = state.get("run_id") or datetime.now().strftime(
        "DUMMY_RUN_%Y%m%d_%H%M%S"
    )
    os.makedirs("reports", exist_ok=True)
    out_path = state.get("output_excel_path") or os.path.join(
        "reports", f"{run_id}_governance_report.xlsx"
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
    kpi_df = pd.DataFrame([{"Metric": k, "Value": v} for k, v in summary.items()])
    audit_df = governance_db.get_audit_history(limit=25)

    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
      kpi_df.to_excel(writer, sheet_name="Executive_Summary", index=False)
      high_eur_df.to_excel(writer, sheet_name="High_EUR_Review", index=False)
      flagged_df.to_excel(
          writer, sheet_name="Flagged_Duplicates", index=False
      )
      df.to_excel(writer, sheet_name="All_Evaluated_Items", index=False)
      audit_df.to_excel(writer, sheet_name="Audit_History", index=False)

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
        notes="Dummy Excel batch run",
    )

    return {
        "output_excel_path": out_path,
        "status_message": f"Exported dummy batch report to {out_path}.",
    }

  graph = StateGraph(DummyBatchState)
  graph.add_node("fetch_dummy_excel", fetch_dummy_excel)
  graph.add_node("ingest_excel_rules_and_stage", ingest_excel_rules_and_stage)
  graph.add_node("evaluate_duplicates", evaluate_duplicates)
  graph.add_node("build_executive_summary", build_executive_summary)
  graph.add_node("export_excel_report", export_excel_report)

  graph.add_edge(START, "fetch_dummy_excel")
  graph.add_edge("fetch_dummy_excel", "ingest_excel_rules_and_stage")
  graph.add_edge("ingest_excel_rules_and_stage", "evaluate_duplicates")
  graph.add_edge("evaluate_duplicates", "build_executive_summary")
  graph.add_edge("build_executive_summary", "export_excel_report")
  graph.add_edge("export_excel_report", END)

  return graph.compile()


def run_dummy_batch(
    data_excel_path: str = "data/dummy_ap_dataset.xlsx",
    rules_excel_path: str = "data/dummy_rules.xlsx",
    output_excel_path: str | None = None,
    db: APGovernanceDatabase | None = None,
) -> DummyBatchState:
  app = build_dummy_batch_graph(db=db)
  return app.invoke({
      "data_excel_path": data_excel_path,
      "rules_excel_path": rules_excel_path,
      "output_excel_path": output_excel_path or "",
  })