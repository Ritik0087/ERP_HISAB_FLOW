/* HisabFlow 4.0 features
   - Layout: grouped sidebar, collapsible rail, company switcher, Create menu
   - Multiple companies
   - Credit notes (sales returns) and debit notes (purchase returns)
   - GSTR-1 / GSTR-3B summaries with Excel + JSON export
   - Keyboard shortcuts (Tally-style F-keys) and Ctrl+K command palette
   - Dashboard charts
   - Invoice templates (Modern, Classic GST, Minimal, Thermal 80mm)
   Loaded after app.js; it extends the functions defined there. */

// =====================================================================================
// 1. Navigation metadata + wrappers around app.js functions
// =====================================================================================
Object.assign(titles, {
  creditnotes: ['Credit notes', 'Sales returns and price reductions.'],
  debitnotes: ['Debit notes', 'Purchase returns to suppliers.'],
  gst: ['GST returns', 'GSTR-1 and GSTR-3B preparation.'],
});
titles.payments[0] = 'Receipts & dues'; titles.inventory[0] = 'Stock'; titles.purchases[0] = 'Purchase bills'; titles.settings[0] = 'Settings';
const VIEW_GROUP = {
  dashboard: 'Overview', invoices: 'Sales', creditnotes: 'Sales', payments: 'Sales', quotations: 'Sales', recurring: 'Sales', customers: 'Sales',
  purchases: 'Purchases', debitnotes: 'Purchases', suppliers: 'Purchases', expenses: 'Purchases',
  items: 'Inventory', inventory: 'Inventory', warranties: 'Inventory',
  gst: 'Accounts & GST', cashflow: 'Accounts & GST', reports: 'Accounts & GST',
  staff: 'Team & tools', assistant: 'Team & tools', settings: 'Team & tools',
};

const _navigate = navigate;
navigate = function (view) {
  if (!titles[view]) view = 'dashboard';
  _navigate(view);
  $('#crumbGroup').textContent = VIEW_GROUP[view] || '';
  const btn = $(`.nav-btn[data-view="${view}"]`);
  btn?.closest('.nav-group')?.classList.remove('collapsed');
  if (view === 'creditnotes') renderCreditNotes();
  if (view === 'debitnotes') renderDebitNotes();
  if (view === 'gst') initGST();
  if (view === 'settings') { renderDesignPanel(); renderCompaniesPanel(); }
  if (view === 'dashboard') renderCharts();
  toggleMobileNav(false);
  try { localStorage.setItem('hf.view', view); } catch (_) {}
};

const _renderAll = renderAll;
renderAll = function () {
  _renderAll();
  renderCreditNotes(); renderDebitNotes(); renderCharts(); renderCompanyChip();
  if (activeView === 'gst') renderGST();
  if (activeView === 'settings') { renderDesignPanel(); renderCompaniesPanel(); }
};

const _saveBusiness = saveBusiness;
saveBusiness = async function () { await _saveBusiness(); loadCompanies(); };

// Cash-flow ledger: refunds paid against credit notes are money out.
const _cashFlowRows = cashFlowRows;
cashFlowRows = function () {
  const list = _cashFlowRows();
  (state.credit_notes || []).filter(c => Number(c.refund_amount) > 0).forEach(c => list.push({
    date: c.note_date, direction: 'Outflow', category: 'Customer refund', description: `${c.customer_name} · ${c.note_number}`,
    method: c.refund_method || 'Cash', amount: Number(c.refund_amount), source: 'Credit note',
  }));
  return list.sort((a, b) => b.date.localeCompare(a.date));
};

// Reports: show sales returns for the period so net sales are visible.
const _renderReports = renderReports;
renderReports = function () {
  _renderReports();
  const from = $('#reportFrom')?.value || monthStart(), to = $('#reportTo')?.value || today();
  const notes = (state.credit_notes || []).filter(c => c.note_date >= from && c.note_date <= to);
  const inv = state.invoices.filter(i => i.invoice_date >= from && i.invoice_date <= to);
  const gross = inv.reduce((a, i) => a + Number(i.subtotal || 0), 0), ret = notes.reduce((a, c) => a + Number(c.subtotal || 0), 0);
  const box = document.createElement('div');
  box.className = 'notice info-note'; box.style.marginBottom = '15px';
  box.innerHTML = `<b>Sales returns in this period:</b> ${notes.length} credit note${notes.length === 1 ? '' : 's'} · taxable value ${money(ret)}. <b>Net sales before tax:</b> ${money(gross - ret)} (invoiced ${money(gross)}). Profit estimates above use invoiced sales; subtract returns for a net view.`;
  $('#reportContent')?.prepend(box);
};

// Invoice details: add a Credit note action and list existing credit notes.
const _viewInvoice = viewInvoice;
viewInvoice = async function (id) {
  await _viewInvoice(id);
  if ($('#modalTitle').textContent !== 'Invoice details') return;
  const inv = state.invoices.find(i => i.id === id); if (!inv) return;
  const foot = $('#modalFoot');
  const notes = (state.credit_notes || []).filter(c => c.invoice_id === id);
  if (notes.length) {
    [...foot.querySelectorAll('button')].filter(b => b.textContent.includes('Edit invoice')).forEach(b => b.remove());
    $('#modalBody').insertAdjacentHTML('beforeend', `<div class="notice info-note" style="margin-top:12px"><b>Credit notes:</b> ${notes.map(c => `<a class="click" onclick="viewCreditNote(${c.id})">${esc(c.note_number)}</a> (${money(c.total)}${Number(c.refund_amount) ? ', refunded ' + money(c.refund_amount) : ''})`).join(' · ')}. Invoices with credit notes are locked for editing.</div>`);
  }
  if (Number(inv.credited || 0) < Number(inv.total) - 0.009) {
    const b = document.createElement('button'); b.className = 'btn'; b.textContent = '↩ Credit note';
    b.onclick = () => { closeModal(); openCreditNoteForm(id); };
    foot.insertBefore(b, foot.querySelector('.btn.primary'));
  }
};

// =====================================================================================
// 2. Layout: sidebar rail, groups, mobile drawer, menus
// =====================================================================================
function toggleRail() {
  document.body.classList.toggle('rail');
  try { localStorage.setItem('hf.rail', document.body.classList.contains('rail') ? '1' : '0'); } catch (_) {}
}
function toggleGroup(btn) {
  const g = btn.closest('.nav-group'); g.classList.toggle('collapsed');
  btn.setAttribute('aria-expanded', String(!g.classList.contains('collapsed')));
  try { localStorage.setItem('hf.groups', JSON.stringify($$('.nav-group.collapsed').map(x => x.dataset.group))); } catch (_) {}
}
function applyLayoutPrefs() {
  try {
    if (localStorage.getItem('hf.rail') === '1') document.body.classList.add('rail');
    JSON.parse(localStorage.getItem('hf.groups') || '[]').forEach(k => $(`.nav-group[data-group="${k}"]`)?.classList.add('collapsed'));
  } catch (_) {}
}
function toggleMobileNav(force) {
  const open = force === undefined ? !document.body.classList.contains('nav-open') : force;
  document.body.classList.toggle('nav-open', open);
}
function closeMenus() { $$('.menu.show').forEach(m => m.classList.remove('show')); closeUserMenu?.(); }
function toggleNewMenu(e) { e.stopPropagation(); const m = $('#newMenu'), on = !m.classList.contains('show'); closeMenus(); m.classList.toggle('show', on); }
function toggleCompanyMenu(e) { e.stopPropagation(); const m = $('#companyMenu'), on = !m.classList.contains('show'); closeMenus(); m.classList.toggle('show', on); }
document.addEventListener('click', e => { if (!e.target.closest('.menu,.new-menu,.company-switch')) $$('.menu.show').forEach(m => m.classList.remove('show')); });

// =====================================================================================
// 3. Companies
// =====================================================================================
let companies = [];
async function loadCompanies() {
  try { companies = await api('/api/companies'); } catch (_) { companies = []; }
  renderCompanyChip(); renderCompaniesPanel();
}
function renderCompanyChip() {
  const cur = companies.find(c => c.current);
  const name = state.business?.name || cur?.name || 'My business';
  if ($('#companyName')) $('#companyName').textContent = name;
  if ($('#companyDot')) $('#companyDot').textContent = initials({ display_name: name });
  const menu = $('#companyMenu'); if (!menu) return;
  menu.innerHTML = '<div class="menu-title">Switch company</div>' + companies.map(c => `<button onclick="switchCompany(${c.id})" class="${c.current ? 'on' : ''}"><span class="mi">${esc(initials({ display_name: c.name }))}</span><span>${esc(c.name)}</span>${c.current ? '<span class="tick">✓</span>' : ''}</button>`).join('')
    + (isOwner() ? '<div class="menu-sep"></div><button onclick="closeMenus();openCompanyForm()"><span class="mi">＋</span><span>Create new company</span></button>' : '');
}
async function switchCompany(id) {
  closeMenus();
  if (companies.find(c => c.id === id)?.current) return;
  try {
    await api('/api/companies/switch', { method: 'POST', body: JSON.stringify({ id }) });
    closeModal(); await startApp(true); navigate('dashboard');
    toast(`Switched to ${state.business?.name || 'company'}`);
  } catch (e) { toast(e.message, 'error'); }
}
function openCompanyForm() {
  const body = `<form id="companyForm"><div class="form-grid"><div class="field span2"><label>Company / business name *</label><input name="name" required maxlength="80" placeholder="e.g. Shree Ganesh Traders – Branch 2"></div><div class="field span2"><label>State</label><select name="state"><option value="">Select state</option>${state.states.map(s => `<option>${esc(s)}</option>`).join('')}</select></div></div><div class="notice info-note" style="margin-top:12px">The new company starts empty with its own invoice numbering, GST returns and backups. You will be switched to it so you can finish its business details (GSTIN, address, logo).</div></form>`;
  showModal('Create new company', body, `<button class="btn" onclick="closeModal()">Cancel</button><button class="btn primary" onclick="saveCompany()">Create company</button>`);
  setTimeout(() => $('#companyForm [name=name]')?.focus(), 30);
}
async function saveCompany() {
  const f = $('#companyForm'); if (!f.reportValidity()) return;
  try {
    await api('/api/companies/create', { method: 'POST', body: JSON.stringify({ name: f.name.value.trim(), state: f.state.value }) });
    closeModal(); await startApp(true); navigate('settings'); toast('Company created. Complete its business details.');
  } catch (e) { toast(e.message, 'error'); }
}
function renderCompaniesPanel() {
  const el = $('#companiesList'); if (!el) return;
  el.innerHTML = companies.map(c => `<div class="user-row"><span class="avatar" style="border-radius:10px">${esc(initials({ display_name: c.name }))}</span><div class="grow"><b>${esc(c.name)}</b> ${c.current ? '<span class="role-pill">Current</span>' : ''}<span class="tiny">${c.id === 1 ? 'Main company · data/hisabflow.db' : 'data/companies/company-' + c.id + '.db'}</span></div>${c.current ? '' : `<button class="btn small" onclick="switchCompany(${c.id})">Switch</button>`}${c.id !== 1 && !c.current ? ` <button class="btn small danger" onclick="archiveCompany(${c.id})">Archive</button>` : ''}</div>`).join('') || '<div class="empty-inline">No companies.</div>';
}
function archiveCompany(id) {
  const c = companies.find(x => x.id === id); if (!c) return;
  showModal('Archive company', `<form id="archiveForm"><div class="notice">Archiving removes <b>${esc(c.name)}</b> from HisabFlow. Its database file is moved to <code>data/archived_companies</code> (and a backup is taken), so it can be recovered later — but it will no longer appear here.</div><div class="field" style="margin-top:12px"><label>Type the company name to confirm</label><input name="confirm" autocomplete="off" placeholder="${esc(c.name)}"></div></form>`,
    `<button class="btn" onclick="closeModal()">Cancel</button><button class="btn danger" onclick="confirmArchive(${id})">Archive company</button>`);
}
async function confirmArchive(id) {
  try {
    await api('/api/companies/archive', { method: 'POST', body: JSON.stringify({ id, confirm_name: $('#archiveForm [name=confirm]').value }) });
    closeModal(); await loadCompanies(); toast('Company archived');
  } catch (e) { toast(e.message, 'error'); }
}

