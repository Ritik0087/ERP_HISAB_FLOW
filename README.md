# HisabFlow 4.0 — Offline-first business management

HisabFlow is a Windows-friendly business operations prototype for Indian micro and small businesses. It combines billing, customer and supplier records, inventory, expenses, cash movement, staff payroll and basic business insights in one local application.

The application runs a small Python server on `127.0.0.1` and opens its interface in the browser. Records are stored in a local SQLite database on the same computer.

## Run on Windows

1. Install Python 3.10 or newer from https://www.python.org/downloads/ and select **Add Python to PATH** during setup.
2. Extract the entire ZIP folder to a normal location, for example `C:\HisabFlow`.
3. Double-click `run_hisabflow.bat`.
4. The application opens at `http://127.0.0.1:8765`.
5. Keep the black command window open while using HisabFlow. Close it or press Ctrl+C to stop.

No pip install, paid hosting, online account or internet connection is required for the core local application. The QR generator is bundled in `vendor/` with its upstream licence.

## New in 4.0

### Layout
- Sidebar grouped like an accounting package: **Sales, Purchases, Inventory, Accounts & GST, Team & tools**. Groups can be collapsed, and the **‹** button shrinks the sidebar to icons.
- Top bar: company switcher, a search box (**Ctrl + K**) and a **＋ Create** menu for every voucher type.
- On phones the menu opens from the **☰** button.

### Keyboard shortcuts (Tally-style)
| Key | Action |
|---|---|
| F8 / Ctrl+F8 | Sales invoice / credit note |
| F9 / Ctrl+F9 | Purchase bill / debit note |
| F6 | Receipt (customer payment) |
| F5 | Payment (supplier bill, expense or salary) |
| F7 / F4 | Expense / cash entry |
| Alt+C | Create customer, supplier, item or employee |
| Ctrl+K or / | Search and jump to anything |
| Ctrl+S or Ctrl+Enter | Save the open form |
| G then D, I, N, C, P, S, T, K, E, X, R, F | Go to a page |
| ? | Show all shortcuts |

### Item details (serial numbers and more)
- Each invoice or quotation line has a **details** box under the description. Use it for serial / IMEI numbers, model, colour, size, batch or warranty, one per line if you like. Details print under the item name on every template.
- You can set **default details** on an item in Items & services. They are filled in automatically when the item is added to an invoice.
- To find which invoice sold a serial number, press **Ctrl+K** and type the number.

### Credit notes and debit notes
- **Credit notes** (sales returns or price reductions) are issued against an invoice, line by line. They reduce the invoice balance, can put goods back in stock and can record a cash refund. Numbering: `CN/2026-27/0001`.
- An invoice that has a credit note is locked for editing. Issue another credit note instead.
- **Debit notes** are returns to a supplier (the purchase returns from earlier versions), now with several lines per note and a printable document. New numbers use `DN/2026-27/0001`; older `PR-00001` numbers are kept.

### GST returns (Accounts & GST → GST returns)
- **GSTR-1:** B2B, B2C Large, B2C Small, credit notes (CDNR/CDNUR), nil-rated, HSN summary (split into B2B and B2C) and documents issued.
- **GSTR-3B:** outward supplies (3.1), inter-state supplies to unregistered customers (3.2), ITC from purchase bills less debit notes (4), exempt inward supplies (5), and an estimate of tax payable after ITC set-off (6.1).
- **Excel – GST sales register** (main button): one row per invoice item with the columns *GSTIN, Date, Customer Name, Invoice ID, Taxable Value, CGST, SGST, IGST, Total Invoice Value, HSN Code, Item Name*, plus a Total row. Credit notes are on a second sheet with negative amounts. *Total Invoice Value* repeats on each item row of the same invoice; the Total row counts each invoice only once.
- **Detailed return sheets:** a workbook laid out like the GST portal for GSTR-1 or GSTR-3B. **JSON:** a GSTR-1 file in the GST offline tool's layout.
- **Limits:** these are preparation aids. HisabFlow does not file returns. Exempt and non-GST supplies are all shown as nil-rated, and ITC is not matched with GSTR-2B and does not exclude blocked credits. Import the JSON into the official GST offline tool to validate it before uploading, and have a GST professional review your returns.

