"""GST return summaries for HisabFlow: GSTR-1 and GSTR-3B, plus Excel (.xlsx) and GSTR-1 JSON export.

These are preparation aids built from records saved in HisabFlow. They do not file anything.
Always review the figures and validate exported files in the official GST offline tool before upload.
"""
from __future__ import annotations

import io
import json
import re
import zipfile
from collections import defaultdict
from datetime import date
from xml.sax.saxutils import escape

STATE_CODES = {
    "Jammu and Kashmir": "01", "Himachal Pradesh": "02", "Punjab": "03", "Chandigarh": "04", "Uttarakhand": "05",
    "Haryana": "06", "Delhi": "07", "Rajasthan": "08", "Uttar Pradesh": "09", "Bihar": "10", "Sikkim": "11",
    "Arunachal Pradesh": "12", "Nagaland": "13", "Manipur": "14", "Mizoram": "15", "Tripura": "16", "Meghalaya": "17",
    "Assam": "18", "West Bengal": "19", "Jharkhand": "20", "Odisha": "21", "Chhattisgarh": "22", "Madhya Pradesh": "23",
    "Gujarat": "24", "Dadra and Nagar Haveli and Daman and Diu": "26", "Maharashtra": "27", "Karnataka": "29",
    "Goa": "30", "Lakshadweep": "31", "Kerala": "32", "Tamil Nadu": "33", "Puducherry": "34",
    "Andaman and Nicobar Islands": "35", "Telangana": "36", "Andhra Pradesh": "37", "Ladakh": "38",
}
UQC = {"pcs": "PCS", "nos": "NOS", "kg": "KGS", "ltr": "LTR", "meter": "MTR", "box": "BOX", "set": "SET",
       "hour": "OTH", "service": "NA"}
GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z0-9]{13}$")


def r2(v) -> float:
    return round(float(v or 0) + 0.0, 2)


def pos_label(state: str) -> str:
    code = STATE_CODES.get(state or "")
    return f"{code}-{state}" if code else (state or "")


def b2cl_limit(iso_date: str) -> float:
    # B2C (Large) threshold for inter-state invoices: Rs 2.5 lakh before 1 Aug 2024, Rs 1 lakh from then.
    return 100000.0 if iso_date >= "2024-08-01" else 250000.0


def uqc_for(unit: str, hsn: str) -> str:
    if (hsn or "").startswith("99"): return "NA"
    return UQC.get((unit or "").strip().lower(), "OTH")


def _rows(con, sql, args=()):
    cur = con.execute(sql, args); cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _business(con):
    return _rows(con, "SELECT * FROM business WHERE id=1")[0]


def _dmy(iso: str) -> str:
    d = date.fromisoformat(iso); return d.strftime("%d-%m-%Y")