// =====================================================================================
// 4. Credit notes (sales returns)
// =====================================================================================
const CREDIT_REASONS = ['Sales return', 'Damaged / defective goods', 'Price reduction / discount', 'Wrong item supplied', 'Excess billing', 'Other'];
function renderCreditNotes() {
  if (!$('#creditTable')) return;
  const list = state.credit_notes || [], q = ($('#creditSearch')?.value || '').toLowerCase();
  const total = list.reduce((a, c) => a + Number(c.total), 0), refunds = list.reduce((a, c) => a + Number(c.refund_amount || 0), 0);
  const month = list.filter(c => c.note_date >= monthStart()).reduce((a, c) => a + Number(c.total), 0);
  $('#creditStats').innerHTML = statCard('CREDIT NOTES', String(list.length), 'All credit notes issued', '↩') + statCard('TOTAL CREDITED', money(total), 'Including GST', '₹') + statCard('THIS MONTH', money(month), 'Credited this month', '▥') + statCard('REFUNDS PAID', money(refunds), 'Cash returned to customers', '↗');
  const rows = list.filter(c => (c.note_number + ' ' + c.customer_name + ' ' + c.invoice_number).toLowerCase().includes(q));
  $('#creditTable').innerHTML = rows.length ? `<div class="table-wrap"><table><thead><tr><th>Credit note</th><th>Date</th><th>Customer</th><th>Against invoice</th><th>Reason</th><th class="right">Amount</th><th class="right">Refunded</th><th></th></tr></thead><tbody>${rows.map(c => `<tr><td class="strong">${esc(c.note_number)}</td><td>${dateFmt(c.note_date)}</td><td>${esc(c.customer_name)}</td><td><a class="click" onclick="viewInvoice(${c.invoice_id})">${esc(c.invoice_number)}</a></td><td>${esc(c.reason)}</td><td class="right strong">${money(c.total)}</td><td class="right">${Number(c.refund_amount) ? money(c.refund_amount) : '—'}</td><td><button class="btn small" onclick="viewCreditNote(${c.id})">View</button> <button class="btn small" onclick="printCreditNote(${c.id})">Print</button></td></tr>`).join('')}</tbody></table></div>`
    : emptyState(list.length ? 'No matching credit notes' : 'No credit notes yet', 'Issue a credit note when a customer returns goods or you reduce the price on an invoice.', 'New credit note', 'openCreditNoteForm()');
}
function invoicePicker(title, list, onPick, emptyMsg) {
  if (!list.length) { toast(emptyMsg, 'error'); return; }
  const body = `<div class="field"><label>Search</label><input id="pickSearch" placeholder="Type invoice no. or customer…" autocomplete="off"></div><div class="pick-list" id="pickList"></div>`;
  showModal(title, body, `<button class="btn" onclick="closeModal()">Cancel</button>`);
  const draw = () => {
    const q = ($('#pickSearch').value || '').toLowerCase();
    $('#pickList').innerHTML = list.filter(x => x.text.toLowerCase().includes(q)).slice(0, 60).map(x => `<button class="pick" data-id="${x.id}"><span><b>${esc(x.title)}</b><small>${esc(x.sub)}</small></span><span class="strong">${x.amount}</span></button>`).join('') || '<div class="empty-inline">Nothing matches.</div>';
    $$('#pickList .pick').forEach(b => b.onclick = () => onPick(Number(b.dataset.id)));
  };
  $('#pickSearch').oninput = draw; draw(); setTimeout(() => $('#pickSearch').focus(), 30);
  $('#pickSearch').onkeydown = e => { if (e.key === 'Enter') { e.preventDefault(); $('#pickList .pick')?.click(); } };
}
async function openCreditNoteForm(invoiceId = null) {
  if (!invoiceId) {
    const list = state.invoices.filter(i => Number(i.credited || 0) < Number(i.total) - 0.009).map(i => ({ id: i.id, title: i.invoice_number + ' · ' + i.customer_name, sub: dateFmt(i.invoice_date) + ' · ' + i.status, amount: money(i.total), text: i.invoice_number + ' ' + i.customer_name }));
    return invoicePicker('Credit note — choose the invoice', list, id => openCreditNoteForm(id), 'There are no invoices to issue a credit note against.');
  }
  let inv; try { inv = await api('/api/invoice?id=' + invoiceId); } catch (e) { toast(e.message, 'error'); return; }
  const lines = inv.items.map(it => ({ ...it, left: Math.max(0, Number(it.qty) - Number(it.credited_qty || 0)) }));
  if (!lines.some(l => l.left > 0)) { toast('Everything on this invoice has already been credited', 'error'); return; }
  const owedNow = Number(inv.balance);
  const body = `<form id="creditForm"><input type="hidden" name="invoice_id" value="${inv.id}">
    <div class="notice info-note">Against <b>${esc(inv.invoice_number)}</b> dated ${dateFmt(inv.invoice_date)} · ${esc(inv.customer.name)} · Invoice total ${money(inv.total)} · Current balance ${money(owedNow)}</div>
    <div class="form-grid three" style="margin-top:14px"><div class="field"><label>Credit note date *</label><input type="date" name="note_date" value="${today() < inv.invoice_date ? inv.invoice_date : today()}" min="${inv.invoice_date}" required></div>
    <div class="field"><label>Reason</label><select name="reason">${CREDIT_REASONS.map(r => `<option>${r}</option>`).join('')}</select></div>
    <div class="field"><label>Stock</label><label class="check" style="min-height:39px"><input type="checkbox" name="restock" checked> Put returned goods back in stock</label></div></div>
    <div class="section-head" style="margin:16px 0 8px"><div><h3>Items to credit</h3><p>Enter the returned quantity. Lower the rate for a price reduction.</p></div><button type="button" class="btn small" onclick="creditAll()">Credit full remaining quantity</button></div>
    <div class="table-wrap"><table class="item-table"><thead><tr><th>Item</th><th class="right">Invoiced</th><th class="right">Left to credit</th><th style="width:110px">Credit qty</th><th style="width:120px">Rate ₹</th><th class="right">GST</th><th class="right">Amount</th></tr></thead><tbody id="creditLines">
    ${lines.map(l => `<tr data-id="${l.id}" data-gst="${l.gst_rate}" data-left="${l.left}" data-max-rate="${l.rate}"><td><b>${esc(l.description)}</b>${l.details ? `<div class="item-details">${esc(l.details)}</div>` : ''}<div class="tiny">${l.hsn ? 'HSN ' + esc(l.hsn) : ''}</div></td><td class="right">${num(l.qty)} ${esc(l.unit)}</td><td class="right">${num(l.left)}</td><td><input class="cn-qty" type="number" min="0" max="${l.left}" step="0.001" value="0" ${l.left <= 0 ? 'disabled' : ''}></td><td><input class="cn-rate" type="number" min="0" max="${l.rate}" step="0.01" value="${l.rate}"></td><td class="right">${num(l.gst_rate)}%</td><td class="right cn-amt">₹0.00</td></tr>`).join('')}
    </tbody></table></div>
    <div class="invoice-total"><div class="total-row"><span>Taxable value</span><b id="cnSub">₹0.00</b></div><div class="total-row"><span>GST</span><b id="cnTax">₹0.00</b></div><div class="total-row grand"><span>Credit note total</span><b id="cnTotal">₹0.00</b></div></div>
    <div id="refundBox"></div>
    <div class="field" style="margin-top:12px"><label>Notes</label><textarea name="notes" placeholder="Optional"></textarea></div></form>`;
  showModal('New credit note', body, `<button class="btn" onclick="closeModal()">Cancel</button><button class="btn primary" onclick="saveCreditNote()">Save credit note</button>`, true);
  window._cnOwed = owedNow;
  $$('#creditLines input').forEach(i => i.addEventListener('input', calcCreditNote));
  calcCreditNote();
}
function creditAll() { $$('#creditLines tr').forEach(tr => { const q = tr.querySelector('.cn-qty'); if (!q.disabled) q.value = tr.dataset.left; }); calcCreditNote(); }
function calcCreditNote() {
  let sub = 0, tax = 0;
  $$('#creditLines tr').forEach(tr => {
    const q = Number(tr.querySelector('.cn-qty').value || 0), r = Number(tr.querySelector('.cn-rate').value || 0), g = Number(tr.dataset.gst);
    const base = Math.round(q * r * 100) / 100, t = Math.round(base * g) / 100;
    sub += base; tax += t; tr.querySelector('.cn-amt').textContent = money(base + t);
  });
  const total = sub + tax, after = window._cnOwed - total;
  $('#cnSub').textContent = money(sub); $('#cnTax').textContent = money(tax); $('#cnTotal').textContent = money(total);
  const box = $('#refundBox'); const prev = box.querySelector('[name=refund_amount]')?.value;
  box.innerHTML = after < -0.009
    ? `<div class="notice" style="margin-top:12px">After this credit note the customer has overpaid by <b>${money(-after)}</b>. Record a refund if you paid it back now; otherwise it stays as a balance in the customer's favour.</div><div class="form-grid" style="margin-top:10px"><div class="field"><label>Refund paid now (₹)</label><input type="number" name="refund_amount" min="0" max="${(-after).toFixed(2)}" step="0.01" value="${prev ?? (-after).toFixed(2)}"></div><div class="field"><label>Refund method</label><select name="refund_method"><option>Cash</option><option>UPI</option><option>Bank transfer</option><option>Cheque</option></select></div></div>`
    : `<div class="tiny" style="margin-top:10px">Balance after this credit note: <b>${money(Math.max(after, 0))}</b></div>`;
}
async function saveCreditNote() {
  const f = $('#creditForm'); if (!f.reportValidity()) return;
  const items = $$('#creditLines tr').map(tr => ({ invoice_item_id: Number(tr.dataset.id), qty: tr.querySelector('.cn-qty').value, rate: tr.querySelector('.cn-rate').value })).filter(x => Number(x.qty) > 0);
  if (!items.length) { toast('Enter a credit quantity for at least one item', 'error'); return; }
  const d = { invoice_id: Number(f.invoice_id.value), note_date: f.note_date.value, reason: f.reason.value, restock: f.restock.checked, notes: f.notes.value, items, refund_amount: f.refund_amount?.value || 0, refund_method: f.refund_method?.value || '' };
  try {
    const r = await api('/api/credit-notes', { method: 'POST', body: JSON.stringify(d) });
    await reload(); closeModal(); toast(`Credit note ${r.note_number} saved`); viewCreditNote(r.id);
  } catch (e) { toast(e.message, 'error'); }
}
async function viewCreditNote(id) {
  try {
    const c = await api('/api/credit-note?id=' + id);
    const body = `<div class="doc-head"><div><div class="doc-kind">Credit note</div><div class="doc-no">${esc(c.note_number)}</div><div class="tiny">${dateFmt(c.note_date)} · against ${esc(c.invoice_number)} (${dateFmt(c.invoice_date)})</div></div><div style="text-align:right"><b>${esc(c.customer.name)}</b><div class="tiny">${esc(c.customer.gstin ? 'GSTIN ' + c.customer.gstin : 'Unregistered')}</div><div class="tiny">Reason: ${esc(c.reason)}</div></div></div>
      <div class="table-wrap" style="margin-top:12px"><table><thead><tr><th>Item</th><th class="right">Qty</th><th class="right">Rate</th><th class="right">GST</th><th class="right">Amount</th></tr></thead><tbody>${c.items.map(x => `<tr><td>${esc(x.description)}${x.details ? `<div class="item-details">${esc(x.details)}</div>` : ''}</td><td class="right">${num(x.qty)} ${esc(x.unit)}</td><td class="right">${money(x.rate)}</td><td class="right">${num(x.gst_rate)}%</td><td class="right">${money(x.line_total)}</td></tr>`).join('')}</tbody></table></div>
      <div class="invoice-total"><div class="total-row"><span>Taxable value</span><b>${money(c.subtotal)}</b></div>${c.igst > 0 ? `<div class="total-row"><span>IGST</span><b>${money(c.igst)}</b></div>` : `<div class="total-row"><span>CGST</span><b>${money(c.cgst)}</b></div><div class="total-row"><span>SGST</span><b>${money(c.sgst)}</b></div>`}<div class="total-row grand"><span>Total credited</span><b>${money(c.total)}</b></div>${Number(c.refund_amount) ? `<div class="total-row"><span>Refunded (${esc(c.refund_method)})</span><b>${money(c.refund_amount)}</b></div>` : ''}</div>
      <div class="tiny">${c.restock ? 'Returned goods were added back to stock.' : 'Stock was not changed.'}${c.notes ? ' · ' + esc(c.notes) : ''}</div>`;
    showModal('Credit note', body, `<button class="btn" onclick="closeModal();viewInvoice(${c.invoice_id})">Open invoice</button><span style="flex:1"></span><button class="btn" onclick="closeModal()">Close</button><button class="btn primary" onclick="printCreditNote(${c.id})">Print / Save PDF</button>`, true);
  } catch (e) { toast(e.message, 'error'); }
}

