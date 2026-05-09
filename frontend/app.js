const els = {
  fileInput: document.querySelector("#fileInput"),
  ocrText: document.querySelector("#ocrText"),
  uploadBtn: document.querySelector("#uploadBtn"),
  sampleBtn: document.querySelector("#sampleBtn"),
  refreshBtn: document.querySelector("#refreshBtn"),
  uploadStatus: document.querySelector("#uploadStatus"),
  receiptList: document.querySelector("#receiptList"),
  detailTitle: document.querySelector("#detailTitle"),
  detailMeta: document.querySelector("#detailMeta"),
  emptyState: document.querySelector("#emptyState"),
  detailView: document.querySelector("#detailView"),
  sourcePreview: document.querySelector("#sourcePreview"),
  rawTextBox: document.querySelector("#rawTextBox"),
  qualityBadge: document.querySelector("#qualityBadge"),
  statusBadge: document.querySelector("#statusBadge"),
  validationBox: document.querySelector("#validationBox"),
  headerForm: document.querySelector("#headerForm"),
  saveHeaderBtn: document.querySelector("#saveHeaderBtn"),
  itemsBody: document.querySelector("#itemsBody"),
  itemCount: document.querySelector("#itemCount"),
  eventsList: document.querySelector("#eventsList"),
  approveBtn: document.querySelector("#approveBtn"),
  retryBtn: document.querySelector("#retryBtn"),
};

const SAMPLE_TEXT = `FRESH VALLEY MARKET #204
1450 LAKE ROAD
05/05/2026 18:23
REG 03 CASHIER MARIA

BANANAS 1.245 LB @ .69/LB 0.86
ORGANIC MILK 1 GAL 5.49
YOGURT 4 @ 1.25 5.00
WHOLE WHEAT BREAD 3.79
ROMA TOMATOES 2.10 LB @ 1.49/LB 3.13
CARD PRICE -1.00
EGGS LARGE 12 CT 4.99
MFR COUPON -0.75

SUBTOTAL 23.26
SALES TAX 0.94
TOTAL 22.45
VISA **** 4242 22.45
AUTH 7G92K
THANK YOU`;

let currentReceiptId = null;
let pollHandle = null;

els.uploadBtn.addEventListener("click", uploadAndProcess);
els.refreshBtn.addEventListener("click", loadReceipts);
els.sampleBtn.addEventListener("click", loadSample);
els.saveHeaderBtn.addEventListener("click", saveHeader);
els.approveBtn.addEventListener("click", approveCurrent);
els.retryBtn.addEventListener("click", retryCurrent);

loadReceipts();

function loadSample() {
  els.ocrText.value = SAMPLE_TEXT;
  const file = new File([SAMPLE_TEXT], "sample-receipt.txt", { type: "text/plain" });
  const transfer = new DataTransfer();
  transfer.items.add(file);
  els.fileInput.files = transfer.files;
  setStatus("Sample receipt loaded.");
}

async function uploadAndProcess() {
  const file = els.fileInput.files[0];
  if (!file) {
    setStatus("Choose a file first.");
    return;
  }

  setStatus("Creating upload session...");
  const session = await api("/api/receipts/uploads", {
    method: "POST",
    body: {
      file_name: file.name,
      content_type: file.type || contentTypeFromName(file.name),
      file_size_bytes: file.size,
      source: "web_upload",
      client_timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      currency_code: "NGN",
    },
  });

  setStatus("Uploading file...");
  const uploadResponse = await fetch(session.upload_url, {
    method: session.method,
    headers: { "Content-Type": file.type || contentTypeFromName(file.name) },
    body: file,
  });
  if (!uploadResponse.ok) {
    throw new Error(await uploadResponse.text());
  }

  setStatus("Processing...");
  await api(`/api/receipts/${session.receipt_id}/process`, {
    method: "POST",
    body: { ocr_text: els.ocrText.value.trim() },
  });
  currentReceiptId = session.receipt_id;
  await loadReceipts();
  startPolling(session.receipt_id);
}

async function loadReceipts() {
  const data = await api("/api/receipts");
  els.receiptList.innerHTML = "";
  if (!data.receipts.length) {
    els.receiptList.innerHTML = `<div class="muted">No receipts yet.</div>`;
    return;
  }
  data.receipts.forEach((receipt) => {
    const row = document.createElement("button");
    row.type = "button";
    row.className = `receipt-row ${receipt.id === currentReceiptId ? "active" : ""}`;
    row.innerHTML = `
      <strong>${escapeHtml(receipt.store_name || receipt.original_file_name || "Receipt")}</strong>
      <span class="muted">${receipt.receipt_date || "No date"} · ${money(receipt.total_amount, receipt.currency_code)}</span>
      <span class="badge ${receipt.status}">${receipt.status}</span>
    `;
    row.addEventListener("click", () => {
      currentReceiptId = receipt.id;
      startPolling(receipt.id);
      loadReceipts();
    });
    els.receiptList.appendChild(row);
  });
}