# ------------------------------------------------------------------ GSTR-1
def gstr1(con, start: str, end: str) -> dict:
    biz = _business(con)
    warnings: list[str] = []
    invoices = _rows(con, "SELECT * FROM invoices WHERE invoice_date BETWEEN ? AND ? ORDER BY invoice_date, id", (start, end))
    items = defaultdict(list)
    if invoices:
        ids = [i["id"] for i in invoices]
        for it in _rows(con, f"SELECT * FROM invoice_items WHERE invoice_id IN ({','.join('?'*len(ids))}) ORDER BY id", ids):
            items[it["invoice_id"]].append(it)
    b2b, b2cl, nil = [], [], defaultdict(float)
    b2cs = defaultdict(lambda: {"taxable": 0.0, "igst": 0.0, "cgst": 0.0, "sgst": 0.0})
    hsn = {"B2B": defaultdict(lambda: {"description": "", "qty": 0.0, "total_value": 0.0, "taxable": 0.0, "igst": 0.0, "cgst": 0.0, "sgst": 0.0}),
           "B2C": defaultdict(lambda: {"description": "", "qty": 0.0, "total_value": 0.0, "taxable": 0.0, "igst": 0.0, "cgst": 0.0, "sgst": 0.0})}
    missing_hsn = set(); bad_gstin = set()
    totals = {"taxable": 0.0, "igst": 0.0, "cgst": 0.0, "sgst": 0.0, "invoice_value": 0.0}

    def add_hsn(kind, it, sign=1):
        code = (it.get("hsn") or "").strip()
        if not code: missing_hsn.add(it["description"])
        key = (code or "MISSING", uqc_for(it.get("unit"), code), r2(it["gst_rate"]))
        h = hsn[kind][key]; h["description"] = h["description"] or it["description"][:30]
        h["qty"] += sign * float(it["qty"]) if key[1] != "NA" else 0
        h["total_value"] += sign * float(it["line_total"]); h["taxable"] += sign * float(it["taxable"])
        for t in ("igst", "cgst", "sgst"): h[t] += sign * float(it[t])

    for inv in invoices:
        cust = json.loads(inv["customer_snapshot"] or "{}"); gstin = (cust.get("gstin") or "").strip().upper()
        if gstin and not GSTIN_RE.match(gstin): bad_gstin.add(cust.get("name", ""))
        inter = inv["supply_type"] == "IGST"; pos = inv["place_of_supply"] or biz["state"]
        if not pos: warnings.append(f"{inv['invoice_number']}: place of supply is missing")
        by_rate = defaultdict(lambda: {"taxable": 0.0, "igst": 0.0, "cgst": 0.0, "sgst": 0.0})
        for it in items[inv["id"]]:
            add_hsn("B2B" if gstin else "B2C", it)
            if float(it["gst_rate"]) == 0:
                nil[("INTER" if inter else "INTRA") + ("B2B" if gstin else "B2C")] += float(it["taxable"]); continue
            b = by_rate[r2(it["gst_rate"])]
            b["taxable"] += float(it["taxable"])
            for t in ("igst", "cgst", "sgst"): b[t] += float(it[t])
        totals["invoice_value"] += float(inv["total"])
        for rate, b in sorted(by_rate.items()):
            for k in ("taxable", "igst", "cgst", "sgst"): totals[k] += b[k]
            if gstin:
                b2b.append({"gstin": gstin, "name": cust.get("name", ""), "invoice_number": inv["invoice_number"], "invoice_date": inv["invoice_date"],
                            "invoice_value": r2(inv["total"]), "pos": pos_label(pos), "reverse_charge": "N", "invoice_type": "Regular B2B",
                            "rate": rate, "taxable": r2(b["taxable"]), "igst": r2(b["igst"]), "cgst": r2(b["cgst"]), "sgst": r2(b["sgst"]), "cess": 0})
            elif inter and float(inv["total"]) > b2cl_limit(inv["invoice_date"]):
                b2cl.append({"invoice_number": inv["invoice_number"], "invoice_date": inv["invoice_date"], "invoice_value": r2(inv["total"]),
                             "pos": pos_label(pos), "rate": rate, "taxable": r2(b["taxable"]), "igst": r2(b["igst"]), "cess": 0})
            else:
                s = b2cs[("INTER" if inter else "INTRA", pos_label(pos), rate)]
                for k in ("taxable", "igst", "cgst", "sgst"): s[k] += b[k]

    # Credit notes issued in the period
    cdnr, cdnur = [], []
    notes = _rows(con, "SELECT cn.*, i.invoice_number orig_number, i.invoice_date orig_date, i.total orig_total FROM credit_notes cn JOIN invoices i ON i.id=cn.invoice_id WHERE cn.note_date BETWEEN ? AND ? ORDER BY cn.note_date, cn.id", (start, end))
    cn_totals = {"taxable": 0.0, "igst": 0.0, "cgst": 0.0, "sgst": 0.0, "value": 0.0}
    for cn in notes:
        cust = json.loads(cn["customer_snapshot"] or "{}"); gstin = (cust.get("gstin") or "").strip().upper()
        inter = cn["supply_type"] == "IGST"; pos = cn["place_of_supply"] or biz["state"]
        by_rate = defaultdict(lambda: {"taxable": 0.0, "igst": 0.0, "cgst": 0.0, "sgst": 0.0})
        for it in _rows(con, "SELECT * FROM credit_note_items WHERE credit_note_id=?", (cn["id"],)):
            add_hsn("B2B" if gstin else "B2C", it, -1)
            if float(it["gst_rate"]) == 0:
                nil[("INTER" if inter else "INTRA") + ("B2B" if gstin else "B2C")] -= float(it["taxable"]); continue
            b = by_rate[r2(it["gst_rate"])]; b["taxable"] += float(it["taxable"])
            for t in ("igst", "cgst", "sgst"): b[t] += float(it[t])
        cn_totals["value"] += float(cn["total"])
        for rate, b in sorted(by_rate.items()):
            for k in ("taxable", "igst", "cgst", "sgst"): cn_totals[k] += b[k]
            base = {"note_number": cn["note_number"], "note_date": cn["note_date"], "note_type": "C", "pos": pos_label(pos),
                    "note_value": r2(cn["total"]), "rate": rate, "taxable": r2(b["taxable"]), "igst": r2(b["igst"]),
                    "cgst": r2(b["cgst"]), "sgst": r2(b["sgst"]), "cess": 0, "original_invoice": cn["orig_number"], "original_date": cn["orig_date"]}
            if gstin:
                cdnr.append({"gstin": gstin, "name": cust.get("name", ""), "reverse_charge": "N", "note_supply_type": "Regular B2B", **base})
            elif inter and float(cn["orig_total"]) > b2cl_limit(cn["orig_date"]):
                cdnur.append({"ur_type": "B2CL", **base})
            else:  # small B2C credit notes are netted off the B2CS summary
                s = b2cs[("INTER" if inter else "INTRA", pos_label(pos), rate)]
                for k in ("taxable", "igst", "cgst", "sgst"): s[k] -= b[k]

    b2cs_rows = [{"supply": k[0], "pos": k[1], "type": "OE", "rate": k[2], "taxable": r2(v["taxable"]), "igst": r2(v["igst"]),
                  "cgst": r2(v["cgst"]), "sgst": r2(v["sgst"]), "cess": 0} for k, v in sorted(b2cs.items()) if abs(v["taxable"]) > .004]
    nil_labels = [("INTERB2B", "Inter-State supplies to registered persons"), ("INTRAB2B", "Intra-State supplies to registered persons"),
                  ("INTERB2C", "Inter-State supplies to unregistered persons"), ("INTRAB2C", "Intra-State supplies to unregistered persons")]
    nil_rows = [{"code": c, "description": d, "nil_rated": r2(nil.get(c, 0)), "exempted": 0, "non_gst": 0} for c, d in nil_labels]

    def hsn_rows(kind):
        return [{"hsn": k[0], "description": v["description"], "uqc": k[1], "qty": round(v["qty"], 3), "total_value": r2(v["total_value"]),
                 "rate": k[2], "taxable": r2(v["taxable"]), "igst": r2(v["igst"]), "cgst": r2(v["cgst"]), "sgst": r2(v["sgst"]), "cess": 0}
                for k, v in sorted(hsn[kind].items())]

    docs = []
    if invoices:
        nums = [i["invoice_number"] for i in sorted(invoices, key=lambda x: x["id"])]
        docs.append({"nature": "Invoices for outward supply", "from": nums[0], "to": nums[-1], "total": len(nums), "cancelled": 0, "net_issued": len(nums)})
    if notes:
        nums = [n["note_number"] for n in sorted(notes, key=lambda x: x["id"])]
        docs.append({"nature": "Credit Note", "from": nums[0], "to": nums[-1], "total": len(nums), "cancelled": 0, "net_issued": len(nums)})
    if not biz.get("gstin"): warnings.insert(0, "Your business GSTIN is not set in Settings. GSTR-1 needs a GSTIN.")
    if missing_hsn: warnings.append(f"{len(missing_hsn)} item(s) have no HSN/SAC code (e.g. {', '.join(sorted(missing_hsn)[:3])}). Add HSN codes to items for the HSN summary.")
    if bad_gstin: warnings.append(f"Customer GSTIN format looks wrong for: {', '.join(sorted(bad_gstin)[:5])}")
    if any(r["nil_rated"] for r in nil_rows): warnings.append("0% items are shown as nil-rated. Move any exempt or non-GST supplies to the correct column before filing.")
    return {
        "return": "GSTR-1", "from": start, "to": end, "gstin": biz.get("gstin", ""), "business": biz.get("name", ""), "state": biz.get("state", ""),
        "b2b": b2b, "b2cl": b2cl, "b2cs": b2cs_rows, "cdnr": cdnr, "cdnur": cdnur, "nil": nil_rows,
        "hsn_b2b": hsn_rows("B2B"), "hsn_b2c": hsn_rows("B2C"), "docs": docs,
        "summary": {"invoices": len(invoices), "credit_notes": len(notes), "invoice_value": r2(totals["invoice_value"]),
                    "taxable": r2(totals["taxable"]), "igst": r2(totals["igst"]), "cgst": r2(totals["cgst"]), "sgst": r2(totals["sgst"]),
                    "credit_taxable": r2(cn_totals["taxable"]), "credit_tax": r2(cn_totals["igst"] + cn_totals["cgst"] + cn_totals["sgst"]),
                    "net_tax": r2(totals["igst"] + totals["cgst"] + totals["sgst"] - cn_totals["igst"] - cn_totals["cgst"] - cn_totals["sgst"])},
        "warnings": warnings,
    }


