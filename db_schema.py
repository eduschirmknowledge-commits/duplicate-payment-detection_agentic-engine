import os
import sqlite3
from dotenv import load_dotenv

load_dotenv()


class GovernanceSchemaManager:
  """Creates and seeds SQLite tables for AP duplicate governance and audit history."""

  def __init__(self, db_path: str | None = None):
    self.db_path = db_path or os.getenv("SQLITE_DB_PATH", "ap_governance.db")

  def connect(self):
    return sqlite3.connect(self.db_path)

  def init_schema(self):
    conn = self.connect()
    cursor = conn.cursor()

    cursor.execute("""
            CREATE TABLE IF NOT EXISTS open_items (
                invoice_id TEXT,
                vendor_code TEXT,
                vendor_name TEXT,
                company_code TEXT,
                invoice_date TEXT,
                posting_date TEXT,
                due_date TEXT,
                net_amount REAL,
                tax_amount REAL,
                gross_amount REAL,
                currency TEXT,
                description TEXT,
                ibanco TEXT,
                vat_id TEXT,
                po_number TEXT,
                cost_center TEXT,
                receiving_person TEXT,
                payment_block TEXT,
                clearing_status TEXT,
                PRIMARY KEY (invoice_id, vendor_code, company_code)
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
            CREATE TABLE IF NOT EXISTS run_audit_log (
                run_id TEXT PRIMARY KEY,
                executed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                source_label TEXT,
                total_rows INTEGER DEFAULT 0,
                evaluated_rows INTEGER DEFAULT 0,
                suppressed_rows INTEGER DEFAULT 0,
                flagged_count INTEGER DEFAULT 0,
                mandatory_review_5k_count INTEGER DEFAULT 0,
                mandatory_review_10k_count INTEGER DEFAULT 0,
                total_flagged_eur REAL DEFAULT 0.0,
                excel_path TEXT,
                notes TEXT
            )
        """)

    cursor.execute("""
            CREATE TABLE IF NOT EXISTS rule_matrix (
                rule_code TEXT PRIMARY KEY,
                rule_name TEXT NOT NULL,
                base_probability INTEGER NOT NULL DEFAULT 50,
                escalation_step INTEGER NOT NULL DEFAULT 10,
                max_probability INTEGER NOT NULL DEFAULT 100,
                eur_review_threshold REAL NOT NULL DEFAULT 5000.0,
                eur_hard_stop_threshold REAL NOT NULL DEFAULT 10000.0,
                is_active INTEGER NOT NULL DEFAULT 1,
                description TEXT
            )
        """)

    cursor.execute("""
            CREATE TABLE IF NOT EXISTS fx_rates_to_eur (
                currency TEXT PRIMARY KEY,
                rate_to_eur REAL NOT NULL
            )
        """)

    conn.commit()
    conn.close()

  def seed_rule_matrix(self):
    rules = [
        (
            "L1",
            "Suffix/Prefix Morph (1234 vs 1234-INB)",
            50,
            10,
            100,
            5000.0,
            10000.0,
            1,
            (
                "Normalized invoice core matches; non-sequential; net amount"
                " matches & invoice date within +-3 days"
            ),
        ),
        (
            "L2",
            "Multi-Entity Bank Clone (XYZ vs XYZ GmbH)",
            50,
            10,
            100,
            5000.0,
            10000.0,
            1,
            (
                "Different vendor code/name with shared IBAN/SWIFT or VAT ID;"
                " exact net amount & exact date match"
            ),
        ),
        (
            "L3",
            "Zero-Padding / Formatting Trick (1234 vs 001234)",
            50,
            10,
            100,
            5000.0,
            10000.0,
            1,
            (
                "Numeric invoice core matches after stripping leading zeros;"
                " same vendor, net amount & optional desc > 80%"
            ),
        ),
        (
            "L4",
            "Non-PO Overhead Double-Dip",
            50,
            10,
            100,
            5000.0,
            10000.0,
            1,
            (
                "Non-PO with same cost center/receiving person; exact net"
                " amount, date within 30 days & optional desc match"
            ),
        ),
        (
            "L5",
            "Net Amount + Date + Optional Description Match",
            50,
            10,
            100,
            5000.0,
            10000.0,
            1,
            (
                "Different non-sequential invoice IDs; exact net amount & exact"
                " invoice date"
            ),
        ),
        (
            "L6",
            "Split-Threshold Tolerance Check (1% Net Tolerance)",
            50,
            10,
            100,
            5000.0,
            10000.0,
            1,
            (
                "Net amount within 1% tolerance & same vendor; exact date &"
                " invoice similarity > 70%"
            ),
        ),
        (
            "L7",
            "Vendor Name Variation + Vendor Account Number",
            50,
            10,
            100,
            5000.0,
            10000.0,
            1,
            (
                "Matching vendor code & vendor name variation (>=80%); net"
                " amount match & date within +-3 days"
            ),
        ),
        (
            "L8",
            "Invoice Variation + Vendor Name Variation",
            50,
            10,
            100,
            5000.0,
            10000.0,
            1,
            (
                "Non-sequential invoice variation + vendor name variation"
                " (>=80%)"
            ),
        ),
        (
            "L9",
            "Invoice Variation + Vendor Code Variation",
            50,
            10,
            100,
            5000.0,
            10000.0,
            1,
            "Non-sequential invoice variation + vendor code variation",
        ),
        (
            "L10",
            "Vendor Name + Any Core Condition",
            50,
            10,
            100,
            5000.0,
            10000.0,
            1,
            (
                "Vendor name match/variation (>=80%) + any core duplicate"
                " condition"
            ),
        ),
        (
            "L11",
            "Vendor Number + Any of the 7 Conditions",
            50,
            10,
            100,
            5000.0,
            10000.0,
            1,
            "Vendor number/account match or variation + any of L1-L7",
        ),
        (
            "L12",
            "Cross-Currency Same Invoice / Amount (EUR vs USD)",
            50,
            10,
            100,
            5000.0,
            10000.0,
            1,
            (
                "Different currency (e.g. EUR vs USD) with matching/similar"
                " invoice ID or matching net amount/date"
            ),
        ),
        (
            "L13",
            "Posting Date Catalyst (<=7 Days Window)",
            50,
            10,
            100,
            5000.0,
            10000.0,
            1,
            (
                "Second document posted within 7 days of peer posting date"
                " alongside matching signals"
            ),
        ),
        (
            "L14",
            "Payment Block / Clearing State Conflict",
            50,
            10,
            100,
            5000.0,
            10000.0,
            1,
            (
                "Matching/similar duplicate candidate where payment block or"
                " clearing status differs/conflicts"
            ),
        ),
        (
            "L15",
            "Vendor Bank Account Change / Multi-IBAN Split",
            50,
            10,
            100,
            5000.0,
            10000.0,
            1,
            (
                "Vendor has multiple/changed bank accounts (IBAN) paired with"
                " matching invoice ID, net amount, or date window"
            ),
        ),
    ]

    conn = self.connect()
    cursor = conn.cursor()
    cursor.executemany(
        """
        INSERT OR IGNORE INTO rule_matrix (
            rule_code, rule_name, base_probability, escalation_step,
            max_probability, eur_review_threshold, eur_hard_stop_threshold,
            is_active, description
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rules,
    )
    conn.commit()
    conn.close()

  def seed_default_fx_rates(self):
    rates = [
        ("EUR", 1.0),
        ("USD", 0.92),
        ("GBP", 1.18),
        ("CHF", 1.05),
        ("INR", 0.011),
    ]
    conn = self.connect()
    cursor = conn.cursor()
    cursor.executemany(
        """
        INSERT OR IGNORE INTO fx_rates_to_eur (currency, rate_to_eur)
        VALUES (?, ?)
        """,
        rates,
    )
    conn.commit()
    conn.close()