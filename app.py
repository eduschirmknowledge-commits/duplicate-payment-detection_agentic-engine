from datetime import datetime
import os
from database import APGovernanceDatabase
from dummy_dataset_generator import DummyExcelDatasetGenerator
from dummy_excel_rules import DummyExcelRulesLoader
from dummy_graph_workflow import (
    build_dummy_batch_graph,
    enrich_findings_with_basis_and_peers,
)
from feedback_ingestion import FeedbackIngestionService
from langgraph.graph.state import CompiledStateGraph
import pandas as pd
import streamlit as st

st.set_page_config(
    page_title="AP Duplicate Governance — Dummy Mode",
    page_icon="🧪",
    layout="wide",
)


@st.cache_resource
def get_db() -> APGovernanceDatabase:
  return APGovernanceDatabase()


@st.cache_resource
def get_feedback_service() -> FeedbackIngestionService:
  return FeedbackIngestionService()


def stream_dummy_workflow(
    app_graph: CompiledStateGraph,
    data_excel_path: str,
    rules_excel_path: str,
    output_excel_path: str | None = None,
) -> dict:
  """Streams node-by-node execution of the dummy LangGraph workflow."""
  run_id = datetime.now().strftime("DUMMY_RUN_%Y%m%d_%H%M%S")
  initial_state = {
      "run_id": run_id,
      "data_excel_path": data_excel_path,
      "rules_excel_path": rules_excel_path,
      "output_excel_path": output_excel_path or "",
  }

  node_steps = [
      ("fetch_dummy_excel", "1/5 Fetching dummy payment rows from Excel"),
      (
          "ingest_excel_rules_and_stage",
          "2/5 Syncing dummy Excel rules & staging open items",
      ),
      (
          "evaluate_duplicates",
          "3/5 Evaluating L1–L15 duplicate rules & EUR tiers",
      ),
      ("build_executive_summary", "4/5 Building dummy run executive summary"),
      (
          "export_excel_report",
          "5/5 Exporting Excel report & recording SQLite audit log",
      ),
  ]
  step_order = [name for name, _ in node_steps]
  step_labels = dict(node_steps)

  merged_state: dict = dict(initial_state)
  progress_bar = st.progress(0, text="Starting dummy workflow...")
  status_container = st.status(f"Streaming {run_id}...", expanded=True)

  completed_count = 0
  for event in app_graph.stream(initial_state):
    for node_name, node_output in event.items():
      if isinstance(node_output, dict):
        merged_state.update(node_output)
      completed_count += 1
      label = step_labels.get(node_name, f"Completed {node_name}")
      msg = (
          node_output.get("status_message", "")
          if isinstance(node_output, dict)
          else ""
      )
      status_container.write(f"**{label}** — {msg}")
      pct = (
          (step_order.index(node_name) + 1) / len(step_order)
          if node_name in step_order
          else min(1.0, completed_count / len(step_order))
      )
      progress_bar.progress(pct, text=label)

  progress_bar.progress(1.0, text="Dummy workflow complete.")
  status_container.update(
      label=f"Finished {merged_state.get('run_id', run_id)}",
      state="complete",
      expanded=False,
  )
  return merged_state


