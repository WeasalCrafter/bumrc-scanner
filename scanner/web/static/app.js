// BUMRC scanner UI. Plain JS, no build step.
"use strict";

const $ = (id) => document.getElementById(id);

// Field layout for the review form: [key, label, kind, wide?]
const FIELDS = [
    ["name", "Name", "text", true],
    ["mpn", "MPN", "text"],
    ["manufacturer", "Manufacturer", "text"],
    ["distributor", "Distributor", "distributors"],
    ["distributor_pn", "Distributor part #", "text"],
    ["original_qty", "Quantity", "number"],
    ["location", "Location", "text"],
    ["type", "Type", "types"],
    ["mount", "Mount", "mounts"],
    ["package", "Package", "text"],
    ["value_text", "Value", "text"],
    ["tolerance", "Tolerance", "text"],
    ["voltage_rating", "Voltage rating", "text"],
    ["power_rating", "Power rating", "text"],
    ["dielectric", "Dielectric", "text"],
    ["lot", "Lot", "text"],
    ["date_code", "Date code", "text"],
    ["description", "Description", "text", true],
    ["digikey_url", "DigiKey link", "text", true],
    ["datasheet_url", "Datasheet link", "text", true],
];
const REQUIRED = ["distributor", "mpn", "original_qty"];
const CAPTURE_FIELDS = ["distributor", "distributor_pn", "mpn", "manufacturer", "original_qty", "lot", "date_code"];

let options = { distributors: [], types: [], mounts: [] };
let scanFields = {};     // everything the server sent back (incl. hidden fields like label_hash)
let itemId = null;

// ------------------------------------------------------------------ helpers

async function api(method, url, body) {
    const opts = { method, headers: {} };
    if (body !== undefined) {
        opts.headers["Content-Type"] = "application/json";
        opts.body = JSON.stringify(body);
    }
    let r;
    try {
        r = await fetch(url, opts);
    } catch (e) {
        throw new Error("lost connection to the scanner app");
    }
    const data = await r.json().catch(() => ({}));
    if (!r.ok) {
        const d = data.detail;
        const err = new Error(typeof d === "string" ? d : (d && d.message) || `error ${r.status}`);
        err.detail = d;
        throw err;
    }
    return data;
}

function msg(container, text, kind = "err") {
    const el = typeof container === "string" ? $(container) : container;
    el.replaceChildren();
    if (!text) return;
    const div = document.createElement("div");
    div.className = `msg ${kind}`;
    div.textContent = text;
    el.append(div);
}

function formatId(n) { return "BUMRC-" + String(n).padStart(6, "0"); }   // same as on the label

function busy(button, on, text) {
    if (on) { button.dataset.label = button.textContent; button.textContent = text || "Working…"; }
    else if (button.dataset.label) button.textContent = button.dataset.label;
    button.disabled = on;
}

// ------------------------------------------------------------------ left panel

// What the scan tab's left panel last showed (snapshot / label), restored when you come back to it.
let activeTab = "scan";
let scanView = ["none", "", ""];

function showView(kind, src, caption = "") {
    if (activeTab === "scan" && !String(src).startsWith("/stream.mjpg")) scanView = [kind, src, caption];
    const img = $("view-img");
    img.className = kind === "label" ? "label" : "";
    if (kind === "none") {
        img.hidden = true;
        img.removeAttribute("src");
        $("view-empty").hidden = false;
        $("view-empty").textContent = src || "Camera is off";
    } else {
        img.src = src;
        img.hidden = false;
        $("view-empty").hidden = true;
    }
    $("view-caption").textContent = caption;
}

function showStream() { showView("img", "/stream.mjpg?t=" + Date.now(), "Live camera"); }

// ------------------------------------------------------------------ status chips