// =====================================================================================
// 5. Debit notes (purchase returns)
// =====================================================================================
function renderDebitNotes() {
  if (!$('#debitTable')) return;
  const list = state.debit_notes || [];
  const total = list.reduce((a, c) => a + Number(c.amount), 0), month = list.filter(c => c.return_date >= monthStart()).reduce((a, c) => a + Number(c.amount), 0);
  $('#debitStats').innerHTML = statCard('DEBIT NOTES', String(list.length), 'Purchase returns recorded', '↪') + statCard('TOTAL VALUE', money(total), 'Including GST', '₹') + statCard('THIS MONTH', money(month), 'Returned this month', '▥') + statCard('SUPPLIERS', String(new Set(list.map(x => x.supplier_id)).size), 'With returns', '⚑');
  $('#debitTable').innerHTML = list.length ? `<div class="table-wrap"><table><thead><tr><th>Debit note</th><th>Date</th><th>Supplier</th><th>Against bill</th><th>Reason</th><th class="right">Amount</th><th></th></tr></thead><tbody>${list.map(d => `<tr><td class="strong">${esc(d.return_number)}</td><td>${dateFmt(d.return_date)}</td><td>${esc(d.supplier_name)}</td><td><a class="click" onclick="viewPurchase(${d.purchase_id})">${esc(d.purchase_number)}</a></td><td>${esc(d.notes || '—')}</td><td class="right strong">${money(d.amount)}</td><td><button class="btn small" onclick="viewDebitNote(${d.id})">View</button> <button class="btn small" onclick="printDebitNote(${d.id})">Print</button></td></tr>`).join('')}</tbody></table></div>`
    : emptyState('No debit notes yet', 'Record a debit note when you return goods to a supplier.', 'New debit note', 'openDebitNoteForm()');
}
async function openDebitNoteForm(purchaseId = null) {
  if (!purchaseId) {
    const list = state.purchases.map(p => ({ id: p.id, title: p.purchase_number + ' · ' + p.supplier_name, sub: dateFmt(p.purchase_date) + ' · balance ' + money(p.balance), amount: money(p.total), text: p.purchase_number + ' ' + p.supplier_name }));
    return invoicePicker('Debit note — choose the purchase bill', list, id => openDebitNoteForm(id), 'Record a purchase bill first.');
  }
  let p; try { p = await api('/api/purchase?id=' + purchaseId); } catch (e) { toast(e.message, 'error'); return; }
  const lines = p.items.filter(x => x.product_id);
  if (!lines.length) { toast('This bill has no catalogue items that can be returned', 'error'); return; }
  const body = `<form id="debitForm"><input type="hidden" name="purchase_id" value="${p.id}">
    <div class="notice info-note">Returning goods from <b>${esc(p.purchase_number)}</b> dated ${dateFmt(p.purchase_date)} · ${esc(p.supplier.name || '')} · Bill total ${money(p.total)}${p.returned ? ' · already returned ' + money(p.returned) : ''}</div>
    <div class="form-grid" style="margin-top:14px"><div class="field"><label>Debit note date *</label><input type="date" name="return_date" value="${today() < p.purchase_date ? p.purchase_date : today()}" min="${p.purchase_date}" required></div><div class="field"><label>Reason</label><input name="notes" placeholder="Damaged / excess / wrong item / rate difference"></div></div>
    <div class="table-wrap" style="margin-top:14px"><table class="item-table"><thead><tr><th>Item</th><th class="right">Purchased</th><th style="width:110px">Return qty</th><th style="width:120px">Rate ₹</th><th style="width:90px">GST %</th><th class="right">Amount</th></tr></thead><tbody id="debitLines">
    ${lines.map(l => `<tr data-pid="${l.product_id}"><td><b>${esc(l.description)}</b></td><td class="right">${num(l.qty)}</td><td><input class="dn-qty" type="number" min="0" max="${l.qty}" step="0.001" value="0"></td><td><input class="dn-rate" type="number" min="0" step="0.01" value="${l.rate}"></td><td><input class="dn-gst" type="number" min="0" max="100" step="0.01" value="${l.gst_rate || 0}"></td><td class="right dn-amt">₹0.00</td></tr>`).join('')}
    </tbody></table></div><div class="invoice-total"><div class="total-row grand"><span>Debit note total</span><b id="dnTotal">₹0.00</b></div></div>
    <div class="tiny">Stock is reduced for inventory-tracked items. The supplier balance and input tax credit (GSTR-3B) reduce by this amount.</div></form>`;
  showModal('New debit note', body, `<button class="btn" onclick="closeModal()">Cancel</button><button class="btn primary" onclick="saveDebitNote()">Save debit note</button>`, true);
  const calc = () => { let t = 0; $$('#debitLines tr').forEach(tr => { const q = +tr.querySelector('.dn-qty').value || 0, r = +tr.querySelector('.dn-rate').value || 0, g = +tr.querySelector('.dn-gst').value || 0; const base = Math.round(q * r * 100) / 100, a = base + Math.round(base * g) / 100; t += a; tr.querySelector('.dn-amt').textContent = money(a); }); $('#dnTotal').textContent = money(t); };
  $$('#debitLines input').forEach(i => i.addEventListener('input', calc)); calc();
}
openPurchaseReturn = openDebitNoteForm; // "Debit note" buttons in the purchases table
async function saveDebitNote() {
  const f = $('#debitForm'); if (!f.reportValidity()) return;
  const items = $$('#debitLines tr').map(tr => ({ product_id: Number(tr.dataset.pid), qty: tr.querySelector('.dn-qty').value, rate: tr.querySelector('.dn-rate').value, gst_rate: tr.querySelector('.dn-gst').value })).filter(x => Number(x.qty) > 0);
  if (!items.length) { toast('Enter a return quantity for at least one item', 'error'); return; }
  try {
    const r = await api('/api/purchase-returns', { method: 'POST', body: JSON.stringify({ purchase_id: Number(f.purchase_id.value), return_date: f.return_date.value, notes: f.notes.value, items }) });
    await reload(); closeModal(); toast(`Debit note ${r.return_number} saved`);
    const dn = (state.debit_notes || []).find(x => x.return_number === r.return_number); if (dn) viewDebitNote(dn.id);
  } catch (e) { toast(e.message, 'error'); }
}
async function viewDebitNote(id) {
  try {
    const d = await api('/api/debit-note?id=' + id);
    const body = `<div class="doc-head"><div><div class="doc-kind">Debit note</div><div class="doc-no">${esc(d.return_number)}</div><div class="tiny">${dateFmt(d.return_date)} · against ${esc(d.purchase_number)} (${dateFmt(d.purchase_date)})</div></div><div style="text-align:right"><b>${esc(d.supplier.name || '')}</b><div class="tiny">${esc(d.supplier.gstin ? 'GSTIN ' + d.supplier.gstin : 'Unregistered supplier')}</div></div></div>
      <div class="table-wrap" style="margin-top:12px"><table><thead><tr><th>Item</th><th class="right">Qty</th><th class="right">Rate</th><th class="right">GST</th><th class="right">Amount</th></tr></thead><tbody>${d.items.map(x => `<tr><td>${esc(x.description)}</td><td class="right">${num(x.qty)}</td><td class="right">${money(x.rate)}</td><td class="right">${num(x.gst_rate)}%</td><td class="right">${money(x.amount)}</td></tr>`).join('')}</tbody></table></div>
      <div class="invoice-total"><div class="total-row grand"><span>Total</span><b>${money(d.amount)}</b></div></div>${d.notes ? `<div class="tiny">Reason: ${esc(d.notes)}</div>` : ''}`;
    showModal('Debit note', body, `<span style="flex:1"></span><button class="btn" onclick="closeModal()">Close</button><button class="btn primary" onclick="printDebitNote(${d.id})">Print / Save PDF</button>`, true);
  } catch (e) { toast(e.message, 'error'); }
}

// =====================================================================================
// 6. Receipt / payment pickers and quick-create
// =====================================================================================
function openReceiptPicker() {
  const list = state.invoices.filter(i => i.balance > 0.009).map(i => ({ id: i.id, title: i.customer_name + ' · ' + i.invoice_number, sub: dateFmt(i.invoice_date) + ' · due ' + money(i.balance), amount: money(i.balance), text: i.invoice_number + ' ' + i.customer_name }));
  invoicePicker('Receipt — which invoice was paid?', list, id => openPaymentForm(id), 'No invoices have a pending balance.');
}
function openPaymentPicker() {
  const bills = state.purchases.filter(p => p.balance > 0.009), sal = state.payroll.filter(p => p.balance > 0.009);
  const body = `<div class="choice-grid"><button class="choice" onclick="closeModal();openExpenseForm()"><span class="ci">↗</span><b>Record an expense</b><small>Rent, electricity, transport…</small></button><button class="choice" onclick="closeModal();openCashEntryForm()"><span class="ci">⇄</span><b>Other cash entry</b><small>Withdrawal, deposit, loan…</small></button></div>
    <div class="section-head" style="margin:18px 0 8px"><div><h3>Pay a supplier bill</h3><p>${bills.length} bill${bills.length === 1 ? '' : 's'} with a balance</p></div></div>
    <div class="pick-list">${bills.map(p => `<button class="pick" onclick="closeModal();openSupplierPayment(${p.id})"><span><b>${esc(p.supplier_name)} · ${esc(p.purchase_number)}</b><small>${dateFmt(p.purchase_date)}</small></span><span class="strong">${money(p.balance)}</span></button>`).join('') || '<div class="empty-inline">No supplier balances.</div>'}</div>
    ${sal.length ? `<div class="section-head" style="margin:18px 0 8px"><div><h3>Pay salary</h3></div></div><div class="pick-list">${sal.map(p => `<button class="pick" onclick="closeModal();paySalary(${p.id})"><span><b>${esc(p.employee_name)}</b><small>${esc(p.payroll_month)}</small></span><span class="strong">${money(p.balance)}</span></button>`).join('')}</div>` : ''}`;
  showModal('Payment — money going out', body, `<button class="btn" onclick="closeModal()">Close</button>`);
}
function openCreateMaster() {
  const body = `<div class="choice-grid four"><button class="choice" onclick="closeModal();openCustomerForm()"><span class="ci">♙</span><b>Customer</b><small>Sundry debtor</small></button><button class="choice" onclick="closeModal();openSupplierForm()"><span class="ci">⚑</span><b>Supplier</b><small>Sundry creditor</small></button><button class="choice" onclick="closeModal();openProductForm()"><span class="ci">◈</span><b>Item / service</b><small>Stock item</small></button><button class="choice" onclick="closeModal();openStaffForm()"><span class="ci">☺</span><b>Employee</b><small>Staff member</small></button></div>`;
  showModal('Create', body, `<button class="btn" onclick="closeModal()">Close</button>`);
}

// =====================================================================================
// 7. Keyboard shortcuts
// =====================================================================================
const SHORTCUTS = [
  ['F8', 'New sales invoice'], ['Ctrl + F8', 'New credit note (sales return)'], ['F9', 'New purchase bill'], ['Ctrl + F9', 'New debit note (purchase return)'],
  ['F6', 'Receipt — record a customer payment'], ['F5', 'Payment — pay a supplier, expense or salary'], ['F7', 'New expense'], ['F4', 'Cash entry (contra / deposit / withdrawal)'],
  ['Alt + C', 'Create customer, supplier, item or employee'], ['Ctrl + K  or  /', 'Search and jump to anything'], ['Ctrl + S  or  Ctrl + Enter', 'Save the open form'],
  ['Esc', 'Close the open window or menu'], ['?', 'Show this list'],
  ['G then D', 'Go to Dashboard'], ['G then I', 'Go to Invoices'], ['G then N', 'Go to Credit notes'], ['G then C', 'Go to Customers'], ['G then P', 'Go to Purchases'],
  ['G then S', 'Go to Suppliers'], ['G then T', 'Go to Items'], ['G then K', 'Go to Stock'], ['G then E', 'Go to Expenses'], ['G then X', 'Go to GST returns'], ['G then R', 'Go to Reports'], ['G then F', 'Go to Cash flow'],
];
function showShortcuts() {
  const kb = k => k.split(/\s+(\+|or|then)\s+/).map(p => ['+', 'or', 'then'].includes(p) ? `<span class="tiny"> ${p} </span>` : `<kbd>${esc(p)}</kbd>`).join('');
  showModal('Keyboard shortcuts', `<p class="tiny" style="margin-top:0">Work like Tally — keep your hands on the keyboard. Function keys work anywhere except while signing in.</p><div class="shortcut-grid">${SHORTCUTS.map(([k, d]) => `<div class="sc-row"><span>${esc(d)}</span><span class="sc-keys">${kb(k)}</span></div>`).join('')}</div>`, `<button class="btn primary" onclick="closeModal()">Got it</button>`, true);
}
function modalHasForm() { return $('#modalBackdrop').classList.contains('show') && !!$('#modalBody form'); }
function runShortcut(fn) {
  if (modalHasForm() && !confirm('Close the form that is open? Unsaved changes will be lost.')) return;
  closeMenus(); fn();
}
let gPending = 0;
document.addEventListener('keydown', e => {
  if (!currentUser || $('#authScreen').classList.contains('show')) return;
  const k = e.key, lower = (k || '').toLowerCase();
  if ((e.ctrlKey || e.metaKey) && lower === 'k') { e.preventDefault(); openPalette(); return; }
  if ($('#palette')?.classList.contains('show')) return;
  const modalOpen = $('#modalBackdrop').classList.contains('show');
  if (modalOpen && (e.ctrlKey || e.metaKey) && (lower === 's' || k === 'Enter')) { e.preventDefault(); $('#modalFoot .btn.primary')?.click(); return; }
  const fkeys = {
    F8: () => e.ctrlKey ? openCreditNoteForm() : openInvoiceForm(), F9: () => e.ctrlKey ? openDebitNoteForm() : openPurchaseForm(),
    F6: openReceiptPicker, F5: openPaymentPicker, F7: () => openExpenseForm(), F4: () => openCashEntryForm(),
  };
  if (fkeys[k]) { e.preventDefault(); runShortcut(fkeys[k]); return; }
  if (e.altKey && lower === 'c') { e.preventDefault(); runShortcut(openCreateMaster); return; }
  const typing = e.target.closest?.('input,textarea,select,[contenteditable="true"]');
  if (typing || modalOpen || e.ctrlKey || e.altKey || e.metaKey) return;
  if (k === '?') { e.preventDefault(); showShortcuts(); return; }
  if (k === '/') { e.preventDefault(); openPalette(); return; }
  if (lower === 'g') { gPending = Date.now(); return; }
  if (gPending && Date.now() - gPending < 1500) {
    gPending = 0;
    const map = { d: 'dashboard', i: 'invoices', n: 'creditnotes', c: 'customers', p: 'purchases', s: 'suppliers', t: 'items', k: 'inventory', e: 'expenses', x: 'gst', r: 'reports', f: 'cashflow' };
    if (map[lower]) { e.preventDefault(); navigate(map[lower]); }
  }
});