function startPolling(receiptId) {
  if (pollHandle) {
    clearInterval(pollHandle);
  }
  loadReceipt(receiptId);
  pollHandle = setInterval(async () => {
    const receipt = await loadReceipt(receiptId);
    if (!["queued", "processing"].includes(receipt.status)) {
      clearInterval(pollHandle);
      pollHandle = null;
      loadReceipts();
    }
  }, 1300);
}

async function loadReceipt(receiptId) {
  const receipt = await api(`/api/receipts/${receiptId}`);
  renderReceipt(receipt);
  return receipt;
}

function renderReceipt(receipt) {
  els.emptyState.classList.add("hidden");
  els.detailView.classList.remove("hidden");
  els.detailTitle.textContent = receipt.store_name || receipt.original_file_name || "Receipt";
  els.detailMeta.textContent = `${receipt.receipt_date || "No date"} · ${money(receipt.total_amount, receipt.currency_code)} · ${receipt.id}`;
  setBadge(els.statusBadge, receipt.status);
  els.approveBtn.disabled = !receipt.items.length || receipt.status === "approved";
  els.retryBtn.disabled = !receipt.id;

  renderSource(receipt);
  renderValidation(receipt.validation_errors || []);
  renderHeader(receipt);
  renderItems(receipt.items || []);
  renderEvents(receipt.events || []);
}

function renderSource(receipt) {
  const page = receipt.pages?.[0];
  const quality = page?.quality_score;
  els.qualityBadge.textContent = quality == null ? "No score" : `Quality ${Math.round(quality * 100)}%`;
  els.qualityBadge.className = `badge ${quality != null && quality < 0.55 ? "needs_review" : "validated"}`;

  if ((receipt.content_type || "").startsWith("image/")) {
    els.sourcePreview.innerHTML = `<img src="${receipt.file_url}" alt="Receipt source" />`;
  } else {
    els.sourcePreview.innerHTML = `<div class="muted">${escapeHtml(receipt.original_file_name || "Document")}</div>`;
  }

  els.rawTextBox.textContent = receipt.raw_text || "No OCR text stored yet.";
}

function renderValidation(errors) {
  els.validationBox.innerHTML = "";
  if (!errors.length) {
    els.validationBox.innerHTML = `<div class="validation-item">Validation passed.</div>`;
    return;
  }
  errors.forEach((error) => {
    const item = document.createElement("div");
    item.className = `validation-item ${error.severity === "error" ? "error" : ""}`;
    item.textContent = `${error.code}: ${error.message}`;
    els.validationBox.appendChild(item);
  });
}

function renderHeader(receipt) {
  setFormValue("store_raw_name", receipt.store_raw_name || receipt.store_name || "");
  setFormValue("ticket_number", receipt.ticket_number || receipt.transaction_id || "");
  setFormValue("receipt_date", receipt.receipt_date || "");
  setFormValue("receipt_time", trimSeconds(receipt.receipt_time || ""));
  setFormValue("currency_code", receipt.currency_code || "NGN");
  setFormValue("customer_name", receipt.customer_name || "");
  setFormValue("seller", receipt.seller || "");
  setFormValue("total_amount", numberValue(receipt.total_amount));
}

function renderItems(items) {
  els.itemCount.textContent = `${items.length} row${items.length === 1 ? "" : "s"}`;
  els.itemsBody.innerHTML = "";
  if (!items.length) {
    els.itemsBody.innerHTML = `<tr><td colspan="7" class="muted">No item rows extracted.</td></tr>`;
    return;
  }
  items.forEach((item) => {
    const tr = document.createElement("tr");
    tr.dataset.itemId = item.id;
    tr.innerHTML = `
      <td>${item.line_number}</td>
      <td class="item-name"><input data-field="item_name" value="${escapeAttr(item.item_name || item.item_name_clean || "")}" /></td>
      <td><input data-field="quantity" value="${numberValue(item.quantity)}" /></td>
      <td><input data-field="unit_price" value="${numberValue(item.unit_price ?? item.unit_price_amount)}" /></td>
      <td><input data-field="total_price" value="${numberValue(item.total_price ?? item.total_price_amount)}" /></td>
      <td><span class="confidence ${item.confidence < 0.8 ? "low" : ""}">${Math.round((item.confidence || 0) * 100)}%</span></td>
      <td><button class="secondary-btn save-item" type="button">Save</button></td>
    `;
    tr.querySelector(".save-item").addEventListener("click", () => saveItem(item.id, tr));
    els.itemsBody.appendChild(tr);
  });
}