async function refreshStatus() {
    let s;
    try { s = await api("GET", "/api/status"); } catch { return; }
    options = s.options;
    const chips = [
        ["Camera", s.camera.error ? "bad" : (s.camera.open ? "" : "off"),
            s.camera.error || (s.camera.test_images ? "test images" : (s.camera.open ? "open" : "closed"))],
        ["Printer", s.printer ? "bad" : "", s.printer || "ready"],
        ["DigiKey", s.digikey ? "" : "bad", s.digikey ? "ready" : "no API keys"],
        ["Server", s.server ? "" : "bad", s.server ? "configured" : "not configured"],
    ];
    $("chips").replaceChildren(...chips.map(([name, cls, title]) => {
        const c = document.createElement("span");
        c.className = "chip " + cls;
        c.textContent = name;
        c.title = title;
        return c;
    }));
    $("btn-next-image").hidden = !s.camera.test_images;
    const set = s.camera.settings && s.camera.settings.controls;
    $("cam-info").textContent = `${s.camera.source}` +
        (s.camera.size ? ` · ${s.camera.size[0]}×${s.camera.size[1]}` : "") +
        (set ? ` · saved settings: ${Object.entries(set).map(([k, v]) => `${k} ${v}`).join(", ")}` : " · no saved settings yet");
    return s;
}

// ------------------------------------------------------------------ scan tab

function showStep(name) {
    for (const s of ["idle", "scanning", "review", "label", "done"]) $("step-" + s).hidden = s !== name;
}

function select(kind, value) {
    const sel = document.createElement("select");
    sel.append(new Option("—", ""));
    for (const o of options[kind] || []) sel.append(new Option(o, o));
    if (value && ![...sel.options].some((o) => o.value === value)) sel.append(new Option(value, value));
    sel.value = value || "";
    return sel;
}

function renderFields(fields, missing) {
    const form = $("fields");
    form.replaceChildren();
    for (const [key, label, kind, wide] of FIELDS) {
        const wrap = document.createElement("label");
        wrap.textContent = label + (REQUIRED.includes(key) ? " *" : "");
        if (wide) wrap.classList.add("wide");
        if (missing.includes(key)) wrap.classList.add("missing");
        let input;
        if (["distributors", "types", "mounts"].includes(kind)) input = select(kind, fields[key]);
        else {
            input = document.createElement("input");
            input.type = kind === "number" ? "number" : "text";
            if (kind === "number") input.min = "0";
            input.value = fields[key] ?? "";
        }
        input.name = key;
        input.addEventListener("input", () => wrap.classList.toggle("missing",
            REQUIRED.includes(key) && !input.value.trim()));
        wrap.append(input);
        form.append(wrap);
    }
}

function readFields() {
    const out = { ...scanFields };
    for (const el of $("fields").elements) {
        if (!el.name) continue;
        const v = el.value.trim();
        out[el.name] = v === "" ? null : (el.type === "number" ? Number(v) : v);
    }
    out.qty_remaining = out.original_qty;     // a new bag is full
    return out;
}

function renderCodes(codes) {
    $("code-count").textContent = codes.length;
    $("code-list").replaceChildren(...codes.map((c) => {
        const li = document.createElement("li");
        const code = document.createElement("code");
        // show the invisible separators so the raw text is readable
        code.textContent = c.text.replace(/\x1d/g, "‹GS›").replace(/\x1e/g, "‹RS›").replace(/\x04/g, "‹EOT›");
        li.append(`${c.format}${c.vertical ? " (sideways)" : ""}, seen ${c.hits}×: `, code);
        return li;
    }));
}

function showReview(data) {
    scanFields = data.fields;
    renderFields(data.fields, data.missing);
    if (data.codes) renderCodes(data.codes);
    const notes = [];
    if (data.codes && data.codes.length === 0) notes.push(["No codes found. Check focus (Camera tab) and rescan, or type the details in.", "err"]);
    for (const w of data.camera_warnings || []) notes.push(["Camera settings: " + w, "warn"]);
    if (data.lookup_problem) notes.push(["DigiKey: " + data.lookup_problem, "warn"]);
    if (data.missing.length) notes.push(["Missing: " + data.missing.join(", "), "warn"]);
    $("review-msg").replaceChildren();
    for (const [t, k] of notes) {
        const d = document.createElement("div");
        d.className = "msg " + k;
        d.textContent = t;
        $("review-msg").append(d);
    }
    if ((data.manufacturer_choices || []).length) $("review-msg").append(manufacturerPicker(data.manufacturer_choices));
    showStep("review");
}