### Invoice templates (Settings → Invoice design)
- Four templates: **Modern**, **Classic GST** (boxed, Tally-style), **Minimal** (black and white) and **Thermal 80mm** (POS receipt printers).
- You can choose an accent colour and write terms & conditions.
- Optional extras: a **UPI payment QR** for the balance due (needs a UPI ID in business settings) and an **HSN-wise tax summary**.
- Every printed document shows the **amount in words** (Indian numbering: lakh, crore).
- The same design is used for invoices, credit notes, debit notes, quotations and purchase bills. A live preview updates as you change settings.

### Multiple companies
- Use the company switcher (top bar) → **Create new company**.
- Each company has its own customers, items, invoices, numbering, GST returns and daily backups (`data/companies/company-N.db`).
- Users and passwords are shared by all companies.
- Owners can **archive** a company in Settings. Its file is moved to `data/archived_companies/` and a backup is taken first, so it can be recovered.

### Dashboard charts
- Sales versus collections over the last 12 months, top customers, and expense split.
- Point at a chart to see exact figures, or click **Table** to see the numbers as a table.

## Sign-in and users (new in 3.1)

HisabFlow now asks for a password. Everything stays offline: accounts are stored in the local database, and no email or internet account is involved.

- **First launch:** you are asked to create the **owner** account (name, username, password).
- **Owner** accounts can do everything, including business settings, backups/restores and managing users.
- **Staff** accounts can create invoices and record daily transactions, but cannot change business settings, users or backups.
- Add, disable or reset staff passwords in **Business settings → Users & access**.
- Change your own password from the account menu (top-right) → **Change password**.
- Tick **Keep me signed in for 30 days** on the login screen to stay signed in on this computer; otherwise the session lasts 12 hours.
- After 5 wrong passwords, that username is locked for 5 minutes.

**Forgot the password?** There is no email reset because the app works offline. Open a Command Prompt in the HisabFlow folder and run:

```
python app.py --reset-password YOUR_USERNAME
python app.py --list-users
```

Backups (JSON) contain business records only. They do not include login accounts or passwords, and restoring a backup never changes the users.

## Version 3.0 features

### Business profile and printed documents
- Business profile, GSTIN format field, state, address, contact, bank and UPI details
- Upload a PNG, JPG or WebP business logo (650 KB maximum)
- Business logo on invoice, purchase and quotation printouts, warranty cards and salary slips
- Print documents or use the browser's **Save as PDF** option

### Billing and editing
- Customer and product catalogue
- GST invoice calculation: CGST/SGST for same-state and IGST for interstate transactions
- Financial-year invoice numbering
- Multiple invoice payments, partial-payment history and customer balances
- Edit saved invoices while preserving their invoice number and payment history
- Invoice edits recalculate tax and totals and reverse/reapply tracked stock effects
- Purchase bills can be edited only before supplier payments or purchase returns exist; this protects linked accounting records
- Edit expense entries and unconverted quotations
- Converted quotations are locked; they can be viewed and printed

### Barcode and QR tools
- Save a unique barcode/SKU for each product
- Scan a code into invoice or purchase lines using a USB barcode scanner (keyboard-wedge type)
- Camera scan for supported barcode/QR formats in browsers that expose `BarcodeDetector`, with camera permission
- Generate and print HisabFlow product QR labels
- QR labels contain a local product code/ID; this offline edition does not provide public cloud product lookup

### Inventory and purchasing
- Optional stock tracking per product or service
- Opening stock, low-stock threshold, stock valuation and adjustment history
- Stock deducted on invoices and received on purchase bills
- Supplier records, supplier balances, purchase returns and supplier payment history
- Purchase return recording and stock reduction