function renderEvents(events) {
  els.eventsList.innerHTML = "";
  if (!events.length) {
    els.eventsList.innerHTML = `<div class="muted">No events yet.</div>`;
    return;
  }
  events.forEach((event) => {
    const row = document.createElement("div");
    row.className = "event-row";
    row.innerHTML = `
      <span>${formatDateTime(event.created_at)}</span>
      <strong>${escapeHtml(event.status)}</strong>
      <span>${escapeHtml(event.message || event.event_type)}</span>
    `;
    els.eventsList.appendChild(row);
  });
}

async function saveHeader() {
  if (!currentReceiptId) {
    return;
  }
  const data = Object.fromEntries(new FormData(els.headerForm).entries());
  const receipt = await api(`/api/receipts/${currentReceiptId}`, { method: "PATCH", body: data });
  renderReceipt(receipt);
  await loadReceipts();
  setStatus("Header saved.");
}

async function saveItem(itemId, row) {
  const payload = {};
  row.querySelectorAll("input[data-field]").forEach((input) => {
    payload[input.dataset.field] = input.value;
  });
  await api(`/api/receipt-items/${itemId}`, { method: "PATCH", body: payload });
  if (currentReceiptId) {
    await loadReceipt(currentReceiptId);
    await loadReceipts();
  }
  setStatus("Item saved.");
}

async function approveCurrent() {
  if (!currentReceiptId) {
    return;
  }
  const receipt = await api(`/api/receipts/${currentReceiptId}/approve`, { method: "POST" });
  renderReceipt(receipt);
  await loadReceipts();
  setStatus("Receipt approved.");
}

async function retryCurrent() {
  if (!currentReceiptId) {
    return;
  }
  await api(`/api/receipts/${currentReceiptId}/retry`, {
    method: "POST",
    body: { ocr_text: els.ocrText.value.trim() },
  });
  startPolling(currentReceiptId);
  setStatus("Retry queued.");
}

async function api(url, options = {}) {
  const fetchOptions = { method: options.method || "GET", headers: options.headers || {} };
  if (options.body !== undefined) {
    fetchOptions.headers["Content-Type"] = "application/json";
    fetchOptions.body = JSON.stringify(options.body);
  }
  const response = await fetch(url, fetchOptions);
  if (!response.ok) {
    let detail = await response.text();
    try {
      detail = JSON.parse(detail).detail || detail;
    } catch (_) {
      // Keep raw text.
    }
    setStatus(`Error: ${detail}`);
    throw new Error(detail);
  }
  return response.json();
}

function setStatus(message) {
  els.uploadStatus.textContent = message;
}

function setBadge(el, status) {
  el.textContent = status || "unknown";
  el.className = `badge ${status || ""}`;
}

function setFormValue(name, value) {
  const input = els.headerForm.querySelector(`[name="${name}"]`);
  if (input) {
    input.value = value ?? "";
  }
}

function contentTypeFromName(name) {
  const lower = name.toLowerCase();
  if (lower.endsWith(".txt")) return "text/plain";
  if (lower.endsWith(".pdf")) return "application/pdf";
  if (lower.endsWith(".png")) return "image/png";
  if (lower.endsWith(".webp")) return "image/webp";
  if (lower.endsWith(".tif") || lower.endsWith(".tiff")) return "image/tiff";
  return "image/jpeg";
}

function money(value, currency = "NGN") {
  if (value === null || value === undefined || value === "") {
    return "No total";
  }
  try {
    return new Intl.NumberFormat(undefined, { style: "currency", currency }).format(Number(value));
  } catch (_) {
    return `${currency} ${value}`;
  }
}

function numberValue(value) {
  return value === null || value === undefined ? "" : String(value);
}

function trimSeconds(value) {
  return value ? value.slice(0, 5) : "";
}

function formatDateTime(value) {
  if (!value) return "";
  return new Date(value).toLocaleString();
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function escapeAttr(value) {
  return escapeHtml(value).replaceAll("'", "&#39;");
}
