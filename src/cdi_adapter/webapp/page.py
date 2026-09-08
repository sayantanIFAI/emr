from .theme import BRAND_CSS, HEADER_HTML, UPLOAD_ICON

_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>CareFlow Polyclinic — records to FHIR</title>
<style>%%CSS%%</style>
</head>
<body>
%%HEADER%%
<main class="cf-main">
  <div class="card" id="form-card">
    <h2>Upload patient documents</h2>
    <p class="hint">Prescriptions, lab reports, discharge summaries, vitals sheets…
      The patient's <b>name, sex and date of birth are read from the documents</b> and a
      CareFlow patient ID is generated automatically.</p>

    <div class="dropzone" id="dz" tabindex="0" role="button" aria-label="Choose or drop documents">
      %%ICON%%
      <div class="dz-title">Drop files here or click to choose</div>
      <div class="dz-sub">PDF, JPG, PNG or TIFF · up to 10 documents</div>
      <div class="dz-files" id="dzfiles"></div>
    </div>
    <input type="file" id="files" multiple hidden
      accept=".pdf,.png,.jpg,.jpeg,.tif,.tiff,image/*,application/pdf"/>

    <label class="fld" for="abha">ABHA number (optional — helps match an existing patient)</label>
    <input type="text" id="abha" placeholder="14-XXXX-XXXX-XXXX" autocomplete="off"/>

    <div style="margin-top:16px;display:flex;gap:10px;align-items:center">
      <button class="btn btn-primary" id="go">Process documents</button>
      <span id="hint" class="muted"></span>
    </div>
  </div>

  <div class="card" id="progress-card" hidden>
    <h2>Pipeline</h2>
    <p class="hint">ingest → classify → OCR → extract → terminology → validate. Uncertain
      facts are held for review, never auto-trusted.</p>
    <div id="docs"></div>
  </div>

  <div class="card" id="patient-card" hidden>
    <h2>Patient (detected from documents)</h2>
    <div class="patient" id="patient"></div>
  </div>

  <div class="card" id="result-card" hidden>
    <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">
      <h2 style="margin:0">ABDM FHIR record bundles</h2>
      <div>
        <button class="btn btn-ghost btn-sm" id="dl">Download all JSON</button>
        <button class="btn btn-ghost btn-sm" id="copyall">Copy combined</button>
      </div>
    </div>
    <div id="bundles"></div>
  </div>
</main>

<script>
const $ = s => document.querySelector(s);
let JOB=null, TIMER=null, RESULT=null;

const dz=$("#dz"), fileInput=$("#files");
dz.onclick=()=>fileInput.click();
dz.onkeydown=e=>{ if(e.key==="Enter"||e.key===" "){ e.preventDefault(); fileInput.click(); } };
dz.ondragover=e=>{ e.preventDefault(); dz.classList.add("drag"); };
dz.ondragleave=()=>dz.classList.remove("drag");
dz.ondrop=e=>{ e.preventDefault(); dz.classList.remove("drag"); fileInput.files=e.dataTransfer.files; showFiles(); };
fileInput.onchange=showFiles;
function showFiles(){
  const n=fileInput.files.length;
  $("#dzfiles").textContent = n ? [...fileInput.files].map(f=>f.name).join("  ·  ") : "";
}

$("#go").onclick=async()=>{
  const files=fileInput.files;
  if(!files.length){ $("#hint").textContent="pick at least one file"; return; }
  const fd=new FormData();
  fd.append("abha", $("#abha").value||"");
  for(const f of files) fd.append("files", f);
  $("#go").disabled=true; $("#hint").textContent="uploading…";
  const r=await fetch("api/jobs",{method:"POST",body:fd});
  if(!r.ok){ $("#hint").textContent="error: "+(await r.text()); $("#go").disabled=false; return; }
  JOB=(await r.json()).job_id;
  $("#hint").textContent="job "+JOB;
  $("#progress-card").hidden=false;
  poll();
};

function poll(){
  clearTimeout(TIMER);
  TIMER=setTimeout(async()=>{
    const j=await (await fetch("api/jobs/"+JOB)).json();
    render(j);
    if(j.state==="done"||j.state==="error"){ loadResult(); } else poll();
  }, 1500);
}

function render(j){
  if(j.patient){ $("#patient-card").hidden=false; renderPatient(j.patient); }
  $("#docs").innerHTML=j.documents.map(d=>{
    let cls="", label=d.step||d.status;
    if(d.status==="done"){ cls="ok"; label="done"; }
    if(d.status==="error"){ cls="err"; label="error"; }
    if(d.status==="queued"){ label="queued"; }
    const ico = d.status==="running" ? '<span class="spin"></span>'
      : d.status==="done" ? '<span class="ico ok">✔</span>'
      : d.status==="error" ? '<span class="ico err">✕</span>' : '<span class="ico">•</span>';
    return '<div class="prow">'+ico
      +'<span class="fn">'+esc(d.filename)+'</span>'
      +(d.doc_type?'<span class="pill blue">'+esc(d.doc_type)+'</span>':'')
      +(d.facts?'<span class="pill">'+d.facts+' facts</span>':'')
      +(d.accepted?'<span class="pill ok">'+d.accepted+' accepted</span>':'')
      +(d.in_review?'<span class="pill warn">'+d.in_review+' review</span>':'')
      +'<span class="pill '+cls+'">'+esc(label)+'</span></div>'
      +(d.error?'<div class="err-txt" style="font-size:12px;padding:2px 0 8px">'+esc(d.error)+'</div>':'');
  }).join("");
}

