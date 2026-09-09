from .theme import BRAND_CSS, HEADER_HTML, UPLOAD_ICON

_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Intelligent OCR driven Agentic EMR generation — CareFlow Polyclinic</title>
<style>%%CSS%%</style>
</head>
<body>
%%HEADER%%
<div class="cf-pagetitle">
  <h1>Intelligent OCR driven Agentic EMR generation</h1>
  <p class="cf-sub">Look up an existing patient, or upload a new one's documents · identity, coding and ABDM FHIR are generated · a human reviews and edits before bundles are produced.</p>
</div>
<main class="cf-main">
  <div class="card" id="form-card">
    <h2>Patient</h2>
    <label class="fld" for="pref">Existing patient — CareFlow ID · ABHA ID · mobile number</label>
    <div style="display:flex;gap:8px">
      <input type="text" id="pref" placeholder="CFP-2026-000901  /  14-1111-2222-3333  /  9830011234" autocomplete="off" style="flex:1"/>
      <button class="btn btn-ghost" id="lookup">Look up</button>
    </div>
    <div id="matched" class="muted" style="margin-top:8px;font-size:13px"></div>

    <label class="fld">Documents</label>
    <p class="hint" style="margin:0 0 8px">For a <b>new patient</b>, name / sex / date of birth are read from the documents and a CareFlow ID is generated.</p>
    <div class="dropzone" id="dz" tabindex="0" role="button" aria-label="Choose or drop documents">
      %%ICON%%
      <div class="dz-title">Drop files here or click to choose</div>
      <div class="dz-sub">PDF, JPG, PNG or TIFF · up to 10 documents · processed in parallel</div>
      <div class="dz-files" id="dzfiles"></div>
    </div>
    <input type="file" id="files" multiple hidden
      accept=".pdf,.png,.jpg,.jpeg,.tif,.tiff,image/*,application/pdf"/>
    <label class="fld" for="abha">ABHA number (optional, for a new patient)</label>
    <input type="text" id="abha" placeholder="14-XXXX-XXXX-XXXX" autocomplete="off"/>
    <div style="margin-top:16px;display:flex;gap:10px;align-items:center">
      <button class="btn btn-primary" id="go">Generate EMR</button>
      <span id="hint" class="muted"></span>
    </div>
  </div>

  <div class="card danger" id="mismatch-card" hidden>
    <h2>⚠ Patient mismatch — processing stopped</h2>
    <div id="mismatch"></div>
  </div>

  <div class="card" id="emr-card" hidden>
    <h2>Generate EMR</h2>
    <div class="emr-tabs" id="emrtabs"></div>
    <div style="overflow-x:auto"><table class="emr-grid" id="emrgrid"></table></div>
  </div>

  <div class="card" id="patient-card" hidden>
    <h2>Patient</h2>
    <div class="patient" id="patient"></div>
  </div>

  <div class="card" id="details-card" hidden>
    <h2>Complete patient details</h2>
    <p class="hint">New patient <b id="d_pid"></b>. Save the full record so this patient can be looked up next time.</p>
    <div class="row">
      <div><label class="fld">Full name</label><input type="text" id="d_name"/></div>
      <div><label class="fld">Mobile number *</label><input type="text" id="d_mobile" placeholder="10-digit mobile"/></div>
    </div>
    <div class="row">
      <div><label class="fld">Date of birth</label><input type="text" id="d_dob" placeholder="YYYY-MM-DD"/></div>
      <div><label class="fld">Sex</label><input type="text" id="d_sex" placeholder="M / F / O"/></div>
      <div><label class="fld">ABHA ID</label><input type="text" id="d_abha" placeholder="14-XXXX-XXXX-XXXX"/></div>
    </div>
    <label class="fld">Address</label><input type="text" id="d_addr" placeholder="House, street, city, PIN"/>
    <div style="margin-top:14px;display:flex;gap:10px;align-items:center">
      <button class="btn btn-primary" id="savepat">Save patient</button>
      <span id="savemsg" class="muted"></span>
    </div>
  </div>

  <div class="card" id="sent-card" hidden>
    <h2>Sent to the reviewer</h2>
    <p class="hint" id="sentmsg">The extracted facts are now in the reviewer <b>workbasket</b>.
      Human review, correction, approval and ABDM FHIR bundle generation all happen there.</p>
    <div style="margin-top:14px;display:flex;gap:10px;flex-wrap:wrap">
      <a class="btn btn-primary" href="/reviewer">Open the reviewer workbasket →</a>
      <a class="btn btn-ghost" href="/admin">Admin / data inspector</a>
    </div>
  </div>
</main>