def gstr1_portal_json(rep: dict) -> dict:
    """GSTR-1 in the layout used by the GST offline tool. Validate it in the official tool before uploading."""
    code = lambda label: (label or "").split("-")[0]
    end = date.fromisoformat(rep["to"])

    def itm(rows, intra_cols=True):
        out = []
        for n, r in enumerate(rows, 1):
            det = {"txval": r["taxable"], "rt": r["rate"], "iamt": r["igst"], "csamt": 0}
            if intra_cols: det.update({"camt": r.get("cgst", 0), "samt": r.get("sgst", 0)})
            out.append({"num": n, "itm_det": det})
        return out

    def group(rows, key):
        g = defaultdict(list)
        for r in rows: g[key(r)].append(r)
        return g

    b2b = []
    for ctin, rs in group(rep["b2b"], lambda r: r["gstin"]).items():
        invs = [{"inum": num, "idt": _dmy(lines[0]["invoice_date"]), "val": lines[0]["invoice_value"], "pos": code(lines[0]["pos"]),
                 "rchrg": "N", "inv_typ": "R", "itms": itm(lines)} for num, lines in group(rs, lambda r: r["invoice_number"]).items()]
        b2b.append({"ctin": ctin, "inv": invs})
    b2cl = []
    for pos, rs in group(rep["b2cl"], lambda r: r["pos"]).items():
        b2cl.append({"pos": code(pos), "inv": [{"inum": num, "idt": _dmy(lines[0]["invoice_date"]), "val": lines[0]["invoice_value"],
                                               "itms": itm(lines, False)} for num, lines in group(rs, lambda r: r["invoice_number"]).items()]})
    b2cs = [{"sply_ty": r["supply"], "pos": code(r["pos"]), "typ": "OE", "txval": r["taxable"], "rt": r["rate"], "iamt": r["igst"],
             "camt": r["cgst"], "samt": r["sgst"], "csamt": 0} for r in rep["b2cs"]]
    cdnr = []
    for ctin, rs in group(rep["cdnr"], lambda r: r["gstin"]).items():
        cdnr.append({"ctin": ctin, "nt": [{"ntty": "C", "nt_num": num, "nt_dt": _dmy(lines[0]["note_date"]), "val": lines[0]["note_value"],
                                           "pos": code(lines[0]["pos"]), "rchrg": "N", "inv_typ": "R", "itms": itm(lines)}
                                          for num, lines in group(rs, lambda r: r["note_number"]).items()]})
    cdnur = [{"typ": "B2CL", "ntty": "C", "nt_num": num, "nt_dt": _dmy(lines[0]["note_date"]), "val": lines[0]["note_value"],
              "pos": code(lines[0]["pos"]), "itms": itm(lines, False)} for num, lines in group(rep["cdnur"], lambda r: r["note_number"]).items()]
    nil_codes = {"INTERB2B": "INTRB2B", "INTRAB2B": "INTRAB2B", "INTERB2C": "INTRB2C", "INTRAB2C": "INTRAB2C"}
    hsn_json = lambda rows: [{"num": n, "hsn_sc": r["hsn"], "desc": r["description"], "uqc": r["uqc"], "qty": r["qty"], "rt": r["rate"],
                              "txval": r["taxable"], "iamt": r["igst"], "camt": r["cgst"], "samt": r["sgst"], "csamt": 0} for n, r in enumerate(rows, 1)]
    docs = [{"doc_num": 1 if d["nature"].startswith("Invoices") else 5, "doc_typ": d["nature"],
             "docs": [{"num": 1, "from": d["from"], "to": d["to"], "totnum": d["total"], "cancel": d["cancelled"], "net_issue": d["net_issued"]}]}
            for d in rep["docs"]]
    out = {"gstin": rep["gstin"], "fp": end.strftime("%m%Y")}
    if b2b: out["b2b"] = b2b
    if b2cl: out["b2cl"] = b2cl
    if b2cs: out["b2cs"] = b2cs
    if cdnr: out["cdnr"] = cdnr
    if cdnur: out["cdnur"] = cdnur
    if any(r["nil_rated"] for r in rep["nil"]):
        out["nil"] = {"inv": [{"sply_ty": nil_codes[r["code"]], "nil_amt": r["nil_rated"], "expt_amt": 0, "ngsup_amt": 0} for r in rep["nil"]]}
    if rep["hsn_b2b"] or rep["hsn_b2c"]:
        out["hsn"] = {"hsn_b2b": hsn_json(rep["hsn_b2b"]), "hsn_b2c": hsn_json(rep["hsn_b2c"])}
    if docs: out["doc_issue"] = {"doc_det": docs}
    return out