### Cash flow and expenses
- Cash movement ledger with date filters and CSV export
- Customer receipts, supplier payments, expense payments and payroll payments appear in the ledger
- Manual inflow/outflow records for owner capital, loan funds, deposits, withdrawals and other movements
- Edit or delete manual cash entries
- Expense categories, payment modes and edit capability
- Do not create manual entries for receipts or payouts that are already recorded elsewhere, or you will double-count them

### Staff and payroll
- Employee profiles, contact details, role, joining date and active/inactive status
- Prepare monthly salary using basic salary + allowances − deductions
- Record full or partial salary payments
- Salary balance tracking and printable salary slips
- This is a simple payroll register, not statutory payroll processing

### Local business assistant and smart insights
- Ask supported questions about today's sales, this month's sales, collections, outstanding dues, low stock, top products, expenses, cash flow and payroll
- Smart alerts for low-stock items, outstanding customer balances and month-on-month sales comparison
- Expense category suggestions based on description keywords
- Optional voice-to-text questions in English or Hindi using the browser's speech-recognition service
- The current assistant is deterministic and rules-based. It is **not a generative AI model**. Text questions and business records are processed by the local app; optional voice transcription is handled by the browser speech service and may require internet access

### Other modules retained
- Customer khata / ledger and WhatsApp reminder hand-off links
- Purchase and supplier tracking
- Quotations with conversion to invoices
- Warranty register with serial numbers, expiry dates and printable QR cards
- Recurring invoice templates (weekly, monthly, quarterly, yearly) with user-triggered invoice generation
- Sales, collection, expense and estimated profit reports
- CSV export, JSON backup / restore and automatic dated SQLite backups

## Important limits

- The app is local-only and designed for one computer. Several local user accounts are supported, but there is no cloud sync, remote access or multi-branch access.
- USB scanners work as keyboard input. Camera scanning depends on browser support for the `BarcodeDetector` API and camera permissions; Chrome/Edge versions may differ.
- WhatsApp actions open a pre-filled message for the user to review and send. They do not send automatically.
- Recurring invoices are generated only when the user clicks **Generate invoice** on a due template. There is no background scheduler while the app is closed.
- The AI/business assistant uses local keyword rules and calculations. OCR invoice extraction, predictive forecasting and generative AI provider integration are not included in this build.
- The cash-flow view totals recorded movements; it does not import bank statements, reconcile bank accounts or calculate opening balances automatically.
- Payroll does not calculate statutory PF, ESI, TDS, professional tax or generate government filings.
- Purchase bills with a recorded supplier payment or return are not editable. Use an adjustment record or correction process instead.

## GST and business-use caution

HisabFlow checks the GSTIN's 15-character alphanumeric format only; it does not verify GST registration with GSTN. HSN/SAC codes, GST rates, place of supply, tax treatment and invoice disclosures are user-entered and must be reviewed for each transaction. The app does not file GST returns, generate e-way bills or register IRNs/e-invoices. Have a GST professional review the setup before using it for statutory billing or filings.

## Data and backups

- Main company database: `data/hisabflow.db` (also stores user accounts)
- Additional companies: `data/companies/company-N.db`
- Automatic snapshots: `data/auto_backups/`
- Use **Business settings → Download backup** for a portable JSON copy.
- Restoring a backup replaces the current business records. Download a fresh backup before restoring.
- Store a copy of the backup on a separate drive or secure cloud location; automatic snapshots on the same computer do not protect against disk failure.

## Technology

Python standard-library HTTP server + SQLite + HTML/CSS/JavaScript (`ui/index.html`, `ui/styles.css`, `ui/app.js`, `ui/features.js`). GST calculations and the Excel writer are in `gst.py`. QR generation uses the vendored Python `qrcode` library (licence included in `vendor/`). The server binds to `127.0.0.1` and is intended for use on the same computer only.
"# ERP_HISAB_FLOW" 