// Several companies make this MPN: one button per maker DigiKey lists.
function manufacturerPicker(makers) {
    const box = document.createElement("div");
    box.className = "msg warn picker";
    box.append("Which manufacturer is on the bag? ");
    for (const name of makers) {
        const b = document.createElement("button");
        b.type = "button";
        b.textContent = name;
        b.addEventListener("click", () => {
            $("fields").elements.manufacturer.value = name;
            doLookup();
        });
        box.append(b);
    }
    return box;
}

function lockTabs(on) { for (const b of document.querySelectorAll(".tabs button")) b.disabled = on; }

async function doScan() {
    showStep("scanning");
    lockTabs(true);
    // the stream starts once the server has the camera open
    const startStream = setTimeout(showStream, 400);
    try {
        const data = await api("POST", "/api/scan");
        clearTimeout(startStream);
        showView("img", "/api/scan/snapshot.jpg?t=" + Date.now(),
            `Best frame · ${data.frames} frames in ${data.seconds}s`);
        showReview(data);
    } catch (e) {
        clearTimeout(startStream);
        showView("none");
        showStep("idle");
        alert("Scan failed: " + e.message);
    } finally { lockTabs(false); }
    refreshStatus();
}

async function doLookup() {
    const b = $("btn-lookup");
    busy(b, true, "Looking up…");
    try {
        const data = await api("POST", "/api/lookup", { fields: readFields() });
        showReview({ ...data, codes: null });
        if (!data.lookup_problem) msg("review-msg", "Filled in from DigiKey.", "ok");
    } catch (e) {
        msg("review-msg", e.message);
    } finally { busy(b, false); }
}

async function doUpload() {
    const member = $("member").value.trim();
    if (!member) {
        msg("review-msg", "Enter your name at the top. The server records who added each bag.");
        $("member").focus();
        return;
    }
    const fields = readFields();
    const b = $("btn-upload");
    busy(b, true, "Uploading…");
    try {
        const r = await api("POST", "/api/upload", { fields, member });
        itemId = r.id;
        $("item-id").textContent = formatId(itemId);
        $("label-name").value = fields.name || fields.mpn || "";
        $("btn-finish").hidden = true;
        $("btn-print").textContent = "Print label";
        msg("label-msg", "");
        showStep("label");
        updateLabelPreview();
    } catch (e) {
        msg("review-msg", "Upload failed: " + e.message);
    } finally { busy(b, false); }
}

let previewTimer = null;
function updateLabelPreview() {
    clearTimeout(previewTimer);
    previewTimer = setTimeout(() => {
        const q = new URLSearchParams({ item_id: itemId, name: $("label-name").value });
        showView("label", "/api/label.png?" + q, "Label preview (62 mm red/black)");
    }, 250);
}

async function doPrint() {
    const b = $("btn-print");
    busy(b, true, "Printing…");
    try {
        await api("POST", "/api/print", { item_id: itemId, name: $("label-name").value });
        msg("label-msg", "Sent to the printer. If it didn't come out, press Print again.", "ok");
        b.dataset.label = "Print again";
        $("btn-finish").hidden = false;
    } catch (e) {
        const saved = e.detail && e.detail.saved ? ` The label image was saved to ${e.detail.saved}.` : "";
        msg("label-msg", e.message + saved);
        $("btn-finish").hidden = false;
    } finally { busy(b, false); }
}

function doFinish() {
    $("done-msg").textContent = `Bag ${formatId(itemId)} is in the inventory. You can move on to the next one.`;
    showView("none", "Ready for the next bag");
    showStep("done");
}

// ------------------------------------------------------------------ camera tab

async function openCamera() {
    try {
        const r = await api("POST", "/api/camera/open");
        if (r.warnings && r.warnings.length) msg("settings-msg", r.warnings.join("; "), "warn");
        showStream();
        await loadControls();
    } catch (e) { alert(e.message); }
    refreshStatus();
}