function renderPatient(p){
  const nm=p.name||"Unknown";
  const initials=nm.split(/\s+/).map(x=>x[0]||"").slice(0,2).join("").toUpperCase()||"–";
  const sex={M:"Male",F:"Female",O:"Other"}[p.sex]||"—";
  const prov=p.provisional?' <span class="pill warn">provisional</span>':'';
  const conf=(p.identity_confidence!=null)?(' · identity '+(p.identity_confidence*100).toFixed(0)+'%'):'';
  const dob = p.birth_date ? (' · DOB '+esc(p.birth_date)) : (p.age_years!=null ? (' · age '+p.age_years) : '');
  const abha = p.abha_number ? (' · ABHA '+esc(p.abha_number)) : '';
  $("#patient").innerHTML='<div class="avatar">'+esc(initials)+'</div>'
    +'<div><div class="pid tab-nums">'+esc(p.mpi_id||"CFP-…")+'</div>'
    +'<div class="meta"><b>'+esc(nm)+'</b> · '+sex+dob+abha+conf+prov+'</div></div>';
}

async function loadResult(){
  const r=await fetch("api/jobs/"+JOB+"/fhir");
  if(!r.ok) return;
  RESULT=await r.json();
  if(RESULT.patient) renderPatient(RESULT.patient);
  $("#result-card").hidden=false;
  const rev=RESULT.needs_review||0;
  let html='<p class="muted" style="margin:8px 0 4px">'+(RESULT.artifact_count||0)+' bundle(s): '
    +'<span class="pill ok">'+(RESULT.ready_to_share||0)+' ready to share</span> '
    +(rev?'<span class="pill warn">'+rev+' need review</span> ':'')
    +'· IG '+esc(RESULT.ig_package||"nrces.fhir.r4.ndhm")+'</p>';
  if(rev) html+='<p style="margin:6px 0 10px"><a href="review" target="_blank">→ open the review queue</a>'
    +' to adjudicate held facts, then <button class="btn btn-ghost btn-sm" id="recheck">re-check</button></p>';
  html+=(RESULT.bundles||[]).map((b,i)=>{
    const errs=(b.issues||[]).filter(x=>x.severity==="error");
    const held=b.held_facts||[];
    const ss=b.bundle_status==="ready_to_share"?"ok":"warn";
    return '<details class="bundle" '+(i===0?"open":"")+'>'
      +'<summary>'+esc(b.filename)+' → <code class="k">'+esc(b.artifact_type)+'</code> '
      +'<span class="pill '+ss+'">'+esc(b.bundle_status)+'</span> '
      +'<span class="pill">'+b.asserted_facts+' asserted</span> '
      +(held.length?'<span class="pill warn">'+held.length+' held</span> ':'')
      +(errs.length?'<span class="pill err">'+errs.length+' errors</span>':'')
      +'</summary><div class="body">'
      +(held.length?'<div class="muted" style="font-size:12px;margin:6px 0">Held for review: '
         +held.map(h=>esc(h.fact_type+" '"+h.text+"'")).join("  ·  ")+'</div>':'')
      +factsTable(b.bundle)
      +'<button class="btn btn-ghost btn-sm" onclick=\'cp('+JSON.stringify(JSON.stringify(b.bundle))+')\'>Copy this bundle</button>'
      +'<pre class="json">'+esc(JSON.stringify(b.bundle,null,2))+'</pre>'
      +'</div></details>';
  }).join("");
  $("#bundles").innerHTML=html;
  const rc=$("#recheck"); if(rc) rc.onclick=loadResult;
}

function factsTable(bundle){
  const rows=[];
  for(const e of (bundle.entry||[])){
    const r=e.resource;
    if(["Condition","Observation","MedicationRequest","Procedure","AllergyIntolerance","DiagnosticReport"].includes(r.resourceType)){
      const cc=r.code||r.medicationCodeableConcept||{};
      const cod=(cc.coding||[])[0]||{};
      let v=""; if(r.valueQuantity) v=r.valueQuantity.value+" "+(r.valueQuantity.unit||"");
      else if(r.valueString) v=r.valueString; else if(r.conclusion) v=r.conclusion;
      rows.push('<tr><td>'+r.resourceType+'</td><td>'+esc(cc.text||cod.display||"")+'</td><td>'
        +(cod.system?'<code class="k">'+sys(cod.system)+'</code> '+cod.code:'<span class="muted">local</span>')
        +'</td><td class="tab-nums">'+esc(String(v))+'</td></tr>');
    }
  }
  if(!rows.length) return '<p class="muted" style="font-size:12px">no coded clinical facts asserted in this bundle</p>';
  return '<table class="data"><tr><th>Resource</th><th>Concept</th><th>Code</th><th>Value</th></tr>'+rows.join("")+'</table>';
}
const sys=s=>s.includes("snomed")?"SNOMED":s.includes("loinc")?"LOINC":s.includes("icd")?"ICD-10":s;
$("#dl").onclick=()=>window.location="api/jobs/"+JOB+"/fhir/download";
$("#copyall").onclick=()=>cp(JSON.stringify(RESULT,null,2));
function cp(t){ navigator.clipboard.writeText(t); }
function esc(s){ return String(s==null?"":s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
</script>
</body>
</html>
"""

PAGE = _HTML.replace("%%CSS%%", BRAND_CSS).replace("%%HEADER%%", HEADER_HTML).replace("%%ICON%%", UPLOAD_ICON)
