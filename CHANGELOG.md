# Changelog

## 4.2 — GST sales register export

- **Excel – GST sales register** button on GST returns: one row per invoice item with columns GSTIN, Date, Customer Name, Invoice ID, Taxable Value, CGST, SGST, IGST, Total Invoice Value, HSN Code, Item Name. Dates are real Excel dates (dd-mm-yyyy), amounts are numbers, and a bold Total row is included.
- Credit notes for the period go on a second sheet in the same layout, with negative amounts and an Against Invoice column.
- The previous portal-style GSTR-1 / GSTR-3B workbook is still available as **Detailed return sheets**.

## 4.1 — Item details on invoices

- Every invoice and quotation line has a **details** box under the description for serial / IMEI numbers, model, colour, size, batch, warranty and similar notes. Line breaks are kept.
- Items can store **default details** that are copied onto new invoice lines, including lines added by barcode scan.
- Details appear on screen and in all print templates (Modern, Classic GST, Minimal, Thermal). They are carried into invoices converted from quotations, into credit notes and into recurring templates.
- Ctrl+K search finds the invoice that sold a serial number.

## 4.0 — Credit/debit notes, GST returns, shortcuts, companies, new layout

- New layout: grouped, collapsible sidebar (Sales / Purchases / Inventory / Accounts & GST / Team & tools) with an icon-only mode. New top bar with breadcrumb, company switcher, search and a Create menu. Slide-in menu on phones.
- Credit notes against invoices, line by line, with optional restocking and cash refunds. Invoice balances, statuses, the dashboard, cash flow, reports and the assistant now include credit notes. Invoices with credit notes are locked for editing.
- Debit notes (purchase returns) now support several lines, have their own list and print view, and use `DN/FY/0001` numbering.
- GSTR-1 (B2B, B2CL, B2CS, CDNR, CDNUR, nil, HSN B2B/B2C, documents) and GSTR-3B (3.1, 3.2, 4, 5, 6.1 set-off estimate) screens, with Excel (.xlsx) and GSTR-1 JSON export. Excel files are written with the Python standard library.
- Keyboard shortcuts: F4–F9, Ctrl+F8/F9, Alt+C, Ctrl+S / Ctrl+Enter, G-key navigation and a shortcut help sheet (?).
- Command palette (Ctrl+K) that searches invoices, customers, items, suppliers, bills, notes, quotations and staff, and runs actions.
- Dashboard charts: 12-month sales vs collections, top customers and expense split, each with tooltips and a table view.
- Invoice templates: Modern, Classic GST, Minimal and Thermal 80mm, with accent colour, terms & conditions, UPI payment QR, HSN tax summary, amount in words and a live preview. Used for invoices, credit/debit notes, quotations and purchase bills.
- Multiple companies, each in its own database file with its own backups. Create, switch and archive companies. Users are shared.
- The frontend is split into `index.html`, `styles.css`, `app.js` and `features.js`. The server only serves files from `ui/` that it explicitly allows.


## 3.1 — Sign-in, security, bug fixes and new look

- Added an offline sign-in system: first-run owner setup, login/logout, "keep me signed in", change password, owner/staff roles, user management, lockout after repeated failures, and `--reset-password` / `--list-users` command-line recovery. Passwords are stored as salted PBKDF2-SHA256 hashes.
- Security: the local server now rejects requests from other websites (Origin/Host checks and JSON-only POSTs). Previously, any web page open in the browser could post to the app, including to the restore endpoint.
- Fixed: sidebar navigation buttons did nothing (they were never connected to a click handler).
- Fixed: "today" and "this month" used UTC dates, so in India the month start was the last day of the previous month and early-morning entries landed on the previous day.
- Fixed: editing an invoice reset each saved line's description, price and GST to the current catalogue values.
- Fixed: invoice, purchase, quotation and return numbers were based on a count, which could produce duplicate numbers after a restore or prefix change. Purchase and quotation numbers now use the document's year.
- Fixed: stock quantities were rounded to 2 decimals in product edits, adjustments and purchase edits (invoices use 3).
- Fixed: barcode lookup could return the wrong product when a barcode matched another product's ID.
- Fixed: restoring a backup could fail on foreign keys because tables were inserted in file order. A safety snapshot is now taken before every restore.
- Fixed: business logos printed at full size on invoices, quotations, warranty cards and payslips.
- Fixed: payroll entries for employees later marked inactive could not be edited.
- Fixed: the Reports "Export CSV" button exported all invoices instead of the selected period. CSV cells are now protected against spreadsheet formula injection.
- Fixed: database connections were never closed, and GET requests had no error handling. Error messages for duplicate barcodes, duplicate payroll months and invalid IDs are now clear.
- Starting the app while it is already running opens the existing window instead of crashing.
- New colourful, cleaner interface: redesigned sidebar, dashboard, stat cards, forms, tables, modals, toasts (errors shown in red) and mobile layout.


## 3.0 — Business operations expansion

- Added business logo upload and placement on invoices, purchase bills, quotations, warranty cards and salary slips.
- Added edit flows for saved invoices, eligible purchase bills, expenses and unconverted quotations.
- Kept invoice numbers and payment history when editing invoices; recalculated totals and tracked stock effects.
- Added barcode/SKU fields, product QR label generation, USB scanner input and supported-browser camera scanning on invoice and purchase forms.
- Added cash-flow ledger for receipts, supplier payments, expenses, payroll and manual cash entries.
- Added employee profiles, monthly payroll preparation, partial salary payments and printable salary slips.
- Added offline rules-based business assistant, cash-flow/payroll answers, voice question input and smart business alerts.
- Added product barcode lookup and improved stock validation for repeated product lines in an invoice.
- Extended database backup/restore for employee, payroll and cash-flow records.

## 2.0 — Business management expansion

- Added inventory, supplier/purchase management, purchase returns, customer/supplier ledgers, expenses, quotations, warranties and recurring invoice templates.
- Added WhatsApp message hand-off links and CSV/JSON exports.

## 0.1 — Initial MVP

- Added business setup, customer and product records, GST invoice calculation, PDF printing, payment tracking and basic reports.
