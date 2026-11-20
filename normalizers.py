from datetime import datetime
import re


class APNormalizers:
  """Pure normalization and comparison helpers for FBL1N fields."""

  @staticmethod
  def normalize_invoice_core(inv_id: str) -> str:
    cleaned = re.sub(
        r"^(INV|BILL|RE|DOC|INB)[-_/\s]*", "", inv_id.upper().strip()
    )
    cleaned = re.sub(r"[-_/\s]*(INB|EU|DE|US|RE|COPY|DUP)$", "", cleaned)
    cleaned = re.sub(r"[^A-Z0-9]", "", cleaned)
    return cleaned

  @staticmethod
  def normalize_numeric_core(inv_id: str) -> str:
    digits = re.sub(r"\D", "", inv_id)
    stripped = digits.lstrip("0")
    return stripped if stripped else ("0" if digits else "")

  @staticmethod
  def normalize_vendor_name(name: str) -> str:
    val = name.upper().strip()
    val = re.sub(
        r"\b(GMBH|AG|KG|UG|INC|LTD|LIMITED|LLC|CORP|CO|SA|BV|NV|PLC)\b",
        "",
        val,
    )
    val = re.sub(r"[^A-Z0-9\s]", " ", val)
    val = re.sub(r"\s+", " ", val).strip()
    return val

  @staticmethod
  def normalize_vendor_code(code: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", code.upper().strip()).lstrip("0")

  @staticmethod
  def is_sequential_invoice(inv_a: str, inv_b: str) -> bool:
    a_digits = "".join(ch for ch in inv_a if ch.isdigit())
    b_digits = "".join(ch for ch in inv_b if ch.isdigit())
    if a_digits and b_digits and a_digits.isdigit() and b_digits.isdigit():
      return abs(int(a_digits) - int(b_digits)) == 1
    return False

  @staticmethod
  def date_diff_days(d1: str, d2: str) -> int | None:
    if not d1 or not d2:
      return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%Y/%m/%d"):
      try:
        dt1 = datetime.strptime(d1.strip(), fmt)
        dt2 = datetime.strptime(d2.strip(), fmt)
        return abs((dt1 - dt2).days)
      except ValueError:
        continue
    return None

  @staticmethod
  def amount_match_within_tol(a: float, b: float, tol: float = 0.01) -> bool:
    if a == 0.0 and b == 0.0:
      return False
    denom = max(abs(a), abs(b), 1e-9)
    return abs(a - b) / denom <= tol