# ------------------------------------------------------------------ GSTR-3B
def set_off(out: dict, itc: dict) -> dict:
    """Simplified ITC utilisation: IGST credit -> IGST, CGST, SGST; CGST -> CGST, IGST; SGST -> SGST, IGST.
    CGST and SGST credits are never used against each other."""
    o = {k: max(float(v), 0.0) for k, v in out.items()}; c = {k: max(float(v), 0.0) for k, v in itc.items()}
    used = defaultdict(float)

    def use(src, dst):
        amt = min(c[src], o[dst]); c[src] -= amt; o[dst] -= amt; used[f"{src}->{dst}"] += amt

    use("igst", "igst"); use("igst", "cgst"); use("igst", "sgst")
    use("cgst", "cgst"); use("cgst", "igst"); use("sgst", "sgst"); use("sgst", "igst")
    return {"cash": {k: r2(v) for k, v in o.items()}, "carry_forward": {k: r2(v) for k, v in c.items()},
            "utilised": {k: r2(v) for k, v in used.items() if v > .004}}


def gstr3b(con, start: str, end: str) -> dict:
    biz = _business(con); my_state = biz.get("state", ""); warnings: list[str] = []
    out_taxable = {"taxable": 0.0, "igst": 0.0, "cgst": 0.0, "sgst": 0.0}; nil_out = 0.0
    unreg_inter = defaultdict(lambda: {"taxable": 0.0, "igst": 0.0})

    def scan(sql_items, sign):
        nonlocal nil_out
        for it in _rows(con, sql_items, (start, end)):
            cust = json.loads(it["customer_snapshot"] or "{}"); reg = bool((cust.get("gstin") or "").strip())
            if float(it["gst_rate"]) == 0: nil_out += sign * float(it["taxable"]); continue
            out_taxable["taxable"] += sign * float(it["taxable"])
            for t in ("igst", "cgst", "sgst"): out_taxable[t] += sign * float(it[t])
            if not reg and it["supply_type"] == "IGST":
                u = unreg_inter[pos_label(it["place_of_supply"])]; u["taxable"] += sign * float(it["taxable"]); u["igst"] += sign * float(it["igst"])

    scan("SELECT ii.*, i.customer_snapshot, i.supply_type, i.place_of_supply FROM invoice_items ii JOIN invoices i ON i.id=ii.invoice_id WHERE i.invoice_date BETWEEN ? AND ?", 1)
    scan("SELECT ci.*, cn.customer_snapshot, cn.supply_type, cn.place_of_supply FROM credit_note_items ci JOIN credit_notes cn ON cn.id=ci.credit_note_id WHERE cn.note_date BETWEEN ? AND ?", -1)

    itc = {"igst": 0.0, "cgst": 0.0, "sgst": 0.0}; reduced = {"igst": 0.0, "cgst": 0.0, "sgst": 0.0}
    inward_nil = {"inter": 0.0, "intra": 0.0}; unregistered_tax = 0.0; purchase_count = 0

    def split(tax, supplier):
        inter = bool(supplier.get("state") and my_state and supplier.get("state") != my_state)
        return ({"igst": tax, "cgst": 0.0, "sgst": 0.0} if inter else {"igst": 0.0, "cgst": round(tax / 2, 2), "sgst": round(tax - round(tax / 2, 2), 2)}), inter

    for pu in _rows(con, "SELECT * FROM purchases WHERE purchase_date BETWEEN ? AND ?", (start, end)):
        purchase_count += 1; sup = json.loads(pu["supplier_snapshot"] or "{}")
        for it in _rows(con, "SELECT * FROM purchase_items WHERE purchase_id=?", (pu["id"],)):
            if float(it["gst_rate"]) == 0:
                inter = bool(sup.get("state") and my_state and sup.get("state") != my_state)
                inward_nil["inter" if inter else "intra"] += float(it["taxable"]); continue
            if not (sup.get("gstin") or "").strip(): unregistered_tax += float(it["tax"]); continue
            parts, _ = split(float(it["tax"]), sup)
            for k in itc: itc[k] += parts[k]
    for dn in _rows(con, "SELECT pr.*, pu.supplier_snapshot FROM purchase_returns pr JOIN purchases pu ON pu.id=pr.purchase_id WHERE pr.return_date BETWEEN ? AND ?", (start, end)):
        sup = json.loads(dn["supplier_snapshot"] or "{}")
        if not (sup.get("gstin") or "").strip(): continue
        for it in _rows(con, "SELECT * FROM purchase_return_items WHERE return_id=?", (dn["id"],)):
            rate = float(it["gst_rate"] or 0)
            tax = float(it["tax"]) if float(it["taxable"] or 0) else float(it["amount"]) - float(it["amount"]) / (1 + rate / 100)
            parts, _ = split(round(tax, 2), sup)
            for k in reduced: reduced[k] += parts[k]
    net_itc = {k: r2(itc[k] - reduced[k]) for k in itc}
    output_tax = {k: r2(out_taxable[k]) for k in ("igst", "cgst", "sgst")}
    payment = set_off(output_tax, net_itc)
    if unregistered_tax: warnings.append(f"Tax of ₹{unregistered_tax:,.2f} on purchases from suppliers without a GSTIN was not counted as ITC.")
    warnings.append("ITC is taken from all purchase bills with GST. Check it against GSTR-2B and remove blocked credits (Section 17(5)) before filing.")
    if not biz.get("gstin"): warnings.insert(0, "Your business GSTIN is not set in Settings.")
    return {
        "return": "GSTR-3B", "from": start, "to": end, "gstin": biz.get("gstin", ""), "business": biz.get("name", ""),
        "outward": {"taxable": r2(out_taxable["taxable"]), **output_tax, "cess": 0},
        "nil_outward": r2(nil_out),
        "unregistered_inter": [{"pos": k, "taxable": r2(v["taxable"]), "igst": r2(v["igst"])} for k, v in sorted(unreg_inter.items()) if abs(v["taxable"]) > .004],
        "itc_available": {k: r2(v) for k, v in itc.items()}, "itc_reduced": {k: r2(v) for k, v in reduced.items()}, "itc_net": net_itc,
        "inward_nil": {k: r2(v) for k, v in inward_nil.items()}, "purchases": purchase_count,
        "payment": payment, "warnings": warnings,
    }


