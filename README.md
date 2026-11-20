# 🛡️ Accounts Payable (AP) Duplicate Payment Governance Engine

An automated, intelligent Accounts Payable governance system designed to detect, flag, and prevent duplicate vendor payments in enterprise ERP environments. 

The engine combines deterministic rule matrices (L1–L9 checks), fuzzy string matching for invoice and vendor variations, and a stateful **LangGraph** orchestration workflow with an interactive **Streamlit** auditor dashboard.

---

## 🌟 Key Architecture & Modes

This project supports two operational pathways:

### 1. Production Mode (Enterprise SAP / FBL1N Staging)
- Designed to ingest SAP vendor open items (standard SAP fields: `BELNR`, `LIFNR`, `BUKRS`, `BLDAT`, `ZFBDT`, `WAERS`).
- Automated staging, currency normalization (FX rates to EUR/USD), and historical audit trail persistence in SQLite.
- Batch and real-time graph workflows with auditor feedback ingestion to suppress verified false positives.

### 2. Offline Simulation Mode (Excel / Demo Fixture)
- Built for testing, demonstrations, and environments without live SAP RFC/OData connections.
- Generates synthetic vendor datasets with deliberate duplicate patterns (e.g., transposed digits, shared IBAN with different vendor names, fuzzy invoice numbers).
- Ingests dynamic Excel-based rule definitions and exports comprehensive governance reports.

---

## 🔍 Detection Rules Matrix (L1 – L9)

- **L1 — Normalized Invoice Core Match:** Strips prefixes, leading zeros, and delimiters to identify identical invoice cores within defined posting windows.
- **L2 — Shared Banking Info / Different Vendor:** Flags payments sharing an IBAN/SWIFT across distinct vendor codes or names.
- **L3 — Numeric Invoice Core Match:** Matches invoice numbers where alphabetic vendor markers vary but numeric cores align.
- **L4 — Non-PO Matching:** Detects non-purchase-order expenses sharing cost centers, dates, and amounts.
- **L5 — Exact Amount & Date Window:** Identifies identical gross amounts posted within close proximity across similar vendor records.
- **Fuzzy Matching:** RapidFuzz-assisted similarity scoring for vendor name aliases and transposed invoice numbers.

---

## 🚀 Getting Started

### 1. Prerequisites
- Python 3.10+
- Virtual environment (recommended)

### 2. Installation
```bash
# Clone the repository
git clone https://github.com/eduschirmknowledge-commits/duplicate-payment-detection_agentic-engine.git
cd duplicate-payment-detection_agentic-engine

# Create and activate virtual environment
python -m venv .venv
# On Windows:
.venv\Scripts\activate
# On Linux/macOS:
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Configuration
Copy `.env.example` to `.env` and fill in any required variables:
```bash
cp .env.example .env
```

### 4. Running the Dashboard
Launch the interactive Streamlit auditor workspace:
```bash
streamlit run app.py
```

---

## 📁 Project Structure

```text
├── app.py                      # Interactive Streamlit auditor dashboard
├── database.py                 # SQLite database & staging manager
├── db_schema.py                # Table schemas, audit log & rule matrices
├── duplicate_rules.py          # Core L1–L9 duplicate detection algorithms
├── feedback_ingestion.py       # Auditor review & feedback suppression loop
├── graph_workflow.py           # Production SAP batch LangGraph workflow
├── dummy_graph_workflow.py     # Offline Excel simulation workflow
├── dummy_excel_rules.py        # Dynamic rule loader from Excel spreadsheets
├── dummy_dataset_generator.py  # Synthetic AP transaction generator
├── normalizers.py              # String, date, and invoice ID normalizers
├── data/                       # Sample test datasets and rules
├── requirements.txt            # Project dependencies
└── README.md                   # Documentation
```

---

## ⚖️ License
MIT License. Free for educational and commercial use.