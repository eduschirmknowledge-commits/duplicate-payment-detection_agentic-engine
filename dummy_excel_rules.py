import os
from database import APGovernanceDatabase
import pandas as pd


class DummyExcelRulesLoader:
  """Loads or initializes dummy rule configurations from an Excel workbook
  and applies them to `rule_matrix` in `APGovernanceDatabase`.
  """

  DEFAULT_RULES: list[dict] = [
      {
          "rule_code": "L1",
          "rule_name": "Suffix/Prefix Morph (1234 vs 1234-INB)",
          "base_probability": 50,
          "escalation_step": 10,
          "max_probability": 100,
          "eur_review_threshold": 5000.0,
          "eur_hard_stop_threshold": 10000.0,
          "is_active": 1,
          "description": (
              "Normalized invoice core matches; non-sequential; net amount"
              " matches & invoice date within +-3 days"
          ),
      },
      {
          "rule_code": "L2",
          "rule_name": "Multi-Entity Bank Clone (XYZ vs XYZ GmbH)",
          "base_probability": 50,
          "escalation_step": 10,
          "max_probability": 100,
          "eur_review_threshold": 5000.0,
          "eur_hard_stop_threshold": 10000.0,
          "is_active": 1,
          "description": (
              "Different vendor code/name with shared IBAN/SWIFT or VAT ID;"
              " exact net amount & exact date match"
          ),
      },
      {
          "rule_code": "L3",
          "rule_name": "Zero-Padding / Formatting Trick (1234 vs 001234)",
          "base_probability": 50,
          "escalation_step": 10,
          "max_probability": 100,
          "eur_review_threshold": 5000.0,
          "eur_hard_stop_threshold": 10000.0,
          "is_active": 1,
          "description": (
              "Numeric invoice core matches after stripping leading zeros; same"
              " vendor, net amount & optional desc"
          ),
      },
      {
          "rule_code": "L4",
          "rule_name": "Non-PO Overhead Double-Dip",
          "base_probability": 50,
          "escalation_step": 10,
          "max_probability": 100,
          "eur_review_threshold": 5000.0,
          "eur_hard_stop_threshold": 10000.0,
          "is_active": 1,
          "description": (
              "Non-PO with same cost center/receiving person; exact net"
              " amount, date within 30 days & optional desc"
          ),
      },
      {
          "rule_code": "L5",
          "rule_name": "Net Amount + Date + Optional Description Match",
          "base_probability": 50,
          "escalation_step": 10,
          "max_probability": 100,
          "eur_review_threshold": 5000.0,
          "eur_hard_stop_threshold": 10000.0,
          "is_active": 1,
          "description": (
              "Different non-sequential invoice IDs; exact net amount & exact"
              " invoice date"
          ),
      },
      {
          "rule_code": "L6",
          "rule_name": "Split-Threshold Tolerance Check (1% Net Tolerance)",
          "base_probability": 50,
          "escalation_step": 10,
          "max_probability": 100,
          "eur_review_threshold": 5000.0,
          "eur_hard_stop_threshold": 10000.0,
          "is_active": 1,
          "description": (
              "Net amount within 1% tolerance & same vendor; exact date &"
              " invoice similarity > 70%"
          ),
      },
      {
          "rule_code": "L7",
          "rule_name": "Vendor Name Variation + Vendor Account Number",
          "base_probability": 50,
          "escalation_step": 10,
          "max_probability": 100,
          "eur_review_threshold": 5000.0,
          "eur_hard_stop_threshold": 10000.0,
          "is_active": 1,
          "description": (
              "Matching vendor code & vendor name variation (>=80%); net"
              " amount match & date within +-3 days"
          ),
      },
      {
          "rule_code": "L8",
          "rule_name": "Invoice Variation + Vendor Name Variation",
          "base_probability": 50,
          "escalation_step": 10,
          "max_probability": 100,
          "eur_review_threshold": 5000.0,
          "eur_hard_stop_threshold": 10000.0,
          "is_active": 1,
          "description": (
              "Non-sequential invoice variation + vendor name variation"
              " (>=80%)"
          ),
      },
      {
          "rule_code": "L9",
          "rule_name": "Invoice Variation + Vendor Code Variation",
          "base_probability": 50,
          "escalation_step": 10,
          "max_probability": 100,
          "eur_review_threshold": 5000.0,
          "eur_hard_stop_threshold": 10000.0,
          "is_active": 1,
          "description": (
              "Non-sequential invoice variation + vendor code variation"
          ),
      },
      {
          "rule_code": "L10",
          "rule_name": "Vendor Name + Any Core Condition",
          "base_probability": 50,
          "escalation_step": 10,
          "max_probability": 100,
          "eur_review_threshold": 5000.0,
          "eur_hard_stop_threshold": 10000.0,
          "is_active": 1,
          "description": (
              "Vendor name match/variation (>=80%) + any core duplicate"
              " condition"
          ),
      },
      {
          "rule_code": "L11",
          "rule_name": "Vendor Number + Any of the 7 Conditions",
          "base_probability": 50,
          "escalation_step": 10,
          "max_probability": 100,
          "eur_review_threshold": 5000.0,
          "eur_hard_stop_threshold": 10000.0,
          "is_active": 1,
          "description": (
              "Vendor number/account match or variation + any of L1-L7"
          ),
      },
      {
          "rule_code": "L12",
          "rule_name": "Cross-Currency Same Invoice / Amount (EUR vs USD)",
          "base_probability": 50,
          "escalation_step": 10,
          "max_probability": 100,
          "eur_review_threshold": 5000.0,
          "eur_hard_stop_threshold": 10000.0,
          "is_active": 1,
          "description": (
              "Different currency with matching/similar invoice ID or matching"
              " net amount/date"
          ),
      },
      {
          "rule_code": "L13",
          "rule_name": "Posting Date Catalyst (<=7 Days Window)",
          "base_probability": 50,
          "escalation_step": 10,
          "max_probability": 100,
          "eur_review_threshold": 5000.0,
          "eur_hard_stop_threshold": 10000.0,
          "is_active": 1,
          "description": (
              "Second document posted within 7 days of peer posting date"
              " alongside matching signals"
          ),
      },
      {
          "rule_code": "L14",
          "rule_name": "Payment Block / Clearing State Conflict",
          "base_probability": 50,
          "escalation_step": 10,
          "max_probability": 100,
          "eur_review_threshold": 5000.0,
          "eur_hard_stop_threshold": 10000.0,
          "is_active": 1,
          "description": (
              "Matching/similar duplicate candidate where payment block or"
              " clearing status differs/conflicts"
          ),
      },
      {
          "rule_code": "L15",
          "rule_name": "Vendor Bank Account Change / Multi-IBAN Split",
          "base_probability": 50,
          "escalation_step": 10,
          "max_probability": 100,
          "eur_review_threshold": 5000.0,
          "eur_hard_stop_threshold": 10000.0,
          "is_active": 1,
          "description": (
              "Vendor has multiple/changed bank accounts (IBAN) paired with"
              " matching signals"
          ),
      },
  ]

  def __init__(self, rules_excel_path: str = "data/dummy_rules.xlsx"):
    self.rules_excel_path = rules_excel_path

  def create_default_rules_excel(
      self, output_path: str | None = None
  ) -> str:
    target = output_path or self.rules_excel_path
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    df = pd.DataFrame(self.DEFAULT_RULES)
    with pd.ExcelWriter(target, engine="openpyxl") as writer:
      df.to_excel(writer, sheet_name="Dummy_Rules", index=False)
    return target

  def sync_rules_to_db(
      self,
      db: APGovernanceDatabase,
      rules_excel_path: str | None = None,
      sheet_name: str = "Dummy_Rules",
  ) -> int:
    path = rules_excel_path or self.rules_excel_path
    if not os.path.exists(path):
      self.create_default_rules_excel(path)

    df = pd.read_excel(path, sheet_name=sheet_name)
    if df.empty:
      return 0

    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
    conn = db._connect()
    cursor = conn.cursor()
    updated = 0

    for _, r in df.iterrows():
      code = str(r.get("rule_code") or "").strip().upper()
      if not code:
        continue
      name = str(r.get("rule_name") or code).strip()
      base_p = int(r.get("base_probability") or 50)
      esc_s = int(r.get("escalation_step") or 10)
      max_p = int(r.get("max_probability") or 100)
      rev_t = float(r.get("eur_review_threshold") or 5000.0)
      hard_t = float(r.get("eur_hard_stop_threshold") or 10000.0)
      active = int(r.get("is_active") if pd.notna(r.get("is_active")) else 1)
      desc = str(r.get("description") or "").strip()

      cursor.execute(
          """
                INSERT OR REPLACE INTO rule_matrix (
                    rule_code, rule_name, base_probability, escalation_step,
                    max_probability, eur_review_threshold, eur_hard_stop_threshold,
                    is_active, description
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
          (code, name, base_p, esc_s, max_p, rev_t, hard_t, active, desc),
      )
      updated += 1

    conn.commit()
    conn.close()
    return updated