# ------------------------------------------------------------------ sheet layouts
def gstr1_sheets(rep: dict):
    S = lambda rows, cols: [[r.get(c) for c in cols] for r in rows]
    sm = rep["summary"]
    return [
        ("Summary", ["Item", "Value"], [["Return", "GSTR-1"], ["GSTIN", rep["gstin"]], ["Business", rep["business"]], ["Period", f"{rep['from']} to {rep['to']}"],
                                        ["Invoices", sm["invoices"]], ["Total invoice value", sm["invoice_value"]], ["Taxable value", sm["taxable"]],
                                        ["IGST", sm["igst"]], ["CGST", sm["cgst"]], ["SGST", sm["sgst"]], ["Credit notes", sm["credit_notes"]],
                                        ["Credit note taxable value", sm["credit_taxable"]], ["Credit note tax", sm["credit_tax"]], ["Net tax", sm["net_tax"]]]
                                       + [["Warning", w] for w in rep["warnings"]]),
        ("b2b", ["GSTIN of Recipient", "Receiver Name", "Invoice Number", "Invoice date", "Invoice Value", "Place Of Supply", "Reverse Charge",
                 "Invoice Type", "Rate", "Taxable Value", "Integrated Tax", "Central Tax", "State/UT Tax", "Cess Amount"],
         S(rep["b2b"], ["gstin", "name", "invoice_number", "invoice_date", "invoice_value", "pos", "reverse_charge", "invoice_type", "rate", "taxable", "igst", "cgst", "sgst", "cess"])),
        ("b2cl", ["Invoice Number", "Invoice date", "Invoice Value", "Place Of Supply", "Rate", "Taxable Value", "Integrated Tax", "Cess Amount"],
         S(rep["b2cl"], ["invoice_number", "invoice_date", "invoice_value", "pos", "rate", "taxable", "igst", "cess"])),
        ("b2cs", ["Supply Type", "Type", "Place Of Supply", "Rate", "Taxable Value", "Integrated Tax", "Central Tax", "State/UT Tax", "Cess Amount"],
         S(rep["b2cs"], ["supply", "type", "pos", "rate", "taxable", "igst", "cgst", "sgst", "cess"])),
        ("cdnr", ["GSTIN of Recipient", "Receiver Name", "Note Number", "Note Date", "Note Type", "Place Of Supply", "Reverse Charge", "Note Supply Type",
                  "Note Value", "Rate", "Taxable Value", "Integrated Tax", "Central Tax", "State/UT Tax", "Original Invoice", "Original Invoice Date"],
         S(rep["cdnr"], ["gstin", "name", "note_number", "note_date", "note_type", "pos", "reverse_charge", "note_supply_type", "note_value", "rate", "taxable", "igst", "cgst", "sgst", "original_invoice", "original_date"])),
        ("cdnur", ["UR Type", "Note Number", "Note Date", "Note Type", "Place Of Supply", "Note Value", "Rate", "Taxable Value", "Integrated Tax", "Original Invoice"],
         S(rep["cdnur"], ["ur_type", "note_number", "note_date", "note_type", "pos", "note_value", "rate", "taxable", "igst", "original_invoice"])),
        ("exemp", ["Description", "Nil Rated Supplies", "Exempted (other than nil rated/non GST supply)", "Non-GST Supplies"],
         S(rep["nil"], ["description", "nil_rated", "exempted", "non_gst"])),
        ("hsn(b2b)", ["HSN", "Description", "UQC", "Total Quantity", "Total Value", "Rate", "Taxable Value", "Integrated Tax", "Central Tax", "State/UT Tax", "Cess Amount"],
         S(rep["hsn_b2b"], ["hsn", "description", "uqc", "qty", "total_value", "rate", "taxable", "igst", "cgst", "sgst", "cess"])),
        ("hsn(b2c)", ["HSN", "Description", "UQC", "Total Quantity", "Total Value", "Rate", "Taxable Value", "Integrated Tax", "Central Tax", "State/UT Tax", "Cess Amount"],
         S(rep["hsn_b2c"], ["hsn", "description", "uqc", "qty", "total_value", "rate", "taxable", "igst", "cgst", "sgst", "cess"])),
        ("docs", ["Nature of Document", "Sr. No. From", "Sr. No. To", "Total Number", "Cancelled", "Net Issued"],
         S(rep["docs"], ["nature", "from", "to", "total", "cancelled", "net_issued"])),
    ]