def render_executive_top_summary(summary: dict, results_df: pd.DataFrame) -> None:
  """Renders an executive top summary banner + key leadership metrics."""
  total_rows = int(summary.get("total_rows") or 0)
  evaluated_rows = int(
      summary.get("evaluated_rows")
      or (len(results_df) if isinstance(results_df, pd.DataFrame) else 0)
  )
  flagged_count = int(summary.get("flagged_count") or 0)
  m5k = int(summary.get("mandatory_5k_count") or 0)
  m10k = int(summary.get("mandatory_10k_count") or 0)
  flagged_eur = float(summary.get("total_flagged_eur") or 0.0)
  paid_cnt = int(summary.get("matched_paid_count") or 0)
  unpaid_cnt = int(summary.get("matched_unpaid_count") or 0)
  yield_pct = (
      (flagged_count / evaluated_rows * 100.0) if evaluated_rows > 0 else 0.0
  )

  st.info(
      f"**Executive Top Summary ({summary.get('run_id', 'Latest Run')})** — "
      f"Analyzed **{evaluated_rows:,}** evaluated line items (out of"
      f" **{total_rows:,}** ingested rows) and flagged **{flagged_count:,}**"
      f" potential duplicates (**{yield_pct:.2f}%** hit rate) representing"
      f" **€{flagged_eur:,.2f}** in potential exposure. Matched peers show"
      f" **{paid_cnt:,}** Paid/Cleared and **{unpaid_cnt:,}** Unpaid/Open,"
      f" with **{m5k:,}** at `>= €5,000` and **{m10k:,}** at `>= €10,000`."
  )

  m1, m2, m3, m4, m5 = st.columns(5)
  m1.metric("Evaluated Items", f"{evaluated_rows:,}")
  m2.metric("Flagged Duplicates", f"{flagged_count:,}")
  m3.metric("Mandatory >= €5k", f"{m5k:,}")
  m4.metric("Mandatory >= €10k", f"{m10k:,}")
  m5.metric("Flagged Exposure (EUR)", f"€{flagged_eur:,.2f}")


def render_side_corner_gains_summary(
    summary: dict, results_df: pd.DataFrame, report_path: str
) -> None:
  """Renders the side-corner summary panel after analyzing the workbook and generating the report."""
  total_rows = int(summary.get("total_rows") or 0)
  evaluated_rows = int(
      summary.get("evaluated_rows")
      or (len(results_df) if isinstance(results_df, pd.DataFrame) else 0)
  )
  flagged_count = int(summary.get("flagged_count") or 0)
  suppressed_rows = int(summary.get("suppressed_rows") or 0)
  flagged_eur = float(summary.get("total_flagged_eur") or 0.0)
  paid_cnt = int(summary.get("matched_paid_count") or 0)
  unpaid_cnt = int(summary.get("matched_unpaid_count") or 0)
  m5k = int(summary.get("mandatory_5k_count") or 0)
  m10k = int(summary.get("mandatory_10k_count") or 0)

  hit_rate = (
      (flagged_count / evaluated_rows * 100.0) if evaluated_rows > 0 else 0.0
  )
  clean_items = max(0, evaluated_rows - flagged_count)

  with st.container(border=True):
    st.markdown("### 📌 Side-Corner Gains Summary")
    st.caption(f"Source: `{summary.get('source_label', 'Dummy Excel')}`")
    st.markdown(
        f"**Duplicate Yield:** **{flagged_count:,}** duplicate flagged out of"
        f" **{evaluated_rows:,}** analyzed line items (**{hit_rate:.2f}%**)"
    )
    st.progress(min(1.0, hit_rate / 100.0) if evaluated_rows > 0 else 0.0)

    st.divider()
    st.write(f"• **Total Rows Ingested:** `{total_rows:,}`")
    st.write(f"• **Suppressed Exceptions:** `{suppressed_rows:,}`")
    st.write(f"• **Non-Flagged / Clean:** `{clean_items:,}`")
    st.write(f"• **Duplicate Flagged:** `{flagged_count:,}`")
    st.write(f"• **Matched Paid / Cleared:** `{paid_cnt:,}`")
    st.write(f"• **Matched Unpaid / Open:** `{unpaid_cnt:,}`")
    st.write(f"• **Review Gates (`>=€5k` / `>=€10k`):** `{m5k:,}` / `{m10k:,}`")
    st.write(f"• **Total Flagged EUR:** `€{flagged_eur:,.2f}`")

    if report_path and os.path.exists(report_path):
      st.divider()
      with open(report_path, "rb") as f:
        st.download_button(
            label="⬇ Download Excel Report",
            data=f.read(),
            file_name=os.path.basename(report_path),
            mime=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
            use_container_width=True,
        )