async function closeCamera(clearView = true) {
    await api("POST", "/api/camera/close").catch(() => {});
    if (clearView) showView("none");
    $("controls").innerHTML = '<p class="muted">Open the camera to see its controls.</p>';
    refreshStatus();
}

async function loadControls() {
    const ctrls = await api("GET", "/api/camera/controls");
    const box = $("controls");
    box.replaceChildren();
    const names = Object.keys(ctrls);
    if (!names.length) { box.innerHTML = '<p class="muted">This camera reports no adjustable controls.</p>'; return; }
    for (const name of names) {
        const c = ctrls[name];
        const label = document.createElement("label");
        const title = document.createElement("span");
        title.textContent = name;
        const input = document.createElement("input");
        const out = document.createElement("span");
        if (c.kind === "bool") {
            input.type = "checkbox";
            input.checked = !!c.value;
        } else {
            input.type = "range";
            input.min = c.min; input.max = c.max; input.step = c.step || 1; input.value = c.value;
            out.textContent = c.value;
        }
        input.addEventListener("input", () => { out.textContent = input.type === "checkbox" ? "" : input.value; });
        input.addEventListener("change", async () => {
            const value = input.type === "checkbox" ? (input.checked ? 1 : 0) : Number(input.value);
            try {
                await api("POST", "/api/camera/controls", { name, value });
                if (name === "autofocus") loadControls();
            } catch (e) { msg("settings-msg", e.message); }
        });
        label.append(title, input, out);
        box.append(label);
    }
}

async function saveSettings() {
    try {
        const r = await api("POST", "/api/camera/settings");
        msg("settings-msg", "Saved: " + Object.entries(r.controls).map(([k, v]) => `${k} ${v}`).join(", "), "ok");
    } catch (e) { msg("settings-msg", e.message); }
    refreshStatus();
}

let calibPoll = null;
async function startCalibration() {
    try {
        await api("POST", "/api/calibrate");
    } catch (e) { $("calib-msg").textContent = e.message; return; }
    showStream();
    $("calib-result").replaceChildren();
    $("calib-progress").hidden = false;
    $("btn-calibrate").disabled = true;
    $("btn-calibrate-cancel").hidden = false;
    clearInterval(calibPoll);
    calibPoll = setInterval(pollCalibration, 700);
}

async function pollCalibration() {
    const s = await api("GET", "/api/calibrate").catch(() => null);
    if (!s) return;
    $("calib-progress").firstElementChild.style.width = Math.round(s.progress * 100) + "%";
    $("calib-msg").textContent = s.error ? "Stopped: " + s.error : s.message;
    if (s.running) return;
    clearInterval(calibPoll);
    $("btn-calibrate").disabled = false;
    $("btn-calibrate-cancel").hidden = true;
    if (s.result) showCalibrationResult(s.result);
    loadControls().catch(() => {});
}

function showCalibrationResult(r) {
    const box = $("calib-result");
    box.replaceChildren();
    const best = Object.entries(r.best).map(([k, v]) => `${k} ${v}`).join(", ") || "nothing to change";
    msg(box, `Best: ${best} (${r.best_codes} codes). Press Save settings to keep it.`, r.best_codes ? "ok" : "warn");
    for (const n of r.notes) { const p = document.createElement("p"); p.className = "muted"; p.textContent = n; box.append(p); }
    const t = document.createElement("table");
    t.innerHTML = "<tr><th>Stage</th><th>Focus</th><th>Sharpness</th><th>Codes</th><th>Image sharpness</th></tr>";
    for (const p of r.points.filter(p => p.codes !== null)) {    // sharpness-only sweep steps are left out
        const tr = t.insertRow();
        if (p.focus === r.best.focus && p.sharpness === r.best.sharpness) tr.className = "best";
        for (const v of [p.stage, p.focus ?? "—", p.sharpness ?? "—", p.codes, Math.round(p.focus_measure)])
            tr.insertCell().textContent = v;
    }
    box.append(t);
}

async function saveCapture() {
    const desc = $("cap-desc").value.trim();
    if (!desc) { msg("cap-confirm", "Add a short description first."); return; }
    const b = $("btn-capture");
    busy(b, true, "Saving…");
    try {
        const r = await api("POST", "/api/captures", { description: desc, distributor: $("cap-dist").value || null });
        showCaptureConfirm(r.name, r.decoded);
    } catch (e) { msg("cap-confirm", e.message); }
    finally { busy(b, false); }
}

