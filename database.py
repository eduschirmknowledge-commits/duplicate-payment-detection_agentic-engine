from db_schema import GovernanceSchemaManager
from duplicate_rules import DuplicateRuleEvaluator
import pandas as pd


class APGovernanceDatabase:
  """Main AP governance database interface including audit history."""

  def __init__(self, db_path: str | None = None):
    self.schema = GovernanceSchemaManager(db_path=db_path)
    self.db_path = self.schema.db_path
    self.evaluator = DuplicateRuleEvaluator()
    self.schema.init_schema()
    self.schema.seed_rule_matrix()
    self.schema.seed_default_fx_rates()

  def _connect(self):
    return self.schema.connect()

  def set_fx_rate_to_eur(self, currency: str, rate_to_eur: float):
    conn = self._connect()
    cursor = conn.cursor()
    cursor.execute(
        """
            INSERT OR REPLACE INTO fx_rates_to_eur (currency, rate_to_eur)
            VALUES (?, ?)
            """,
        (str(currency).upper().strip(), float(rate_to_eur)),
    )
    conn.commit()
    conn.close()

  def load_fbl1n_staging(self, df: pd.DataFrame):
    df_clean = df.copy()
    df_clean.columns = [
        str(c).strip().lower().replace(" ", "_") for c in df_clean.columns
    ]

    alias_map = {
        "document_number": "invoice_id",
        "belnr": "invoice_id",
        "vendor": "vendor_code",
        "lifnr": "vendor_code",
        "bukrs": "company_code",
        "bldat": "invoice_date",
        "budat": "posting_date",
        "zfbdt": "due_date",
        "waers": "currency",
        "sgtxt": "description",
        "iban": "ibanco",
        "zlspr": "payment_block",
        "augbl": "clearing_status",
        "kostl": "cost_center",
        "ebeln": "po_number",
    }
    for src, dst in alias_map.items():
      if dst not in df_clean.columns and src in df_clean.columns:
        df_clean[dst] = df_clean[src]

    expected_cols = [
        "invoice_id",
        "vendor_code",
        "vendor_name",
        "company_code",
        "invoice_date",
        "posting_date",
        "due_date",
        "net_amount",
        "tax_amount",
        "gross_amount",
        "currency",
        "description",
        "ibanco",
        "vat_id",
        "po_number",
        "cost_center",
        "receiving_person",
        "payment_block",
        "clearing_status",
    ]
    for col in expected_cols:
      if col not in df_clean.columns:
        df_clean[col] = ""

    conn = self._connect()
    df_clean[expected_cols].to_sql(
        "open_items", conn, if_exists="replace", index=False
    )
    conn.commit()
    conn.close()

  def evaluate_duplicate_risk(self) -> pd.DataFrame:
    conn = self._connect()
    open_df = pd.read_sql("SELECT * FROM open_items", conn)
    if open_df.empty:
      conn.close()
      return pd.DataFrame()

    resolved_df = pd.read_sql(
        "SELECT invoice_id, vendor_code FROM resolved_exceptions", conn
    )
    resolved_set = set(
        zip(
            resolved_df["invoice_id"].astype(str).str.strip(),
            resolved_df["vendor_code"].astype(str).str.strip(),
        )
    )

    fx_df = pd.read_sql("SELECT currency, rate_to_eur FROM fx_rates_to_eur", conn)
    fx_map = {
        str(r["currency"]).upper().strip(): float(r["rate_to_eur"])
        for _, r in fx_df.iterrows()
    }

    rules_df = pd.read_sql("SELECT * FROM rule_matrix", conn)
    conn.close()

    active_rules = (
        set(rules_df[rules_df["is_active"] == 1]["rule_code"].astype(str))
        if not rules_df.empty
        else {f"L{k}" for k in range(1, 16)}
    )
    review_thresh = (
        float(rules_df["eur_review_threshold"].min())
        if not rules_df.empty
        else 5000.0
    )
    hard_stop_thresh = (
        float(rules_df["eur_hard_stop_threshold"].min())
        if not rules_df.empty
        else 10000.0
    )

    return self.evaluator.evaluate_records(
        records=open_df.to_dict(orient="records"),
        resolved_set=resolved_set,
        fx_map=fx_map,
        active_rules=active_rules,
        review_threshold_eur=review_thresh,
        hard_stop_threshold_eur=hard_stop_thresh,
    )

  def resolve_exception(
      self, invoice_id: str, vendor_code: str, status: str, notes: str = ""
  ):
    conn = self._connect()
    cursor = conn.cursor()
    cursor.execute(
        """
            INSERT OR REPLACE INTO resolved_exceptions (
                invoice_id, vendor_code, resolution_status, reviewer_notes, resolved_at
            ) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
        (
            str(invoice_id).strip(),
            str(vendor_code).strip(),
            str(status).strip(),
            str(notes).strip(),
        ),
    )
    conn.commit()
    conn.close()

  def record_audit_run(
      self,
      run_id: str,
      source_label: str,
      total_rows: int,
      evaluated_rows: int,
      suppressed_rows: int,
      flagged_count: int,
      mandatory_review_5k_count: int,
      mandatory_review_10k_count: int,
      total_flagged_eur: float,
      excel_path: str = "",
      notes: str = "",
  ):
    conn = self._connect()
    cursor = conn.cursor()
    cursor.execute(
        """
            INSERT OR REPLACE INTO run_audit_log (
                run_id, executed_at, source_label, total_rows, evaluated_rows,
                suppressed_rows, flagged_count, mandatory_review_5k_count,
                mandatory_review_10k_count, total_flagged_eur, excel_path, notes
            ) VALUES (
                ?, CURRENT_TIMESTAMP, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
        (
            run_id,
            source_label,
            int(total_rows),
            int(evaluated_rows),
            int(suppressed_rows),
            int(flagged_count),
            int(mandatory_review_5k_count),
            int(mandatory_review_10k_count),
            float(total_flagged_eur),
            excel_path,
            notes,
        ),
    )
    conn.commit()
    conn.close()

  def get_audit_history(self, limit: int = 25) -> pd.DataFrame:
    conn = self._connect()
    df = pd.read_sql(
        "SELECT * FROM run_audit_log ORDER BY executed_at DESC LIMIT ?",
        conn,
        params=(int(limit),),
    )
    conn.close()
    return df