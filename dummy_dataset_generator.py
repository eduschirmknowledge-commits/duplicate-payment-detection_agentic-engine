from datetime import datetime, timedelta
import os
import random
from typing import Any
import pandas as pd


class DummyExcelDatasetGenerator:
  """Generates a realistic dummy AP dataset where most lines are clean
  and a controlled set of duplicate/anomaly cases are planted.
  """

  def __init__(self, output_path: str = "data/dummy_ap_dataset.xlsx"):
    self.output_path = output_path

  def generate(
      self,
      num_vendors: int = 300,
      seed: int = 42,
      output_path: str | None = None,
  ) -> str:
    target_path = output_path or self.output_path
    os.makedirs(os.path.dirname(target_path) or ".", exist_ok=True)

    rng = random.Random(seed)
    payment_systems = ["PAY_SYS_A", "PAY_SYS_B", "PAY_SYS_C"]
    company_codes = ["1000", "2000", "3000"]
    currencies = ["EUR", "USD", "GBP"]
    base_date = datetime(2026, 11, 1)

    rows: list[dict[str, Any]] = []
    used_amounts: set[float] = set()

    for idx in range(num_vendors):
      v_code = f"V{20000 + idx}"
      pay_sys = payment_systems[idx % len(payment_systems)]
      comp_code = company_codes[idx % len(company_codes)]
      curr = currencies[idx % len(currencies)]
      v_name = (
          f"Vendor Partner {idx} GmbH"
          if idx % 3 == 0
          else f"Vendor Partner {idx}"
      )

      # Spread dates and amounts so random non-duplicates don't falsely collide
      while True:
        net_amt = round(rng.uniform(450.0, 14500.0) + (idx * 1.37), 2)
        if net_amt not in used_amounts:
          used_amounts.add(net_amt)
          break

      inv_dt = (base_date + timedelta(days=(idx * 3) % 28)).strftime(
          "%Y-%m-%d"
      )
      post_dt = (base_date + timedelta(days=((idx * 3) % 28) + 1)).strftime(
          "%Y-%m-%d"
      )

      rows.append({
          "payment_system": pay_sys,
          "invoice_id": f"INV-{50000 + idx}",
          "vendor_code": v_code,
          "vendor_name": v_name,
          "company_code": comp_code,
          "invoice_date": inv_dt,
          "posting_date": post_dt,
          "due_date": (base_date + timedelta(days=30)).strftime("%Y-%m-%d"),
          "net_amount": net_amt,
          "tax_amount": round(net_amt * 0.19, 2),
          "gross_amount": round(net_amt * 1.19, 2),
          "currency": curr,
          "description": f"Service line {idx}",
          "ibanco": f"DE8937040044{30000000 + idx:08d}",
          "vat_id": f"DE{300000000 + idx}",
          "po_number": f"PO-{40000 + idx}" if idx % 2 == 0 else "",
          "cost_center": f"CC-{100 + (idx % 20)}",
          "receiving_person": f"Reviewer_{idx % 10}",
          "payment_block": "B" if idx % 15 == 0 else "",
          "clearing_status": "CLEARED" if idx % 4 == 0 else "OPEN",
      })

    # Planted cases:
    # 1) True morph duplicate (same number 88100 vs 88100-INB, same amount & vendor, >= EUR 10k)
    # 2) Sequential series INB-002 vs INB-003 (different numbers -> must NOT be treated as INB morph duplicate)
    # 3) Cross-system duplicate with posting date <= 7 days & IBAN change (>= EUR 5k)
    planted_cases = [
        {
            "payment_system": "PAY_SYS_A",
            "invoice_id": "INV-88100",
            "vendor_code": "V20001",
            "vendor_name": "Vendor Partner 1 GmbH",
            "company_code": "1000",
            "invoice_date": "2026-11-10",
            "posting_date": "2026-11-10",
            "due_date": "2026-12-10",
            "net_amount": 12400.0,
            "tax_amount": 2356.0,
            "gross_amount": 14756.0,
            "currency": "EUR",
            "description": "Q4 infrastructure retainer",
            "ibanco": "DE893704004430000001",
            "vat_id": "DE300000001",
            "po_number": "PO-40001",
            "cost_center": "CC-101",
            "receiving_person": "Reviewer_1",
            "payment_block": "",
            "clearing_status": "CLEARED",
        },
        {
            "payment_system": "PAY_SYS_B",
            "invoice_id": "88100-INB",
            "vendor_code": "V20001",
            "vendor_name": "Vendor Partner 1",
            "company_code": "2000",
            "invoice_date": "2026-11-11",
            "posting_date": "2026-11-14",
            "due_date": "2026-12-11",
            "net_amount": 12400.0,
            "tax_amount": 2356.0,
            "gross_amount": 14756.0,
            "currency": "EUR",
            "description": "Q4 infrastructure retainer",
            "ibanco": "DE893704004499999999",
            "vat_id": "DE300000001",
            "po_number": "PO-40001",
            "cost_center": "CC-101",
            "receiving_person": "Reviewer_1",
            "payment_block": "B",
            "clearing_status": "OPEN",
        },
        {
            "payment_system": "PAY_SYS_A",
            "invoice_id": "INB-002",
            "vendor_code": "V20005",
            "vendor_name": "Vendor Partner 5",
            "company_code": "1000",
            "invoice_date": "2026-11-05",
            "posting_date": "2026-11-06",
            "due_date": "2026-12-05",
            "net_amount": 6210.5,
            "tax_amount": 1180.0,
            "gross_amount": 7390.5,
            "currency": "EUR",
            "description": "Sequential bill 2",
            "ibanco": "DE893704004430000005",
            "vat_id": "DE300000005",
            "po_number": "PO-40005",
            "cost_center": "CC-105",
            "receiving_person": "Reviewer_5",
            "payment_block": "",
            "clearing_status": "CLEARED",
        },
        {
            "payment_system": "PAY_SYS_A",
            "invoice_id": "INB-003",
            "vendor_code": "V20005",
            "vendor_name": "Vendor Partner 5",
            "company_code": "1000",
            "invoice_date": "2026-11-18",
            "posting_date": "2026-11-19",
            "due_date": "2026-12-18",
            "net_amount": 7890.25,
            "tax_amount": 1499.15,
            "gross_amount": 9389.4,
            "currency": "EUR",
            "description": "Sequential bill 3",
            "ibanco": "DE893704004430000005",
            "vat_id": "DE300000005",
            "po_number": "PO-40006",
            "cost_center": "CC-105",
            "receiving_person": "Reviewer_5",
            "payment_block": "",
            "clearing_status": "OPEN",
        },
    ]
    rows.extend(planted_cases)

    df = pd.DataFrame(rows)
    with pd.ExcelWriter(target_path, engine="openpyxl") as writer:
      df.to_excel(writer, sheet_name="Dummy_Payment_Data", index=False)

    return target_path


if __name__ == "__main__":
  gen = DummyExcelDatasetGenerator()
  print(f"Generated: {gen.generate()}")