def gstr3b_sheets(rep: dict):
    o, n, a, rd, p = rep["outward"], rep["itc_net"], rep["itc_available"], rep["itc_reduced"], rep["payment"]
    rows = [["3.1(a) Outward taxable supplies (other than zero rated, nil rated and exempted)", o["taxable"], o["igst"], o["cgst"], o["sgst"], 0],
            ["3.1(c) Other outward supplies (nil rated, exempted)", rep["nil_outward"], 0, 0, 0, 0],
            ["4(A)(5) All other ITC", "", a["igst"], a["cgst"], a["sgst"], 0],
            ["Less: ITC reduced by debit notes / purchase returns", "", -rd["igst"], -rd["cgst"], -rd["sgst"], 0],
            ["4(C) Net ITC available", "", n["igst"], n["cgst"], n["sgst"], 0],
            ["5 Inward exempt / nil rated supplies (inter-state)", rep["inward_nil"]["inter"], "", "", "", ""],
            ["5 Inward exempt / nil rated supplies (intra-state)", rep["inward_nil"]["intra"], "", "", "", ""],
            ["6.1 Tax payable in cash (estimate after ITC set-off)", "", p["cash"]["igst"], p["cash"]["cgst"], p["cash"]["sgst"], 0],
            ["ITC carried forward (estimate)", "", p["carry_forward"]["igst"], p["carry_forward"]["cgst"], p["carry_forward"]["sgst"], 0]]
    return [("GSTR-3B", ["Section", "Taxable value", "Integrated Tax", "Central Tax", "State/UT Tax", "Cess"], rows),
            ("3.2 Unregistered inter-state", ["Place of Supply", "Taxable value", "Integrated Tax"], [[r["pos"], r["taxable"], r["igst"]] for r in rep["unregistered_inter"]]),
            ("Notes", ["Note"], [[f"GSTIN: {rep['gstin']} · {rep['business']} · Period {rep['from']} to {rep['to']}"]] + [[w] for w in rep["warnings"]]
             + [["Prepared by HisabFlow from saved records. Verify against GSTR-2B and your books before filing."]])]


