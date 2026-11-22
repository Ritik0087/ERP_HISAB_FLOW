#!/usr/bin/env python3
"""HisabFlow - offline-first business management for Indian micro businesses.
Local-only Windows prototype. Uses Python stdlib + vendored QR-code generator.
"""
from __future__ import annotations
import json, os, re, sqlite3, sys, threading, webbrowser, uuid, hashlib, hmac, secrets, time
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, quote as url_quote
import gst

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "hisabflow.db"
BACKUP_DIR = DATA_DIR / "auto_backups"
COMPANY_DIR = DATA_DIR / "companies"
ARCHIVE_DIR = DATA_DIR / "archived_companies"
UI_DIR = BASE_DIR / "ui"
HOST = "127.0.0.1"
PORT = int(os.environ.get("HISABFLOW_PORT", "8765"))
APP_VERSION = "4.0"

STATES = ["Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh", "Goa", "Gujarat", "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka", "Kerala", "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya", "Mizoram", "Nagaland", "Odisha", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana", "Tripura", "Uttar Pradesh", "Uttarakhand", "West Bengal", "Andaman and Nicobar Islands", "Chandigarh", "Dadra and Nagar Haveli and Daman and Diu", "Delhi", "Jammu and Kashmir", "Ladakh", "Lakshadweep", "Puducherry"]


def money(value) -> float:
    try:
        d = Decimal(str(value if value not in (None, "") else "0"))
        if not d.is_finite(): raise ValueError
        return float(d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError("Enter a valid amount")


def quantity(value) -> float:
    try:
        d = Decimal(str(value if value not in (None, "") else "0"))
        if not d.is_finite() or d <= 0: raise ValueError
        return float(d.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError("Quantity must be greater than zero")


def stock_qty(value, allow_negative=False) -> float:
    """Stock quantities keep 3 decimals (same precision as invoice quantities)."""
    try:
        d = Decimal(str(value if value not in (None, "") else "0"))
        if not d.is_finite() or (d < 0 and not allow_negative): raise ValueError
        return float(d.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError("Enter a valid quantity")


def to_int(value, label="record"):
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValueError(f"Select a valid {label}")


_ctx = threading.local()


def current_db_path():
    """Database of the company selected for the current request (company 1 lives in the master file)."""
    return getattr(_ctx, "db_path", None) or DB_PATH


@contextmanager
def db(path=None):
    """Open a connection with foreign keys on; commit on success, roll back on error, always close."""
    con = sqlite3.connect(path or current_db_path(), timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    try:
        yield con
        con.commit()
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()


def master_db():
    """Users, sessions and the company list always live in the master database."""
    return db(DB_PATH)


def company_path(file_name: str) -> Path:
    return DB_PATH if file_name == DB_PATH.name else COMPANY_DIR / file_name


def next_number(con, table, column, prefix, width=4):
    """Next sequence for numbers like PREFIX0001. Uses the highest existing number, not COUNT(*),
    so gaps (e.g. after restoring a backup or changing a prefix) can never produce duplicates."""
    best = 0
    for (value,) in con.execute(f"SELECT {column} FROM {table} WHERE {column} LIKE ?", (prefix.replace("%", "") + "%",)):
        tail = str(value)[len(prefix):]
        if tail.isdigit(): best = max(best, int(tail))
    return f"{prefix}{best+1:0{width}d}"


# ---------------------------------------------------------------- authentication
PBKDF2_ROUNDS = 240_000
SESSION_COOKIE = "hf_session"
SESSION_HOURS = 12
REMEMBER_DAYS = 30
_login_failures: dict[str, list[float]] = {}
_login_lock = threading.Lock()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${PBKDF2_ROUNDS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, rounds, salt_hex, digest_hex = stored.split("$")
        if algo != "pbkdf2_sha256": return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(rounds))
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, AttributeError):
        return False


def validate_password(password: str):
    if len(password) < 6: raise ValueError("Password must be at least 6 characters")
    if len(password) > 128: raise ValueError("Password is too long")


def validate_username(username: str) -> str:
    username = username.strip()
    if not re.fullmatch(r"[A-Za-z0-9_.@-]{3,40}", username):
        raise ValueError("Username must be 3–40 characters: letters, numbers, . _ - @")
    return username


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def login_blocked(key: str) -> int:
    """Return seconds remaining if this username is temporarily locked after repeated failures."""
    with _login_lock:
        now = time.time(); recent = [t for t in _login_failures.get(key, []) if now - t < 300]
        _login_failures[key] = recent
        if len(recent) >= 5: return int(300 - (now - recent[0])) + 1
        return 0


def record_login_failure(key: str):
    with _login_lock: _login_failures.setdefault(key, []).append(time.time())


def clear_login_failures(key: str):
    with _login_lock: _login_failures.pop(key, None)


def public_user(u):
    return {k: u[k] for k in ("id", "username", "display_name", "role", "active", "created_at", "last_login")} if u else None


def company_for_session(company_id):
    with master_db() as con:
        c = one(con, "SELECT * FROM companies WHERE id=?", (company_id,)) or one(con, "SELECT * FROM companies WHERE id=1")
    path = company_path(c["db_file"])
    if not path.exists(): c = {"id": 1, "db_file": DB_PATH.name, "name": ""}; path = DB_PATH
    return c, path


def rows(con, sql, args=()):
    con.row_factory = sqlite3.Row
    return [dict(r) for r in con.execute(sql, args).fetchall()]


def one(con, sql, args=()):
    con.row_factory = sqlite3.Row
    r = con.execute(sql, args).fetchone()
    return dict(r) if r else None


def add_column(con, table, name, definition):
    existing = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
    if name not in existing:
        con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def init_company_schema(con):
    """Create or upgrade all business tables inside one company database."""
    con.executescript("""
        CREATE TABLE IF NOT EXISTS business (id INTEGER PRIMARY KEY CHECK(id=1), name TEXT NOT NULL DEFAULT '', gstin TEXT NOT NULL DEFAULT '', state TEXT NOT NULL DEFAULT '', address TEXT NOT NULL DEFAULT '', phone TEXT NOT NULL DEFAULT '', email TEXT NOT NULL DEFAULT '', invoice_prefix TEXT NOT NULL DEFAULT 'INV', bank_details TEXT NOT NULL DEFAULT '', upi_id TEXT NOT NULL DEFAULT '', logo_data TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        INSERT OR IGNORE INTO business(id) VALUES(1);
        CREATE TABLE IF NOT EXISTS customers (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, phone TEXT NOT NULL DEFAULT '', email TEXT NOT NULL DEFAULT '', gstin TEXT NOT NULL DEFAULT '', address TEXT NOT NULL DEFAULT '', state TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS suppliers (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, phone TEXT NOT NULL DEFAULT '', email TEXT NOT NULL DEFAULT '', gstin TEXT NOT NULL DEFAULT '', address TEXT NOT NULL DEFAULT '', state TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS products (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, hsn TEXT NOT NULL DEFAULT '', unit TEXT NOT NULL DEFAULT 'pcs', rate REAL NOT NULL DEFAULT 0, gst_rate REAL NOT NULL DEFAULT 0, purchase_rate REAL NOT NULL DEFAULT 0, stock_qty REAL NOT NULL DEFAULT 0, low_stock_level REAL NOT NULL DEFAULT 0, track_stock INTEGER NOT NULL DEFAULT 0, barcode TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS invoices (id INTEGER PRIMARY KEY AUTOINCREMENT, invoice_number TEXT NOT NULL UNIQUE, invoice_date TEXT NOT NULL, customer_id INTEGER NOT NULL, business_snapshot TEXT NOT NULL, customer_snapshot TEXT NOT NULL, place_of_supply TEXT NOT NULL DEFAULT '', supply_type TEXT NOT NULL DEFAULT 'CGST/SGST', subtotal REAL NOT NULL DEFAULT 0, cgst REAL NOT NULL DEFAULT 0, sgst REAL NOT NULL DEFAULT 0, igst REAL NOT NULL DEFAULT 0, total REAL NOT NULL DEFAULT 0, notes TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY(customer_id) REFERENCES customers(id) ON DELETE RESTRICT);
        CREATE TABLE IF NOT EXISTS invoice_items (id INTEGER PRIMARY KEY AUTOINCREMENT, invoice_id INTEGER NOT NULL, product_id INTEGER, description TEXT NOT NULL, hsn TEXT NOT NULL DEFAULT '', unit TEXT NOT NULL DEFAULT 'pcs', qty REAL NOT NULL, rate REAL NOT NULL, gst_rate REAL NOT NULL, taxable REAL NOT NULL, cgst REAL NOT NULL, sgst REAL NOT NULL, igst REAL NOT NULL, line_total REAL NOT NULL, cost_rate REAL NOT NULL DEFAULT 0, FOREIGN KEY(invoice_id) REFERENCES invoices(id) ON DELETE CASCADE, FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE SET NULL);
        CREATE TABLE IF NOT EXISTS payments (id INTEGER PRIMARY KEY AUTOINCREMENT, invoice_id INTEGER NOT NULL, payment_date TEXT NOT NULL, amount REAL NOT NULL, method TEXT NOT NULL DEFAULT 'Cash', reference TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY(invoice_id) REFERENCES invoices(id) ON DELETE RESTRICT);
        CREATE TABLE IF NOT EXISTS purchases (id INTEGER PRIMARY KEY AUTOINCREMENT, purchase_number TEXT NOT NULL UNIQUE, purchase_date TEXT NOT NULL, supplier_id INTEGER NOT NULL, supplier_snapshot TEXT NOT NULL, subtotal REAL NOT NULL DEFAULT 0, tax REAL NOT NULL DEFAULT 0, total REAL NOT NULL DEFAULT 0, notes TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY(supplier_id) REFERENCES suppliers(id) ON DELETE RESTRICT);
        CREATE TABLE IF NOT EXISTS purchase_items (id INTEGER PRIMARY KEY AUTOINCREMENT, purchase_id INTEGER NOT NULL, product_id INTEGER, description TEXT NOT NULL, qty REAL NOT NULL, rate REAL NOT NULL, gst_rate REAL NOT NULL DEFAULT 0, taxable REAL NOT NULL, tax REAL NOT NULL, line_total REAL NOT NULL, FOREIGN KEY(purchase_id) REFERENCES purchases(id) ON DELETE CASCADE, FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE SET NULL);
        CREATE TABLE IF NOT EXISTS supplier_payments (id INTEGER PRIMARY KEY AUTOINCREMENT, purchase_id INTEGER NOT NULL, payment_date TEXT NOT NULL, amount REAL NOT NULL, method TEXT NOT NULL DEFAULT 'Cash', reference TEXT NOT NULL DEFAULT '', FOREIGN KEY(purchase_id) REFERENCES purchases(id) ON DELETE RESTRICT);
        CREATE TABLE IF NOT EXISTS purchase_returns (id INTEGER PRIMARY KEY AUTOINCREMENT, return_number TEXT NOT NULL UNIQUE, return_date TEXT NOT NULL, purchase_id INTEGER NOT NULL, supplier_id INTEGER NOT NULL, amount REAL NOT NULL, notes TEXT NOT NULL DEFAULT '', FOREIGN KEY(purchase_id) REFERENCES purchases(id) ON DELETE RESTRICT, FOREIGN KEY(supplier_id) REFERENCES suppliers(id) ON DELETE RESTRICT);
        CREATE TABLE IF NOT EXISTS purchase_return_items (id INTEGER PRIMARY KEY AUTOINCREMENT, return_id INTEGER NOT NULL, product_id INTEGER, description TEXT NOT NULL, qty REAL NOT NULL, rate REAL NOT NULL, gst_rate REAL NOT NULL DEFAULT 0, amount REAL NOT NULL, FOREIGN KEY(return_id) REFERENCES purchase_returns(id) ON DELETE CASCADE, FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE SET NULL);
        CREATE TABLE IF NOT EXISTS expenses (id INTEGER PRIMARY KEY AUTOINCREMENT, expense_date TEXT NOT NULL, category TEXT NOT NULL, description TEXT NOT NULL, amount REAL NOT NULL, method TEXT NOT NULL DEFAULT 'Cash', reference TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS stock_movements (id INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER NOT NULL, movement_date TEXT NOT NULL, movement_type TEXT NOT NULL, qty_in REAL NOT NULL DEFAULT 0, qty_out REAL NOT NULL DEFAULT 0, reference TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '', FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE RESTRICT);
        CREATE TABLE IF NOT EXISTS quotations (id INTEGER PRIMARY KEY AUTOINCREMENT, quote_number TEXT NOT NULL UNIQUE, quote_date TEXT NOT NULL, customer_id INTEGER NOT NULL, customer_snapshot TEXT NOT NULL, subtotal REAL NOT NULL DEFAULT 0, cgst REAL NOT NULL DEFAULT 0, sgst REAL NOT NULL DEFAULT 0, igst REAL NOT NULL DEFAULT 0, total REAL NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'Draft', notes TEXT NOT NULL DEFAULT '', converted_invoice_id INTEGER, FOREIGN KEY(customer_id) REFERENCES customers(id) ON DELETE RESTRICT);
        CREATE TABLE IF NOT EXISTS quotation_items (id INTEGER PRIMARY KEY AUTOINCREMENT, quotation_id INTEGER NOT NULL, product_id INTEGER, description TEXT NOT NULL, hsn TEXT NOT NULL DEFAULT '', unit TEXT NOT NULL DEFAULT 'pcs', qty REAL NOT NULL, rate REAL NOT NULL, gst_rate REAL NOT NULL, taxable REAL NOT NULL, cgst REAL NOT NULL, sgst REAL NOT NULL, igst REAL NOT NULL, line_total REAL NOT NULL, FOREIGN KEY(quotation_id) REFERENCES quotations(id) ON DELETE CASCADE, FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE SET NULL);
        CREATE TABLE IF NOT EXISTS warranties (id INTEGER PRIMARY KEY AUTOINCREMENT, warranty_code TEXT NOT NULL UNIQUE, customer_id INTEGER NOT NULL, product_id INTEGER, invoice_id INTEGER, serial_number TEXT NOT NULL DEFAULT '', purchase_date TEXT NOT NULL, warranty_months INTEGER NOT NULL, expiry_date TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'Active', notes TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY(customer_id) REFERENCES customers(id) ON DELETE RESTRICT, FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE SET NULL, FOREIGN KEY(invoice_id) REFERENCES invoices(id) ON DELETE SET NULL);
        CREATE TABLE IF NOT EXISTS recurring_templates (id INTEGER PRIMARY KEY AUTOINCREMENT, template_name TEXT NOT NULL, customer_id INTEGER NOT NULL, frequency TEXT NOT NULL, next_due_date TEXT NOT NULL, payload_json TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, last_generated_at TEXT NOT NULL DEFAULT '', FOREIGN KEY(customer_id) REFERENCES customers(id) ON DELETE RESTRICT);
        CREATE INDEX IF NOT EXISTS idx_invoices_date ON invoices(invoice_date);
        CREATE INDEX IF NOT EXISTS idx_payments_invoice ON payments(invoice_id);
        CREATE INDEX IF NOT EXISTS idx_stock_product ON stock_movements(product_id);
        """)
    # Safe schema upgrades for existing v0.1 installations.
    add_column(con, "products", "purchase_rate", "REAL NOT NULL DEFAULT 0")
    add_column(con, "products", "stock_qty", "REAL NOT NULL DEFAULT 0")
    add_column(con, "products", "low_stock_level", "REAL NOT NULL DEFAULT 0")
    add_column(con, "products", "track_stock", "INTEGER NOT NULL DEFAULT 0")
    add_column(con, "invoice_items", "cost_rate", "REAL NOT NULL DEFAULT 0")
    add_column(con, "purchase_return_items", "gst_rate", "REAL NOT NULL DEFAULT 0")
    add_column(con, "business", "logo_data", "TEXT NOT NULL DEFAULT ''")
    add_column(con, "products", "barcode", "TEXT NOT NULL DEFAULT ''")
    con.executescript("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_products_barcode ON products(barcode) WHERE barcode <> '';
        CREATE TABLE IF NOT EXISTS employees (
          id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, phone TEXT NOT NULL DEFAULT '',
          role TEXT NOT NULL DEFAULT '', join_date TEXT NOT NULL DEFAULT '', monthly_salary REAL NOT NULL DEFAULT 0,
          active INTEGER NOT NULL DEFAULT 1, notes TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS payroll (
          id INTEGER PRIMARY KEY AUTOINCREMENT, employee_id INTEGER NOT NULL, payroll_month TEXT NOT NULL,
          basic_salary REAL NOT NULL DEFAULT 0, allowances REAL NOT NULL DEFAULT 0, deductions REAL NOT NULL DEFAULT 0,
          net_pay REAL NOT NULL DEFAULT 0, notes TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(employee_id, payroll_month), FOREIGN KEY(employee_id) REFERENCES employees(id) ON DELETE RESTRICT
        );
        CREATE TABLE IF NOT EXISTS payroll_payments (
          id INTEGER PRIMARY KEY AUTOINCREMENT, payroll_id INTEGER NOT NULL, payment_date TEXT NOT NULL,
          amount REAL NOT NULL, method TEXT NOT NULL DEFAULT 'Bank transfer', reference TEXT NOT NULL DEFAULT '',
          FOREIGN KEY(payroll_id) REFERENCES payroll(id) ON DELETE RESTRICT
        );
        CREATE TABLE IF NOT EXISTS cash_entries (
          id INTEGER PRIMARY KEY AUTOINCREMENT, entry_date TEXT NOT NULL, direction TEXT NOT NULL,
          category TEXT NOT NULL, description TEXT NOT NULL, amount REAL NOT NULL,
          method TEXT NOT NULL DEFAULT 'Cash', reference TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_payroll_month ON payroll(payroll_month);
        CREATE INDEX IF NOT EXISTS idx_cash_entries_date ON cash_entries(entry_date);
        CREATE TABLE IF NOT EXISTS credit_notes (
          id INTEGER PRIMARY KEY AUTOINCREMENT, note_number TEXT NOT NULL UNIQUE, note_date TEXT NOT NULL,
          invoice_id INTEGER NOT NULL, customer_id INTEGER NOT NULL, customer_snapshot TEXT NOT NULL, business_snapshot TEXT NOT NULL,
          place_of_supply TEXT NOT NULL DEFAULT '', supply_type TEXT NOT NULL DEFAULT 'CGST/SGST', reason TEXT NOT NULL DEFAULT 'Sales return',
          subtotal REAL NOT NULL DEFAULT 0, cgst REAL NOT NULL DEFAULT 0, sgst REAL NOT NULL DEFAULT 0, igst REAL NOT NULL DEFAULT 0,
          total REAL NOT NULL DEFAULT 0, restock INTEGER NOT NULL DEFAULT 1, refund_amount REAL NOT NULL DEFAULT 0,
          refund_method TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY(invoice_id) REFERENCES invoices(id) ON DELETE RESTRICT, FOREIGN KEY(customer_id) REFERENCES customers(id) ON DELETE RESTRICT
        );
        CREATE TABLE IF NOT EXISTS credit_note_items (
          id INTEGER PRIMARY KEY AUTOINCREMENT, credit_note_id INTEGER NOT NULL, invoice_item_id INTEGER, product_id INTEGER,
          description TEXT NOT NULL, hsn TEXT NOT NULL DEFAULT '', unit TEXT NOT NULL DEFAULT 'pcs', qty REAL NOT NULL, rate REAL NOT NULL,
          gst_rate REAL NOT NULL DEFAULT 0, taxable REAL NOT NULL, cgst REAL NOT NULL DEFAULT 0, sgst REAL NOT NULL DEFAULT 0,
          igst REAL NOT NULL DEFAULT 0, line_total REAL NOT NULL,
          FOREIGN KEY(credit_note_id) REFERENCES credit_notes(id) ON DELETE CASCADE, FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE SET NULL
        );
        CREATE INDEX IF NOT EXISTS idx_credit_notes_invoice ON credit_notes(invoice_id);
        CREATE INDEX IF NOT EXISTS idx_credit_notes_date ON credit_notes(note_date);
        DROP VIEW IF EXISTS invoice_balances;
        CREATE VIEW invoice_balances AS SELECT i.id invoice_id, i.total,
          COALESCE((SELECT SUM(amount) FROM payments WHERE invoice_id=i.id),0) paid,
          COALESCE((SELECT SUM(total) FROM credit_notes WHERE invoice_id=i.id),0) credited,
          COALESCE((SELECT SUM(refund_amount) FROM credit_notes WHERE invoice_id=i.id),0) refunded
          FROM invoices i;
        """)
    add_column(con, "business", "invoice_template", "TEXT NOT NULL DEFAULT 'modern'")
    add_column(con, "business", "invoice_color", "TEXT NOT NULL DEFAULT '#4f46e5'")
    add_column(con, "business", "invoice_terms", "TEXT NOT NULL DEFAULT ''")
    add_column(con, "business", "show_upi_qr", "INTEGER NOT NULL DEFAULT 1")
    add_column(con, "business", "show_hsn_summary", "INTEGER NOT NULL DEFAULT 1")
    add_column(con, "purchase_return_items", "taxable", "REAL NOT NULL DEFAULT 0")
    add_column(con, "purchase_return_items", "tax", "REAL NOT NULL DEFAULT 0")
    for table in ("invoice_items", "quotation_items", "credit_note_items", "products"):
        add_column(con, table, "details", "TEXT NOT NULL DEFAULT ''")


def init_db():
    DATA_DIR.mkdir(exist_ok=True); COMPANY_DIR.mkdir(exist_ok=True)
    with master_db() as con:
        init_company_schema(con)
        con.executescript("""
        CREATE TABLE IF NOT EXISTS users (
          id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
          display_name TEXT NOT NULL DEFAULT '', password_hash TEXT NOT NULL,
          role TEXT NOT NULL DEFAULT 'staff' CHECK(role IN ('owner','staff')), active INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, last_login TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS sessions (
          token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
          FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS companies (
          id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL DEFAULT '', db_file TEXT NOT NULL UNIQUE,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """)
        add_column(con, "sessions", "company_id", "INTEGER NOT NULL DEFAULT 1")
        con.execute("INSERT OR IGNORE INTO companies(id,name,db_file) VALUES(1,'',?)", (DB_PATH.name,))
        con.execute("DELETE FROM sessions WHERE expires_at < ?", (datetime.now().isoformat(timespec="seconds"),))
        companies = rows(con, "SELECT * FROM companies WHERE id<>1")
    for c in companies:
        path = company_path(c["db_file"])
        if path.exists():
            with db(path) as ccon: init_company_schema(ccon)
    for c in all_company_paths(): auto_backup(path=c[1], prefix=c[2])


def all_company_paths():
    """[(company_id, db_path, backup_prefix)] for every registered company whose file exists."""
    with master_db() as con: companies = rows(con, "SELECT * FROM companies ORDER BY id")
    out = []
    for c in companies:
        path = company_path(c["db_file"])
        if path.exists(): out.append((c["id"], path, "hisabflow" if c["id"] == 1 else f"company{c['id']}"))
    return out


def company_name(path) -> str:
    try:
        with db(path) as con: return (one(con, "SELECT name FROM business WHERE id=1") or {}).get("name") or ""
    except sqlite3.Error:
        return ""


def auto_backup(force=False, suffix="", path=None, prefix=None):
    """Create/refresh one local SQLite snapshot per company per day and keep the latest 14."""
    try:
        path = path or current_db_path()
        if prefix is None:
            prefix = next((c[2] for c in all_company_paths() if Path(c[1]) == Path(path)), "hisabflow")
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        stamp = date.today().isoformat() + (f"-{suffix}-{datetime.now():%H%M%S}" if suffix else "")
        target = BACKUP_DIR / f"{prefix}-{stamp}.db"
        if (target.exists() and not force) or not Path(path).exists(): return
        temp = target.with_suffix(".db.tmp")
        src = sqlite3.connect(path)
        dst = sqlite3.connect(temp)
        try: src.backup(dst)
        finally: dst.close(); src.close()
        temp.replace(target)
        backups = sorted(BACKUP_DIR.glob(f"{prefix}-????-??-??.db"))
        for old in backups[:-14]: old.unlink(missing_ok=True)
        extras = sorted(BACKUP_DIR.glob(f"{prefix}-*-pre-restore-*.db"))
        for old in extras[:-5]: old.unlink(missing_ok=True)
    except Exception:
        pass


def fy_label(iso_date: str) -> str:
    d = date.fromisoformat(iso_date); start = d.year if d.month >= 4 else d.year - 1
    return f"{start}-{str(start+1)[-2:]}"


def create_invoice_records(con, data):
    customer_id = to_int(data.get("customer_id"), "customer")
    inv_date = str(data.get("invoice_date") or date.today().isoformat()); date.fromisoformat(inv_date)
    incoming_items = data.get("items", [])
    if not isinstance(incoming_items, list) or not incoming_items: raise ValueError("Add at least one invoice item")
    business = one(con, "SELECT * FROM business WHERE id=1")
    customer = one(con, "SELECT * FROM customers WHERE id=?", (customer_id,))
    if not business or not business["name"]: raise ValueError("Complete your business setup before creating an invoice")
    if not customer: raise ValueError("Select a valid customer")
    if not business["gstin"] and any(money(x.get("gst_rate", 0)) > 0 for x in incoming_items):
        raise ValueError("Add your business GSTIN in Settings before applying GST, or set each item GST rate to 0%.")
    interstate = bool(customer["state"] and business["state"] and customer["state"] != business["state"])
    computed=[]; subtotal=cgst=sgst=igst=0.0; reserved_stock={}
    for item in incoming_items:
        desc=str(item.get("description", "")).strip()
        if not desc: raise ValueError("Each item needs a description")
        qty=quantity(item.get("qty", 1)); rate=money(item.get("rate", 0)); gst_rate=money(item.get("gst_rate", 0))
        if rate < 0 or gst_rate < 0 or gst_rate > 100: raise ValueError("Enter a valid item price and GST rate")
        product_id=int(item["product_id"]) if item.get("product_id") else None
        p=one(con,"SELECT * FROM products WHERE id=?",(product_id,)) if product_id else None
        if product_id and not p: raise ValueError("A selected product no longer exists")
        if p and p["track_stock"]:
            reserved_stock[product_id]=reserved_stock.get(product_id,0.0)+qty
            if reserved_stock[product_id]>float(p["stock_qty"])+0.0001:
                raise ValueError(f"Insufficient stock for {p['name']}. Available: {numfmt(p['stock_qty'])} {p['unit']}")
        cost_rate=float(p["purchase_rate"] or 0) if p else 0
        taxable=money(qty*rate); tax=money(taxable*gst_rate/100)
        lc=0 if interstate else money(tax/2); ls=0 if interstate else money(tax-lc); li=tax if interstate else 0
        lt=money(taxable+tax); subtotal=money(subtotal+taxable);cgst=money(cgst+lc);sgst=money(sgst+ls);igst=money(igst+li)
        computed.append({"product_id":product_id,"description":desc,"hsn":str(item.get("hsn", "")).strip(),"unit":str(item.get("unit","pcs")).strip() or "pcs","qty":qty,"rate":rate,"gst_rate":gst_rate,"taxable":taxable,"cgst":lc,"sgst":ls,"igst":li,"line_total":lt,"cost_rate":cost_rate,"details":item_details(item)})
    total=money(subtotal+cgst+sgst+igst); prefix=business["invoice_prefix"] or "INV"; fy=fy_label(inv_date)
    invoice_number=next_number(con,"invoices","invoice_number",f"{prefix}/{fy}/"); place=customer["state"] or business["state"]
    cur=con.execute("""INSERT INTO invoices(invoice_number,invoice_date,customer_id,business_snapshot,customer_snapshot,place_of_supply,supply_type,subtotal,cgst,sgst,igst,total,notes) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",(invoice_number,inv_date,customer_id,json.dumps(business),json.dumps(customer),place,"IGST" if interstate else "CGST/SGST",subtotal,cgst,sgst,igst,total,str(data.get("notes", "")).strip()))
    iid=cur.lastrowid
    for it in computed:
        con.execute("""INSERT INTO invoice_items(invoice_id,product_id,description,hsn,unit,qty,rate,gst_rate,taxable,cgst,sgst,igst,line_total,cost_rate,details) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(iid,it["product_id"],it["description"],it["hsn"],it["unit"],it["qty"],it["rate"],it["gst_rate"],it["taxable"],it["cgst"],it["sgst"],it["igst"],it["line_total"],it["cost_rate"],it["details"]))
        if it["product_id"]:
            p=one(con,"SELECT track_stock FROM products WHERE id=?",(it["product_id"],))
            if p and p["track_stock"]:
                con.execute("UPDATE products SET stock_qty=stock_qty-? WHERE id=?",(it["qty"],it["product_id"]))
                con.execute("INSERT INTO stock_movements(product_id,movement_date,movement_type,qty_in,qty_out,reference,notes) VALUES(?,?,?,?,?,?,?)",(it["product_id"],inv_date,"Sale",0,it["qty"],invoice_number,"Stock deducted on invoice"))
    return iid,invoice_number



def update_invoice_records(con, invoice_id, data):
    old=one(con,"SELECT * FROM invoices WHERE id=?",(invoice_id,))
    if not old: raise ValueError("Invoice not found")
    customer_id=to_int(data.get("customer_id"),"customer"); inv_date=str(data.get("invoice_date") or old["invoice_date"]); date.fromisoformat(inv_date)
    customer=one(con,"SELECT * FROM customers WHERE id=?",(customer_id,)); business=one(con,"SELECT * FROM business WHERE id=1")
    if not customer: raise ValueError("Select a valid customer")
    incoming_items=data.get("items",[])
    if not isinstance(incoming_items,list) or not incoming_items: raise ValueError("Add at least one invoice item")
    if one(con,"SELECT id FROM credit_notes WHERE invoice_id=? LIMIT 1",(invoice_id,)):
        raise ValueError("This invoice has a credit note. Issue another credit note or a new invoice instead of editing it.")
    paid=float(one(con,"SELECT COALESCE(SUM(amount),0) paid FROM payments WHERE invoice_id=?",(invoice_id,))["paid"])
    # Reverse the old invoice's stock effect before validating the replacement lines.
    old_items=rows(con,"SELECT * FROM invoice_items WHERE invoice_id=?",(invoice_id,))
    for it in old_items:
        if it["product_id"]:
            pr=one(con,"SELECT * FROM products WHERE id=?",(it["product_id"],))
            if pr and pr["track_stock"]:
                con.execute("UPDATE products SET stock_qty=stock_qty+? WHERE id=?",(it["qty"],it["product_id"]))
                con.execute("INSERT INTO stock_movements(product_id,movement_date,movement_type,qty_in,reference,notes) VALUES(?,?,?,?,?,?)",(it["product_id"],date.today().isoformat(),"Invoice edit reversal",it["qty"],old["invoice_number"],"Original invoice stock effect reversed during edit"))
    interstate=bool(customer["state"] and business["state"] and customer["state"]!=business["state"])
    if not business["gstin"] and any(money(x.get("gst_rate",0))>0 for x in incoming_items):
        raise ValueError("Add your business GSTIN in Settings before applying GST, or set each item GST rate to 0%.")
    computed=[]; subtotal=cgst=sgst=igst=0.0; reserved_stock={}
    for item in incoming_items:
        desc=str(item.get("description","")).strip()
        if not desc: raise ValueError("Each item needs a description")
        qty=quantity(item.get("qty",1)); rate=money(item.get("rate",0)); gst_rate=money(item.get("gst_rate",0))
        if rate<0 or gst_rate<0 or gst_rate>100: raise ValueError("Enter a valid item price and GST rate")
        product_id=int(item["product_id"]) if item.get("product_id") else None
        prod=one(con,"SELECT * FROM products WHERE id=?",(product_id,)) if product_id else None
        if product_id and not prod: raise ValueError("A selected product no longer exists")
        if prod and prod["track_stock"]:
            reserved_stock[product_id]=reserved_stock.get(product_id,0.0)+qty
            if reserved_stock[product_id]>float(prod["stock_qty"])+.0001: raise ValueError(f"Insufficient stock for {prod['name']}. Available: {numfmt(prod['stock_qty'])} {prod['unit']}")
        cost_rate=float(prod["purchase_rate"] or 0) if prod else 0
        taxable=money(qty*rate); tax=money(taxable*gst_rate/100); lc=0 if interstate else money(tax/2); ls=0 if interstate else money(tax-lc); li=tax if interstate else 0
        lt=money(taxable+tax); subtotal=money(subtotal+taxable); cgst=money(cgst+lc); sgst=money(sgst+ls); igst=money(igst+li)
        computed.append({"product_id":product_id,"description":desc,"hsn":str(item.get("hsn","")).strip(),"unit":str(item.get("unit","pcs")).strip() or "pcs","qty":qty,"rate":rate,"gst_rate":gst_rate,"taxable":taxable,"cgst":lc,"sgst":ls,"igst":li,"line_total":lt,"cost_rate":cost_rate,"details":item_details(item)})
    total=money(subtotal+cgst+sgst+igst)
    if paid>total+.009: raise ValueError(f"New invoice total cannot be lower than payments already received (₹{paid:,.2f})")
    place=customer["state"] or business["state"]
    con.execute("UPDATE invoices SET invoice_date=?,customer_id=?,business_snapshot=?,customer_snapshot=?,place_of_supply=?,supply_type=?,subtotal=?,cgst=?,sgst=?,igst=?,total=?,notes=? WHERE id=?",(inv_date,customer_id,json.dumps(business),json.dumps(customer),place,"IGST" if interstate else "CGST/SGST",subtotal,cgst,sgst,igst,total,str(data.get("notes","")).strip(),invoice_id))
    con.execute("DELETE FROM invoice_items WHERE invoice_id=?",(invoice_id,))
    for it in computed:
        con.execute("INSERT INTO invoice_items(invoice_id,product_id,description,hsn,unit,qty,rate,gst_rate,taxable,cgst,sgst,igst,line_total,cost_rate,details) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(invoice_id,it["product_id"],it["description"],it["hsn"],it["unit"],it["qty"],it["rate"],it["gst_rate"],it["taxable"],it["cgst"],it["sgst"],it["igst"],it["line_total"],it["cost_rate"],it["details"]))
        if it["product_id"]:
            pr=one(con,"SELECT track_stock FROM products WHERE id=?",(it["product_id"],))
            if pr and pr["track_stock"]:
                con.execute("UPDATE products SET stock_qty=stock_qty-? WHERE id=?",(it["qty"],it["product_id"]))
                con.execute("INSERT INTO stock_movements(product_id,movement_date,movement_type,qty_in,qty_out,reference,notes) VALUES(?,?,?,?,?,?,?)",(it["product_id"],inv_date,"Invoice edit",0,it["qty"],old["invoice_number"],"Stock deducted after editing invoice"))
    return old["invoice_number"]


def update_purchase_records(con, purchase_id, data):
    old=one(con,"SELECT * FROM purchases WHERE id=?",(purchase_id,))
    if not old: raise ValueError("Purchase not found")
    if one(con,"SELECT id FROM supplier_payments WHERE purchase_id=? LIMIT 1",(purchase_id,)) or one(con,"SELECT id FROM purchase_returns WHERE purchase_id=? LIMIT 1",(purchase_id,)):
        raise ValueError("This purchase has payments or returns. For audit safety, create an adjustment bill instead of editing it.")
    sid=to_int(data.get("supplier_id"),"supplier"); pdate=str(data.get("purchase_date") or old["purchase_date"]); date.fromisoformat(pdate); supplier=one(con,"SELECT * FROM suppliers WHERE id=?",(sid,))
    if not supplier: raise ValueError("Select a valid supplier")
    items=data.get("items",[])
    if not isinstance(items,list) or not items: raise ValueError("Add at least one purchase item")
    old_items=rows(con,"SELECT * FROM purchase_items WHERE purchase_id=?",(purchase_id,)); old_by={}
    for x in old_items:
        if x["product_id"]: old_by[x["product_id"]]=old_by.get(x["product_id"],0)+float(x["qty"])
    computed=[]; new_by={}; subtotal=tax=0.0
    for it in items:
        desc=str(it.get("description","")).strip(); qty=quantity(it.get("qty",0)); rate=money(it.get("rate",0)); gst=money(it.get("gst_rate",0)); pid=int(it["product_id"]) if it.get("product_id") else None
        if not desc or rate<0 or gst<0 or gst>100: raise ValueError("Enter valid purchase item details")
        if pid and not one(con,"SELECT id FROM products WHERE id=?",(pid,)): raise ValueError("A selected product no longer exists")
        base=money(qty*rate); t=money(base*gst/100); subtotal=money(subtotal+base); tax=money(tax+t); computed.append((pid,desc,qty,rate,gst,base,t,money(base+t)))
        if pid: new_by[pid]=new_by.get(pid,0)+qty
    # Only the net stock change is applied, preserving subsequent sales and adjustments.
    for pid in set(old_by)|set(new_by):
        pr=one(con,"SELECT * FROM products WHERE id=?",(pid,))
        if pr and pr["track_stock"]:
            delta=stock_qty(float(new_by.get(pid,0))-float(old_by.get(pid,0)),True); new_qty=stock_qty(float(pr["stock_qty"])+delta,True)
            if new_qty<-.0001: raise ValueError(f"Cannot reduce this bill: available stock for {pr['name']} is not enough to reverse the original receipt")
            con.execute("UPDATE products SET stock_qty=? WHERE id=?",(max(new_qty,0),pid))
            if abs(delta)>.0001: con.execute("INSERT INTO stock_movements(product_id,movement_date,movement_type,qty_in,qty_out,reference,notes) VALUES(?,?,?,?,?,?,?)",(pid,pdate,"Purchase edit",max(delta,0),max(-delta,0),old["purchase_number"],"Stock adjusted after editing purchase"))
    total=money(subtotal+tax)
    con.execute("UPDATE purchases SET purchase_date=?,supplier_id=?,supplier_snapshot=?,subtotal=?,tax=?,total=?,notes=? WHERE id=?",(pdate,sid,json.dumps(supplier),subtotal,tax,total,str(data.get("notes","")).strip(),purchase_id))
    con.execute("DELETE FROM purchase_items WHERE purchase_id=?",(purchase_id,))
    for pid,desc,qty,rate,gst,base,t,line in computed:
        con.execute("INSERT INTO purchase_items(purchase_id,product_id,description,qty,rate,gst_rate,taxable,tax,line_total) VALUES(?,?,?,?,?,?,?,?,?)",(purchase_id,pid,desc,qty,rate,gst,base,t,line))
        if pid: con.execute("UPDATE products SET purchase_rate=? WHERE id=?",(rate,pid))
    return old["purchase_number"]

def numfmt(v): return f"{float(v or 0):g}"


def item_details(item) -> str:
    """Optional sub-description under an item: serial/IMEI numbers, model, colour, batch, warranty..."""
    text = str((item or {}).get("details") or "").replace("\r\n", "\n").strip()
    if len(text) > 1000: raise ValueError("Item details must be 1000 characters or fewer")
    return text


def invoice_status(x) -> str:
    bal = money(x["total"] - x["paid"] - x.get("credited", 0) + x.get("refunded", 0))
    if x.get("credited", 0) >= x["total"] - .009 and abs(bal) <= .009: return "Returned"
    if bal < -.009: return "Refund due"
    if bal <= .009: return "Paid"
    return "Partial" if (x["paid"] > 0 or x.get("credited", 0) > 0) else "Unpaid"


def create_credit_note(con, data):
    """Credit note (sales return / price reduction) against one invoice. Restocks tracked items if asked."""
    iid = to_int(data.get("invoice_id"), "invoice")
    inv = one(con, "SELECT * FROM invoices WHERE id=?", (iid,))
    if not inv: raise ValueError("Invoice not found")
    nd = str(data.get("note_date") or date.today().isoformat()); date.fromisoformat(nd)
    if nd < inv["invoice_date"]: raise ValueError("Credit note date cannot be before the invoice date")
    items = data.get("items", [])
    if not isinstance(items, list) or not items: raise ValueError("Select at least one item to credit")
    inter = inv["supply_type"] == "IGST"; restock = 1 if data.get("restock", True) in (True, 1, "1", "true") else 0
    computed = []; sub = cg = sg = ig = 0.0
    for it in items:
        if money(it.get("qty", 0)) == 0: continue
        orig = one(con, "SELECT * FROM invoice_items WHERE id=? AND invoice_id=?", (to_int(it.get("invoice_item_id"), "invoice line"), iid))
        if not orig: raise ValueError("A selected line does not belong to this invoice")
        qty = quantity(it.get("qty")); rate = money(it.get("rate", orig["rate"]))
        if rate < 0 or rate > float(orig["rate"]) + .001: raise ValueError(f"Credit rate for {orig['description']} must be between 0 and the invoiced rate")
        done = one(con, "SELECT COALESCE(SUM(qty),0) q FROM credit_note_items WHERE invoice_item_id=?", (orig["id"],))["q"]
        if qty > float(orig["qty"]) - float(done) + .0001:
            raise ValueError(f"{orig['description']}: only {numfmt(float(orig['qty'])-float(done))} {orig['unit']} left to credit")
        taxable = money(qty * rate); tax = money(taxable * float(orig["gst_rate"]) / 100)
        c = 0 if inter else money(tax / 2); sg_ = 0 if inter else money(tax - c); i_ = tax if inter else 0
        sub = money(sub + taxable); cg = money(cg + c); sg = money(sg + sg_); ig = money(ig + i_)
        computed.append((orig, qty, rate, taxable, c, sg_, i_, money(taxable + tax)))
    if not computed: raise ValueError("Enter a quantity for at least one line")
    total = money(sub + cg + sg + ig)
    bal = one(con, "SELECT * FROM invoice_balances WHERE invoice_id=?", (iid,))
    after = money(inv["total"] - bal["paid"] - bal["credited"] - total + bal["refunded"])
    refund = money(data.get("refund_amount", 0))
    if refund < 0: raise ValueError("Refund cannot be negative")
    if refund > max(0, -after) + .009: raise ValueError(f"Refund cannot exceed the amount owed back to the customer (₹{max(0,-after):,.2f})")
    business = one(con, "SELECT * FROM business WHERE id=1")
    no = next_number(con, "credit_notes", "note_number", f"CN/{fy_label(nd)}/")
    cid = con.execute("INSERT INTO credit_notes(note_number,note_date,invoice_id,customer_id,customer_snapshot,business_snapshot,place_of_supply,supply_type,reason,subtotal,cgst,sgst,igst,total,restock,refund_amount,refund_method,notes) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (no, nd, iid, inv["customer_id"], inv["customer_snapshot"], json.dumps(business), inv["place_of_supply"], inv["supply_type"], str(data.get("reason") or "Sales return")[:80], sub, cg, sg, ig, total, restock, refund, str(data.get("refund_method") or "")[:40] if refund else "", str(data.get("notes", "")).strip())).lastrowid
    for orig, qty, rate, taxable, c, sg_, i_, lt in computed:
        con.execute("INSERT INTO credit_note_items(credit_note_id,invoice_item_id,product_id,description,hsn,unit,qty,rate,gst_rate,taxable,cgst,sgst,igst,line_total,details) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (cid, orig["id"], orig["product_id"], orig["description"], orig["hsn"], orig["unit"], qty, rate, orig["gst_rate"], taxable, c, sg_, i_, lt, orig["details"]))
        if restock and orig["product_id"]:
            pr = one(con, "SELECT track_stock FROM products WHERE id=?", (orig["product_id"],))
            if pr and pr["track_stock"]:
                con.execute("UPDATE products SET stock_qty=stock_qty+? WHERE id=?", (qty, orig["product_id"]))
                con.execute("INSERT INTO stock_movements(product_id,movement_date,movement_type,qty_in,reference,notes) VALUES(?,?,?,?,?,?)", (orig["product_id"], nd, "Sales return", qty, no, "Returned by customer (credit note)"))
    return cid, no


def qr_svg(payload: str, box_size=7) -> bytes:
    vendor = str(BASE_DIR / "vendor")
    if vendor not in sys.path: sys.path.insert(0, vendor)
    import qrcode
    from qrcode.image.svg import SvgPathImage
    img = qrcode.make(payload, image_factory=SvgPathImage, box_size=box_size, border=3)
    return img.to_string(encoding="unicode").encode("utf-8")


def friendly_integrity(e) -> str:
    msg=str(e)
    if "products.barcode" in msg: return "Another item already uses this barcode / SKU"
    if "payroll.employee_id" in msg: return "Payroll for this employee and month already exists. Edit the existing entry instead."
    if "users.username" in msg: return "That username is already taken"
    if "FOREIGN KEY" in msg: return "A linked record (customer, product, invoice…) does not exist or is still in use"
    if "UNIQUE" in msg: return "A record with the same number already exists. Please try again."
    return f"Could not save: {msg}"


def state_data():
    with db() as con:
        con.execute("PRAGMA foreign_keys=ON")
        business=one(con,"SELECT * FROM business WHERE id=1")
        customers=rows(con,"SELECT * FROM customers ORDER BY name COLLATE NOCASE")
        suppliers=rows(con,"SELECT * FROM suppliers ORDER BY name COLLATE NOCASE")
        products=rows(con,"SELECT * FROM products ORDER BY name COLLATE NOCASE")
        invoices=rows(con,"""SELECT i.*,c.name customer_name,c.phone customer_phone,b.paid,b.credited,b.refunded,COALESCE((SELECT SUM(ii.qty*ii.cost_rate) FROM invoice_items ii WHERE ii.invoice_id=i.id),0) cost_of_goods FROM invoices i JOIN customers c ON c.id=i.customer_id JOIN invoice_balances b ON b.invoice_id=i.id ORDER BY i.invoice_date DESC,i.id DESC""")
        for x in invoices:
            x["balance"]=money(x["total"]-x["paid"]-x["credited"]+x["refunded"]);x["status"]=invoice_status(x)
        payments=rows(con,"SELECT p.*,i.invoice_number,c.name customer_name,c.phone customer_phone FROM payments p JOIN invoices i ON i.id=p.invoice_id JOIN customers c ON c.id=i.customer_id ORDER BY p.payment_date DESC,p.id DESC")
        invoice_items=rows(con,"SELECT ii.*,i.invoice_date FROM invoice_items ii JOIN invoices i ON i.id=ii.invoice_id ORDER BY i.invoice_date DESC,ii.id DESC")
        purchases=rows(con,"""SELECT pu.*,s.name supplier_name,COALESCE((SELECT SUM(sp.amount) FROM supplier_payments sp WHERE sp.purchase_id=pu.id),0) paid,COALESCE((SELECT SUM(pr.amount) FROM purchase_returns pr WHERE pr.purchase_id=pu.id),0) returned FROM purchases pu JOIN suppliers s ON s.id=pu.supplier_id ORDER BY pu.purchase_date DESC,pu.id DESC""")
        for x in purchases: x["balance"]=money(x["total"]-x["paid"]-x["returned"])
        supplier_payments=rows(con,"SELECT sp.*,pu.purchase_number,s.name supplier_name FROM supplier_payments sp JOIN purchases pu ON pu.id=sp.purchase_id JOIN suppliers s ON s.id=pu.supplier_id ORDER BY sp.payment_date DESC,sp.id DESC")
        expenses=rows(con,"SELECT * FROM expenses ORDER BY expense_date DESC,id DESC")
        quotations=rows(con,"SELECT q.*,c.name customer_name FROM quotations q JOIN customers c ON c.id=q.customer_id ORDER BY q.quote_date DESC,q.id DESC")
        warranties=rows(con,"SELECT w.*,c.name customer_name,c.phone customer_phone,p.name product_name FROM warranties w JOIN customers c ON c.id=w.customer_id LEFT JOIN products p ON p.id=w.product_id ORDER BY w.id DESC")
        recurring=rows(con,"SELECT r.*,c.name customer_name FROM recurring_templates r JOIN customers c ON c.id=r.customer_id ORDER BY r.next_due_date")
        employees=rows(con,"SELECT * FROM employees ORDER BY active DESC,name COLLATE NOCASE")
        payroll=rows(con,"SELECT pr.*,e.name employee_name,e.role,COALESCE((SELECT SUM(pp.amount) FROM payroll_payments pp WHERE pp.payroll_id=pr.id),0) paid FROM payroll pr JOIN employees e ON e.id=pr.employee_id ORDER BY pr.payroll_month DESC,pr.id DESC")
        for x in payroll: x["balance"]=money(x["net_pay"]-x["paid"])
        payroll_payments=rows(con,"SELECT pp.*,pr.payroll_month,e.name employee_name FROM payroll_payments pp JOIN payroll pr ON pr.id=pp.payroll_id JOIN employees e ON e.id=pr.employee_id ORDER BY pp.payment_date DESC,pp.id DESC")
        cash_entries=rows(con,"SELECT * FROM cash_entries ORDER BY entry_date DESC,id DESC")
        credit_notes=rows(con,"SELECT cn.id,cn.note_number,cn.note_date,cn.invoice_id,cn.customer_id,cn.reason,cn.subtotal,cn.cgst,cn.sgst,cn.igst,cn.total,cn.restock,cn.refund_amount,cn.refund_method,cn.notes,i.invoice_number,c.name customer_name FROM credit_notes cn JOIN invoices i ON i.id=cn.invoice_id JOIN customers c ON c.id=cn.customer_id ORDER BY cn.note_date DESC,cn.id DESC")
        debit_notes=rows(con,"SELECT pr.*,pu.purchase_number,s.name supplier_name FROM purchase_returns pr JOIN purchases pu ON pu.id=pr.purchase_id JOIN suppliers s ON s.id=pr.supplier_id ORDER BY pr.return_date DESC,pr.id DESC")
        return {"credit_notes":credit_notes,"debit_notes":debit_notes,"business":business,"customers":customers,"suppliers":suppliers,"products":products,"invoices":invoices,"invoice_items":invoice_items,"payments":payments,"purchases":purchases,"supplier_payments":supplier_payments,"expenses":expenses,"quotations":quotations,"warranties":warranties,"recurring_templates":recurring,"employees":employees,"payroll":payroll,"payroll_payments":payroll_payments,"cash_entries":cash_entries,"states":STATES}


class Handler(BaseHTTPRequestHandler):
    server_version="HisabFlowLocal"
    def log_message(self,fmt,*args): return
    def send_json(self,status,payload):
        raw=json.dumps(payload,ensure_ascii=False).encode("utf-8");self.send_response(status);self.send_header("Content-Type","application/json; charset=utf-8");self.send_header("Content-Length",str(len(raw)));self.send_header("Cache-Control","no-store")
        for c in getattr(self,"_cookies",[]): self.send_header("Set-Cookie",c)
        self._cookies=[]
        self.security_headers();self.end_headers();self.wfile.write(raw)
    def send_svg(self,raw):
        self.send_response(200);self.send_header("Content-Type","image/svg+xml");self.send_header("Content-Length",str(len(raw)));self.send_header("Cache-Control","no-store");self.security_headers();self.end_headers();self.wfile.write(raw)
    def security_headers(self):
        self.send_header("X-Content-Type-Options","nosniff");self.send_header("X-Frame-Options","DENY");self.send_header("Referrer-Policy","no-referrer")
    def send_file(self,path,content_type):
        try: raw=path.read_bytes()
        except OSError: self.send_error(404);return
        self.send_response(200);self.send_header("Content-Type",content_type);self.send_header("Content-Length",str(len(raw)));self.send_header("Cache-Control","no-store");self.security_headers();self.end_headers();self.wfile.write(raw)
    def read_json(self):
        try: n=int(self.headers.get("Content-Length","0"))
        except ValueError: raise ValueError("Invalid request")
        if n<0 or n>3_000_000: raise ValueError("Request is too large")
        try:
            obj=json.loads(self.rfile.read(n).decode("utf-8") if n else "{}")
            if not isinstance(obj,dict): raise ValueError("Invalid request")
            return obj
        except (json.JSONDecodeError,UnicodeDecodeError): raise ValueError("Invalid JSON request")
    def error(self,msg,status=400): self.send_json(status,{"error":str(msg)})

    # ------------------------------------------------------------ security + auth
    def trusted_request(self):
        """Block DNS-rebinding (unexpected Host) and cross-site requests (foreign Origin)."""
        host=(self.headers.get("Host") or "").lower()
        allowed={f"127.0.0.1:{PORT}",f"localhost:{PORT}"}
        if PORT==80: allowed|={"127.0.0.1","localhost"}
        if host not in allowed: return False
        origin=self.headers.get("Origin")
        if origin and origin.lower() not in {f"http://{h}" for h in allowed}: return False
        return True
    def session_token(self):
        for part in (self.headers.get("Cookie") or "").split(";"):
            k,_,v=part.strip().partition("=")
            if k==SESSION_COOKIE and v: return v
        return ""
    def current_user(self):
        if getattr(self,"_user_loaded",False): return self._user
        self._user=None; self._user_loaded=True; token=self.session_token()
        if token:
            with master_db() as con:
                self._user=one(con,"SELECT u.*,s.company_id session_company FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>? AND u.active=1",(token_hash(token),datetime.now().isoformat(timespec="seconds")))
        return self._user
    def set_session_cookie(self,token,max_age):
        self._cookies=getattr(self,"_cookies",[])+[f"{SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict"+(f"; Max-Age={max_age}" if max_age is not None else "")]
    def clear_session_cookie(self):
        self._cookies=getattr(self,"_cookies",[])+[f"{SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"]
    def start_session(self,con,user,remember=False):
        token=secrets.token_urlsafe(32); now=datetime.now()
        expires=now+(timedelta(days=REMEMBER_DAYS) if remember else timedelta(hours=SESSION_HOURS))
        con.execute("DELETE FROM sessions WHERE expires_at<?",(now.isoformat(timespec="seconds"),))
        con.execute("INSERT INTO sessions(token_hash,user_id,created_at,expires_at) VALUES(?,?,?,?)",(token_hash(token),user["id"],now.isoformat(timespec="seconds"),expires.isoformat(timespec="seconds")))
        con.execute("UPDATE users SET last_login=? WHERE id=?",(now.isoformat(timespec="seconds"),user["id"]))
        self.set_session_cookie(token,int((expires-now).total_seconds()) if remember else None)

    def handle_auth(self,path,d):
        """Public authentication endpoints. Returns True when the request was handled."""
        if path=="/api/auth/setup":
            with master_db() as con:
                con.execute("BEGIN IMMEDIATE")
                if one(con,"SELECT id FROM users LIMIT 1"): raise ValueError("Setup is already complete. Please sign in.")
                username=validate_username(str(d.get("username",""))); password=str(d.get("password",""))
                validate_password(password)
                name=str(d.get("display_name","")).strip()[:60] or username
                uid=con.execute("INSERT INTO users(username,display_name,password_hash,role) VALUES(?,?,?,'owner')",(username,name,hash_password(password))).lastrowid
                user=one(con,"SELECT * FROM users WHERE id=?",(uid,)); self.start_session(con,user,bool(d.get("remember")))
            self.send_json(200,{"ok":True,"user":public_user(user)}); return True
        if path=="/api/auth/login":
            username=str(d.get("username","")).strip(); password=str(d.get("password","")); key=username.lower()
            wait=login_blocked(key)
            if wait: raise ValueError(f"Too many failed attempts. Try again in {wait//60+1} minute(s).")
            with master_db() as con:
                user=one(con,"SELECT * FROM users WHERE username=? COLLATE NOCASE",(username,))
                if not user or not verify_password(password,user["password_hash"]):
                    record_login_failure(key); raise ValueError("Incorrect username or password")
                if not user["active"]: raise ValueError("This account has been disabled. Ask the owner to enable it.")
                clear_login_failures(key); self.start_session(con,user,bool(d.get("remember")))
            self.send_json(200,{"ok":True,"user":public_user(user)}); return True
        if path=="/api/auth/logout":
            token=self.session_token()
            if token:
                with master_db() as con: con.execute("DELETE FROM sessions WHERE token_hash=?",(token_hash(token),))
            self.clear_session_cookie(); self.send_json(200,{"ok":True}); return True
        return False

    def handle_account(self,path,d,user):
        """Endpoints for the signed-in user and (owner only) user management."""
        if path=="/api/auth/change-password":
            current=str(d.get("current_password","")); new=str(d.get("new_password",""))
            validate_password(new)
            with master_db() as con:
                u=one(con,"SELECT * FROM users WHERE id=?",(user["id"],))
                if not verify_password(current,u["password_hash"]): raise ValueError("Current password is incorrect")
                con.execute("UPDATE users SET password_hash=? WHERE id=?",(hash_password(new),user["id"]))
                # Sign out every other device that used the old password.
                con.execute("DELETE FROM sessions WHERE user_id=? AND token_hash<>?",(user["id"],token_hash(self.session_token())))
            self.send_json(200,{"ok":True}); return True
        if path in ("/api/users","/api/users/delete"):
            if user["role"]!="owner": self.error("Only the owner can manage users",403); return True
            with master_db() as con:
                con.execute("BEGIN IMMEDIATE")
                if path=="/api/users/delete":
                    uid=to_int(d.get("id"),"user")
                    if uid==user["id"]: raise ValueError("You cannot delete your own account")
                    target=one(con,"SELECT * FROM users WHERE id=?",(uid,))
                    if not target: raise ValueError("User not found")
                    con.execute("DELETE FROM sessions WHERE user_id=?",(uid,)); con.execute("DELETE FROM users WHERE id=?",(uid,))
                    self.send_json(200,{"ok":True}); return True
                role=str(d.get("role","staff")); active=1 if d.get("active",True) in (True,1,"1","true") else 0
                if role not in ("owner","staff"): raise ValueError("Choose a valid role")
                name=str(d.get("display_name","")).strip()[:60]; password=str(d.get("password",""))
                if d.get("id"):
                    uid=to_int(d.get("id"),"user"); target=one(con,"SELECT * FROM users WHERE id=?",(uid,))
                    if not target: raise ValueError("User not found")
                    if uid==user["id"] and (role!="owner" or not active): raise ValueError("You cannot remove your own owner access")
                    con.execute("UPDATE users SET display_name=?,role=?,active=? WHERE id=?",(name or target["username"],role,active,uid))
                    if password:
                        validate_password(password); con.execute("UPDATE users SET password_hash=? WHERE id=?",(hash_password(password),uid))
                    if password or not active: con.execute("DELETE FROM sessions WHERE user_id=?",(uid,))
                else:
                    username=validate_username(str(d.get("username",""))); validate_password(password)
                    if one(con,"SELECT id FROM users WHERE username=? COLLATE NOCASE",(username,)): raise ValueError("That username is already taken")
                    uid=con.execute("INSERT INTO users(username,display_name,password_hash,role,active) VALUES(?,?,?,?,?)",(username,name or username,hash_password(password),role,active)).lastrowid
            self.send_json(200,{"ok":True,"id":uid}); return True
        return False

    OWNER_ONLY={"/api/business","/api/business/invoice-design","/api/restore","/api/backup","/api/companies/create","/api/companies/archive"}
    STATIC_TYPES={".js":"application/javascript; charset=utf-8",".css":"text/css; charset=utf-8"}

    def send_download(self,raw,content_type,filename):
        self.send_response(200);self.send_header("Content-Type",content_type);self.send_header("Content-Length",str(len(raw)))
        self.send_header("Content-Disposition",f"attachment; filename=\"{filename}\"");self.send_header("Cache-Control","no-store");self.security_headers();self.end_headers();self.wfile.write(raw)

    def handle_companies(self,method,path,user):
        """Company list, create, switch and archive. Company data lives in separate database files."""
        if method=="GET" and path=="/api/companies":
            with master_db() as con: comps=rows(con,"SELECT * FROM companies ORDER BY id")
            out=[]
            for c in comps:
                pth=company_path(c["db_file"])
                if pth.exists(): out.append({"id":c["id"],"name":company_name(pth) or c["name"] or f"Company {c['id']}","current":c["id"]==_ctx.company_id})
            self.send_json(200,out); return True
        if method!="POST": return False
        if path=="/api/companies/create":
            d=self.read_json(); name=str(d.get("name","")).strip()[:80]; state=str(d.get("state","")).strip()
            if not name: raise ValueError("Enter the company name")
            if state and state not in STATES: raise ValueError("Choose a valid state")
            COMPANY_DIR.mkdir(exist_ok=True)
            with master_db() as con:
                cid=con.execute("INSERT INTO companies(name,db_file) VALUES(?,?)",(name,f"pending-{uuid.uuid4().hex}")).lastrowid
                fname=f"company-{cid}.db"; con.execute("UPDATE companies SET db_file=? WHERE id=?",(fname,cid))
            with db(COMPANY_DIR/fname) as ccon:
                init_company_schema(ccon)
                ccon.execute("UPDATE business SET name=?,state=?,invoice_prefix=? WHERE id=1",(name,state,"INV"))
            with master_db() as con: con.execute("UPDATE sessions SET company_id=? WHERE token_hash=?",(cid,token_hash(self.session_token())))
            self.send_json(200,{"ok":True,"id":cid}); return True
        if path=="/api/companies/switch":
            cid=to_int(self.read_json().get("id"),"company")
            with master_db() as con:
                c=one(con,"SELECT * FROM companies WHERE id=?",(cid,))
                if not c or not company_path(c["db_file"]).exists(): raise ValueError("Company not found")
                con.execute("UPDATE sessions SET company_id=? WHERE token_hash=?",(cid,token_hash(self.session_token())))
            self.send_json(200,{"ok":True}); return True
        if path=="/api/companies/archive":
            d=self.read_json(); cid=to_int(d.get("id"),"company")
            if cid==1: raise ValueError("The main company cannot be archived")
            with master_db() as con:
                c=one(con,"SELECT * FROM companies WHERE id=?",(cid,))
                if not c: raise ValueError("Company not found")
                pth=company_path(c["db_file"]); name=company_name(pth) or c["name"]
                if str(d.get("confirm_name","")).strip().lower()!=name.strip().lower(): raise ValueError("Type the company name exactly to confirm")
                auto_backup(force=True,suffix="archived",path=pth,prefix=f"company{cid}")
                ARCHIVE_DIR.mkdir(exist_ok=True)
                if pth.exists(): pth.replace(ARCHIVE_DIR/f"company-{cid}-{datetime.now():%Y%m%d-%H%M%S}.db")
                con.execute("DELETE FROM companies WHERE id=?",(cid,)); con.execute("UPDATE sessions SET company_id=1 WHERE company_id=?",(cid,))
            self.send_json(200,{"ok":True}); return True
        return False

    def dispatch(self,method):
        self._cookies=[]; self._user_loaded=False; _ctx.db_path=None; _ctx.company_id=1
        if not self.trusted_request(): return self.error("Request blocked: HisabFlow only accepts requests from its own browser tab on this computer.",403)
        p=urlparse(self.path)
        try:
            if p.path=="/health" and method=="GET": return self.send_json(200,{"ok":True,"version":APP_VERSION})
            if not p.path.startswith("/api/"):
                if method=="GET" and p.path in ("/","/index.html"): return self.send_file(UI_DIR/"index.html","text/html; charset=utf-8")
                m=re.fullmatch(r"/ui/([a-z0-9_-]+(\.js|\.css))",p.path)
                if method=="GET" and m: return self.send_file(UI_DIR/m.group(1),self.STATIC_TYPES[m.group(2)])
                return self.send_error(404)
            if method=="POST":
                # A JSON content type forces a CORS preflight, so other websites cannot post here.
                ctype=(self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                if ctype!="application/json": return self.error("Requests must be sent as JSON",415)
            if p.path=="/api/auth/status" and method=="GET":
                with master_db() as con: has_users=bool(one(con,"SELECT id FROM users LIMIT 1"))
                u=self.current_user(); comp=None
                if u:
                    c,pth=company_for_session(u["session_company"]); comp={"id":c["id"],"name":company_name(pth) or c["name"]}
                return self.send_json(200,{"setup_required":not has_users,"user":public_user(u),"company":comp,"version":APP_VERSION})
            if method=="POST" and p.path in ("/api/auth/setup","/api/auth/login","/api/auth/logout"):
                if self.handle_auth(p.path,self.read_json()): return
            user=self.current_user()
            if not user: return self.error("Please sign in to continue",401)
            company,_ctx.db_path=company_for_session(user["session_company"]); _ctx.company_id=company["id"]
            if p.path in self.OWNER_ONLY and user["role"]!="owner": return self.error("Only the business owner can do this",403)
            if method=="GET" and p.path=="/api/users":
                if user["role"]!="owner": return self.error("Only the owner can manage users",403)
                with master_db() as con: users=[public_user(u) for u in rows(con,"SELECT * FROM users ORDER BY role='owner' DESC,username COLLATE NOCASE")]
                return self.send_json(200,users)
            if p.path.startswith("/api/companies"):
                if self.handle_companies(method,p.path,user): return
            if method=="POST" and p.path in ("/api/auth/change-password","/api/users","/api/users/delete"):
                if self.handle_account(p.path,self.read_json(),user): return
            if method=="GET": return self.route_GET()
            if method=="POST": return self.route_POST()
            if method=="DELETE": return self.route_DELETE()
        except (ValueError,TypeError,KeyError) as e: self.error(str(e))
        except sqlite3.IntegrityError as e: self.error(friendly_integrity(e))
        except sqlite3.OperationalError as e: self.error(f"Database is busy or unavailable: {e}",503)
        except Exception as e: self.error(f"Unexpected error: {e}",500)

    def do_GET(self): self.dispatch("GET")
    def do_POST(self): self.dispatch("POST")
    def do_DELETE(self): self.dispatch("DELETE")

    def route_GET(self):
        p=urlparse(self.path);q=parse_qs(p.query)
        if p.path=="/api/state": self.send_json(200,state_data())
        elif p.path=="/api/product/lookup":
            code=str(q.get("code",[""])[0]).strip()
            with db() as con:
                product=one(con,"SELECT * FROM products WHERE barcode=? OR (CAST(id AS TEXT)=? AND ?<>'') ORDER BY (barcode=?) DESC LIMIT 1",(code,code,code,code)) if code else None
            if product:self.send_json(200,product)
            else:self.error("No product found for this barcode / QR code",404)
        elif p.path=="/api/product/qr":
            try: pid=int(q.get("id",[""])[0])
            except: return self.error("Invalid product ID")
            with db() as con: product=one(con,"SELECT id,name,barcode FROM products WHERE id=?",(pid,))
            if not product:return self.error("Product not found",404)
            try: raw=qr_svg("HISABFLOW-PRODUCT:"+(product["barcode"] or str(product["id"])),7)
            except Exception: return self.error("QR code generator is unavailable",500)
            self.send_svg(raw)
        elif p.path=="/api/assistant/summary":
            ms=date.today().replace(day=1).isoformat()
            with db() as con:
                summary={"sales":one(con,"SELECT COALESCE(SUM(total),0) v FROM invoices WHERE invoice_date>=?",(ms,))["v"],"collections":one(con,"SELECT COALESCE(SUM(amount),0) v FROM payments WHERE payment_date>=?",(ms,))["v"],"expenses":one(con,"SELECT COALESCE(SUM(amount),0) v FROM expenses WHERE expense_date>=?",(ms,))["v"],"outstanding":one(con,"SELECT COALESCE(SUM(MAX(total-paid-credited+refunded,0)),0) v FROM invoice_balances")["v"],"low_stock":rows(con,"SELECT name,stock_qty,low_stock_level,unit FROM products WHERE track_stock=1 AND stock_qty<=low_stock_level ORDER BY name"),"employees":one(con,"SELECT COUNT(*) n FROM employees WHERE active=1")["n"]}
            self.send_json(200,summary)
        elif p.path=="/api/invoice":
            try: iid=int(q.get("id",[""])[0])
            except: return self.error("Invalid invoice ID")
            with db() as con:
                inv=one(con,"SELECT * FROM invoices WHERE id=?",(iid,))
                if not inv:return self.error("Invoice not found",404)
                inv["items"]=rows(con,"SELECT * FROM invoice_items WHERE invoice_id=? ORDER BY id",(iid,));inv["payments"]=rows(con,"SELECT * FROM payments WHERE invoice_id=? ORDER BY payment_date,id",(iid,));inv["business"]=json.loads(inv.pop("business_snapshot"));inv["customer"]=json.loads(inv.pop("customer_snapshot"));inv["paid"]=money(sum(x["amount"] for x in inv["payments"]))
                inv["credit_notes"]=rows(con,"SELECT id,note_number,note_date,total,refund_amount,reason FROM credit_notes WHERE invoice_id=? ORDER BY note_date,id",(iid,))
                inv["credited"]=money(sum(x["total"] for x in inv["credit_notes"]));inv["refunded"]=money(sum(x["refund_amount"] for x in inv["credit_notes"]))
                inv["balance"]=money(inv["total"]-inv["paid"]-inv["credited"]+inv["refunded"]);inv["status"]=invoice_status(inv)
                for it in inv["items"]: it["credited_qty"]=one(con,"SELECT COALESCE(SUM(qty),0) q FROM credit_note_items WHERE invoice_item_id=?",(it["id"],))["q"]
            self.send_json(200,inv)
        elif p.path=="/api/purchase":
            try: puid=int(q.get("id",[""])[0])
            except: return self.error("Invalid purchase ID")
            with db() as con:
                pu=one(con,"SELECT * FROM purchases WHERE id=?",(puid,))
                if not pu:return self.error("Purchase not found",404)
                pu["items"]=rows(con,"SELECT * FROM purchase_items WHERE purchase_id=? ORDER BY id",(puid,))
                pu["supplier"]=json.loads(pu.pop("supplier_snapshot"))
                pu["payments"]=rows(con,"SELECT * FROM supplier_payments WHERE purchase_id=? ORDER BY payment_date,id",(puid,))
                pu["paid"]=money(sum(x["amount"] for x in pu["payments"]))
                pu["returns"]=rows(con,"SELECT * FROM purchase_returns WHERE purchase_id=? ORDER BY return_date,id",(puid,))
                pu["returned"]=money(sum(x["amount"] for x in pu["returns"]))
                pu["balance"]=money(pu["total"]-pu["paid"]-pu["returned"])
            self.send_json(200,pu)
        elif p.path=="/api/quotation":
            try: qid=int(q.get("id",[""])[0])
            except: return self.error("Invalid quotation ID")
            with db() as con:
                quote=one(con,"SELECT * FROM quotations WHERE id=?",(qid,))
                if not quote:return self.error("Quotation not found",404)
                quote["items"]=rows(con,"SELECT * FROM quotation_items WHERE quotation_id=? ORDER BY id",(qid,));quote["customer"]=json.loads(quote.pop("customer_snapshot"))
            self.send_json(200,quote)
        elif p.path=="/api/warranty/qr":
            try: wid=int(q.get("id",[""])[0])
            except: return self.error("Invalid warranty ID")
            with db() as con: w=one(con,"SELECT warranty_code FROM warranties WHERE id=?",(wid,))
            if not w:return self.error("Warranty not found",404)
            try: raw=qr_svg("HISABFLOW-WARRANTY:"+w["warranty_code"],6)
            except Exception: return self.error("QR code generator is unavailable",500)
            self.send_svg(raw)
        elif p.path=="/api/credit-note":
            cid=to_int(q.get("id",[""])[0],"credit note")
            with db() as con:
                cn=one(con,"SELECT cn.*,i.invoice_number,i.invoice_date FROM credit_notes cn JOIN invoices i ON i.id=cn.invoice_id WHERE cn.id=?",(cid,))
                if not cn: return self.error("Credit note not found",404)
                cn["items"]=rows(con,"SELECT * FROM credit_note_items WHERE credit_note_id=? ORDER BY id",(cid,))
                cn["customer"]=json.loads(cn.pop("customer_snapshot"));cn["business"]=json.loads(cn.pop("business_snapshot"))
            self.send_json(200,cn)
        elif p.path=="/api/debit-note":
            rid=to_int(q.get("id",[""])[0],"debit note")
            with db() as con:
                dn=one(con,"SELECT pr.*,pu.purchase_number,pu.purchase_date,pu.supplier_snapshot FROM purchase_returns pr JOIN purchases pu ON pu.id=pr.purchase_id WHERE pr.id=?",(rid,))
                if not dn: return self.error("Debit note not found",404)
                dn["items"]=rows(con,"SELECT * FROM purchase_return_items WHERE return_id=? ORDER BY id",(rid,))
                dn["supplier"]=json.loads(dn.pop("supplier_snapshot"));dn["business"]=one(con,"SELECT * FROM business WHERE id=1")
            self.send_json(200,dn)
        elif p.path in ("/api/gst","/api/gst/export"):
            ret=q.get("return",["gstr1"])[0];start=q.get("from",[""])[0];end=q.get("to",[""])[0]
            date.fromisoformat(start);date.fromisoformat(end)
            if start>end: return self.error("From date must be before To date")
            if ret not in ("gstr1","gstr3b","register"): return self.error("Unknown return type")
            if ret=="register":
                if p.path!="/api/gst/export": return self.error("Use the export link for the sales register")
                with db() as con: reg=gst.sales_register(con,start,end)
                return self.send_download(gst.to_xlsx(gst.register_sheets(reg)),"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",f"GST_Sales_Register_{start}_to_{end}.xlsx")
            with db() as con: report=gst.gstr1(con,start,end) if ret=="gstr1" else gst.gstr3b(con,start,end)
            if p.path=="/api/gst": return self.send_json(200,report)
            fmt=q.get("format",["xlsx"])[0]; base=f"{ret.upper()}_{start}_to_{end}"
            if fmt=="json":
                if ret!="gstr1": return self.error("JSON export is available for GSTR-1 only")
                raw=json.dumps(gst.gstr1_portal_json(report),ensure_ascii=False,indent=1).encode("utf-8")
                return self.send_download(raw,"application/json",base+".json")
            if fmt!="xlsx": return self.error("Unknown export format")
            raw=gst.to_xlsx(gst.gstr1_sheets(report) if ret=="gstr1" else gst.gstr3b_sheets(report))
            return self.send_download(raw,"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",base+".xlsx")
        elif p.path=="/api/upi-qr":
            iid=to_int(q.get("invoice_id",[""])[0],"invoice")
            with db() as con:
                b=one(con,"SELECT name,upi_id FROM business WHERE id=1");inv=one(con,"SELECT invoice_number FROM invoices WHERE id=?",(iid,))
                bal=one(con,"SELECT * FROM invoice_balances WHERE invoice_id=?",(iid,))
            if not inv or not b or not b["upi_id"]: return self.error("UPI ID not set",404)
            amount=money(bal["total"]-bal["paid"]-bal["credited"]+bal["refunded"])
            uri=f"upi://pay?pa={url_quote(b['upi_id'])}&pn={url_quote(b['name'][:40])}&cu=INR&tn={url_quote('Invoice '+inv['invoice_number'])}"+(f"&am={amount:.2f}" if amount>0 else "")
            try: raw=qr_svg(uri,5)
            except Exception: return self.error("QR code generator is unavailable",500)
            self.send_svg(raw)
        elif p.path=="/api/backup":
            with db() as con:
                table_names=[r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name NOT IN ('users','sessions','companies')")]
                payload={"schema_version":3,"tables":{t:rows(con,f'SELECT * FROM {t}') for t in table_names}}
            self.send_json(200,payload)
        else:self.error("Not found",404)

    def route_POST(self):
        p=urlparse(self.path)
        try:
            d=self.read_json()
            if p.path=="/api/credit-notes":
                with db() as con:
                    con.execute("BEGIN IMMEDIATE");cid,no=create_credit_note(con,d)
                return self.send_json(200,{"ok":True,"id":cid,"note_number":no})
            if p.path=="/api/business/invoice-design":
                tpl=str(d.get("invoice_template","modern"));color=str(d.get("invoice_color","#4f46e5"))
                if tpl not in ("modern","classic","minimal","thermal"):return self.error("Choose a valid invoice template")
                if not re.fullmatch(r"#[0-9a-fA-F]{6}",color):return self.error("Choose a valid colour")
                with db() as con:
                    con.execute("UPDATE business SET invoice_template=?,invoice_color=?,invoice_terms=?,show_upi_qr=?,show_hsn_summary=? WHERE id=1",(tpl,color,str(d.get("invoice_terms","")).strip()[:1500],1 if d.get("show_upi_qr") else 0,1 if d.get("show_hsn_summary") else 0))
                return self.send_json(200,{"ok":True})
            if p.path=="/api/business":
                fields={k:str(d.get(k,"")).strip() for k in ["name","gstin","state","address","phone","email","invoice_prefix","bank_details","upi_id"]}
                logo_data=str(d.get("logo_data", ""))
                if logo_data:
                    if len(logo_data)>900000 or not re.fullmatch(r"data:image/(png|jpeg|webp);base64,[A-Za-z0-9+/=]+",logo_data): return self.error("Logo must be PNG, JPG or WebP and under 650 KB")
                if not fields["name"] or not fields["state"]: return self.error("Business name and state are required")
                if fields["gstin"] and not re.fullmatch(r"[0-9A-Za-z]{15}",fields["gstin"]):return self.error("GSTIN should contain 15 letters/numbers")
                fields["invoice_prefix"]=re.sub(r"[^A-Za-z0-9-]","",fields["invoice_prefix"].upper())[:12] or "INV"
                with db() as con:
                    if logo_data: fields["logo_data"]=logo_data
                    con.execute("UPDATE business SET name=?,gstin=?,state=?,address=?,phone=?,email=?,invoice_prefix=?,bank_details=?,upi_id=?,logo_data=CASE WHEN ?='' THEN logo_data ELSE ? END,updated_at=CURRENT_TIMESTAMP WHERE id=1",tuple(fields[k] for k in ["name","gstin","state","address","phone","email","invoice_prefix","bank_details","upi_id"])+(logo_data,logo_data))
                with master_db() as con: con.execute("UPDATE companies SET name=? WHERE id=?",(fields["name"],_ctx.company_id))
                return self.send_json(200,{"ok":True})
            if p.path in ("/api/customers","/api/suppliers"):
                table="customers" if p.path.endswith("customers") else "suppliers";name=str(d.get("name","")).strip()
                if not name:return self.error("Name is required")
                fields=[name]+[str(d.get(k,"")).strip() for k in ["phone","email","gstin","address","state"]]
                if fields[3] and not re.fullmatch(r"[0-9A-Za-z]{15}",fields[3]):return self.error("GSTIN should contain 15 letters/numbers")
                with db() as con:
                    if d.get("id"):
                        rid=to_int(d["id"]);cur=con.execute(f"UPDATE {table} SET name=?,phone=?,email=?,gstin=?,address=?,state=? WHERE id=?",(*fields,rid))
                        if not cur.rowcount:return self.error("Record not found",404)
                    else: rid=con.execute(f"INSERT INTO {table}(name,phone,email,gstin,address,state) VALUES(?,?,?,?,?,?)",fields).lastrowid
                return self.send_json(200,{"ok":True,"id":rid})
            if p.path=="/api/products":
                name=str(d.get("name","")).strip()
                if not name:return self.error("Item name is required")
                rate=money(d.get("rate",0));gst=money(d.get("gst_rate",0));cost=money(d.get("purchase_rate",0));stock=stock_qty(d.get("stock_qty",0));low=stock_qty(d.get("low_stock_level",0));track=1 if d.get("track_stock") in (True,1,"1","true","on") else 0
                if min(rate,gst,cost,stock,low)<0 or gst>100:return self.error("Enter valid item prices, stock and GST rate")
                barcode=str(d.get("barcode","")).strip()
                if len(barcode)>80:return self.error("Barcode must be 80 characters or fewer")
                fields=[name,str(d.get("hsn","")).strip(),str(d.get("unit","pcs")).strip() or "pcs",rate,gst,cost,stock,low,track,barcode,item_details(d)]
                with db() as con:
                    if d.get("id"):
                        pid=to_int(d["id"],"item");old=one(con,"SELECT stock_qty FROM products WHERE id=?",(pid,))
                        if not old:return self.error("Item not found",404)
                        con.execute("UPDATE products SET name=?,hsn=?,unit=?,rate=?,gst_rate=?,purchase_rate=?,stock_qty=?,low_stock_level=?,track_stock=?,barcode=?,details=? WHERE id=?",(*fields,pid))
                        if old and track and abs(float(old["stock_qty"])-stock)>.0001:
                            delta=stock-float(old["stock_qty"]);con.execute("INSERT INTO stock_movements(product_id,movement_date,movement_type,qty_in,qty_out,reference,notes) VALUES(?,?,?,?,?,?,?)",(pid,date.today().isoformat(),"Opening/adjustment",max(delta,0),max(-delta,0),"Product edit","Stock updated from item form"))
                    else:
                        pid=con.execute("INSERT INTO products(name,hsn,unit,rate,gst_rate,purchase_rate,stock_qty,low_stock_level,track_stock,barcode,details) VALUES(?,?,?,?,?,?,?,?,?,?,?)",fields).lastrowid
                        if track and stock>0:con.execute("INSERT INTO stock_movements(product_id,movement_date,movement_type,qty_in,reference,notes) VALUES(?,?,?,?,?,?)",(pid,date.today().isoformat(),"Opening stock",stock,"Opening balance","Initial stock"))
                return self.send_json(200,{"ok":True,"id":pid})
            if p.path=="/api/invoices":
                with db() as con:
                    con.execute("PRAGMA foreign_keys=ON");con.execute("BEGIN IMMEDIATE");iid,no=create_invoice_records(con,d);con.commit()
                return self.send_json(200,{"ok":True,"id":iid,"invoice_number":no})
            if p.path=="/api/invoices/update":
                iid=int(d.get("id",0))
                with db() as con:
                    con.execute("PRAGMA foreign_keys=ON");con.execute("BEGIN IMMEDIATE");no=update_invoice_records(con,iid,d);con.commit()
                return self.send_json(200,{"ok":True,"id":iid,"invoice_number":no})
            if p.path=="/api/payments":
                iid=int(d.get("invoice_id",0));amount=money(d.get("amount",0));pd=str(d.get("payment_date") or date.today().isoformat());date.fromisoformat(pd)
                if amount<=0:return self.error("Payment amount must be greater than zero")
                with db() as con:
                    b=one(con,"SELECT * FROM invoice_balances WHERE invoice_id=?",(iid,))
                    if not b:return self.error("Invoice not found",404)
                    bal=money(b["total"]-b["paid"]-b["credited"]+b["refunded"])
                    if amount>bal+.009:return self.error(f"Payment is higher than the outstanding balance (₹{bal:,.2f})")
                    con.execute("INSERT INTO payments(invoice_id,payment_date,amount,method,reference) VALUES(?,?,?,?,?)",(iid,pd,amount,str(d.get("method","Cash")),str(d.get("reference",""))))
                return self.send_json(200,{"ok":True})
            if p.path=="/api/purchases":
                sid=to_int(d.get("supplier_id"),"supplier");pdate=str(d.get("purchase_date") or date.today().isoformat());date.fromisoformat(pdate);items=d.get("items",[])
                if not isinstance(items,list) or not items:return self.error("Add at least one purchase item")
                with db() as con:
                    con.execute("PRAGMA foreign_keys=ON");con.execute("BEGIN IMMEDIATE");supplier=one(con,"SELECT * FROM suppliers WHERE id=?",(sid,))
                    if not supplier:return self.error("Select a valid supplier")
                    subtotal=tax=0;computed=[]
                    for it in items:
                        desc=str(it.get("description","")).strip();qty=quantity(it.get("qty",0));rate=money(it.get("rate",0));gst=money(it.get("gst_rate",0));pid=int(it["product_id"]) if it.get("product_id") else None
                        if not desc or rate<0 or gst<0 or gst>100:raise ValueError("Enter valid purchase item details")
                        base=money(qty*rate);t=money(base*gst/100);subtotal=money(subtotal+base);tax=money(tax+t);computed.append((pid,desc,qty,rate,gst,base,t,money(base+t)))
                    total=money(subtotal+tax);no=next_number(con,"purchases","purchase_number",f"PUR/{pdate[:4]}/")
                    pid=con.execute("INSERT INTO purchases(purchase_number,purchase_date,supplier_id,supplier_snapshot,subtotal,tax,total,notes) VALUES(?,?,?,?,?,?,?,?)",(no,pdate,sid,json.dumps(supplier),subtotal,tax,total,str(d.get("notes","")))).lastrowid
                    for product_id,desc,qty,rate,gst,base,t,line_total in computed:
                        con.execute("INSERT INTO purchase_items(purchase_id,product_id,description,qty,rate,gst_rate,taxable,tax,line_total) VALUES(?,?,?,?,?,?,?,?,?)",(pid,product_id,desc,qty,rate,gst,base,t,line_total))
                        if product_id:
                            prod=one(con,"SELECT * FROM products WHERE id=?",(product_id,))
                            if not prod:raise ValueError("A purchased item does not exist")
                            if prod["track_stock"]:
                                old_qty=float(prod["stock_qty"]);new_qty=stock_qty(old_qty+qty,True);old_cost=float(prod["purchase_rate"] or 0);new_cost=money(((old_qty*old_cost)+(qty*rate))/new_qty) if new_qty else rate
                                con.execute("UPDATE products SET stock_qty=?,purchase_rate=? WHERE id=?",(new_qty,new_cost,product_id))
                                con.execute("INSERT INTO stock_movements(product_id,movement_date,movement_type,qty_in,reference,notes) VALUES(?,?,?,?,?,?)",(product_id,pdate,"Purchase",qty,no,"Stock received from supplier purchase"))
                    if money(d.get("paid_amount",0))<0:raise ValueError("Paid amount cannot be negative")
                    if money(d.get("paid_amount",0))>0:
                        paid=money(d["paid_amount"])
                        if paid>total+.009:raise ValueError("Initial payment cannot exceed purchase total")
                        con.execute("INSERT INTO supplier_payments(purchase_id,payment_date,amount,method,reference) VALUES(?,?,?,?,?)",(pid,pdate,paid,str(d.get("method","Cash")),str(d.get("reference",""))))
                    con.commit()
                return self.send_json(200,{"ok":True,"id":pid,"purchase_number":no})
            if p.path=="/api/purchases/update":
                pid=int(d.get("id",0))
                with db() as con:
                    con.execute("PRAGMA foreign_keys=ON");con.execute("BEGIN IMMEDIATE");no=update_purchase_records(con,pid,d);con.commit()
                return self.send_json(200,{"ok":True,"id":pid,"purchase_number":no})
            if p.path=="/api/supplier-payments":
                pid=int(d.get("purchase_id",0));amount=money(d.get("amount",0));pd=str(d.get("payment_date") or date.today().isoformat());date.fromisoformat(pd)
                if amount<=0:return self.error("Payment must be greater than zero")
                with db() as con:
                    con.execute("PRAGMA foreign_keys=ON");pu=one(con,"SELECT total FROM purchases WHERE id=?",(pid,))
                    if not pu:return self.error("Purchase not found",404)
                    paid=one(con,"SELECT COALESCE(SUM(amount),0) a FROM supplier_payments WHERE purchase_id=?",(pid,))["a"];ret=one(con,"SELECT COALESCE(SUM(amount),0) a FROM purchase_returns WHERE purchase_id=?",(pid,))["a"];bal=money(pu["total"]-paid-ret)
                    if amount>bal+.009:return self.error(f"Payment exceeds supplier balance of ₹{bal:,.2f}")
                    con.execute("INSERT INTO supplier_payments(purchase_id,payment_date,amount,method,reference) VALUES(?,?,?,?,?)",(pid,pd,amount,str(d.get("method","Cash")),str(d.get("reference",""))))
                return self.send_json(200,{"ok":True})
            if p.path=="/api/expenses":
                ed=str(d.get("expense_date") or date.today().isoformat());date.fromisoformat(ed);cat=str(d.get("category","Other")).strip();desc=str(d.get("description","")).strip();amt=money(d.get("amount",0))
                if not desc or amt<=0:return self.error("Expense description and positive amount are required")
                with db() as con:
                    if d.get("id"):
                        cur=con.execute("UPDATE expenses SET expense_date=?,category=?,description=?,amount=?,method=?,reference=? WHERE id=?",(ed,cat,desc,amt,str(d.get("method","Cash")),str(d.get("reference","")),int(d["id"])))
                        if not cur.rowcount:return self.error("Expense not found",404)
                    else: con.execute("INSERT INTO expenses(expense_date,category,description,amount,method,reference) VALUES(?,?,?,?,?,?)",(ed,cat,desc,amt,str(d.get("method","Cash")),str(d.get("reference",""))))
                return self.send_json(200,{"ok":True})
            if p.path=="/api/stock-adjustments":
                product_id=to_int(d.get("product_id"),"product");delta=stock_qty(d.get("quantity",0),True);md=str(d.get("movement_date") or date.today().isoformat());date.fromisoformat(md);reason=str(d.get("reason","Adjustment")).strip()
                if delta==0:return self.error("Adjustment quantity cannot be zero")
                with db() as con:
                    pdt=one(con,"SELECT * FROM products WHERE id=?",(product_id,))
                    if not pdt:return self.error("Product not found")
                    if not pdt["track_stock"]:return self.error("Enable stock tracking for this item first")
                    new=stock_qty(float(pdt["stock_qty"])+delta,True)
                    if new<0:return self.error("Stock cannot become negative")
                    con.execute("UPDATE products SET stock_qty=? WHERE id=?",(new,product_id))
                    con.execute("INSERT INTO stock_movements(product_id,movement_date,movement_type,qty_in,qty_out,reference,notes) VALUES(?,?,?,?,?,?,?)",(product_id,md,"Adjustment",max(delta,0),max(-delta,0),"Manual",reason))
                return self.send_json(200,{"ok":True})
            if p.path=="/api/purchase-returns":
                puid=to_int(d.get("purchase_id"),"purchase");rdate=str(d.get("return_date") or date.today().isoformat());date.fromisoformat(rdate);items=d.get("items",[])
                if not isinstance(items,list) or not items:return self.error("Add at least one return item")
                with db() as con:
                    con.execute("PRAGMA foreign_keys=ON");con.execute("BEGIN IMMEDIATE");pu=one(con,"SELECT * FROM purchases WHERE id=?",(puid,))
                    if not pu:return self.error("Purchase not found")
                    if rdate<pu["purchase_date"]:return self.error("Debit note date cannot be before the purchase date")
                    total=0;computed=[]
                    for it in items:
                        product_id=int(it.get("product_id") or 0);qty=quantity(it.get("qty",0));rate=money(it.get("rate",0));prod=one(con,"SELECT * FROM products WHERE id=?",(product_id,))
                        if not prod:raise ValueError("Select a valid product for each return line")
                        if prod["track_stock"] and qty>float(prod["stock_qty"])+.0001:raise ValueError(f"Cannot return more stock than available for {prod['name']}")
                        purchased=one(con,"SELECT COALESCE(SUM(qty),0) q FROM purchase_items WHERE purchase_id=? AND product_id=?",(puid,product_id))["q"]
                        already=one(con,"SELECT COALESCE(SUM(pri.qty),0) q FROM purchase_return_items pri JOIN purchase_returns pr ON pr.id=pri.return_id WHERE pr.purchase_id=? AND pri.product_id=?",(puid,product_id))["q"]
                        if qty>float(purchased)-float(already)+.0001:raise ValueError(f"Return quantity exceeds the original purchased quantity for {prod['name']}")
                        base=money(qty*rate); gst_rate=money(it.get("gst_rate",0))
                        if rate<0 or gst_rate<0 or gst_rate>100:raise ValueError("Enter a valid return rate and GST")
                        amount=money(base+(base*gst_rate/100));total=money(total+amount);computed.append((product_id,prod["name"],qty,rate,gst_rate,amount,prod["track_stock"],base,money(amount-base)))
                    no=next_number(con,"purchase_returns","return_number",f"DN/{fy_label(rdate)}/")
                    rid=con.execute("INSERT INTO purchase_returns(return_number,return_date,purchase_id,supplier_id,amount,notes) VALUES(?,?,?,?,?,?)",(no,rdate,puid,pu["supplier_id"],total,str(d.get("notes","")))).lastrowid
                    for product_id,desc,qty,rate,gst_rate,amount,tracked,base,tax in computed:
                        con.execute("INSERT INTO purchase_return_items(return_id,product_id,description,qty,rate,gst_rate,amount,taxable,tax) VALUES(?,?,?,?,?,?,?,?,?)",(rid,product_id,desc,qty,rate,gst_rate,amount,base,tax))
                        if tracked:
                            con.execute("UPDATE products SET stock_qty=stock_qty-? WHERE id=?",(qty,product_id));con.execute("INSERT INTO stock_movements(product_id,movement_date,movement_type,qty_out,reference,notes) VALUES(?,?,?,?,?,?)",(product_id,rdate,"Purchase return",qty,no,"Returned to supplier"))
                return self.send_json(200,{"ok":True,"return_number":no})
            if p.path=="/api/quotations":
                customer_id=to_int(d.get("customer_id"),"customer");qd=str(d.get("quote_date") or date.today().isoformat());date.fromisoformat(qd);items=d.get("items",[])
                if not isinstance(items,list) or not items:return self.error("Add at least one quotation item")
                with db() as con:
                    cust=one(con,"SELECT * FROM customers WHERE id=?",(customer_id,));biz=one(con,"SELECT * FROM business WHERE id=1")
                    if not cust:return self.error("Select a valid customer")
                    inter=bool(cust["state"] and biz["state"] and cust["state"]!=biz["state"]);sub=cc=ss=ii=0;calc=[]
                    for it in items:
                        desc=str(it.get("description","")).strip();qty=quantity(it.get("qty",1));rate=money(it.get("rate",0));gst=money(it.get("gst_rate",0));pid=int(it["product_id"]) if it.get("product_id") else None
                        if not desc or rate<0 or gst<0 or gst>100:raise ValueError("Invalid quotation item")
                        base=money(qty*rate);tax=money(base*gst/100);c=0 if inter else money(tax/2);s=0 if inter else money(tax-c);ig=tax if inter else 0
                        sub=money(sub+base);cc=money(cc+c);ss=money(ss+s);ii=money(ii+ig);calc.append((pid,desc,str(it.get("hsn","")),str(it.get("unit","pcs")),qty,rate,gst,base,c,s,ig,money(base+tax),item_details(it)))
                    total=money(sub+cc+ss+ii);no=next_number(con,"quotations","quote_number",f"QT/{qd[:4]}/")
                    qid=con.execute("INSERT INTO quotations(quote_number,quote_date,customer_id,customer_snapshot,subtotal,cgst,sgst,igst,total,notes) VALUES(?,?,?,?,?,?,?,?,?,?)",(no,qd,customer_id,json.dumps(cust),sub,cc,ss,ii,total,str(d.get("notes","")))).lastrowid
                    for it in calc:con.execute("INSERT INTO quotation_items(quotation_id,product_id,description,hsn,unit,qty,rate,gst_rate,taxable,cgst,sgst,igst,line_total,details) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(qid,*it))
                return self.send_json(200,{"ok":True,"id":qid,"quote_number":no})
            if p.path=="/api/quotations/update":
                qid=int(d.get("id",0));customer_id=int(d.get("customer_id",0));qd=str(d.get("quote_date") or date.today().isoformat());date.fromisoformat(qd);items=d.get("items",[])
                with db() as con:
                    con.execute("PRAGMA foreign_keys=ON");con.execute("BEGIN IMMEDIATE")
                    q=one(con,"SELECT * FROM quotations WHERE id=?",(qid,));cust=one(con,"SELECT * FROM customers WHERE id=?",(customer_id,))
                    if not q:raise ValueError("Quotation not found")
                    if q["converted_invoice_id"]:raise ValueError("Converted quotations cannot be edited")
                    if not cust:raise ValueError("Select a valid customer")
                    if not items:raise ValueError("Add at least one quotation item")
                    biz=one(con,"SELECT * FROM business WHERE id=1");inter=bool(cust["state"] and biz["state"] and cust["state"]!=biz["state"]);sub=cc=ss=ii=0;calc=[]
                    for it in items:
                        desc=str(it.get("description","")).strip();qty=quantity(it.get("qty",1));rate=money(it.get("rate",0));gst=money(it.get("gst_rate",0));pid=int(it["product_id"]) if it.get("product_id") else None
                        if not desc or rate<0 or gst<0 or gst>100:raise ValueError("Invalid quotation item")
                        base=money(qty*rate);tax=money(base*gst/100);c=0 if inter else money(tax/2);ssv=0 if inter else money(tax-c);ig=tax if inter else 0;sub=money(sub+base);cc=money(cc+c);ss=money(ss+ssv);ii=money(ii+ig);calc.append((pid,desc,str(it.get("hsn","")),str(it.get("unit","pcs")),qty,rate,gst,base,c,ssv,ig,money(base+tax),item_details(it)))
                    total=money(sub+cc+ss+ii)
                    con.execute("UPDATE quotations SET quote_date=?,customer_id=?,customer_snapshot=?,subtotal=?,cgst=?,sgst=?,igst=?,total=?,notes=? WHERE id=?",(qd,customer_id,json.dumps(cust),sub,cc,ss,ii,total,str(d.get("notes","")),qid));con.execute("DELETE FROM quotation_items WHERE quotation_id=?",(qid,))
                    for it in calc:con.execute("INSERT INTO quotation_items(quotation_id,product_id,description,hsn,unit,qty,rate,gst_rate,taxable,cgst,sgst,igst,line_total,details) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(qid,*it))
                    con.commit()
                return self.send_json(200,{"ok":True,"id":qid})
            if p.path=="/api/quotations/status":
                qid=int(d.get("id",0));status=str(d.get("status",""))
                if status not in ("Draft","Accepted","Rejected"):return self.error("Invalid quotation status")
                with db() as con:
                    cur=con.execute("UPDATE quotations SET status=? WHERE id=? AND converted_invoice_id IS NULL",(status,qid))
                    if not cur.rowcount:return self.error("Quotation not found or already converted")
                return self.send_json(200,{"ok":True})
            if p.path=="/api/quotations/convert":
                qid=int(d.get("id",0))
                with db() as con:
                    con.execute("PRAGMA foreign_keys=ON");con.execute("BEGIN IMMEDIATE");q=one(con,"SELECT * FROM quotations WHERE id=?",(qid,))
                    if not q:return self.error("Quotation not found")
                    if q["converted_invoice_id"]:return self.error("This quotation has already been converted")
                    items=rows(con,"SELECT * FROM quotation_items WHERE quotation_id=?",(qid,));payload={"customer_id":q["customer_id"],"invoice_date":date.today().isoformat(),"notes":"Converted from "+q["quote_number"]+(" · "+q["notes"] if q["notes"] else ""),"items":items}
                    iid,no=create_invoice_records(con,payload);con.execute("UPDATE quotations SET status='Converted',converted_invoice_id=? WHERE id=?",(iid,qid));con.commit()
                return self.send_json(200,{"ok":True,"invoice_id":iid,"invoice_number":no})
            if p.path=="/api/warranties":
                customer_id=to_int(d.get("customer_id"),"customer");product_id=to_int(d.get("product_id") or 0,"product") or None;months=to_int(d.get("warranty_months",0),"warranty period");pdate=str(d.get("purchase_date") or date.today().isoformat());date.fromisoformat(pdate)
                if months<1 or months>120:return self.error("Warranty period must be from 1 to 120 months")
                from calendar import monthrange
                dt=date.fromisoformat(pdate);m=dt.month-1+months;y=dt.year+m//12;mo=m%12+1;expiry=date(y,mo,min(dt.day,monthrange(y,mo)[1])).isoformat();code="HF-"+uuid.uuid4().hex[:10].upper()
                with db() as con:
                    if not one(con,"SELECT id FROM customers WHERE id=?",(customer_id,)):return self.error("Select a valid customer")
                    wid=con.execute("INSERT INTO warranties(warranty_code,customer_id,product_id,invoice_id,serial_number,purchase_date,warranty_months,expiry_date,notes) VALUES(?,?,?,?,?,?,?,?,?)",(code,customer_id,product_id,int(d.get("invoice_id") or 0) or None,str(d.get("serial_number","")),pdate,months,expiry,str(d.get("notes","")))).lastrowid
                return self.send_json(200,{"ok":True,"id":wid,"warranty_code":code,"expiry_date":expiry})
            if p.path=="/api/recurring":
                name=str(d.get("template_name","Recurring invoice")).strip();customer_id=int(d.get("customer_id",0));freq=str(d.get("frequency","Monthly"));next_date=str(d.get("next_due_date") or date.today().isoformat());date.fromisoformat(next_date);payload=d.get("payload",{})
                if freq not in ("Weekly","Monthly","Quarterly","Yearly"):return self.error("Invalid recurring frequency")
                if not isinstance(payload,dict) or not isinstance(payload.get("items"),list) or not payload.get("items"):return self.error("Recurring invoice needs at least one item")
                with db() as con:
                    if not one(con,"SELECT id FROM customers WHERE id=?",(customer_id,)):return self.error("Select a valid customer")
                    rid=con.execute("INSERT INTO recurring_templates(template_name,customer_id,frequency,next_due_date,payload_json) VALUES(?,?,?,?,?)",(name,customer_id,freq,next_date,json.dumps(payload))).lastrowid
                return self.send_json(200,{"ok":True,"id":rid})
            if p.path=="/api/recurring/generate":
                rid=int(d.get("id",0))
                with db() as con:
                    con.execute("PRAGMA foreign_keys=ON");con.execute("BEGIN IMMEDIATE");r=one(con,"SELECT * FROM recurring_templates WHERE id=? AND active=1",(rid,))
                    if not r:return self.error("Active recurring template not found")
                    if r["next_due_date"]>date.today().isoformat():return self.error("This recurring invoice is not due yet")
                    payload=json.loads(r["payload_json"]);payload["customer_id"]=r["customer_id"];payload["invoice_date"]=date.today().isoformat();payload["notes"]=(payload.get("notes","")+f" · Recurring: {r['template_name']}").strip(" ·")
                    iid,no=create_invoice_records(con,payload)
                        # No external package assumption: advance calendar interval explicitly.
                    due=date.fromisoformat(r["next_due_date"])
                    if r["frequency"]=="Weekly": due=date.fromordinal(due.toordinal()+7)
                    else:
                        months={"Monthly":1,"Quarterly":3,"Yearly":12}[r["frequency"]];m=due.month-1+months;y=due.year+m//12;mo=m%12+1
                        from calendar import monthrange
                        due=date(y,mo,min(due.day,monthrange(y,mo)[1]))
                    con.execute("UPDATE recurring_templates SET next_due_date=?,last_generated_at=? WHERE id=?",(due.isoformat(),datetime.now().isoformat(timespec="seconds"),rid));con.commit()
                return self.send_json(200,{"ok":True,"invoice_id":iid,"invoice_number":no,"next_due_date":due.isoformat()})
            if p.path=="/api/staff":
                name=str(d.get("name","")).strip();role=str(d.get("role","")).strip();phone=str(d.get("phone","")).strip();join_date=str(d.get("join_date") or date.today().isoformat());date.fromisoformat(join_date);salary=money(d.get("monthly_salary",0))
                if not name or salary<0:return self.error("Employee name and a valid salary are required")
                with db() as con:
                    if d.get("id"):
                        rid=int(d["id"]);cur=con.execute("UPDATE employees SET name=?,phone=?,role=?,join_date=?,monthly_salary=?,active=?,notes=? WHERE id=?",(name,phone,role,join_date,salary,1 if d.get("active",True) else 0,str(d.get("notes","")),rid))
                        if not cur.rowcount:return self.error("Employee not found",404)
                    else:rid=con.execute("INSERT INTO employees(name,phone,role,join_date,monthly_salary,active,notes) VALUES(?,?,?,?,?,?,?)",(name,phone,role,join_date,salary,1 if d.get("active",True) else 0,str(d.get("notes","")))).lastrowid
                return self.send_json(200,{"ok":True,"id":rid})
            if p.path=="/api/payroll":
                employee_id=to_int(d.get("employee_id"),"employee");month=str(d.get("payroll_month") or date.today().strftime("%Y-%m"))
                if not re.fullmatch(r"\d{4}-\d{2}",month):return self.error("Choose a valid payroll month")
                with db() as con:
                    existing=one(con,"SELECT * FROM payroll WHERE id=?",(to_int(d["id"],"payroll entry"),)) if d.get("id") else None
                    if d.get("id") and not existing:return self.error("Payroll entry not found",404)
                    emp=one(con,"SELECT * FROM employees WHERE id=?",(employee_id,))
                    if not emp or (not emp["active"] and not (existing and existing["employee_id"]==employee_id)):return self.error("Select an active employee")
                    basic=money(d.get("basic_salary",emp["monthly_salary"]));allow=money(d.get("allowances",0));ded=money(d.get("deductions",0));net=money(basic+allow-ded)
                    if min(basic,allow,ded)<0 or net<0: return self.error("Salary values are invalid")
                    if d.get("id"):
                        rid=int(d["id"]);paid=one(con,"SELECT COALESCE(SUM(amount),0) v FROM payroll_payments WHERE payroll_id=?",(rid,))["v"]
                        if net<paid: return self.error("Net salary cannot be lower than payments already made")
                        con.execute("UPDATE payroll SET employee_id=?,payroll_month=?,basic_salary=?,allowances=?,deductions=?,net_pay=?,notes=? WHERE id=?",(employee_id,month,basic,allow,ded,net,str(d.get("notes","")),rid))
                    else:
                        rid=con.execute("INSERT INTO payroll(employee_id,payroll_month,basic_salary,allowances,deductions,net_pay,notes) VALUES(?,?,?,?,?,?,?)",(employee_id,month,basic,allow,ded,net,str(d.get("notes","")))).lastrowid
                return self.send_json(200,{"ok":True,"id":rid,"net_pay":net})
            if p.path=="/api/payroll/payment":
                rid=int(d.get("payroll_id",0));amount=money(d.get("amount",0));pd=str(d.get("payment_date") or date.today().isoformat());date.fromisoformat(pd)
                if amount<=0:return self.error("Payment must be greater than zero")
                with db() as con:
                    pr=one(con,"SELECT * FROM payroll WHERE id=?",(rid,))
                    if not pr:return self.error("Payroll entry not found")
                    paid=one(con,"SELECT COALESCE(SUM(amount),0) v FROM payroll_payments WHERE payroll_id=?",(rid,))["v"]
                    if amount>float(pr["net_pay"])-paid+.009:return self.error("Payment exceeds salary balance")
                    con.execute("INSERT INTO payroll_payments(payroll_id,payment_date,amount,method,reference) VALUES(?,?,?,?,?)",(rid,pd,amount,str(d.get("method","Bank transfer")),str(d.get("reference",""))))
                return self.send_json(200,{"ok":True})
            if p.path=="/api/cash-entries":
                ed=str(d.get("entry_date") or date.today().isoformat());date.fromisoformat(ed);direction=str(d.get("direction","Outflow"));cat=str(d.get("category","Other")).strip();desc=str(d.get("description","")).strip();amt=money(d.get("amount",0))
                if direction not in ("Inflow","Outflow") or not desc or amt<=0:return self.error("Enter a valid cash-flow entry")
                with db() as con:
                    if d.get("id"):
                        cur=con.execute("UPDATE cash_entries SET entry_date=?,direction=?,category=?,description=?,amount=?,method=?,reference=? WHERE id=?",(ed,direction,cat,desc,amt,str(d.get("method","Cash")),str(d.get("reference","")),int(d["id"])))
                        if not cur.rowcount:return self.error("Cash-flow entry not found",404)
                    else:con.execute("INSERT INTO cash_entries(entry_date,direction,category,description,amount,method,reference) VALUES(?,?,?,?,?,?,?)",(ed,direction,cat,desc,amt,str(d.get("method","Cash")),str(d.get("reference",""))))
                return self.send_json(200,{"ok":True})
            if p.path=="/api/assistant":
                prompt=str(d.get("prompt","")).strip().lower()
                if not prompt:return self.error("Ask a business question first")
                with db() as con:
                    today_sales=one(con,"SELECT COALESCE(SUM(total),0) v FROM invoices WHERE invoice_date=?",(date.today().isoformat(),))["v"]
                    month_sales=one(con,"SELECT COALESCE(SUM(total),0) v FROM invoices WHERE invoice_date>=?",(date.today().replace(day=1).isoformat(),))["v"]
                    collections=one(con,"SELECT COALESCE(SUM(amount),0) v FROM payments WHERE payment_date>=?",(date.today().replace(day=1).isoformat(),))["v"]
                    expenses=one(con,"SELECT COALESCE(SUM(amount),0) v FROM expenses WHERE expense_date>=?",(date.today().replace(day=1).isoformat(),))["v"]
                    purchases=one(con,"SELECT COALESCE(SUM(total),0) v FROM purchases WHERE purchase_date>=?",(date.today().replace(day=1).isoformat(),))["v"]
                    outstanding=rows(con,"SELECT c.name,SUM(b.total-b.paid-b.credited+b.refunded) balance FROM invoice_balances b JOIN invoices i ON i.id=b.invoice_id JOIN customers c ON c.id=i.customer_id GROUP BY c.id HAVING balance>0.009 ORDER BY balance DESC")
                    low=rows(con,"SELECT name,stock_qty,low_stock_level,unit FROM products WHERE track_stock=1 AND stock_qty<=low_stock_level ORDER BY stock_qty")
                    exp_by=rows(con,"SELECT category,SUM(amount) amount FROM expenses WHERE expense_date>=? GROUP BY category ORDER BY amount DESC",(date.today().replace(day=1).isoformat(),))
                    top_products=rows(con,"SELECT description,SUM(qty) qty,SUM(line_total) revenue FROM invoice_items GROUP BY description ORDER BY revenue DESC LIMIT 5")
                    salary_due=one(con,"SELECT COALESCE(SUM(pr.net_pay-COALESCE(pp.paid,0)),0) v FROM payroll pr LEFT JOIN (SELECT payroll_id,SUM(amount) paid FROM payroll_payments GROUP BY payroll_id) pp ON pp.payroll_id=pr.id WHERE pr.payroll_month=?",(date.today().strftime("%Y-%m"),))["v"]
                    manual_in=one(con,"SELECT COALESCE(SUM(amount),0) v FROM cash_entries WHERE direction='Inflow' AND entry_date>=?",(date.today().replace(day=1).isoformat(),))["v"]
                    manual_out=one(con,"SELECT COALESCE(SUM(amount),0) v FROM cash_entries WHERE direction='Outflow' AND entry_date>=?",(date.today().replace(day=1).isoformat(),))["v"]
                    supplier_out=one(con,"SELECT COALESCE(SUM(amount),0) v FROM supplier_payments WHERE payment_date>=?",(date.today().replace(day=1).isoformat(),))["v"]
                    salary_paid=one(con,"SELECT COALESCE(SUM(amount),0) v FROM payroll_payments WHERE payment_date>=?",(date.today().replace(day=1).isoformat(),))["v"]
                fmt=lambda v:f"₹{float(v or 0):,.2f}"
                cash_in=float(collections)+float(manual_in);cash_out=float(expenses)+float(supplier_out)+float(salary_paid)+float(manual_out)
                if any(k in prompt for k in ("cash flow","cashflow","money in","money out","cash movement","cash position")):
                    answer=f"This month's recorded cash movement:\n• Inflows: {fmt(cash_in)} (customer collections + manual inflows)\n• Outflows: {fmt(cash_out)} (expenses + supplier payments + salary paid + manual outflows)\n• Net movement: {fmt(cash_in-cash_out)}. This excludes opening balances and transactions not entered in HisabFlow."
                elif any(k in prompt for k in ("salary","payroll","staff pay","वेतन")):
                    answer=f"Payroll status for {date.today().strftime('%B %Y')}: salary paid {fmt(salary_paid)}, salary pending {fmt(salary_due)}. Prepare payroll for active employees and record payments to update these figures."
                elif any(k in prompt for k in ("today","आज")) and any(k in prompt for k in ("sales","sale","बिक्री")):
                    answer=f"Today's recorded invoice sales are {fmt(today_sales)}. This is invoice value, not necessarily cash collected."
                elif any(k in prompt for k in ("collection","collected","received","जमा")):
                    answer=f"Collections recorded this month are {fmt(collections)}. Only payment entries are counted."
                elif any(k in prompt for k in ("outstanding","due","udhaar","उधार","बाकी","owed")):
                    answer="Top outstanding customers:\n"+("\n".join(f"• {x['name']}: {fmt(x['balance'])}" for x in outstanding[:8]) if outstanding else "No outstanding customer balances found.")
                elif any(k in prompt for k in ("low stock","stock low","reorder","कम स्टॉक")):
                    answer="Low-stock items:\n"+("\n".join(f"• {x['name']}: {numfmt(x['stock_qty'])} {x['unit']} (alert at {numfmt(x['low_stock_level'])})" for x in low) if low else "No products are currently below their low-stock level.")
                elif any(k in prompt for k in ("top product","best product","popular item","best selling")):
                    answer="Top invoiced products by recorded revenue:\n"+("\n".join(f"• {x['description']}: {fmt(x['revenue'])} ({numfmt(x['qty'])} units)" for x in top_products) if top_products else "No invoice line items found yet.")
                elif any(k in prompt for k in ("expense","खर्च")):
                    answer=f"Expenses recorded this month total {fmt(expenses)}. Purchases recorded this month total {fmt(purchases)}. Highest expense categories: "+("; ".join(f"{x['category']} {fmt(x['amount'])}" for x in exp_by[:5]) if exp_by else "none recorded")
                elif any(k in prompt for k in ("profit","margin","मुनाफा")):
                    answer=f"This month: invoice sales {fmt(month_sales)}, expenses {fmt(expenses)}, and purchase bills {fmt(purchases)}. A reliable net-profit figure needs cost-of-goods sold, stock valuation, payroll and tax adjustments; these totals alone are not accounting profit."
                else:
                    answer=f"Business snapshot for this month:\n• Invoice sales: {fmt(month_sales)}\n• Collections: {fmt(collections)}\n• Expenses: {fmt(expenses)}\n• Purchase bills: {fmt(purchases)}\n• Outstanding customer dues: {fmt(sum(x['balance'] for x in outstanding))}\n• Low-stock items: {len(low)}\n\nTry asking about today's sales, collections, outstanding dues, low stock, top products or expenses."
                return self.send_json(200,{"answer":answer,"engine":"Local business insights","privacy":"Local rules-based assistant, not a generative AI model. Text questions and business records stay on this computer; optional voice transcription is handled separately by your browser speech service."})
            if p.path=="/api/restore":
                self.restore(d);return
            self.error("Not found",404)
        except (ValueError,TypeError,KeyError) as e: self.error(str(e))
        except sqlite3.IntegrityError as e: self.error(friendly_integrity(e))
        except Exception as e: self.error(f"Unexpected error: {e}",500)

    def restore(self,data):
        # Restore v2 table backup or legacy v0.1 export. Data is transactional.
        # Login accounts are never replaced by a restore. A safety snapshot is taken first.
        auto_backup(force=True,suffix="pre-restore")
        with db() as con:
            con.execute("PRAGMA foreign_keys=ON");con.execute("BEGIN IMMEDIATE")
            try:
                if "tables" in data and isinstance(data["tables"],dict):
                    order=["credit_note_items","credit_notes","payroll_payments","payroll","employees","cash_entries","payments","supplier_payments","purchase_return_items","purchase_returns","invoice_items","purchase_items","quotation_items","warranties","recurring_templates","quotations","purchases","expenses","stock_movements","invoices","customers","suppliers","products"]
                    for t in order: con.execute(f"DELETE FROM {t}")
                    tables=data["tables"]
                    biz=tables.get("business") or []
                    if isinstance(biz,list) and biz and isinstance(biz[0],dict):
                        b={k:("" if v is None else v) for k,v in biz[0].items()};b["invoice_prefix"]=b.get("invoice_prefix") or "INV"
                        con.execute("UPDATE business SET name=?,gstin=?,state=?,address=?,phone=?,email=?,invoice_prefix=?,bank_details=?,upi_id=?,logo_data=? WHERE id=1",tuple(str(b.get(k,"")) for k in ["name","gstin","state","address","phone","email","invoice_prefix","bank_details","upi_id","logo_data"]))
                    # Insert parent tables first so foreign keys are satisfied (reverse of the delete order).
                    for t in reversed(order):
                        records=tables.get(t) or []
                        if not isinstance(records,list): raise ValueError(f"Backup table {t} is damaged")
                        cols={r[1] for r in con.execute(f"PRAGMA table_info({t})")}
                        for rec in records:
                            if not isinstance(rec,dict): raise ValueError(f"Backup table {t} is damaged")
                            valid={k:v for k,v in rec.items() if k in cols}
                            if valid:
                                keys=list(valid);con.execute(f"INSERT INTO {t}({','.join(keys)}) VALUES({','.join('?' for _ in keys)})",[valid[k] for k in keys])
                else:
                    # Upgrade path for backups made by the first MVP.
                    for t in ["credit_note_items","credit_notes","payroll_payments","payroll","employees","cash_entries","payments","supplier_payments","purchase_return_items","purchase_returns","invoice_items","purchase_items","quotation_items","warranties","recurring_templates","quotations","purchases","expenses","stock_movements","invoices","customers","suppliers","products"]:con.execute(f"DELETE FROM {t}")
                    b={k:("" if v is None else v) for k,v in (data.get("business") or {}).items()};con.execute("UPDATE business SET name=?,gstin=?,state=?,address=?,phone=?,email=?,invoice_prefix=?,bank_details=?,upi_id=?,logo_data=? WHERE id=1",tuple(str(b.get(k,"")) for k in ["name","gstin","state","address","phone","email","invoice_prefix","bank_details","upi_id","logo_data"]))
                    for c in data.get("customers",[]):con.execute("INSERT INTO customers(id,name,phone,email,gstin,address,state) VALUES(?,?,?,?,?,?,?)",(c["id"],c["name"],c.get("phone",""),c.get("email",""),c.get("gstin",""),c.get("address",""),c.get("state","")))
                    for x in data.get("products",[]):con.execute("INSERT INTO products(id,name,hsn,unit,rate,gst_rate) VALUES(?,?,?,?,?,?)",(x["id"],x["name"],x.get("hsn",""),x.get("unit","pcs"),x.get("rate",0),x.get("gst_rate",0)))
                    for i in data.get("invoices",[]):
                        con.execute("INSERT INTO invoices(id,invoice_number,invoice_date,customer_id,business_snapshot,customer_snapshot,place_of_supply,supply_type,subtotal,cgst,sgst,igst,total,notes) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(i["id"],i["invoice_number"],i["invoice_date"],i["customer_id"],i.get("business_snapshot") or json.dumps(b),i.get("customer_snapshot") or "{}",i.get("place_of_supply",""),i.get("supply_type","CGST/SGST"),i.get("subtotal",0),i.get("cgst",0),i.get("sgst",0),i.get("igst",0),i.get("total",0),i.get("notes","")))
                        for it in i.get("items",[]):con.execute("INSERT INTO invoice_items(invoice_id,product_id,description,hsn,unit,qty,rate,gst_rate,taxable,cgst,sgst,igst,line_total) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",(i["id"],it.get("product_id"),it["description"],it.get("hsn",""),it.get("unit","pcs"),it["qty"],it["rate"],it["gst_rate"],it["taxable"],it["cgst"],it["sgst"],it["igst"],it["line_total"]))
                    for x in data.get("payments",[]):con.execute("INSERT INTO payments(id,invoice_id,payment_date,amount,method,reference) VALUES(?,?,?,?,?,?)",(x["id"],x["invoice_id"],x["payment_date"],x["amount"],x.get("method","Cash"),x.get("reference","")))
                con.commit()
            except Exception:con.rollback();raise
        self.send_json(200,{"ok":True})

    def route_DELETE(self):
        p=urlparse(self.path);q=parse_qs(p.query)
        try:
            rid=int(q.get("id",[""])[0]);table={"/api/customers":"customers","/api/products":"products","/api/suppliers":"suppliers","/api/expenses":"expenses","/api/recurring":"recurring_templates","/api/staff":"employees","/api/cash-entries":"cash_entries"}.get(p.path)
            if not table:return self.error("Not found",404)
            with db() as con:
                con.execute("PRAGMA foreign_keys=ON");cur=con.execute(f"DELETE FROM {table} WHERE id=?",(rid,))
                if cur.rowcount==0:return self.error("Record not found",404)
            self.send_json(200,{"ok":True})
        except sqlite3.IntegrityError:self.error("This record is linked to other business records and cannot be deleted.")
        except ValueError:self.error("Invalid record ID")


def periodic_backup():
    for _, path, prefix in all_company_paths(): auto_backup(force=True, path=path, prefix=prefix)
    timer=threading.Timer(3600,periodic_backup);timer.daemon=True;timer.start()


def reset_password_cli(username):
    """Offline password recovery: run `python app.py --reset-password <username>` on this computer."""
    import getpass
    init_db()
    with master_db() as con:
        user = one(con, "SELECT * FROM users WHERE username=? COLLATE NOCASE", (username,))
        if not user:
            names = [r["username"] for r in rows(con, "SELECT username FROM users ORDER BY username")]
            print(f"No user named '{username}'. Existing users: {', '.join(names) or 'none (open the app to create the owner account)'}")
            return 1
        pw = getpass.getpass(f"New password for {user['username']}: ")
        if pw != getpass.getpass("Repeat new password: "):
            print("Passwords do not match."); return 1
        try: validate_password(pw)
        except ValueError as e: print(e); return 1
        con.execute("UPDATE users SET password_hash=?, active=1 WHERE id=?", (hash_password(pw), user["id"]))
        con.execute("DELETE FROM sessions WHERE user_id=?", (user["id"],))
    print(f"Password updated for {user['username']}. Start HisabFlow and sign in.")
    return 0


def main():
    if len(sys.argv) >= 2 and sys.argv[1] in ("--reset-password", "--list-users"):
        if sys.argv[1] == "--list-users":
            init_db()
            with master_db() as con:
                for u in rows(con, "SELECT username, role, active FROM users ORDER BY username"):
                    print(f"{u['username']:<24} {u['role']:<6} {'active' if u['active'] else 'disabled'}")
            return
        if len(sys.argv) < 3: print("Usage: python app.py --reset-password <username>"); sys.exit(1)
        sys.exit(reset_password_cli(sys.argv[2]))
    init_db();url=f"http://{HOST}:{PORT}"
    try: server=ThreadingHTTPServer((HOST,PORT),Handler)
    except OSError:
        print("="*58);print(f"  Port {PORT} is already in use.");print("  HisabFlow may already be running - opening it in your browser.");print("  To use another port: set HISABFLOW_PORT=8766");print("="*58)
        webbrowser.open(url);return
    periodic_backup()
    print("="*58);print(f"  HisabFlow {APP_VERSION} is running locally");print(f"  Open: {url}");print(f"  Database: {DB_PATH}");print(f"  Automatic local backups: {BACKUP_DIR}");print("  Keep this window open while using the app.");print("  Press Ctrl+C to stop.");print("="*58)
    threading.Timer(1.0,lambda:webbrowser.open(url)).start()
    try:server.serve_forever()
    except KeyboardInterrupt:print("\nHisabFlow stopped.")
    finally:
        for _, path, prefix in all_company_paths(): auto_backup(force=True, path=path, prefix=prefix)
        server.server_close()

if __name__=="__main__":main()
