PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>CDI-Adapter · scanned records → ABDM FHIR</title>
<style>
  :root { --bg:#0f1420; --card:#161d2e; --line:#28324a; --fg:#e6ebf5; --mut:#93a1bd;
          --accent:#5b9dff; --ok:#3fd08a; --warn:#f5b642; --err:#ff6b6b; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--fg);
         font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; }
  header { padding:20px 24px; border-bottom:1px solid var(--line); }
  h1 { margin:0; font-size:18px; font-weight:650; }
  .sub { color:var(--mut); font-size:12.5px; margin-top:4px; }
  main { max-width:960px; margin:0 auto; padding:24px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:12px;
          padding:18px 20px; margin-bottom:18px; }
  label { display:block; font-size:12px; color:var(--mut); margin:10px 0 4px; text-transform:uppercase; letter-spacing:.04em; }
  input[type=text], select { width:100%; padding:9px 11px; background:#0d1322; color:var(--fg);
          border:1px solid var(--line); border-radius:8px; font-size:14px; }
  input[type=file] { width:100%; padding:10px; background:#0d1322; border:1px dashed var(--line);
          border-radius:8px; color:var(--mut); }
  .row { display:flex; gap:14px; flex-wrap:wrap; }
  .row > div { flex:1; min-width:200px; }
  button { margin-top:16px; padding:10px 18px; background:var(--accent); color:#04101f; border:0;
          border-radius:8px; font-weight:650; font-size:14px; cursor:pointer; }
  button:disabled { opacity:.5; cursor:default; }
  button.ghost { background:transparent; color:var(--accent); border:1px solid var(--line); font-weight:500; }
  .doc { display:flex; align-items:center; gap:10px; padding:8px 0; border-bottom:1px solid var(--line); font-size:13px; }
  .doc:last-child { border-bottom:0; }
  .pill { font-size:11px; padding:2px 8px; border-radius:20px; background:#0d1322; border:1px solid var(--line); color:var(--mut); }
  .pill.ok { color:var(--ok); border-color:#1f5f43; }
  .pill.err { color:var(--err); border-color:#5f2222; }
  .pill.run { color:var(--accent); border-color:#28497f; }
  .spin { width:12px; height:12px; border:2px solid var(--line); border-top-color:var(--accent);
          border-radius:50%; animation:s .8s linear infinite; display:inline-block; }
  @keyframes s { to { transform:rotate(360deg); } }
  pre { background:#0b1020; border:1px solid var(--line); border-radius:8px; padding:12px;
        overflow:auto; max-height:460px; font-size:12px; line-height:1.45; }
  details { border:1px solid var(--line); border-radius:10px; margin-top:10px; background:#121a2b; }
  summary { cursor:pointer; padding:12px 14px; font-weight:600; display:flex; gap:10px; align-items:center; }
  .body { padding:0 14px 14px; }
  table { width:100%; border-collapse:collapse; font-size:12px; margin:6px 0 12px; }
  th, td { text-align:left; padding:5px 8px; border-bottom:1px solid var(--line); }
  th { color:var(--mut); font-weight:600; }
  code.k { color:var(--accent); }
  .muted { color:var(--mut); }
  .err-txt { color:var(--err); }
  a { color:var(--accent); }
</style>
</head>
<body>
<header>
  <h1>CDI-Adapter — scanned records → ABDM FHIR</h1>
  <div class="sub">Upload a patient's scanned documents (prescription, lab report, discharge summary, vitals…).
  The pipeline classifies each, runs OCR, extracts clinical facts with a vision-language model,
  binds SNOMED CT / LOINC codes, and emits ABDM (NRCeS) FHIR R4 record bundles.</div>
</header>
<main>
  <div class="card" id="form-card">
    <div class="row">
      <div><label>Patient name</label><input type="text" id="pname" placeholder="Anjali Das" value="Anjali Das"/></div>
      <div><label>ABHA number (optional)</label><input type="text" id="abha" placeholder="14-XXXX-XXXX-XXXX"/></div>
      <div><label>Sex</label>
        <select id="gender"><option value="">—</option><option>F</option><option>M</option><option>O</option></select>
      </div>
    </div>
    <label>Documents (up to 10 — PDF / JPG / PNG / TIFF)</label>
    <input type="file" id="files" multiple accept=".pdf,.png,.jpg,.jpeg,.tif,.tiff,image/*,application/pdf"/>
    <button id="go">Process &amp; build FHIR</button>
    <span id="hint" class="muted" style="margin-left:12px"></span>
  </div>

  <div class="card" id="progress-card" style="display:none">
    <strong>Pipeline</strong>
    <div id="docs"></div>
  </div>

  <div class="card" id="result-card" style="display:none">
    <div style="display:flex;justify-content:space-between;align-items:center">
      <strong>ABDM FHIR bundles</strong>
      <div>
        <button class="ghost" id="dl">Download all JSON</button>
        <button class="ghost" id="copyall">Copy combined</button>
      </div>
    </div>
    <div id="bundles"></div>
  </div>
</main>

<script>
const $ = s => document.querySelector(s);
const stepList = ["ingest","classify","ocr","extract","terminology","validate"];
let JOB = null, TIMER = null, RESULT = null;

$("#go").onclick = async () => {
  const files = $("#files").files;
  if (!files.length) { $("#hint").textContent = "pick at least one file"; return; }
  const fd = new FormData();
  fd.append("patient_name", $("#pname").value || "Unknown");
  fd.append("abha", $("#abha").value || "");
  fd.append("gender", $("#gender").value || "");
  for (const f of files) fd.append("files", f);
  $("#go").disabled = true; $("#hint").textContent = "uploading…";
  const r = await fetch("api/jobs", { method:"POST", body:fd });
  if (!r.ok) { $("#hint").textContent = "error: " + (await r.text()); $("#go").disabled = false; return; }
  JOB = (await r.json()).job_id;
  $("#hint").textContent = "job " + JOB;
  $("#progress-card").style.display = "block";
  poll();
};

function poll() {
  clearTimeout(TIMER);
  TIMER = setTimeout(async () => {
    const r = await fetch("api/jobs/" + JOB);
    const j = await r.json();
    render(j);
    if (j.state === "done" || j.state === "error") { loadResult(); }
    else poll();
  }, 1500);
}

function render(j) {
  $("#docs").innerHTML = j.documents.map(d => {
    const done = stepList.indexOf(d.step) < 0 && d.status !== "queued";
    let cls = "run", label = d.step || d.status;
    if (d.status === "done") { cls = "ok"; label = "done"; }
    if (d.status === "error") { cls = "err"; label = "error"; }
    if (d.status === "queued") { cls = ""; label = "queued"; }
    return `<div class="doc">
      ${d.status==="running" ? '<span class="spin"></span>' : d.status==="done" ? '✔' : d.status==="error" ? '✖' : '•'}
      <span style="flex:1">${escapeHtml(d.filename)}</span>
      ${d.doc_type ? `<span class="pill">${d.doc_type}</span>` : ""}
      ${d.facts ? `<span class="pill">${d.facts} facts</span>` : ""}
      ${d.accepted ? `<span class="pill ok">${d.accepted} accepted</span>` : ""}
      ${d.in_review ? `<span class="pill warn">${d.in_review} review</span>` : ""}
      <span class="pill ${cls}">${label}</span>
    </div>` + (d.error ? `<div class="err-txt" style="font-size:12px;padding:2px 0 8px">${escapeHtml(d.error)}</div>` : "");
  }).join("");
}

async function loadResult() {
  const r = await fetch("api/jobs/" + JOB + "/fhir");
  if (!r.ok) { return; }
  RESULT = await r.json();
  $("#result-card").style.display = "block";
  const p = RESULT.patient;
  const rev = RESULT.needs_review || 0;
  $("#bundles").innerHTML =
    `<p class="muted">Patient <b>${escapeHtml(p.name||"—")}</b>${p.abha_number ? " · ABHA "+escapeHtml(p.abha_number):""}
     · ${RESULT.artifact_count} bundle(s): <span class="pill ok">${RESULT.ready_to_share||0} ready to share</span>
     ${rev?`<span class="pill warn">${rev} need review</span>`:""}
     · IG ${escapeHtml(RESULT.ig_package||"nrces.fhir.r4.ndhm")}</p>` +
    (rev ? `<p><a href="review" target="_blank">→ open the review queue</a> to adjudicate held facts, then
       <button class="ghost" id="recheck">re-check</button></p>` : "") +
    RESULT.bundles.map((b,i) => {
      const errs = (b.issues||[]).filter(x=>x.severity==="error");
      const held = b.held_facts||[];
      const ss = b.bundle_status==="ready_to_share" ? "ok" : "warn";
      const facts = factsTable(b.bundle);
      return `<details ${i===0?"open":""}>
        <summary>${escapeHtml(b.filename)} → <code class="k">${b.artifact_type}</code>
          <span class="pill ${ss}">${b.bundle_status}</span>
          <span class="pill">${b.asserted_facts} asserted</span>
          ${held.length?`<span class="pill warn">${held.length} held</span>`:""}
          ${errs.length?`<span class="pill err">${errs.length} errors</span>`:""}
          <span class="pill">${b.bundle.entry.length} resources</span></summary>
        <div class="body">
          ${held.length?`<div class="muted" style="font-size:12px;margin:6px 0">Held for review:
             ${held.map(h=>escapeHtml(h.fact_type+" '"+h.text+"'")).join(" · ")}</div>`:""}
          ${facts}
          <button class="ghost" onclick='copyText(${JSON.stringify(JSON.stringify(b.bundle))})'>Copy this bundle</button>
          <pre>${escapeHtml(JSON.stringify(b.bundle, null, 2))}</pre>
        </div></details>`;
    }).join("");
  const rc = $("#recheck"); if (rc) rc.onclick = loadResult;
}

function factsTable(bundle) {
  const rows = [];
  for (const e of bundle.entry) {
    const r = e.resource;
    if (["Condition","Observation","MedicationRequest","Procedure","AllergyIntolerance","DiagnosticReport"].includes(r.resourceType)) {
      const cc = r.code || r.medicationCodeableConcept || {};
      const cod = (cc.coding||[])[0] || {};
      let val = "";
      if (r.valueQuantity) val = r.valueQuantity.value + " " + (r.valueQuantity.unit||"");
      else if (r.valueString) val = r.valueString;
      else if (r.conclusion) val = r.conclusion;
      rows.push(`<tr><td>${r.resourceType}</td><td>${escapeHtml(cc.text||cod.display||"")}</td>
        <td>${cod.system?`<code class="k">${shortSys(cod.system)}</code> ${cod.code}`:'<span class="muted">local</span>'}</td>
        <td>${escapeHtml(String(val))}</td></tr>`);
    }
  }
  if (!rows.length) return '<p class="muted">no coded clinical facts in this bundle</p>';
  return `<table><tr><th>Resource</th><th>Concept</th><th>Code</th><th>Value</th></tr>${rows.join("")}</table>`;
}
const shortSys = s => s.includes("snomed") ? "SNOMED" : s.includes("loinc") ? "LOINC" : s.includes("icd") ? "ICD-10" : s;

$("#dl").onclick = () => window.location = "api/jobs/" + JOB + "/fhir/download";
$("#copyall").onclick = () => copyText(JSON.stringify(RESULT, null, 2));
function copyText(t){ navigator.clipboard.writeText(t); }
function escapeHtml(s){ return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
</script>
</body>
</html>
"""