# ------------------------------------------------------------------ flat GST sales register (one row per invoice item)
REGISTER_HEADERS = ["GSTIN", "Date", "Customer Name", "Invoice ID", "Taxable Value", "CGST", "SGST", "IGST",
                    "Total Invoice Value", "HSN Code", "Item Name"]


def sales_register(con, start: str, end: str) -> dict:
    """Invoice items and credit-note items in the period, in the simple register layout used by many accountants."""
    def build(head_sql, item_sql, number_key, date_key, sign):
        rows, totals, seen = [], {"taxable": 0.0, "cgst": 0.0, "sgst": 0.0, "igst": 0.0, "value": 0.0}, set()
        for doc in _rows(con, head_sql, (start, end)):
            cust = json.loads(doc["customer_snapshot"] or "{}")
            for it in _rows(con, item_sql, (doc["id"],)):
                vals = [sign * r2(it[k]) + 0.0 for k in ("taxable", "cgst", "sgst", "igst")]  # + 0.0 avoids "-0.00"
                rows.append([(cust.get("gstin") or "").strip().upper(), date.fromisoformat(doc[date_key]), cust.get("name", ""), doc[number_key],
                             *vals, sign * r2(doc["total"]) + 0.0, (it.get("hsn") or "").strip(), it["description"]]
                            + ([doc["invoice_number"]] if sign < 0 else []))
                for k, v in zip(("taxable", "cgst", "sgst", "igst"), vals): totals[k] += v
            if doc["id"] not in seen: seen.add(doc["id"]); totals["value"] += sign * float(doc["total"])
        return rows, {k: r2(v) for k, v in totals.items()}

    inv_rows, inv_tot = build("SELECT * FROM invoices WHERE invoice_date BETWEEN ? AND ? ORDER BY invoice_date, id",
                              "SELECT * FROM invoice_items WHERE invoice_id=? ORDER BY id", "invoice_number", "invoice_date", 1)
    cn_rows, cn_tot = build("SELECT cn.*, i.invoice_number FROM credit_notes cn JOIN invoices i ON i.id=cn.invoice_id WHERE cn.note_date BETWEEN ? AND ? ORDER BY cn.note_date, cn.id",
                            "SELECT * FROM credit_note_items WHERE credit_note_id=? ORDER BY id", "note_number", "note_date", -1)
    return {"from": start, "to": end, "invoices": inv_rows, "invoice_totals": inv_tot, "credit_notes": cn_rows, "credit_totals": cn_tot}


