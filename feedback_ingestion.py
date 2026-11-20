import os
import sqlite3
from dotenv import load_dotenv
import pandas as pd

load_dotenv()


class FeedbackIngestionService:
  """Handles ingesting offline reviewer decisions back into SQLite."""

  SUPPRESS_STATUSES = {
      "VALID",
      "NOT DUPLICATE",
      "FALSE POSITIVE",
      "CLEARED",
      "APPROVED",
      "NO",
  }

  def __init__(self, db_path: str | None = None):
    self.db_path = db_path or os.getenv("SQLITE_DB_PATH", "ap_governance.db")
    self._ensure_tables()

  def _connect(self):
    return sqlite3.connect(self.db_path)

  def _ensure_tables(self):
    conn = self._connect()
    cursor = conn.cursor()

    cursor.execute("""
            CREATE TABLE IF NOT EXISTS feedback_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT,
                invoice_id TEXT,
                vendor_code TEXT,
                company_code TEXT,
                reviewer_decision TEXT,
                reviewer_notes TEXT,
                ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

    cursor.execute("""
            CREATE TABLE IF NOT EXISTS resolved_exceptions (
                invoice_id TEXT,
                vendor_code TEXT,
                resolution_status TEXT,
                reviewer_notes TEXT,
                resolved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (invoice_id, vendor_code)
            )
        """)

    conn.commit()
    conn.close()

  def ingest_dataframe(
      self,
      feedback_df: pd.DataFrame,
      run_id: str = "",
      suppress_statuses: set[str] | None = None,
  ) -> dict[str, int]:
    if feedback_df is None or feedback_df.empty:
      return {"ingested_rows": 0, "suppressed_exceptions": 0}

    df = feedback_df.copy()
    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]

    decision_col = next(
        (
            c
            for c in [
                "reviewer_decision",
                "decision",
                "resolution_status",
                "status",
            ]
            if c in df.columns
        ),
        None,
    )
    notes_col = next(
        (
            c
            for c in ["reviewer_notes", "notes", "comment", "comments"]
            if c in df.columns
        ),
        None,
    )

    if (
        not decision_col
        or "invoice_id" not in df.columns
        or "vendor_code" not in df.columns
    ):
      raise ValueError(
          "Expected columns 'invoice_id', 'vendor_code', and a decision column"
          " ('reviewer_decision' or 'resolution_status')."
      )

    allowed_suppress = {
        s.upper().strip() for s in (suppress_statuses or self.SUPPRESS_STATUSES)
    }

    conn = self._connect()
    cursor = conn.cursor()

    ingested_count = 0
    suppressed_count = 0

    for _, row in df.iterrows():
      inv_id = str(row.get("invoice_id") or "").strip()
      v_code = str(row.get("vendor_code") or "").strip()
      comp_code = str(row.get("company_code") or "").strip()
      decision = str(row.get(decision_col) or "").strip()
      notes = str(row.get(notes_col) or "").strip() if notes_col else ""

      # Skip blank or unreviewed rows
      if not inv_id or not v_code or not decision or decision.lower() == "nan":
        continue

      cursor.execute(
          """
                INSERT INTO feedback_log (
                    run_id, invoice_id, vendor_code, company_code,
                    reviewer_decision, reviewer_notes
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
          (run_id, inv_id, v_code, comp_code, decision, notes),
      )
      ingested_count += 1

      if decision.upper() in allowed_suppress:
        cursor.execute(
            """
                    INSERT OR REPLACE INTO resolved_exceptions (
                        invoice_id, vendor_code, resolution_status, reviewer_notes, resolved_at
                    ) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                    """,
            (inv_id, v_code, decision.upper(), notes),
        )
        suppressed_count += 1

    conn.commit()
    conn.close()

    return {
        "ingested_rows": ingested_count,
        "suppressed_exceptions": suppressed_count,
    }

  def ingest_from_excel(
      self,
      excel_path: str,
      sheet_name: str = "Flagged_Duplicates",
      run_id: str = "",
  ) -> dict[str, int]:
    if not os.path.exists(excel_path):
      raise FileNotFoundError(f"Reviewed Excel file not found: {excel_path}")

    df = pd.read_excel(excel_path, sheet_name=sheet_name)
    return self.ingest_dataframe(feedback_df=df, run_id=run_id)