<script>
const $=s=>document.querySelector(s);
const STAGES=["ingest","classify","ocr","extract","terminology","validate"];
const STAGE_LABEL={ingest:"Ingest",classify:"Classify",ocr:"OCR",extract:"Extract",terminology:"Terminology",validate:"Validate"};
const TICK='<svg class="tick" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg>';
let JOB=null,TIMER=null,FACTS=null,CURDOC=0,RESULT=null,MATCH=null;

$("#lookup").onclick=doLookup;
$("#pref").addEventListener("blur",()=>{ if($("#pref").value.trim()) doLookup(); });
async function doLookup(){
  const q=$("#pref").value.trim();
  if(!q){ $("#matched").textContent=""; MATCH=null; return; }
  const r=await fetch("api/registry/lookup?q="+encodeURIComponent(q));
  const j=await r.json();
  if(j.found){ MATCH=j.patient;
    $("#matched").innerHTML='<span class="pill ok">existing patient</span> <b>'+esc(j.patient.name)
      +'</b> · '+esc(j.patient.patient_id||"—")+' · '+esc(j.patient.mobile||"")
      +(j.patient.dob?' · DOB '+esc(j.patient.dob):'')
      +' — all documents will attach to this patient';
  } else { MATCH=null;
    $("#matched").innerHTML='<span class="pill warn">no match</span> a new patient will be created from the documents';
  }
}

const dz=$("#dz"),fileInput=$("#files");
dz.onclick=()=>fileInput.click();
dz.onkeydown=e=>{ if(e.key==="Enter"||e.key===" "){ e.preventDefault(); fileInput.click(); } };
dz.ondragover=e=>{ e.preventDefault(); dz.classList.add("drag"); };
dz.ondragleave=()=>dz.classList.remove("drag");
dz.ondrop=e=>{ e.preventDefault(); dz.classList.remove("drag"); fileInput.files=e.dataTransfer.files; showFiles(); };
fileInput.onchange=showFiles;
function showFiles(){ const n=fileInput.files.length;
  $("#dzfiles").textContent = n ? [...fileInput.files].map(f=>f.name).join("  ·  ") : ""; }

$("#go").onclick=async()=>{
  const files=fileInput.files;
  if(!files.length){ $("#hint").textContent="pick at least one file"; return; }
  const fd=new FormData();
  fd.append("abha",$("#abha").value||"");
  if(MATCH) fd.append("patient_ref", $("#pref").value.trim());
  for(const f of files) fd.append("files",f);
  $("#go").disabled=true; $("#hint").textContent="processing…";
  const r=await fetch("api/jobs",{method:"POST",body:fd});
  if(!r.ok){ $("#hint").textContent="error: "+(await r.text()); $("#go").disabled=false; return; }
  JOB=(await r.json()).job_id; $("#hint").textContent="processing…";
  $("#emr-card").hidden=false; poll();
};

function poll(){
  clearTimeout(TIMER);
  TIMER=setTimeout(async()=>{
    const j=await (await fetch("api/jobs/"+JOB)).json();
    renderGrid(j);
    if(j.state==="mismatch"){ showMismatch(j); $("#hint").textContent=""; $("#go").disabled=false; return; }
    // the patient card only appears once documents have actually been read
    if(j.patient && (j.state==="review"||j.state==="done")){
      $("#patient-card").hidden=false; renderPatient(j.patient);
    }
    if(j.state==="review"||j.state==="done"){
      $("#hint").textContent="";
      if(j.patient && j.patient.is_new) showDetails(j.patient);
      const n=(j.documents||[]).length;
      $("#sentmsg").innerHTML="<b>"+n+"</b> document"+(n===1?"":"s")+" processed. The extracted facts are"
        +" now in the reviewer <b>workbasket</b> — human review, correction, approval and ABDM FHIR"
        +" bundle generation all happen there.";
      $("#sent-card").hidden=false;
      $("#sent-card").scrollIntoView({behavior:"smooth"});
    } else if(j.state==="error"){ $("#hint").textContent="job error: "+(j.error||""); $("#go").disabled=false; }
    else poll();
  }, 1200);
}