def register_sheets(reg: dict):
    def total_row(t, extra=0):
        return ["Total", "", "", "", t["taxable"], t["cgst"], t["sgst"], t["igst"], t["value"], "", ""] + [""] * extra
    sheets = [("Sales register", REGISTER_HEADERS, reg["invoices"] + ([total_row(reg["invoice_totals"])] if reg["invoices"] else []), {"bold_last": bool(reg["invoices"])})]
    if reg["credit_notes"]:
        sheets.append(("Credit notes", REGISTER_HEADERS[:3] + ["Credit Note ID"] + REGISTER_HEADERS[4:] + ["Against Invoice"],
                       reg["credit_notes"] + [total_row(reg["credit_totals"], 1)], {"bold_last": True}))
    return sheets


# ------------------------------------------------------------------ minimal .xlsx writer (stdlib only)
_BAD_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _col(n: int) -> str:
    s = ""
    while n: n, r = divmod(n - 1, 26); s = chr(65 + r) + s
    return s


def to_xlsx(sheets) -> bytes:
    """sheets: [(name, headers, rows)] -> bytes of an .xlsx workbook (bold header row, frozen, number format)."""
    buf = io.BytesIO(); used = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        names = []
        sheets = [tuple(sh) + ({},) * (4 - len(sh)) for sh in sheets]
        for name, _, _, _ in sheets:
            n = re.sub(r"[\[\]\*\?/\\:]", " ", name)[:31] or "Sheet"
            while n in used: n = (n[:28] + str(len(used)))[:31]
            used.add(n); names.append(n)
        z.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
                    + "".join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(1, len(sheets) + 1)) + "</Types>")
        z.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr("xl/workbook.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
                    + "".join(f'<sheet name="{escape(n, {chr(34): "&quot;"})}" sheetId="{i}" r:id="rId{i}"/>' for i, n in enumerate(names, 1)) + "</sheets></workbook>")
        z.writestr("xl/_rels/workbook.xml.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                    + "".join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1, len(sheets) + 1))
                    + f'<Relationship Id="rId{len(sheets)+1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
        z.writestr("xl/styles.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><numFmts count="2"><numFmt numFmtId="164" formatCode="#,##0.00"/><numFmt numFmtId="165" formatCode="dd-mm-yyyy"/></numFmts><fonts count="3"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts><fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF4F46E5"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="6"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/><xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/><xf numFmtId="165" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/><xf numFmtId="164" fontId="2" fillId="0" borderId="0" xfId="0" applyNumberFormat="1" applyFont="1"/><xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')
        for i, (_, headers, rows, opts) in enumerate(sheets, 1):
            rows = list(rows); last = len(rows) + 1 if opts.get("bold_last") else -1
            widths = [max(10, min(48, len(str(h)) + 2)) for h in headers]
            out = []
            for r_i, row in enumerate([headers] + list(rows), 1):
                cells = []
                for c_i, v in enumerate(row, 1):
                    ref = f"{_col(c_i)}{r_i}"
                    if c_i <= len(widths): widths[c_i - 1] = max(widths[c_i - 1], min(48, len(str(v if v is not None else "")) + 2))
                    if r_i == 1:
                        cells.append(f'<c r="{ref}" t="inlineStr" s="1"><is><t xml:space="preserve">{escape(_BAD_XML.sub("", str(v)))}</t></is></c>')
                    elif isinstance(v, date):
                        cells.append(f'<c r="{ref}" s="3"><v>{(v - date(1899, 12, 30)).days}</v></c>')
                    elif isinstance(v, (int, float)) and not isinstance(v, bool):
                        cells.append(f'<c r="{ref}" s="{4 if r_i == last else 2}"><v>{v}</v></c>')
                    elif v not in (None, ""):
                        cells.append(f'<c r="{ref}" t="inlineStr"{' s="5"' if r_i == last else ''}><is><t xml:space="preserve">{escape(_BAD_XML.sub("", str(v)))}</t></is></c>')
                out.append(f'<row r="{r_i}">{"".join(cells)}</row>')
            cols = "".join(f'<col min="{n}" max="{n}" width="{w}" customWidth="1"/>' for n, w in enumerate(widths, 1))
            z.writestr(f"xl/worksheets/sheet{i}.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
                        + (f"<cols>{cols}</cols>" if cols else "") + f'<sheetData>{"".join(out)}</sheetData></worksheet>')
    return buf.getvalue()