// =====================================================================================
// 8. Command palette (Ctrl+K)
// =====================================================================================
let paletteItems = [], paletteIndex = 0;
function injectPalette() {
  if ($('#palette')) return;
  document.body.insertAdjacentHTML('beforeend', `<div class="palette" id="palette" role="dialog" aria-modal="true" aria-label="Search"><div class="palette-box"><div class="palette-input"><span>⌕</span><input id="paletteInput" placeholder="Search invoices, customers, items, bills… or type a command" autocomplete="off" spellcheck="false"><kbd>Esc</kbd></div><div class="palette-results" id="paletteResults"></div><div class="palette-foot"><span><kbd>↑</kbd><kbd>↓</kbd> move</span><span><kbd>Enter</kbd> open</span><span><kbd>Esc</kbd> close</span></div></div></div>`);
  $('#palette').addEventListener('click', e => { if (e.target.id === 'palette') closePalette(); });
  $('#paletteInput').addEventListener('input', () => { paletteIndex = 0; drawPalette(); });
  $('#paletteInput').addEventListener('keydown', e => {
    if (e.key === 'Escape') { e.preventDefault(); closePalette(); }
    else if (e.key === 'ArrowDown') { e.preventDefault(); paletteIndex = Math.min(paletteIndex + 1, paletteItems.length - 1); highlightPalette(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); paletteIndex = Math.max(paletteIndex - 1, 0); highlightPalette(); }
    else if (e.key === 'Enter') { e.preventDefault(); paletteItems[paletteIndex]?.run(); }
  });
}
function openPalette() {
  if (!currentUser) return;
  closeMenus(); injectPalette(); $('#palette').classList.add('show'); $('#paletteInput').value = ''; paletteIndex = 0; drawPalette();
  $('#paletteInput').focus();
}
function closePalette() { $('#palette')?.classList.remove('show'); }
function paletteSource() {
  const go = (v) => () => { closePalette(); closeModal(); navigate(v); };
  const act = (fn) => () => { closePalette(); fn(); };
  const out = [];
  [['New sales invoice', 'F8', () => openInvoiceForm()], ['New credit note', 'Ctrl F8', () => openCreditNoteForm()], ['New purchase bill', 'F9', () => openPurchaseForm()],
   ['New debit note', 'Ctrl F9', () => openDebitNoteForm()], ['Record receipt', 'F6', openReceiptPicker], ['Record payment', 'F5', openPaymentPicker],
   ['New expense', 'F7', () => openExpenseForm()], ['New quotation', '', () => openQuotationForm()], ['New customer', '', () => openCustomerForm()],
   ['New item', '', () => openProductForm()], ['New supplier', '', () => openSupplierForm()], ['Keyboard shortcuts', '?', showShortcuts]]
    .forEach(([t, k, fn]) => out.push({ group: 'Actions', icon: '＋', title: t, sub: '', key: k, text: t, run: act(fn) }));
  Object.entries(titles).forEach(([v, [t, d]]) => out.push({ group: 'Go to', icon: '→', title: t, sub: d, key: '', text: 'go ' + t + ' ' + (VIEW_GROUP[v] || ''), run: go(v) }));
  state.invoices.forEach(i => out.push({ group: 'Invoices', icon: '▤', title: `${i.invoice_number} · ${i.customer_name}`, sub: `${dateFmt(i.invoice_date)} · ${money(i.total)} · ${i.status}`, text: `${i.invoice_number} ${i.customer_name} ${i.total}`, run: act(() => viewInvoice(i.id)) }));
  (state.invoice_items || []).filter(x => x.details).forEach(x => { const inv = state.invoices.find(i => i.id === x.invoice_id); if (inv) out.push({ group: 'Serial no. / item details', icon: '#', title: `${x.description} · ${inv.invoice_number}`, sub: `${x.details.replace(/\s+/g, ' ').slice(0, 90)} · ${inv.customer_name}`, text: `${x.details} ${x.description} ${inv.invoice_number}`, run: act(() => viewInvoice(inv.id)) }); });
  state.customers.forEach(c => out.push({ group: 'Customers', icon: '♙', title: c.name, sub: [c.phone, c.gstin, c.state].filter(Boolean).join(' · '), text: `${c.name} ${c.phone} ${c.gstin}`, run: act(() => openCustomerLedger(c.id)) }));
  state.products.forEach(p => out.push({ group: 'Items', icon: '◈', title: p.name, sub: `${money(p.rate)}${p.track_stock ? ' · stock ' + num(p.stock_qty) + ' ' + p.unit : ''}${p.barcode ? ' · ' + p.barcode : ''}`, text: `${p.name} ${p.hsn} ${p.barcode}`, run: act(() => openProductForm(p.id)) }));
  state.suppliers.forEach(s => out.push({ group: 'Suppliers', icon: '⚑', title: s.name, sub: [s.phone, s.gstin].filter(Boolean).join(' · '), text: `${s.name} ${s.phone} ${s.gstin}`, run: act(() => openSupplierLedger(s.id)) }));
  state.purchases.forEach(p => out.push({ group: 'Purchase bills', icon: '⇩', title: `${p.purchase_number} · ${p.supplier_name}`, sub: `${dateFmt(p.purchase_date)} · ${money(p.total)}`, text: `${p.purchase_number} ${p.supplier_name}`, run: act(() => viewPurchase(p.id)) }));
  (state.credit_notes || []).forEach(c => out.push({ group: 'Credit notes', icon: '↩', title: `${c.note_number} · ${c.customer_name}`, sub: `${dateFmt(c.note_date)} · ${money(c.total)}`, text: `${c.note_number} ${c.customer_name} ${c.invoice_number}`, run: act(() => viewCreditNote(c.id)) }));
  (state.debit_notes || []).forEach(d => out.push({ group: 'Debit notes', icon: '↪', title: `${d.return_number} · ${d.supplier_name}`, sub: `${dateFmt(d.return_date)} · ${money(d.amount)}`, text: `${d.return_number} ${d.supplier_name} ${d.purchase_number}`, run: act(() => viewDebitNote(d.id)) }));
  state.quotations.forEach(q => out.push({ group: 'Quotations', icon: '▧', title: `${q.quote_number} · ${q.customer_name}`, sub: `${dateFmt(q.quote_date)} · ${money(q.total)} · ${q.status}`, text: `${q.quote_number} ${q.customer_name}`, run: act(() => viewQuotation(q.id)) }));
  state.employees.forEach(e => out.push({ group: 'Staff', icon: '☺', title: e.name, sub: e.role || '', text: `${e.name} ${e.role} ${e.phone}`, run: act(() => openStaffForm(e.id)) }));
  return out;
}
function drawPalette() {
  const q = ($('#paletteInput').value || '').trim().toLowerCase(), tokens = q.split(/\s+/).filter(Boolean);
  let items = paletteSource();
  if (tokens.length) items = items.filter(x => { const h = (x.title + ' ' + x.text + ' ' + x.sub).toLowerCase(); return tokens.every(t => h.includes(t)); });
  else items = items.filter(x => x.group === 'Actions' || x.group === 'Go to').slice(0, 14);
  const perGroup = {}; paletteItems = items.filter(x => (perGroup[x.group] = (perGroup[x.group] || 0) + 1) <= (tokens.length ? 6 : 20)).slice(0, 40);
  let html = '', last = '';
  paletteItems.forEach((x, i) => {
    if (x.group !== last) { html += `<div class="pal-group">${esc(x.group)}</div>`; last = x.group; }
    html += `<button class="pal-item" data-i="${i}"><span class="pal-ico">${x.icon}</span><span class="pal-text"><b>${esc(x.title)}</b>${x.sub ? `<small>${esc(x.sub)}</small>` : ''}</span>${x.key ? `<kbd>${esc(x.key)}</kbd>` : ''}</button>`;
  });
  $('#paletteResults').innerHTML = html || `<div class="empty-inline" style="padding:18px">No results for “${esc(q)}”.</div>`;
  $$('#paletteResults .pal-item').forEach(b => { b.onclick = () => paletteItems[+b.dataset.i]?.run(); b.onmousemove = () => { paletteIndex = +b.dataset.i; highlightPalette(false); }; });
  highlightPalette();
}
function highlightPalette(scroll = true) {
  $$('#paletteResults .pal-item').forEach(b => b.classList.toggle('on', +b.dataset.i === paletteIndex));
  if (scroll) $(`#paletteResults .pal-item[data-i="${paletteIndex}"]`)?.scrollIntoView({ block: 'nearest' });
}