function showCaptureConfirm(name, decoded) {
    const box = $("cap-confirm");
    box.replaceChildren();
    msg(box, `Saved ${name}. Check the values below (fix any the decoder got wrong) and confirm.`, "ok");
    const form = document.createElement("form");
    form.className = "fields";
    for (const key of CAPTURE_FIELDS) {
        const l = document.createElement("label");
        l.textContent = key;
        const i = key === "distributor" ? select("distributors", decoded[key] || $("cap-dist").value) : document.createElement("input");
        if (key !== "distributor") i.value = decoded[key] ?? "";
        i.name = key;
        l.append(i);
        form.append(l);
    }
    const btn = document.createElement("button");
    btn.type = "submit";
    btn.className = "button wide";
    btn.textContent = "Confirm values";
    form.append(btn);
    form.addEventListener("submit", async (ev) => {
        ev.preventDefault();
        const values = Object.fromEntries([...form.elements].filter((e) => e.name).map((e) => [e.name, e.value.trim()]));
        try {
            const spec = await api("POST", `/api/captures/${encodeURIComponent(name)}`, { values });
            const todo = Object.keys(spec.expected_todo || {});
            msg(box, todo.length ? `Saved. Decoder still needs work on: ${todo.join(", ")}` : "Saved. The decoder got everything right.",
                todo.length ? "warn" : "ok");
        } catch (e) { msg(box, e.message); }
    });
    box.append(form);
}

// ------------------------------------------------------------------ tabs & wiring

async function showTab(name) {
    if (name === activeTab) return;
    for (const b of document.querySelectorAll(".tabs button")) b.classList.toggle("active", b.dataset.tab === name);
    $("tab-scan").hidden = name !== "scan";
    $("tab-calibrate").hidden = name !== "calibrate";
    activeTab = name;
    // switching tabs always closes the camera; each tab starts from a clean state
    clearInterval(calibPoll);
    await api("DELETE", "/api/calibrate").catch(() => {});
    await closeCamera(false);
    if (name === "scan") showView(...scanView);
    else {
        showView("none");
        $("btn-calibrate").disabled = false;
        $("btn-calibrate-cancel").hidden = true;
        $("calib-progress").hidden = true;
    }
}

function init() {
    try { $("member").value = localStorage.getItem("member") || ""; } catch {}
    $("member").addEventListener("change", () => { try { localStorage.setItem("member", $("member").value.trim()); } catch {} });

    for (const b of document.querySelectorAll(".tabs button")) b.addEventListener("click", () => showTab(b.dataset.tab));
    $("btn-scan").addEventListener("click", doScan);
    $("btn-rescan").addEventListener("click", () => { showStep("idle"); showView("none"); });
    $("btn-lookup").addEventListener("click", doLookup);
    $("btn-upload").addEventListener("click", doUpload);
    $("label-name").addEventListener("input", updateLabelPreview);
    $("btn-print").addEventListener("click", doPrint);
    $("btn-finish").addEventListener("click", doFinish);
    $("btn-next").addEventListener("click", () => { showStep("idle"); showView("none"); });

    $("btn-cam-open").addEventListener("click", openCamera);
    $("btn-cam-close").addEventListener("click", () => closeCamera());
    $("btn-next-image").addEventListener("click", async () => { await api("POST", "/api/camera/next-image").catch(() => {}); });
    $("btn-calibrate").addEventListener("click", startCalibration);
    $("btn-calibrate-cancel").addEventListener("click", () => api("DELETE", "/api/calibrate"));
    $("btn-save-settings").addEventListener("click", saveSettings);
    $("btn-capture").addEventListener("click", saveCapture);

    refreshStatus().then(() => {
        const sel = $("cap-dist");
        sel.replaceWith(Object.assign(select("distributors", ""), { id: "cap-dist" }));
    });
    setInterval(refreshStatus, 10000);
}

init();