def main():
  db = get_db()
  feedback_svc = get_feedback_service()
  dummy_graph = build_dummy_batch_graph(db=db)

  st.title("🧪 AP Duplicate Payment Governance — Dummy Sandbox")
  st.caption(
      "Local dummy execution only: dummy Excel dataset ingestion, dummy Excel"
      " rule sync (`L1–L15`), `50%` base + escalation scoring, `>= €5,000` /"
      " `>= €10,000` review tiers, Excel report export, feedback ingestion,"
      " and SQLite audit history."
  )

  with st.sidebar:
    st.header("Dummy Inputs & Controls")

    data_excel_path = st.text_input(
        "Dummy Dataset Excel", value="data/dummy_ap_dataset.xlsx"
    )
    rules_excel_path = st.text_input(
        "Dummy Rules Excel", value="data/dummy_rules.xlsx"
    )

    st.divider()
    st.subheader("Generate / Reset Dummy Files")
    num_vendors = st.number_input(
        "Dummy Vendors Count",
        min_value=20,
        max_value=5000,
        value=300,
        step=20,
    )
    seed = st.number_input(
        "Dummy Seed", min_value=1, max_value=999999, value=42
    )

    c1, c2 = st.columns(2)
    with c1:
      if st.button("Generate Dummy Excel", use_container_width=True):
        gen = DummyExcelDatasetGenerator(output_path=data_excel_path)
        saved_data = gen.generate(
            num_vendors=int(num_vendors),
            seed=int(seed),
            output_path=data_excel_path,
        )
        st.success(f"Created `{saved_data}`")

    with c2:
      if st.button("Create Dummy Rules", use_container_width=True):
        loader = DummyExcelRulesLoader(rules_excel_path=rules_excel_path)
        saved_rules = loader.create_default_rules_excel(rules_excel_path)
        st.success(f"Created `{saved_rules}`")

    st.divider()
    run_dummy_clicked = st.button(
        "▶ Run Dummy Workflow (Stream)",
        type="primary",
        use_container_width=True,
    )

  if run_dummy_clicked:
    state = stream_dummy_workflow(
        app_graph=dummy_graph,
        data_excel_path=data_excel_path,
        rules_excel_path=rules_excel_path,
    )
    st.session_state["dummy_run_state"] = state

  run_state = st.session_state.get("dummy_run_state")

  if run_state:
    top_summary = run_state.get("executive_summary") or {}
    top_results = run_state.get("results_df")
    if isinstance(top_results, pd.DataFrame) and not top_results.empty:
      top_results = enrich_findings_with_basis_and_peers(
          top_results, input_df=run_state.get("input_df")
      )
    render_executive_top_summary(top_summary, top_results)

  tab_results, tab_rules_preview, tab_feedback, tab_audit = st.tabs([
      "📊 Dummy Results & Excel Export",
      "📋 Dummy Excel Preview",
      "📝 Feedback Ingestion",
      "📜 SQLite Audit History",
  ])

  with tab_results:
    if not run_state:
      st.info(
          "Click **Run Dummy Workflow (Stream)** in the sidebar to execute the"
          " dummy pipeline and inspect results."
      )
    else:
      summary = run_state.get("executive_summary") or {}
      results_df = run_state.get("results_df")
      report_path = run_state.get("output_excel_path") or ""

      if isinstance(results_df, pd.DataFrame) and not results_df.empty:
        results_df = enrich_findings_with_basis_and_peers(
            results_df, input_df=run_state.get("input_df")
        )

      main_col, side_col = st.columns([3, 1])

      with main_col:
        if isinstance(results_df, pd.DataFrame) and not results_df.empty:
          status_options = sorted(
              results_df["duplicate_status"].dropna().unique().tolist()
          )
          default_status = (
              ["DUPLICATE FLAGGED"]
              if "DUPLICATE FLAGGED" in status_options
              else status_options
          )

          f1, f2, f3 = st.columns([2, 1, 1])
          with f1:
            selected_statuses = st.multiselect(
                "Filter Status",
                options=status_options,
                default=default_status,
            )
          with f2:
            paid_filter_options = sorted(
                results_df["matched_payment_status"].dropna().unique().tolist()
            )
            selected_paid_status = st.multiselect(
                "Matched Invoice Paid Status",
                options=paid_filter_options,
                default=paid_filter_options,
            )
          with f3:
            min_eur = st.number_input(
                "Minimum Amount (EUR)", min_value=0.0, value=0.0, step=500.0
            )

          view_df = results_df[
              results_df["duplicate_status"].isin(selected_statuses)
              & results_df["matched_payment_status"].isin(selected_paid_status)
              & (results_df["amount_eur"] >= min_eur)
          ]

          priority_cols = [
              c
              for c in [
                  "invoice_id",
                  "duplicate_of_invoices",
                  "matched_payment_status",
                  "matching_basis",
                  "vendor_code",
                  "vendor_name",
                  "company_code",
                  "payment_system",
                  "amount_eur",
                  "duplicate_status",
                  "invoice_date",
                  "posting_date",
              ]
              if c in view_df.columns
          ]
          other_cols = [c for c in view_df.columns if c not in priority_cols]
          view_df = view_df[priority_cols + other_cols]

          st.dataframe(view_df, use_container_width=True, hide_index=True)
        else:
          st.warning("No evaluated rows found in the dummy run output.")

      with side_col:
        render_side_corner_gains_summary(
            summary=summary,
            results_df=results_df,
            report_path=report_path,
        )

  with tab_rules_preview:
    col_d, col_r = st.columns(2)
    with col_d:
      st.subheader("Dummy Dataset Preview")
      if os.path.exists(data_excel_path):
        d_df = pd.read_excel(data_excel_path, sheet_name=0)
        st.caption(f"Source: `{data_excel_path}` ({len(d_df)} rows)")
        st.dataframe(d_df.head(50), use_container_width=True, hide_index=True)
      else:
        st.info(
            f"`{data_excel_path}` not created yet. Click **Generate Dummy"
            " Excel** or run the workflow."
        )

    with col_r:
      st.subheader("Dummy Rules Preview")
      if os.path.exists(rules_excel_path):
        r_df = pd.read_excel(rules_excel_path, sheet_name=0)
        st.caption(f"Source: `{rules_excel_path}` ({len(r_df)} rules)")
        st.dataframe(r_df, use_container_width=True, hide_index=True)
      else:
        st.info(
            f"`{rules_excel_path}` not created yet. Click **Create Dummy"
            " Rules** or run the workflow."
        )

  with tab_feedback:
    st.subheader("Ingest Reviewer Decisions from Dummy Report")
    st.write(
        "Upload a reviewed `.xlsx` report (with `reviewer_decision` filled on"
        " the `Flagged_Duplicates` sheet) to record decisions in `feedback_log`"
        " and suppress cleared items in `resolved_exceptions`."
    )
    uploaded_review = st.file_uploader(
        "Upload Reviewed Excel File (.xlsx)",
        type=["xlsx"],
        key="dummy_feedback_upload",
    )
    run_id_tag = st.text_input(
        "Associated Run ID",
        value=(run_state.get("run_id", "") if run_state else ""),
    )

    if uploaded_review is not None and st.button("Ingest Reviewed Excel"):
      os.makedirs("data/reviews", exist_ok=True)
      saved_fb = os.path.join("data/reviews", uploaded_review.name)
      with open(saved_fb, "wb") as f:
        f.write(uploaded_review.getbuffer())

      fb_result = feedback_svc.ingest_from_excel(
          excel_path=saved_fb,
          sheet_name="Flagged_Duplicates",
          run_id=run_id_tag,
      )
      st.success(
          f"Ingested **{fb_result.get('ingested_rows', 0)}** decisions and"
          f" suppressed **{fb_result.get('suppressed_exceptions', 0)}**"
          " exceptions in SQLite."
      )

  with tab_audit:
    st.subheader("SQLite Run Audit History (`run_audit_log`)")
    audit_df = db.get_audit_history(limit=50)
    if audit_df is not None and not audit_df.empty:
      st.dataframe(audit_df, use_container_width=True, hide_index=True)
    else:
      st.info("No runs recorded in `run_audit_log` yet.")


if __name__ == "__main__":
  main()