function renderGrid(j){
  const anyRunning=s=>j.documents.some(d=>d.stages[s]==="running");
  const allDone=s=>j.documents.length && j.documents.every(d=>d.stages[s]==="done");
  $("#emrtabs").innerHTML=STAGES.map(s=>{
    const cls=allDone(s)?"done":anyRunning(s)?"active":"";
    return '<span class="emr-tab '+cls+'"><span class="dot"></span>'+STAGE_LABEL[s]+'</span>';
  }).join("");
  let h='<tr><th class="doc">Document</th>'+STAGES.map(s=>'<th>'+STAGE_LABEL[s]+'</th>').join("")+'<th>Done</th></tr>';
  h+=j.documents.map(d=>{
    const cells=STAGES.map(s=>{
      const st=d.stages[s]||"pending";
      const inner=st==="done"?TICK:st==="running"?'<span class="spin"></span>':st==="error"?'✕':'·';
      return '<td><span class="cell '+st+'">'+inner+'</span></td>';
    }).join("");
    const done=d.status==="done";
    return '<tr><td class="doc"><div class="fn">'+esc(d.filename)+'</div><div class="sub">'
      +(d.doc_type?esc(d.doc_type):'')+(d.facts?' · '+d.facts+' facts':'')+(d.seconds?' · '+d.seconds+'s':'')+'</div></td>'
      +cells+'<td>'+(done?'<span class="cell done">'+TICK+'</span>':d.status==="error"?'<span class="cell error">✕</span>':'<span class="cell running"><span class="spin"></span></span>')+'</td></tr>';
  }).join("");
  $("#emrgrid").innerHTML=h;
}

function showMismatch(j){
  const m=j.mismatch||{};
  $("#mismatch-card").hidden=false;
  $("#emr-card").hidden=true; $("#patient-card").hidden=true;
  $("#sent-card").hidden=true; $("#details-card").hidden=true;
  $("#mismatch").innerHTML=
    '<p>'+esc(j.error||"The uploaded document does not belong to the selected patient.")+'</p>'
    +'<div class="mm-detail"><b>You selected:</b> '+esc(m.selected_name||"?")+' ('+esc(m.selected_id||"?")+')'
    +(m.selected_dob?' · DOB '+esc(m.selected_dob):'')+'<br>'
    +'<b>Document is for:</b> '+esc(m.document_name||"?")+(m.document_dob?' · DOB '+esc(m.document_dob):'')
    +' <span class="muted">('+esc(m.document||"")+')</span></div>'
    +'<p style="margin-top:10px">Nothing was written for '+esc(m.selected_name||"the selected patient")
    +'. Clear the look-up to create a new patient, or upload the correct patient\'s documents.</p>'
    +'<button class="btn btn-primary" onclick="location.reload()">Start over</button>';
  $("#mismatch-card").scrollIntoView({behavior:"smooth"});
}

function renderPatient(p){
  const nm=p.name||"Unknown";
  const initials=nm.split(/\s+/).map(x=>x[0]||"").slice(0,2).join("").toUpperCase()||"–";
  const sex={M:"Male",F:"Female",O:"Other"}[p.sex]||"—";
  const conf=(p.identity_confidence!=null)?(' · identity '+(p.identity_confidence*100).toFixed(0)+'%'):'';
  const dob=p.birth_date?(' · DOB '+esc(p.birth_date)):(p.age_years!=null?(' · age '+p.age_years):'');
  const abha=p.abha_number?(' · ABHA '+esc(p.abha_number)):'';
  const mob=p.mobile?(' · '+esc(p.mobile)):'';
  const tag=p.is_new?' <span class="pill warn">new</span>':' <span class="pill ok">on file</span>';
  $("#patient").innerHTML='<div class="avatar">'+esc(initials)+'</div>'
    +'<div><div class="pid tab-nums">'+esc(p.mpi_id||"CFP-…")+'</div>'
    +'<div class="meta"><b>'+esc(nm)+'</b> · '+sex+dob+mob+abha+conf+tag+'</div></div>';
}

function showDetails(p){
  $("#details-card").hidden=false;
  $("#d_pid").textContent=p.mpi_id||"";
  $("#d_name").value=p.name||""; $("#d_dob").value=p.birth_date||"";
  $("#d_sex").value=p.sex||""; $("#d_abha").value=p.abha_number||"";
  $("#d_mobile").value=p.mobile||""; $("#d_addr").value=p.address||"";
}
$("#savepat").onclick=async()=>{
  const body={patient_id:$("#d_pid").textContent, name:$("#d_name").value, mobile:$("#d_mobile").value,
    dob:$("#d_dob").value||null, gender:$("#d_sex").value||null, address:$("#d_addr").value||null,
    abha_id:$("#d_abha").value||null};
  if(!body.name||!body.mobile){ $("#savemsg").textContent="name and mobile are required"; return; }
  const r=await fetch("api/registry",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
  $("#savemsg").textContent = r.ok ? "saved — this patient can now be looked up" : ("error: "+await r.text());
};

function esc(s){ return String(s==null?"":s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
</script>
</body>
</html>
"""

PAGE = _HTML.replace("%%CSS%%", BRAND_CSS).replace("%%HEADER%%", HEADER_HTML).replace("%%ICON%%", UPLOAD_ICON)