// =====================================================================================
// 9. Dashboard charts (plain SVG, no external libraries — works offline)
// =====================================================================================
const CHART = { s1: '#2a78d6', s2: '#eb6834', violet: '#6250d6', grid: '#e8ebf2', axis: '#64748b', ink: '#0f172a' };
const chartTableMode = {};
function inrCompact(v) {
  const a = Math.abs(v), s = v < 0 ? '−' : '';
  if (a >= 1e7) return `${s}₹${+(a / 1e7).toFixed(a >= 1e8 ? 0 : 1)}Cr`;
  if (a >= 1e5) return `${s}₹${+(a / 1e5).toFixed(a >= 1e6 ? 0 : 1)}L`;
  if (a >= 1e3) return `${s}₹${+(a / 1e3).toFixed(a >= 1e4 ? 0 : 1)}K`;
  return `${s}₹${Math.round(a)}`;
}
function niceMax(v) { if (v <= 0) return 1000; const p = Math.pow(10, Math.floor(Math.log10(v))), f = v / p; return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * p; }
function last12Months() { const d = new Date(), out = []; for (let i = 11; i >= 0; i--) { const m = new Date(d.getFullYear(), d.getMonth() - i, 1); out.push({ key: isoLocal(m).slice(0, 7), label: m.toLocaleDateString('en-IN', { month: 'short' }), full: m.toLocaleDateString('en-IN', { month: 'long', year: 'numeric' }) }); } return out; }
function chartTip() { let t = $('#chartTip'); if (!t) { document.body.insertAdjacentHTML('beforeend', '<div class="chart-tip" id="chartTip" role="tooltip"></div>'); t = $('#chartTip'); } return t; }
function showTip(e, html) { const t = chartTip(); t.innerHTML = html; t.classList.add('show'); const w = t.offsetWidth, h = t.offsetHeight; let x = e.clientX + 14, y = e.clientY - h - 10; if (x + w > innerWidth - 8) x = e.clientX - w - 14; if (y < 8) y = e.clientY + 16; t.style.left = x + 'px'; t.style.top = y + 'px'; }
function hideTip() { $('#chartTip')?.classList.remove('show'); }
function toggleChartTable(id) { chartTableMode[id] = !chartTableMode[id]; renderCharts(); }
function topRoundedBar(x, y, w, h, r = 4) { if (h <= 0) return ''; r = Math.min(r, h, w / 2); return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`; }
function chartTable(headers, rows) { return `<div class="table-wrap"><table><thead><tr>${headers.map((h, i) => `<th class="${i ? 'right' : ''}">${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.map(r => `<tr>${r.map((c, i) => `<td class="${i ? 'right' : ''}">${c}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`; }

function renderCharts() {
  if (!$('#chartSales') || activeView !== 'dashboard') return;
  renderSalesChart(); renderRankedBars('chartCustomers', topCustomerData(), CHART.s1, 'customer'); renderRankedBars('chartExpenses', expenseData(), CHART.violet, 'category');
}
function salesData() {
  const months = last12Months(), idx = Object.fromEntries(months.map((m, i) => [m.key, i]));
  const inv = months.map(() => 0), col = months.map(() => 0);
  state.invoices.forEach(i => { const k = idx[i.invoice_date.slice(0, 7)]; if (k !== undefined) inv[k] += Number(i.total); });
  (state.credit_notes || []).forEach(c => { const k = idx[c.note_date.slice(0, 7)]; if (k !== undefined) { inv[k] -= Number(c.total); col[k] -= Number(c.refund_amount || 0); } });
  state.payments.forEach(p => { const k = idx[p.payment_date.slice(0, 7)]; if (k !== undefined) col[k] += Number(p.amount); });
  return { months, inv, col };
}
function renderSalesChart() {
  const el = $('#chartSales'), { months, inv, col } = salesData();
  if (chartTableMode.chartSales) { el.innerHTML = chartTable(['Month', 'Invoiced (net)', 'Collected'], months.map((m, i) => [esc(m.full), money(inv[i]), money(col[i])])); return; }
  if (!inv.some(v => v) && !col.some(v => v)) { el.innerHTML = '<div class="chart-empty">No sales or collections in the last 12 months yet. Create an invoice to see your trend here.</div>'; return; }
  const W = Math.max(320, el.clientWidth || 640), H = 250, L = 54, R = 10, T = 14, B = 28, pw = W - L - R, ph = H - T - B;
  const max = niceMax(Math.max(...inv, ...col, 1)), ticks = [0, .25, .5, .75, 1].map(f => f * max), y = v => T + ph - Math.max(0, v) / max * ph;
  const band = pw / 12, bw = Math.min(20, Math.max(4, (band - 12) / 2)), gap = 2;
  let svg = `<svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}" role="img" aria-label="Monthly invoiced and collected amounts for the last 12 months">`;
  ticks.forEach(t => { svg += `<line x1="${L}" x2="${W - R}" y1="${y(t)}" y2="${y(t)}" stroke="${CHART.grid}" stroke-width="1"/><text x="${L - 8}" y="${y(t) + 4}" text-anchor="end" class="ax">${inrCompact(t)}</text>`; });
  months.forEach((m, i) => {
    const cx = L + band * i + band / 2, x1 = cx - bw - gap / 2, x2 = cx + gap / 2;
    svg += `<path d="${topRoundedBar(x1, y(inv[i]), bw, T + ph - y(inv[i]))}" fill="${CHART.s1}"/><path d="${topRoundedBar(x2, y(col[i]), bw, T + ph - y(col[i]))}" fill="${CHART.s2}"/>`;
    if (band > 34 || i % 2 === 1) svg += `<text x="${cx}" y="${H - 8}" text-anchor="middle" class="ax">${esc(m.label)}</text>`;
    svg += `<rect class="hit" data-i="${i}" x="${L + band * i}" y="${T}" width="${band}" height="${ph}" fill="transparent"/>`;
  });
  svg += `<line x1="${L}" x2="${W - R}" y1="${T + ph}" y2="${T + ph}" stroke="#cfd5e2" stroke-width="1"/></svg>`;
  el.innerHTML = `<div class="legend"><span><i style="background:${CHART.s1}"></i>Invoiced (net of credit notes)</span><span><i style="background:${CHART.s2}"></i>Collected</span></div>${svg}`;
  el.querySelectorAll('.hit').forEach(r => {
    r.addEventListener('mousemove', e => { const i = +r.dataset.i; r.setAttribute('fill', 'rgba(91,91,240,.06)'); showTip(e, `<b>${esc(months[i].full)}</b><div class="tip-row"><i style="background:${CHART.s1}"></i>Invoiced<span>${money(inv[i])}</span></div><div class="tip-row"><i style="background:${CHART.s2}"></i>Collected<span>${money(col[i])}</span></div>`); });
    r.addEventListener('mouseleave', () => { r.setAttribute('fill', 'transparent'); hideTip(); });
  });
}
function topCustomerData() {
  const since = isoLocal(new Date(new Date().getFullYear(), new Date().getMonth() - 11, 1)), m = {};
  state.invoices.filter(i => i.invoice_date >= since).forEach(i => { m[i.customer_name] = (m[i.customer_name] || 0) + Number(i.total); });
  (state.credit_notes || []).filter(c => c.note_date >= since).forEach(c => { m[c.customer_name] = (m[c.customer_name] || 0) - Number(c.total); });
  return Object.entries(m).filter(([, v]) => v > 0.009).sort((a, b) => b[1] - a[1]);
}
function expenseData() {
  const since = isoLocal(new Date(new Date().getFullYear(), new Date().getMonth() - 11, 1)), m = {};
  state.expenses.filter(x => x.expense_date >= since).forEach(x => { m[x.category] = (m[x.category] || 0) + Number(x.amount); });
  return Object.entries(m).sort((a, b) => b[1] - a[1]);
}
function renderRankedBars(id, data, color, noun) {
  const el = $('#' + id); if (!el) return;
  const total = data.reduce((a, [, v]) => a + v, 0);
  if (chartTableMode[id]) { el.innerHTML = data.length ? chartTable([noun === 'customer' ? 'Customer' : 'Category', 'Amount', 'Share'], data.map(([k, v]) => [esc(k), money(v), (v / total * 100).toFixed(1) + '%'])) : '<div class="chart-empty">No data yet.</div>'; return; }
  if (!data.length) { el.innerHTML = `<div class="chart-empty">${noun === 'customer' ? 'No customer sales in the last 12 months yet.' : 'No expenses recorded in the last 12 months yet.'}</div>`; return; }
  let rows = data.slice(0, 6);
  if (data.length > 6) rows = rows.concat([[`Other (${data.length - 6})`, data.slice(6).reduce((a, [, v]) => a + v, 0)]]);
  const max = Math.max(...rows.map(r => r[1]));
  el.innerHTML = `<div class="rank-list">${rows.map(([k, v], i) => `<div class="rank-row" data-i="${i}"><span class="rank-name" title="${esc(k)}">${esc(k)}</span><span class="rank-track"><span class="rank-bar" style="width:${Math.max(1, v / max * 100).toFixed(2)}%;background:${k.startsWith('Other (') ? '#a3acbf' : color}"></span></span><span class="rank-val">${inrCompact(v)}<small>${(v / total * 100).toFixed(0)}%</small></span></div>`).join('')}</div>`;
  el.querySelectorAll('.rank-row').forEach(r => {
    const [k, v] = rows[+r.dataset.i];
    r.addEventListener('mousemove', e => showTip(e, `<b>${esc(k)}</b><div class="tip-row"><i style="background:${color}"></i>${noun === 'customer' ? 'Net sales' : 'Spent'}<span>${money(v)}</span></div><div class="tiny">${(v / total * 100).toFixed(1)}% of total</div>`));
    r.addEventListener('mouseleave', hideTip);
  });
}
window.addEventListener('resize', () => { clearTimeout(window._chartResize); window._chartResize = setTimeout(renderCharts, 150); });

// =====================================================================================
// 10. GST returns screen
// =====================================================================================
let gstTab = 'gstr1', gstLast = null;
function monthBounds(ym) { const [y, m] = ym.split('-').map(Number); return [isoLocal(new Date(y, m - 1, 1)), isoLocal(new Date(y, m, 0))]; }
function initGST() {
  if (!$('#gstMonth').value) {
    const d = new Date(), prev = isoLocal(new Date(d.getFullYear(), d.getMonth() - 1, 1)).slice(0, 7), cur = today().slice(0, 7);
    // Returns are usually filed for the previous month; fall back to this month when last month is empty.
    $('#gstMonth').value = state.invoices.some(i => i.invoice_date.startsWith(prev)) ? prev : cur;
    gstMonthChanged(); return;
  }
  renderGST();
}
function gstMonthChanged() { const [a, b] = monthBounds($('#gstMonth').value || today().slice(0, 7)); $('#gstFrom').value = a; $('#gstTo').value = b; renderGST(); }
function setGstTab(t) { gstTab = t; $$('.tabs .tab').forEach(b => b.classList.toggle('active', b.dataset.gst === t)); $('#gstJsonBtn').classList.toggle('hide', t !== 'gstr1'); renderGST(); }
async function renderGST() {
  if (!$('#gstContent') || !$('#gstFrom').value) return;
  const from = $('#gstFrom').value, to = $('#gstTo').value, el = $('#gstContent');
  el.innerHTML = '<div class="empty-inline" style="padding:18px">Preparing…</div>';
  try { gstLast = await api(`/api/gst?return=${gstTab}&from=${from}&to=${to}`); }
  catch (e) { el.innerHTML = `<div class="notice">${esc(e.message)}</div>`; return; }
  el.innerHTML = (gstTab === 'gstr1' ? gstr1HTML(gstLast) : gstr3bHTML(gstLast))
    + '<div class="notice" style="margin-top:16px">HisabFlow prepares these summaries from your saved records. It does not file returns. Check the figures with your books and GSTR-2B, and validate exported JSON in the official GST offline tool before uploading. Have a GST professional review your returns.</div>';
}
function warnHTML(w) { return w.length ? `<div class="notice" style="margin-bottom:14px"><b>Please check:</b><ul class="warn-list">${w.map(x => `<li>${esc(x)}</li>`).join('')}</ul></div>` : '<div class="notice success-note" style="margin-bottom:14px">No data problems found for this period.</div>'; }
function gstTable(title, code, cols, rows, render) {
  return `<details class="panel gst-section" ${rows.length ? 'open' : ''}><summary class="panel-head"><div><h3>${esc(title)} <span class="count">${rows.length}</span></h3><p>${esc(code)}</p></div><span class="tiny">${rows.length ? '' : 'Nothing to report'}</span></summary>${rows.length ? `<div class="table-wrap"><table><thead><tr>${cols.map(c => `<th class="${c[1] ? 'right' : ''}">${esc(c[0])}</th>`).join('')}</tr></thead><tbody>${rows.map(r => `<tr>${render(r).map((v, i) => `<td class="${cols[i][1] ? 'right' : ''}">${v}</td>`).join('')}</tr>`).join('')}</tbody></table></div>` : ''}</details>`;
}
function gstr1HTML(g) {
  const s = g.summary, m = v => money(v);
  return warnHTML(g.warnings)
    + `<div class="grid stats">${statCard('INVOICES', String(s.invoices), `Total value ${m(s.invoice_value)}`, '▤')}${statCard('TAXABLE VALUE', m(s.taxable), 'Taxable outward supplies', '₹')}${statCard('TAX ON INVOICES', m(s.igst + s.cgst + s.sgst), `IGST ${m(s.igst)} · CGST ${m(s.cgst)} · SGST ${m(s.sgst)}`, '%')}${statCard('CREDIT NOTES', String(s.credit_notes), `Tax reduced ${m(s.credit_tax)} · net tax ${m(s.net_tax)}`, '↩')}</div>`
    + gstTable('B2B invoices', 'Table 4A — supplies to registered customers', [['Customer GSTIN'], ['Name'], ['Invoice'], ['Date'], ['Value', 1], ['Place of supply'], ['Rate', 1], ['Taxable', 1], ['IGST', 1], ['CGST', 1], ['SGST', 1]], g.b2b, r => [esc(r.gstin), esc(r.name), esc(r.invoice_number), dateFmt(r.invoice_date), m(r.invoice_value), esc(r.pos), r.rate + '%', m(r.taxable), m(r.igst), m(r.cgst), m(r.sgst)])
    + gstTable('B2C Large invoices', 'Table 5 — inter-state to unregistered, above ₹1 lakh', [['Invoice'], ['Date'], ['Value', 1], ['Place of supply'], ['Rate', 1], ['Taxable', 1], ['IGST', 1]], g.b2cl, r => [esc(r.invoice_number), dateFmt(r.invoice_date), m(r.invoice_value), esc(r.pos), r.rate + '%', m(r.taxable), m(r.igst)])
    + gstTable('B2C Small (summary)', 'Table 7 — other supplies to unregistered customers, net of small credit notes', [['Supply'], ['Place of supply'], ['Rate', 1], ['Taxable', 1], ['IGST', 1], ['CGST', 1], ['SGST', 1]], g.b2cs, r => [r.supply === 'INTER' ? 'Inter-state' : 'Intra-state', esc(r.pos), r.rate + '%', m(r.taxable), m(r.igst), m(r.cgst), m(r.sgst)])
    + gstTable('Credit notes — registered', 'Table 9B (CDNR)', [['Customer GSTIN'], ['Name'], ['Note'], ['Date'], ['Original invoice'], ['Rate', 1], ['Taxable', 1], ['IGST', 1], ['CGST', 1], ['SGST', 1]], g.cdnr, r => [esc(r.gstin), esc(r.name), esc(r.note_number), dateFmt(r.note_date), esc(r.original_invoice), r.rate + '%', m(r.taxable), m(r.igst), m(r.cgst), m(r.sgst)])
    + gstTable('Credit notes — unregistered (B2C Large)', 'Table 9B (CDNUR)', [['Note'], ['Date'], ['Place of supply'], ['Rate', 1], ['Taxable', 1], ['IGST', 1]], g.cdnur, r => [esc(r.note_number), dateFmt(r.note_date), esc(r.pos), r.rate + '%', m(r.taxable), m(r.igst)])
    + gstTable('Nil rated, exempt and non-GST', 'Table 8', [['Description'], ['Nil rated', 1], ['Exempted', 1], ['Non-GST', 1]], g.nil.filter(r => r.nil_rated), r => [esc(r.description), m(r.nil_rated), m(r.exempted), m(r.non_gst)])
    + gstTable('HSN summary — B2B', 'Table 12', [['HSN/SAC'], ['Description'], ['UQC'], ['Qty', 1], ['Total value', 1], ['Rate', 1], ['Taxable', 1], ['IGST', 1], ['CGST', 1], ['SGST', 1]], g.hsn_b2b, r => [esc(r.hsn), esc(r.description), esc(r.uqc), num(r.qty), m(r.total_value), r.rate + '%', m(r.taxable), m(r.igst), m(r.cgst), m(r.sgst)])
    + gstTable('HSN summary — B2C', 'Table 12', [['HSN/SAC'], ['Description'], ['UQC'], ['Qty', 1], ['Total value', 1], ['Rate', 1], ['Taxable', 1], ['IGST', 1], ['CGST', 1], ['SGST', 1]], g.hsn_b2c, r => [esc(r.hsn), esc(r.description), esc(r.uqc), num(r.qty), m(r.total_value), r.rate + '%', m(r.taxable), m(r.igst), m(r.cgst), m(r.sgst)])
    + gstTable('Documents issued', 'Table 13', [['Nature of document'], ['From'], ['To'], ['Total', 1], ['Cancelled', 1], ['Net issued', 1]], g.docs, r => [esc(r.nature), esc(r.from), esc(r.to), r.total, r.cancelled, r.net_issued]);
}
function gstr3bHTML(g) {
  const m = v => money(v), o = g.outward, a = g.itc_available, rd = g.itc_reduced, n = g.itc_net, p = g.payment;
  const row = (label, cells, cls = '') => `<tr class="${cls}"><td>${label}</td>${cells.map(c => `<td class="right">${c === '' ? '' : m(c)}</td>`).join('')}</tr>`;
  const outTax = o.igst + o.cgst + o.sgst, itcTax = n.igst + n.cgst + n.sgst, cash = p.cash.igst + p.cash.cgst + p.cash.sgst;
  return warnHTML(g.warnings)
    + `<div class="grid stats">${statCard('OUTPUT TAX', m(outTax), `On taxable value ${m(o.taxable)}`, '↗')}${statCard('NET ITC', m(itcTax), `${g.purchases} purchase bill${g.purchases === 1 ? '' : 's'}`, '⇩')}${statCard('PAYABLE IN CASH (EST.)', m(cash), 'After ITC set-off', '₹')}${statCard('ITC CARRIED FORWARD', m(p.carry_forward.igst + p.carry_forward.cgst + p.carry_forward.sgst), 'Unused credit (estimate)', '◷')}</div>`
    + `<div class="panel" style="margin-bottom:16px"><div class="panel-head"><div><h3>3.1 Outward supplies and 4. Eligible ITC</h3><p>${dateFmt(g.from)} – ${dateFmt(g.to)}</p></div></div><div class="table-wrap"><table><thead><tr><th>Nature of supply</th><th class="right">Taxable value</th><th class="right">IGST</th><th class="right">CGST</th><th class="right">SGST/UTGST</th></tr></thead><tbody>
      ${row('3.1(a) Outward taxable supplies (other than zero rated, nil rated and exempted)', [o.taxable, o.igst, o.cgst, o.sgst])}
      ${row('3.1(c) Other outward supplies (nil rated, exempted)', [g.nil_outward, '', '', ''])}
      ${row('4(A)(5) All other ITC (from purchase bills)', ['', a.igst, a.cgst, a.sgst])}
      ${row('Less: ITC reduced by debit notes / purchase returns', ['', -rd.igst, -rd.cgst, -rd.sgst])}
      ${row('<b>4(C) Net ITC available</b>', ['', n.igst, n.cgst, n.sgst], 'strong')}
      ${row('5. Inward exempt / nil rated supplies — inter-state', [g.inward_nil.inter, '', '', ''])}
      ${row('5. Inward exempt / nil rated supplies — intra-state', [g.inward_nil.intra, '', '', ''])}
    </tbody></table></div></div>`
    + `<div class="grid" style="grid-template-columns:minmax(0,1.2fr) minmax(260px,.8fr);gap:16px;align-items:start"><div class="panel"><div class="panel-head"><div><h3>6.1 Payment of tax (estimate)</h3><p>IGST credit is used first, then CGST and SGST. CGST and SGST credits are never used against each other.</p></div></div><div class="table-wrap"><table><thead><tr><th>Head</th><th class="right">Output tax</th><th class="right">ITC used</th><th class="right">Pay in cash</th><th class="right">ITC left</th></tr></thead><tbody>
      ${['igst', 'cgst', 'sgst'].map(h => `<tr><td class="strong">${h.toUpperCase()}</td><td class="right">${m(o[h])}</td><td class="right">${m(o[h] - p.cash[h])}</td><td class="right strong">${m(p.cash[h])}</td><td class="right">${m(p.carry_forward[h])}</td></tr>`).join('')}
    </tbody></table></div><div class="panel-body tiny">${Object.entries(p.utilised).map(([k, v]) => `${esc(k.replace('->', ' credit → ').toUpperCase())}: ${m(v)}`).join(' · ') || 'No ITC used.'}</div></div>
    <div class="panel"><div class="panel-head"><div><h3>3.2 Inter-state supplies to unregistered persons</h3><p>By place of supply</p></div></div>${g.unregistered_inter.length ? `<div class="table-wrap"><table><thead><tr><th>Place of supply</th><th class="right">Taxable</th><th class="right">IGST</th></tr></thead><tbody>${g.unregistered_inter.map(r => `<tr><td>${esc(r.pos)}</td><td class="right">${m(r.taxable)}</td><td class="right">${m(r.igst)}</td></tr>`).join('')}</tbody></table></div>` : '<div class="empty-inline" style="padding:16px">None in this period.</div>'}</div></div>`;
}
async function exportGST(fmt) {
  const from = $('#gstFrom').value, to = $('#gstTo').value;
  if (!from || !to) { toast('Choose the return period first', 'error'); return; }
  try {
    const ret = fmt === 'register' ? 'register' : gstTab, format = fmt === 'register' ? 'xlsx' : fmt;
    const r = await fetch(`/api/gst/export?return=${ret}&format=${format}&from=${from}&to=${to}`, { credentials: 'same-origin' });
    if (!r.ok) { const d = await r.json().catch(() => ({})); throw new Error(d.error || 'Export failed'); }
    const name = (r.headers.get('Content-Disposition') || '').match(/filename="([^"]+)"/)?.[1] || `${gstTab}.${fmt}`;
    downloadBlob(await r.blob(), name); toast(`${name} downloaded`);
  } catch (e) { toast(e.message, 'error'); }
}

// =====================================================================================
// 11. Invoice templates and printing
// =====================================================================================
const TEMPLATES = [
  { id: 'modern', name: 'Modern', desc: 'Colour header, clean totals' },
  { id: 'classic', name: 'Classic GST', desc: 'Boxed, Tally-style tax invoice' },
  { id: 'minimal', name: 'Minimal', desc: 'Black & white, saves ink' },
  { id: 'thermal', name: 'Thermal 80mm', desc: 'POS receipt printers' },
];
const TPL_COLORS = ['#4f46e5', '#0f766e', '#1d4ed8', '#7c3aed', '#be123c', '#c2410c', '#15803d', '#0f172a'];
const GST_CODES = { 'Jammu and Kashmir': '01', 'Himachal Pradesh': '02', 'Punjab': '03', 'Chandigarh': '04', 'Uttarakhand': '05', 'Haryana': '06', 'Delhi': '07', 'Rajasthan': '08', 'Uttar Pradesh': '09', 'Bihar': '10', 'Sikkim': '11', 'Arunachal Pradesh': '12', 'Nagaland': '13', 'Manipur': '14', 'Mizoram': '15', 'Tripura': '16', 'Meghalaya': '17', 'Assam': '18', 'West Bengal': '19', 'Jharkhand': '20', 'Odisha': '21', 'Chhattisgarh': '22', 'Madhya Pradesh': '23', 'Gujarat': '24', 'Dadra and Nagar Haveli and Daman and Diu': '26', 'Maharashtra': '27', 'Karnataka': '29', 'Goa': '30', 'Lakshadweep': '31', 'Kerala': '32', 'Tamil Nadu': '33', 'Puducherry': '34', 'Andaman and Nicobar Islands': '35', 'Telangana': '36', 'Andhra Pradesh': '37', 'Ladakh': '38' };
const stateWithCode = s => s ? (GST_CODES[s] ? `${s} (${GST_CODES[s]})` : s) : '—';

function amountInWords(amount) {
  const a = ['', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight', 'Nine', 'Ten', 'Eleven', 'Twelve', 'Thirteen', 'Fourteen', 'Fifteen', 'Sixteen', 'Seventeen', 'Eighteen', 'Nineteen'];
  const b = ['', '', 'Twenty', 'Thirty', 'Forty', 'Fifty', 'Sixty', 'Seventy', 'Eighty', 'Ninety'];
  const two = n => n < 20 ? a[n] : b[Math.floor(n / 10)] + (n % 10 ? ' ' + a[n % 10] : '');
  const three = n => n >= 100 ? a[Math.floor(n / 100)] + ' Hundred' + (n % 100 ? ' and ' + two(n % 100) : '') : two(n);
  const words = n => {
    if (n === 0) return 'Zero';
    const parts = [], crore = Math.floor(n / 1e7); n %= 1e7;
    const lakh = Math.floor(n / 1e5); n %= 1e5; const th = Math.floor(n / 1000); n %= 1000;
    if (crore) parts.push(words(crore) + ' Crore'); if (lakh) parts.push(two(lakh) + ' Lakh'); if (th) parts.push(two(th) + ' Thousand'); if (n) parts.push(three(n));
    return parts.join(' ');
  };
  const paiseTotal = Math.round(Math.abs(Number(amount) || 0) * 100), rupees = Math.floor(paiseTotal / 100), paise = paiseTotal % 100;
  return 'Rupees ' + words(rupees) + (paise ? ' and ' + two(paise) + ' Paise' : '') + ' Only';
}
function currentDesign(draft) {
  const b = state.business || {};
  return draft || { invoice_template: b.invoice_template || 'modern', invoice_color: b.invoice_color || '#4f46e5', invoice_terms: b.invoice_terms || '', show_upi_qr: b.show_upi_qr ?? 1, show_hsn_summary: b.show_hsn_summary ?? 1 };
}
function splitTax(tax, inter) { const c = Math.round(tax / 2 * 100) / 100; return inter ? { cgst: 0, sgst: 0, igst: tax } : { cgst: c, sgst: Math.round((tax - c) * 100) / 100, igst: 0 }; }
function bizFor(snapshot) { const cur = state.business || {}; const b = { ...cur, ...(snapshot || {}) }; ['logo_data', 'bank_details', 'upi_id'].forEach(k => { if (!b[k]) b[k] = cur[k]; }); return b; }

function modelInvoice(inv) {
  const b = bizFor(inv.business);
  return { kind: 'invoice', title: b.gstin ? 'Tax Invoice' : 'Invoice', copy: 'Original for recipient', number: inv.invoice_number, date: inv.invoice_date, business: b,
    party: { label: 'Bill to', ...inv.customer }, meta: [['Place of supply', stateWithCode(inv.place_of_supply)], ['Reverse charge', 'No'], ['Supply type', inv.supply_type]],
    items: inv.items, inter: inv.supply_type === 'IGST', totals: { subtotal: inv.subtotal, cgst: inv.cgst, sgst: inv.sgst, igst: inv.igst, total: inv.total },
    extra: [['Amount received', inv.paid], ...(inv.credited ? [['Credit notes', inv.credited]] : []), ...(inv.refunded ? [['Refunded', -inv.refunded]] : []), ['Balance due', inv.balance, 'strong']],
    notes: inv.notes, qr: inv.balance > 0.009 && b.upi_id ? `/api/upi-qr?invoice_id=${inv.id}` : '', qrAmount: inv.balance, bank: true, sign: true };
}
function modelCreditNote(c) {
  const b = bizFor(c.business);
  return { kind: 'credit', title: 'Credit Note', copy: 'Original for recipient', number: c.note_number, date: c.note_date, business: b, party: { label: 'Issued to', ...c.customer },
    meta: [['Against invoice', `${c.invoice_number} dt. ${dateFmt(c.invoice_date)}`], ['Reason', c.reason], ['Place of supply', stateWithCode(c.place_of_supply)]],
    items: c.items, inter: c.supply_type === 'IGST', totals: { subtotal: c.subtotal, cgst: c.cgst, sgst: c.sgst, igst: c.igst, total: c.total },
    extra: Number(c.refund_amount) ? [['Refunded to customer', c.refund_amount]] : [], notes: c.notes, sign: true };
}
function supplierInter(sup) { const s = state.business?.state; return !!(sup?.state && s && sup.state !== s); }
function purchaseLines(items, inter) {
  return items.map(x => { const taxable = Number(x.taxable || x.qty * x.rate), tax = Number(x.tax ?? (x.amount != null ? x.amount - taxable : 0)); return { ...x, unit: x.unit || '', hsn: x.hsn || '', taxable, ...splitTax(Math.round(tax * 100) / 100, inter), line_total: Number(x.line_total ?? x.amount ?? taxable + tax) }; });
}
function sumLines(lines) { const t = { subtotal: 0, cgst: 0, sgst: 0, igst: 0, total: 0 }; lines.forEach(l => { t.subtotal += +l.taxable; t.cgst += +l.cgst; t.sgst += +l.sgst; t.igst += +l.igst; t.total += +l.line_total; }); return t; }
function modelDebitNote(d) {
  const inter = supplierInter(d.supplier), lines = purchaseLines(d.items, inter);
  return { kind: 'debit', title: 'Debit Note', copy: 'Purchase return', number: d.return_number, date: d.return_date, business: bizFor(d.business), party: { label: 'To supplier', ...d.supplier },
    meta: [['Against bill', `${d.purchase_number} dt. ${dateFmt(d.purchase_date)}`], ['Reason', d.notes || 'Goods returned']], items: lines, inter, totals: { ...sumLines(lines), total: d.amount }, extra: [], notes: '', sign: true };
}
function modelPurchase(p) {
  const inter = supplierInter(p.supplier), lines = purchaseLines(p.items, inter);
  return { kind: 'purchase', title: 'Purchase Bill', copy: 'Internal record', number: p.purchase_number, date: p.purchase_date, business: bizFor(), party: { label: 'Supplier', ...p.supplier },
    meta: [], items: lines, inter, totals: { ...sumLines(lines), total: p.total }, extra: [['Paid', p.paid], ...(p.returned ? [['Returned (debit notes)', p.returned]] : []), ['Balance payable', p.balance, 'strong']], notes: p.notes, sign: false };
}
function modelQuotation(q) {
  return { kind: 'quote', title: 'Quotation', copy: 'Estimate — not a tax invoice', number: q.quote_number, date: q.quote_date, business: bizFor(), party: { label: 'Prepared for', ...q.customer },
    meta: [['Status', q.status]], items: q.items, inter: Number(q.igst) > 0, totals: { subtotal: q.subtotal, cgst: q.cgst, sgst: q.sgst, igst: q.igst, total: q.total }, extra: [], notes: q.notes, sign: true };
}
function sampleModel() {
  const b = state.business || {}, inter = false;
  const items = [{ description: 'LED Bulb 9W', hsn: '8539', qty: 10, unit: 'pcs', rate: 120, gst_rate: 18 }, { description: 'Ceiling fan 1200mm', details: 'Serial no: CF26-00891, CF26-00892\nColour: White · 2-year warranty', hsn: '8414', qty: 2, unit: 'pcs', rate: 1850, gst_rate: 18 }, { description: 'Installation service', hsn: '998719', qty: 1, unit: 'service', rate: 500, gst_rate: 18 }]
    .map(x => { const taxable = x.qty * x.rate, tax = Math.round(taxable * x.gst_rate) / 100; return { ...x, taxable, ...splitTax(tax, inter), line_total: taxable + tax }; });
  const t = sumLines(items);
  return { kind: 'invoice', title: b.gstin ? 'Tax Invoice' : 'Invoice', copy: 'Original for recipient', number: `${b.invoice_prefix || 'INV'}/2026-27/0042`, date: today(), business: { name: 'Your Business Name', ...b },
    party: { label: 'Bill to', name: 'Sample Customer Pvt Ltd', address: '12 MG Road, Pune 411001', gstin: '27AAACS1234C1Z9', state: b.state || 'Maharashtra', phone: '98xxxxxx10' },
    meta: [['Place of supply', stateWithCode(b.state || 'Maharashtra')], ['Reverse charge', 'No'], ['Supply type', 'CGST/SGST']], items, inter, totals: t,
    extra: [['Amount received', 2000], ['Balance due', t.total - 2000, 'strong']], notes: 'Thank you for your business!', qr: b.upi_id ? 'SAMPLE' : '', qrAmount: t.total - 2000, bank: true, sign: true };
}

function hsnSummary(items, inter) {
  const m = {};
  items.forEach(x => { const k = (x.hsn || '—') + '|' + x.gst_rate; m[k] = m[k] || { hsn: x.hsn || '—', rate: +x.gst_rate, taxable: 0, cgst: 0, sgst: 0, igst: 0 }; ['taxable', 'cgst', 'sgst', 'igst'].forEach(f => m[k][f] += Number(x[f] || 0)); });
  const rows = Object.values(m); if (!rows.length) return '';
  const head = inter ? '<th class="r">IGST</th>' : '<th class="r">CGST</th><th class="r">SGST</th>';
  return `<table class="hsn"><thead><tr><th>HSN/SAC</th><th class="r">Taxable value</th><th class="r">Rate</th>${head}<th class="r">Total tax</th></tr></thead><tbody>${rows.map(r => `<tr><td>${esc(r.hsn)}</td><td class="r">${money(r.taxable)}</td><td class="r">${num(r.rate)}%</td>${inter ? `<td class="r">${money(r.igst)}</td>` : `<td class="r">${money(r.cgst)}</td><td class="r">${money(r.sgst)}</td>`}<td class="r">${money(r.cgst + r.sgst + r.igst)}</td></tr>`).join('')}</tbody></table>`;
}
function renderDocHTML(m, design, opts = {}) {
  const tpl = design.invoice_template || 'modern', color = /^#[0-9a-f]{6}$/i.test(design.invoice_color || '') ? design.invoice_color : '#4f46e5';
  const b = m.business || {}, p = m.party || {}, t = m.totals;
  const logo = b.logo_data ? `<img class="logo" src="${b.logo_data}" alt="">` : '';
  const qrBlock = design.show_upi_qr && m.qr ? `<div class="qr">${m.qr === 'SAMPLE' ? '<div class="qr-ph">UPI QR</div>' : `<img src="${m.qr}" alt="UPI QR">`}<div><b>Scan to pay ${money(m.qrAmount)}</b><br><span>UPI: ${esc(b.upi_id)}</span></div></div>` : '';
  const bank = m.bank && (b.bank_details || b.upi_id) ? `<div class="bank"><b>Bank / payment details</b><br>${esc(b.bank_details || '').replace(/\n/g, '<br>')}${b.upi_id ? `<br>UPI: ${esc(b.upi_id)}` : ''}</div>` : '';
  const terms = design.invoice_terms && m.kind !== 'purchase' ? `<div class="terms"><b>Terms &amp; conditions</b><br>${esc(design.invoice_terms).replace(/\n/g, '<br>')}</div>` : '';
  const taxRows = (m.inter ? [['IGST', t.igst]] : [['CGST', t.cgst], ['SGST', t.sgst]]).filter(([, v]) => Number(v) || !m.inter);
  const totalsHTML = `<table class="totals"><tr><td>Taxable value</td><td class="r">${money(t.subtotal)}</td></tr>${taxRows.map(([k, v]) => `<tr><td>${k}</td><td class="r">${money(v)}</td></tr>`).join('')}<tr class="grand"><td>Total</td><td class="r">${money(t.total)}</td></tr>${(m.extra || []).map(([k, v, cls]) => `<tr class="${cls || ''}"><td>${esc(k)}</td><td class="r">${money(v)}</td></tr>`).join('')}</table>`;
  const words = `<div class="words"><b>Amount in words:</b> ${amountInWords(t.total)}</div>`;
  const sign = m.sign ? `<div class="sign">For <b>${esc(b.name || '')}</b><div class="sign-space"></div>Authorised signatory</div>` : '';
  const partyHTML = `<div class="label">${esc(p.label || '')}</div><div class="pname">${esc(p.name || '')}</div>${p.address ? `<div>${esc(p.address).replace(/\n/g, '<br>')}</div>` : ''}${p.phone ? `<div>Phone: ${esc(p.phone)}</div>` : ''}<div>${p.gstin ? 'GSTIN: <b>' + esc(p.gstin) + '</b>' : 'Unregistered'}</div>${p.state ? `<div>State: ${esc(stateWithCode(p.state))}</div>` : ''}`;
  const metaHTML = (withMeta = true) => `<table class="meta"><tr><td>${esc(m.kind === 'quote' ? 'Quotation no.' : m.title + ' no.')}</td><td><b>${esc(m.number)}</b></td></tr><tr><td>Date</td><td><b>${dateFmt(m.date)}</b></td></tr>${withMeta ? m.meta.map(([k, v]) => `<tr><td>${esc(k)}</td><td>${esc(v)}</td></tr>`).join('') : ''}</table>`;
  const bizHTML = `${logo}<div><div class="bname">${esc(b.name || '')}</div><div>${esc(b.address || '').replace(/\n/g, '<br>')}</div><div>${[b.phone && 'Phone: ' + esc(b.phone), b.email && esc(b.email)].filter(Boolean).join(' · ')}</div><div>${b.gstin ? 'GSTIN: <b>' + esc(b.gstin) + '</b>' : ''}${b.state ? ' · State: ' + esc(stateWithCode(b.state)) : ''}</div></div>`;
  const autoPrint = opts.autoPrint ? `<script>window.addEventListener('load',()=>setTimeout(()=>window.print(),350));<\/script>` : '';

  if (tpl === 'thermal') {
    const lines = m.items.map(x => `<tr><td colspan="3">${esc(x.description)}${x.details ? `<div class="det">${esc(x.details)}</div>` : ''}</td></tr><tr class="sub"><td>${num(x.qty)} × ${money(x.rate)}</td><td class="r">${num(x.gst_rate)}%</td><td class="r">${money(x.line_total)}</td></tr>`).join('');
    return `<!doctype html><html><head><meta charset="utf-8"><title>${esc(m.number)}</title><style>
      @page{size:80mm auto;margin:3mm}*{box-sizing:border-box}body{font:12px/1.35 "Courier New",ui-monospace,monospace;color:#000;width:72mm;margin:0 auto;padding:4px 0}
      .c{text-align:center}.r{text-align:right}.logo{max-width:40mm;max-height:18mm;display:block;margin:0 auto 4px;filter:grayscale(1)}h1{font-size:15px;margin:2px 0}hr{border:0;border-top:1px dashed #000;margin:6px 0}
      table{width:100%;border-collapse:collapse}td{padding:1px 0;vertical-align:top}.sub td{padding-bottom:4px}.tot td{padding:1px 0}.grand td{font-weight:bold;font-size:14px;border-top:1px dashed #000;padding-top:4px}
      .det{white-space:pre-line;font-size:10px}.qr{text-align:center;margin-top:6px}.qr img,.qr-ph{width:34mm;height:34mm}.qr-ph{border:1px dashed #000;display:inline-grid;place-items:center}.small{font-size:10px}
      @media screen{body{background:#fff;box-shadow:0 0 0 1px #ddd;padding:8px}}</style></head><body>
      <div class="c">${logo}<h1>${esc(b.name || '')}</h1><div class="small">${esc(b.address || '')}</div>${b.phone ? `<div class="small">Ph: ${esc(b.phone)}</div>` : ''}${b.gstin ? `<div class="small">GSTIN: ${esc(b.gstin)}</div>` : ''}</div><hr>
      <div class="c"><b>${esc(m.title.toUpperCase())}</b></div><div>No: ${esc(m.number)}<br>Date: ${dateFmt(m.date)}<br>${esc(p.label)}: ${esc(p.name || '')}${p.gstin ? '<br>GSTIN: ' + esc(p.gstin) : ''}</div><hr>
      <table>${lines}</table><hr><table class="tot"><tr><td>Taxable</td><td class="r">${money(t.subtotal)}</td></tr>${taxRows.map(([k, v]) => `<tr><td>${k}</td><td class="r">${money(v)}</td></tr>`).join('')}<tr class="grand"><td>TOTAL</td><td class="r">${money(t.total)}</td></tr>${(m.extra || []).map(([k, v]) => `<tr><td>${esc(k)}</td><td class="r">${money(v)}</td></tr>`).join('')}</table>
      <div class="small" style="margin-top:4px">${amountInWords(t.total)}</div>${design.show_upi_qr && m.qr ? `<div class="qr">${m.qr === 'SAMPLE' ? '<div class="qr-ph">UPI QR</div>' : `<img src="${m.qr}">`}<div class="small">Scan to pay ${money(m.qrAmount)}</div></div>` : ''}
      <hr><div class="c small">${esc(design.invoice_terms || 'Thank you! Visit again.').replace(/\n/g, '<br>')}</div>${autoPrint}</body></html>`;
  }

  const cols = m.inter ? ['IGST'] : ['CGST', 'SGST'];
  const itemHead = tpl === 'classic'
    ? `<tr><th>#</th><th>Description of goods / services</th><th>HSN/SAC</th><th class="r">Qty</th><th class="r">Rate</th><th class="r">Taxable</th><th class="r">GST %</th>${cols.map(c => `<th class="r">${c}</th>`).join('')}<th class="r">Amount</th></tr>`
    : `<tr><th>#</th><th>Item</th><th class="r">Qty</th><th class="r">Rate</th><th class="r">GST</th><th class="r">Amount</th></tr>`;
  const itemRows = m.items.map((x, i) => tpl === 'classic'
    ? `<tr><td>${i + 1}</td><td>${esc(x.description)}${x.details ? `<div class="det">${esc(x.details)}</div>` : ''}</td><td>${esc(x.hsn || '')}</td><td class="r">${num(x.qty)} ${esc(x.unit || '')}</td><td class="r">${money(x.rate)}</td><td class="r">${money(x.taxable)}</td><td class="r">${num(x.gst_rate)}%</td>${(m.inter ? [x.igst] : [x.cgst, x.sgst]).map(v => `<td class="r">${money(v)}</td>`).join('')}<td class="r"><b>${money(x.line_total)}</b></td></tr>`
    : `<tr><td>${i + 1}</td><td><b>${esc(x.description)}</b>${x.details ? `<div class="det">${esc(x.details)}</div>` : ''}${x.hsn ? `<div class="muted">HSN/SAC ${esc(x.hsn)}</div>` : ''}</td><td class="r">${num(x.qty)} ${esc(x.unit || '')}</td><td class="r">${money(x.rate)}</td><td class="r">${num(x.gst_rate)}%</td><td class="r"><b>${money(x.line_total)}</b></td></tr>`).join('');
  const hsn = design.show_hsn_summary && m.kind !== 'quote' ? hsnSummary(m.items, m.inter) : '';
  const base = `*{box-sizing:border-box}body{margin:0;color:#111827;font:12px/1.45 "Segoe UI",Arial,sans-serif;-webkit-print-color-adjust:exact;print-color-adjust:exact}
    .page{max-width:820px;margin:0 auto;padding:28px}.r{text-align:right}.muted{color:#6b7280;font-size:10.5px}table{border-collapse:collapse;width:100%}
    .logo{max-width:110px;max-height:70px;object-fit:contain}.bname{font-size:20px;font-weight:800;letter-spacing:-.3px}.pname{font-size:14px;font-weight:700;margin:2px 0}
    .label{font-size:10px;letter-spacing:1px;text-transform:uppercase;color:#6b7280;font-weight:700}.meta td{padding:2px 0}.meta td:first-child{color:#6b7280;padding-right:12px}
    .totals td{padding:5px 8px}.totals .grand td{font-size:15px;font-weight:800}.totals .strong td{font-weight:800}
    .words{margin-top:8px;font-size:11px}.terms,.bank{font-size:10.5px;color:#374151;margin-top:10px}.sign{text-align:right;margin-top:16px;font-size:11px}.sign-space{height:44px}
    .qr{display:flex;gap:10px;align-items:center;margin-top:10px;font-size:11px}.qr img,.qr-ph{width:92px;height:92px}.qr-ph{border:1.5px dashed #9ca3af;display:grid;place-items:center;color:#6b7280;font-size:10px}
    .det{white-space:pre-line;font-size:10.5px;color:#4b5563;font-weight:400;margin-top:2px;line-height:1.35}.hsn{margin-top:12px;font-size:10.5px}.hsn th,.hsn td{border:1px solid #e5e7eb;padding:4px 6px}.hsn th{background:#f9fafb;text-align:left}
    .foot{margin-top:18px;font-size:9.5px;color:#9ca3af;text-align:center}.copy{font-size:9.5px;letter-spacing:1px;text-transform:uppercase}
    @page{size:A4;margin:10mm}@media print{.page{padding:0;max-width:none}}`;
  let css = '', bodyHTML = '';
  const bottom = `<div class="bottom"><div class="left">${words}${hsn}${bank}${qrBlock}${m.notes ? `<div class="terms"><b>Notes</b><br>${esc(m.notes)}</div>` : ''}${terms}</div><div class="right">${totalsHTML}${sign}</div></div>`;
  if (tpl === 'modern') {
    css = `.band{background:${color};color:#fff;padding:22px 26px;border-radius:14px 14px 0 0;display:flex;justify-content:space-between;gap:20px;align-items:flex-start}
      .band .biz{display:flex;gap:14px;align-items:flex-start}.band .logo{background:#fff;border-radius:10px;padding:5px}.band .muted,.band .label{color:rgba(255,255,255,.8)}
      .doc-title{font-size:24px;font-weight:800;text-transform:uppercase;letter-spacing:1px;text-align:right}.wrap{border:1px solid #e5e7eb;border-top:0;border-radius:0 0 14px 14px;padding:22px 26px}
      .parties{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:18px}.items th{background:${color}14;color:${color};text-align:left;font-size:10.5px;text-transform:uppercase;letter-spacing:.5px;padding:9px 8px}
      .items td{padding:9px 8px;border-bottom:1px solid #f1f5f9;vertical-align:top}.bottom{display:grid;grid-template-columns:1fr 300px;gap:24px;margin-top:16px}
      .totals{background:#f9fafb;border-radius:10px}.totals .grand td{background:${color};color:#fff}.totals .grand td:first-child{border-radius:8px 0 0 8px}.totals .grand td:last-child{border-radius:0 8px 8px 0}`;
    bodyHTML = `<div class="band"><div class="biz">${bizHTML}</div><div><div class="doc-title">${esc(m.title)}</div><div class="copy" style="text-align:right">${esc(m.copy || '')}</div></div></div>
      <div class="wrap"><div class="parties"><div>${partyHTML}</div><div>${metaHTML()}</div></div><table class="items"><thead>${itemHead}</thead><tbody>${itemRows}</tbody></table>${bottom}</div>`;
  } else if (tpl === 'classic') {
    css = `.box{border:1.5px solid #111}.title{text-align:center;font-size:16px;font-weight:800;letter-spacing:2px;padding:6px;border-bottom:1.5px solid #111;background:${color}12;text-transform:uppercase}
      .row{display:grid;grid-template-columns:1fr 1fr}.row>div{padding:10px 12px}.row>div+div{border-left:1.5px solid #111}.hdr{display:flex;gap:12px;align-items:flex-start}
      .sec{border-top:1.5px solid #111}.items th,.items td{border:1px solid #111;padding:5px 6px;vertical-align:top}.items th{background:${color}12;font-size:10.5px;text-align:left}
      .items{border-left:0;border-right:0}.items tr>*:first-child{border-left:0}.items tr>*:last-child{border-right:0}
      .bottom{display:grid;grid-template-columns:1fr 290px;border-top:1.5px solid #111}.bottom .left{padding:8px 12px;border-right:1.5px solid #111}.bottom .right{padding:0}
      .totals td{border-bottom:1px solid #111}.totals .grand td{background:${color}12}.sign{padding:0 12px 10px}.hsn th,.hsn td{border-color:#111}`;
    bodyHTML = `<div class="copy r">${esc(m.copy || '')}</div><div class="box"><div class="title">${esc(m.title)}</div>
      <div class="row"><div class="hdr">${bizHTML}</div><div>${metaHTML(false)}</div></div><div class="row sec"><div>${partyHTML}</div><div><div class="label">Details</div>${m.meta.map(([k, v]) => `<div>${esc(k)}: <b>${esc(v)}</b></div>`).join('')}</div></div>
      <div class="sec"><table class="items"><thead>${itemHead}</thead><tbody>${itemRows}</tbody></table></div>${bottom}</div>`;
  } else {
    css = `.top{display:flex;justify-content:space-between;gap:20px;border-bottom:2px solid #111;padding-bottom:14px}.top .biz{display:flex;gap:12px}.logo{filter:grayscale(1)}
      .doc-title{font-size:22px;font-weight:300;letter-spacing:3px;text-transform:uppercase;text-align:right}.parties{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin:16px 0}
      .items th{border-bottom:1.5px solid #111;text-align:left;font-size:10.5px;text-transform:uppercase;letter-spacing:.6px;padding:7px 6px}.items td{padding:8px 6px;border-bottom:1px solid #e5e7eb}
      .bottom{display:grid;grid-template-columns:1fr 280px;gap:24px;margin-top:14px}.totals .grand td{border-top:2px solid #111;border-bottom:2px solid #111}`;
    bodyHTML = `<div class="top"><div class="biz">${bizHTML}</div><div><div class="doc-title">${esc(m.title)}</div><div class="copy r">${esc(m.copy || '')}</div></div></div>
      <div class="parties"><div>${partyHTML}</div><div>${metaHTML()}</div></div><table class="items"><thead>${itemHead}</thead><tbody>${itemRows}</tbody></table>${bottom}`;
  }
  return `<!doctype html><html><head><meta charset="utf-8"><title>${esc(m.title)} ${esc(m.number)}</title><style>${base}${css}</style></head><body><div class="page">${bodyHTML}<div class="foot">${m.kind === 'invoice' && !b.gstin ? 'This is not a GST tax invoice. ' : ''}Computer-generated document · HisabFlow</div></div>${autoPrint}</body></html>`;
}
function openPrintWindow() { const w = window.open('', '_blank'); if (!w) toast('Allow pop-ups for this page to print', 'error'); else w.document.write('<p style="font:14px Segoe UI,sans-serif;padding:30px;color:#64748b">Preparing document…</p>'); return w; }
function writeDoc(w, model, opts = { autoPrint: true }) { w.document.open(); w.document.write(renderDocHTML(model, currentDesign(), opts)); w.document.close(); }
async function printWith(url, toModel) { const w = openPrintWindow(); if (!w) return; try { writeDoc(w, toModel(await api(url))); } catch (e) { try { w.close(); } catch (_) {} toast(e.message, 'error'); } }
printInvoice = id => printWith('/api/invoice?id=' + id, modelInvoice);
printQuotation = id => printWith('/api/quotation?id=' + id, modelQuotation);
printPurchase = id => printWith('/api/purchase?id=' + id, modelPurchase);
function printCreditNote(id) { return printWith('/api/credit-note?id=' + id, modelCreditNote); }
function printDebitNote(id) { return printWith('/api/debit-note?id=' + id, modelDebitNote); }

// Settings → Invoice design
let designDraft = null;
function renderDesignPanel() {
  if (!$('#tplGrid')) return;
  designDraft = { ...currentDesign() };
  $('#tplGrid').innerHTML = TEMPLATES.map(t => `<button type="button" class="tpl-card ${designDraft.invoice_template === t.id ? 'on' : ''}" data-tpl="${t.id}"><span class="tpl-thumb tpl-${t.id}"><i></i><i></i><i></i><i></i></span><b>${t.name}</b><small>${t.desc}</small></button>`).join('');
  $('#tplColors').innerHTML = TPL_COLORS.map(c => `<button type="button" class="swatch ${designDraft.invoice_color === c ? 'on' : ''}" data-c="${c}" style="background:${c}" aria-label="Colour ${c}"></button>`).join('') + `<input type="color" id="tplCustomColor" value="${designDraft.invoice_color}" title="Custom colour">`;
  $('#tplQr').checked = !!Number(designDraft.show_upi_qr); $('#tplHsn').checked = !!Number(designDraft.show_hsn_summary); $('#tplTerms').value = designDraft.invoice_terms || '';
  $$('#tplGrid .tpl-card').forEach(b => b.onclick = () => { designDraft.invoice_template = b.dataset.tpl; $$('#tplGrid .tpl-card').forEach(x => x.classList.toggle('on', x === b)); updatePreview(); });
  $$('#tplColors .swatch').forEach(b => b.onclick = () => { designDraft.invoice_color = b.dataset.c; $('#tplCustomColor').value = b.dataset.c; $$('#tplColors .swatch').forEach(x => x.classList.toggle('on', x === b)); updatePreview(); });
  $('#tplCustomColor').oninput = e => { designDraft.invoice_color = e.target.value; $$('#tplColors .swatch').forEach(x => x.classList.remove('on')); updatePreview(); };
  $('#tplQr').onchange = e => { designDraft.show_upi_qr = e.target.checked ? 1 : 0; updatePreview(); };
  $('#tplHsn').onchange = e => { designDraft.show_hsn_summary = e.target.checked ? 1 : 0; updatePreview(); };
  $('#tplTerms').oninput = e => { designDraft.invoice_terms = e.target.value; clearTimeout(window._tplT); window._tplT = setTimeout(updatePreview, 250); };
  updatePreview();
}
function updatePreview() {
  const f = $('#tplPreview'); if (!f || !designDraft) return;
  f.srcdoc = renderDocHTML(sampleModel(), designDraft, {});
  const wrap = f.parentElement, page = designDraft.invoice_template === 'thermal' ? 380 : 840;
  const scale = Math.min(1, (wrap.clientWidth || page) / page);
  f.style.width = page + 'px'; f.style.height = Math.round(1180) + 'px'; f.style.transform = `scale(${scale})`;
  wrap.style.height = Math.round(1180 * scale) + 'px';
}
window.addEventListener('resize', () => { if (activeView === 'settings') updatePreview(); });
function previewInvoiceDesign() { const w = openPrintWindow(); if (w) { w.document.open(); w.document.write(renderDocHTML(sampleModel(), designDraft || currentDesign(), {})); w.document.close(); } }
async function saveInvoiceDesign() {
  try {
    await api('/api/business/invoice-design', { method: 'POST', body: JSON.stringify({ ...designDraft, show_upi_qr: !!Number(designDraft.show_upi_qr), show_hsn_summary: !!Number(designDraft.show_hsn_summary) }) });
    await reload(); toast('Invoice design saved');
  } catch (e) { toast(e.message, 'error'); }
}

// =====================================================================================
// 12. Start-up
// =====================================================================================
const _startApp = startApp;
startApp = async function (keepView) {
  await _startApp();
  await loadCompanies();
  if (keepView === true) return;
  let saved = null; try { saved = localStorage.getItem('hf.view'); } catch (_) {}
  if (state.business?.name && saved && titles[saved] && saved !== activeView) navigate(saved);
  else navigate(activeView);
};
(async function boot() {
  applyLayoutPrefs(); injectPalette();
  try {
    const st = await api('/api/auth/status');
    if (st.version && $('#appVersion')) $('#appVersion').textContent = st.version;
    if (st.setup_required) { showAuth('setup'); return; }
    if (!st.user) { showAuth('login'); return; }
    currentUser = st.user; await startApp();
  } catch (e) {
    document.body.innerHTML = '<div style="min-height:100vh;display:grid;place-items:center;background:linear-gradient(135deg,#1e1b4b,#6d28d9);font:15px Segoe UI,system-ui;color:#fff;padding:30px;text-align:center"><div><div style="font-size:42px">⚠</div><h2>Could not connect to HisabFlow</h2><p style="color:#ddd6fe">Close this tab, then double-click <b>run_hisabflow.bat</b> again and keep its window open.</p></div></div>';
  }
})();

// =====================================================================================
// 13. Item details boxes (serial numbers, model, colour…) grow to fit their text
// =====================================================================================
function autoGrow(el) { el.style.height = 'auto'; el.style.height = Math.min(el.scrollHeight + 2, 160) + 'px'; }
function growAllDetails() { $$('.line-details').forEach(autoGrow); }
document.addEventListener('input', e => { if (e.target.matches?.('.line-details')) autoGrow(e.target); });
document.addEventListener('change', e => { if (e.target.matches?.('.line-product,.ql-product')) setTimeout(growAllDetails, 0); });
const _addInvoiceLine = addInvoiceLine;
addInvoiceLine = function (item = {}) { _addInvoiceLine(item); growAllDetails(); };
const _addQuoteLine = addQuoteLine;
addQuoteLine = function (item = {}) { _addQuoteLine(item); growAllDetails(); };
