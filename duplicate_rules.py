from normalizers import APNormalizers
import pandas as pd
from rapidfuzz import fuzz


class DuplicateRuleEvaluator:
  """Evaluates L1-L15 duplicate rules, probability scoring, and EUR review gates."""

  def __init__(self, norm: APNormalizers | None = None):
    self.norm = norm or APNormalizers()

  def evaluate_records(
      self,
      records: list[dict],
      resolved_set: set[tuple[str, str]],
      fx_map: dict[str, float],
      active_rules: set[str],
      review_threshold_eur: float = 5000.0,
      hard_stop_threshold_eur: float = 10000.0,
  ) -> pd.DataFrame:
    vendor_ibans: dict[str, set[str]] = {}
    for rec in records:
      v_key = self.norm.normalize_vendor_code(
          str(rec.get("vendor_code") or "")
      ) or self.norm.normalize_vendor_name(str(rec.get("vendor_name") or ""))
      iban_val = str(rec.get("ibanco") or "").upper().strip()
      if v_key and iban_val:
        vendor_ibans.setdefault(v_key, set()).add(iban_val)

    results = []

    for i, row in enumerate(records):
      inv_id = str(row.get("invoice_id") or "").strip()
      v_code = str(row.get("vendor_code") or "").strip()
      v_name = str(row.get("vendor_name") or "").strip()
      comp_code = str(row.get("company_code") or "").strip()
      inv_date = str(row.get("invoice_date") or "").strip()
      post_date = str(row.get("posting_date") or "").strip()
      net_amt = float(row.get("net_amount") or 0.0)
      curr = str(row.get("currency") or "EUR").upper().strip()
      desc = str(row.get("description") or "").strip()
      iban = str(row.get("ibanco") or "").upper().strip()
      vat_id = str(row.get("vat_id") or "").upper().strip()
      po_num = str(row.get("po_number") or "").strip()
      cost_ctr = str(row.get("cost_center") or "").strip()
      recv_person = str(row.get("receiving_person") or "").strip()
      pay_block = str(row.get("payment_block") or "").upper().strip()
      clr_status = str(row.get("clearing_status") or "").upper().strip()

      if (inv_id, v_code) in resolved_set:
        continue

      eur_rate = fx_map.get(curr, 1.0)
      amount_eur = abs(net_amt) * eur_rate

      norm_inv_a = self.norm.normalize_invoice_core(inv_id)
      num_core_a = self.norm.normalize_numeric_core(inv_id)
      norm_vname_a = self.norm.normalize_vendor_name(v_name)
      norm_vcode_a = self.norm.normalize_vendor_code(v_code)
      v_key_a = norm_vcode_a or norm_vname_a
      vendor_has_multi_iban = len(vendor_ibans.get(v_key_a, set())) > 1

      matched_rules = set()
      extra_signals = set()
      matched_peers = set()

      for j, peer in enumerate(records):
        if i == j:
          continue

        peer_inv = str(peer.get("invoice_id") or "").strip()
        if self.norm.is_sequential_invoice(inv_id, peer_inv):
          continue

        peer_vcode = str(peer.get("vendor_code") or "").strip()
        peer_vname = str(peer.get("vendor_name") or "").strip()
        peer_date = str(peer.get("invoice_date") or "").strip()
        peer_post_date = str(peer.get("posting_date") or "").strip()
        peer_amt = float(peer.get("net_amount") or 0.0)
        peer_curr = str(peer.get("currency") or "EUR").upper().strip()
        peer_desc = str(peer.get("description") or "").strip()
        peer_iban = str(peer.get("ibanco") or "").upper().strip()
        peer_vat = str(peer.get("vat_id") or "").upper().strip()
        peer_po = str(peer.get("po_number") or "").strip()
        peer_cc = str(peer.get("cost_center") or "").strip()
        peer_recv = str(peer.get("receiving_person") or "").strip()
        peer_block = str(peer.get("payment_block") or "").upper().strip()
        peer_clr = str(peer.get("clearing_status") or "").upper().strip()

        norm_inv_b = self.norm.normalize_invoice_core(peer_inv)
        num_core_b = self.norm.normalize_numeric_core(peer_inv)
        norm_vname_b = self.norm.normalize_vendor_name(peer_vname)
        norm_vcode_b = self.norm.normalize_vendor_code(peer_vcode)

        day_diff = self.norm.date_diff_days(inv_date, peer_date)
        exact_date = day_diff == 0
        within_3_days = day_diff is not None and day_diff <= 3
        within_30_days = day_diff is not None and day_diff <= 30

        post_day_diff = self.norm.date_diff_days(post_date, peer_post_date)
        posting_within_7_days = post_day_diff is not None and post_day_diff <= 7

        exact_amt = net_amt == peer_amt
        amt_within_1pct = self.norm.amount_match_within_tol(
            net_amt, peer_amt, 0.01
        )
        cross_currency = bool(curr and peer_curr and curr != peer_curr)

        same_vcode = bool(
            v_code and peer_vcode and v_code.upper() == peer_vcode.upper()
        )
        vcode_var = bool(
            norm_vcode_a
            and norm_vcode_b
            and (
                norm_vcode_a == norm_vcode_b
                or fuzz.ratio(norm_vcode_a, norm_vcode_b) >= 80
            )
            and not same_vcode
        )
        vname_sim = (
            fuzz.token_sort_ratio(norm_vname_a, norm_vname_b)
            if (norm_vname_a and norm_vname_b)
            else 0
        )
        vname_match_or_var = vname_sim >= 80
        diff_vendor_profile = (not same_vcode) or (
            norm_vname_a != norm_vname_b and vname_sim < 95
        )

        inv_sim = fuzz.ratio(inv_id.upper(), peer_inv.upper())
        norm_inv_match = bool(
            norm_inv_a and norm_inv_b and norm_inv_a == norm_inv_b
        )
        num_core_match = bool(
            num_core_a and num_core_b and num_core_a == num_core_b
        )
        inv_variation = bool(
            norm_inv_match
            or num_core_match
            or inv_sim >= 70
            or (
                norm_inv_a
                and norm_inv_b
                and (norm_inv_a in norm_inv_b or norm_inv_b in norm_inv_a)
            )
        )

        desc_sim = (
            fuzz.token_set_ratio(desc.upper(), peer_desc.upper())
            if (desc and peer_desc)
            else None
        )
        desc_ok_l3 = (desc_sim is None) or (desc_sim > 80)
        desc_ok_l4 = (desc_sim is None) or (desc_sim > 85)
        desc_ok_l5 = (desc_sim is None) or (desc_sim > 90)

        shared_bank_or_vat = bool(
            (iban and peer_iban and iban == peer_iban)
            or (vat_id and peer_vat and vat_id == peer_vat)
        )
        bank_account_changed = bool(
            (iban and peer_iban and iban != peer_iban)
            or (
                vendor_has_multi_iban
                and (same_vcode or vcode_var or vname_match_or_var)
            )
        )
        payment_block_conflict = bool(
            (pay_block != peer_block and bool(pay_block or peer_block))
            or (clr_status != peer_clr and bool(clr_status or peer_clr))
        )
        non_po_same_owner = bool(
            (not po_num and not peer_po)
            and (
                (cost_ctr and peer_cc and cost_ctr.upper() == peer_cc.upper())
                or (
                    recv_person
                    and peer_recv
                    and recv_person.upper() == peer_recv.upper()
                )
            )
        )

        any_core_cond = bool(
            (exact_amt and exact_date)
            or (exact_amt and inv_variation)
            or (shared_bank_or_vat and (exact_amt or inv_variation))
            or (
                desc_sim is not None
                and desc_sim > 80
                and (exact_amt or exact_date)
            )
        )

        peer_hit = False

        if "L1" in active_rules and norm_inv_match and exact_amt and within_3_days:
          matched_rules.add("L1")
          peer_hit = True
        if (
            "L2" in active_rules
            and diff_vendor_profile
            and shared_bank_or_vat
            and exact_amt
            and exact_date
        ):
          matched_rules.add("L2")
          peer_hit = True
        if (
            "L3" in active_rules
            and num_core_match
            and same_vcode
            and exact_amt
            and desc_ok_l3
        ):
          matched_rules.add("L3")
          peer_hit = True
        if (
            "L4" in active_rules
            and non_po_same_owner
            and exact_amt
            and within_30_days
            and desc_ok_l4
        ):
          matched_rules.add("L4")
          peer_hit = True
        if (
            "L5" in active_rules
            and inv_id.upper() != peer_inv.upper()
            and exact_amt
            and exact_date
            and desc_ok_l5
        ):
          matched_rules.add("L5")
          peer_hit = True
        if (
            "L6" in active_rules
            and amt_within_1pct
            and same_vcode
            and exact_date
            and inv_sim > 70
        ):
          matched_rules.add("L6")
          peer_hit = True
        if (
            "L7" in active_rules
            and same_vcode
            and vname_match_or_var
            and exact_amt
            and within_3_days
        ):
          matched_rules.add("L7")
          peer_hit = True
        if "L8" in active_rules and inv_variation and vname_match_or_var:
          matched_rules.add("L8")
          peer_hit = True
        if "L9" in active_rules and inv_variation and vcode_var:
          matched_rules.add("L9")
          peer_hit = True
        if "L10" in active_rules and vname_match_or_var and any_core_cond:
          matched_rules.add("L10")
          peer_hit = True
        if (
            "L11" in active_rules
            and (same_vcode or vcode_var)
            and matched_rules.intersection(
                {"L1", "L2", "L3", "L4", "L5", "L6", "L7"}
            )
        ):
          matched_rules.add("L11")
          peer_hit = True
        if (
            "L12" in active_rules
            and cross_currency
            and (
                inv_variation
                or exact_amt
                or amt_within_1pct
                or (exact_date and (inv_variation or exact_amt))
            )
        ):
          matched_rules.add("L12")
          peer_hit = True
        if (
            "L13" in active_rules
            and posting_within_7_days
            and (
                exact_amt
                or amt_within_1pct
                or inv_variation
                or same_vcode
                or vname_match_or_var
            )
        ):
          matched_rules.add("L13")
          peer_hit = True
        if (
            "L14" in active_rules
            and payment_block_conflict
            and (
                exact_amt
                or amt_within_1pct
                or inv_variation
                or exact_date
                or posting_within_7_days
            )
        ):
          matched_rules.add("L14")
          peer_hit = True
        if (
            "L15" in active_rules
            and bank_account_changed
            and (
                exact_amt
                or amt_within_1pct
                or inv_variation
                or exact_date
                or posting_within_7_days
            )
        ):
          matched_rules.add("L15")
          peer_hit = True

        if peer_hit:
          matched_peers.add(peer_inv)

        if posting_within_7_days and (
            exact_amt or amt_within_1pct or inv_variation or exact_date
        ):
          extra_signals.add(f"Posting Date Catalyst ({post_day_diff}d)")
        if exact_date:
          extra_signals.add("Exact Invoice Date")
        if exact_amt and not cross_currency:
          extra_signals.add("Exact Net Amount")
        if cross_currency and (exact_amt or amt_within_1pct or inv_variation):
          extra_signals.add(f"Cross-Currency ({curr} vs {peer_curr})")
        if inv_sim >= 85 or norm_inv_match:
          extra_signals.add("Strong Invoice ID Match")
        if shared_bank_or_vat:
          extra_signals.add("Shared IBAN/VAT")
        if bank_account_changed:
          extra_signals.add("Vendor Bank Account Change")
        if payment_block_conflict:
          extra_signals.add("Payment Block / Clearing Conflict")
        if vname_match_or_var:
          extra_signals.add("Vendor Name Match/Variation")
        if same_vcode or vcode_var:
          extra_signals.add("Vendor Account Match/Variation")
        if desc_sim is not None and desc_sim >= 85:
          extra_signals.add("Optional Description Match")

      if not matched_rules:
        probability_score = 0
        duplicate_status = "NOT FLAGGED"
      else:
        extra_count = max(0, len(extra_signals) - 1) + max(
            0, len(matched_rules) - 1
        )
        probability_score = min(100, 50 + (extra_count * 10))
        duplicate_status = "DUPLICATE FLAGGED"

      if amount_eur >= hard_stop_threshold_eur and matched_rules:
        review_action = "MANDATORY REVIEW (>= EUR 10,000)"
      elif amount_eur >= review_threshold_eur and matched_rules:
        review_action = "MANDATORY REVIEW (>= EUR 5,000)"
      elif matched_rules:
        review_action = "STANDARD DUPLICATE REVIEW"
      else:
        review_action = "CLEAR FOR PAYMENT RUN"

      results.append({
          "invoice_id": inv_id,
          "vendor_code": v_code,
          "vendor_name": v_name,
          "company_code": comp_code,
          "invoice_date": inv_date,
          "posting_date": post_date,
          "payment_block": pay_block,
          "clearing_status": clr_status,
          "ibanco": iban,
          "currency": curr,
          "net_amount": net_amt,
          "amount_eur": round(amount_eur, 2),
          "duplicate_status": duplicate_status,
          "probability_score": probability_score,
          "review_action": review_action,
          "matched_rules": (
              ", ".join(sorted(matched_rules)) if matched_rules else "None"
          ),
          "extra_signals": (
              ", ".join(sorted(extra_signals)) if extra_signals else "None"
          ),
          "matched_peer_invoices": (
              ", ".join(sorted(matched_peers)) if matched_peers else "None"
          ),
      })

    return pd.DataFrame(results)