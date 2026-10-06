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
      <input type="text" id="pref" placeholder="CFP-2026-000901  /  14-1111-2222-3333  /  9830011234" autocomplete="off" maxlength="40" style="flex:1"/>
      <button class="btn btn-ghost" id="lookup">Look up</button>
    </div>
    <div id="matched" class="muted" style="margin-top:8px;font-size:13px"></div>

    <label class="fld">Pages of the prescription</label>
    <p class="hint" style="margin:0 0 8px">Add every page, in order. For a <b>new patient</b>, name / sex / date of birth are read from the pages and a CareFlow ID is generated.</p>
    <div class="dropzone" id="dz" tabindex="0" role="button" aria-label="Choose or drop photos, scans or PDFs">
      %%ICON%%
      <div class="dz-title">Drop photos, scans or PDFs here, or click to choose</div>
      <div class="dz-sub" id="dzsub">JPG, PNG, TIFF or PDF</div>
    </div>
    <div class="up-actions">
      <button class="btn btn-ghost" type="button" id="cam">Take a picture</button>
      <button class="btn btn-ghost" type="button" id="pick">Choose files</button>
      <span class="muted" id="pgcount" aria-live="polite"></span>
    </div>
    <input type="file" id="files" multiple hidden
      accept=".pdf,.png,.jpg,.jpeg,.tif,.tiff,image/jpeg,image/png,image/tiff,application/pdf"/>
    <input type="file" id="camera" hidden accept="image/*" capture="environment"/>
    <ol class="pagelist" id="pages" aria-label="Pages to send"></ol>
    <fieldset class="grouping" id="grouping" hidden>
      <legend>These files are</legend>
      <label><input type="radio" name="grp" value="one_document" checked/> pages of <b>one prescription</b> (one result)</label>
      <label><input type="radio" name="grp" value="separate"/> <b>separate files</b> (each is read on its own)</label>
      <div class="muted" id="grphint"></div>
    </fieldset>
    <div class="up-err" id="uperr" role="alert" hidden></div>
    <label class="fld" for="abha">ABHA number (optional, for a new patient)</label>
    <input type="text" id="abha" placeholder="14-XXXX-XXXX-XXXX" autocomplete="off" inputmode="numeric" maxlength="20"/>
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

  <div class="card" id="result-card" hidden>
    <h2>Result (JSON)</h2>
    <p class="hint" id="resnotice"></p>
    <div class="res-top">
      <select id="resdoc" aria-label="Document" hidden></select>
      <span id="respill"></span>
      <a class="btn btn-ghost btn-sm" id="resdl" href="#" download>Download JSON</a>
    </div>
    <pre class="resjson" id="resjson" tabindex="0" aria-label="Result JSON"></pre>
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

const dz=$("#dz"),fileInput=$("#files"),camInput=$("#camera");
// What the server will accept (settings, GET api/upload/limits). The server checks everything
// again: this only lets the screen say it sooner. Defaults are used only if that call fails.
let LIMITS={max_files:10,max_file_mb:50,max_total_mb:150,min_short_side_px:600};
const OK_TYPES=["application/pdf","image/png","image/jpeg","image/tiff"];
const OK_EXT=/\.(pdf|png|jpe?g|tiff?)$/i;
let PAGES=[],NEXT_ID=1,SEND_KEY=null,SENDING=false,LOCKED=false;

function limitText(){ return "JPG, PNG, TIFF or PDF · up to "+LIMITS.max_files+" files · each up to "+LIMITS.max_file_mb+" MB"; }
$("#dzsub").textContent=limitText();
fetch("api/upload/limits").then(r=>r.ok?r.json():null).then(j=>{ if(j){ LIMITS=j; $("#dzsub").textContent=limitText(); render(); } }).catch(()=>{});

function say(msg){ const e=$("#uperr"); e.hidden=!msg; e.textContent=msg||""; }
function mb(n){ return n<1e6 ? Math.max(1,Math.round(n/1e3))+" KB" : (n/1e6).toFixed(1).replace(/\.0$/,"")+" MB"; }
function isPdf(f){ return f.type==="application/pdf"||/\.pdf$/i.test(f.name); }
function isTiff(f){ return f.type==="image/tiff"||/\.tiff?$/i.test(f.name); }

function addFiles(list){
  if(LOCKED||SENDING) return;
  let problem="";
  for(const f of list){
    if(!(OK_TYPES.includes(f.type)||OK_EXT.test(f.name))){ problem=problem||('Please add a photo, PDF or scan. "'+f.name+'" is not one.'); continue; }
    if(f.size>LIMITS.max_file_mb*1e6){ problem=problem||('"'+f.name+'" is larger than '+LIMITS.max_file_mb+' MB.'); continue; }
    if(PAGES.length>=LIMITS.max_files){ problem=problem||('You can send up to '+LIMITS.max_files+' files at once.'); break; }
    if(PAGES.reduce((n,p)=>n+p.file.size,0)+f.size>LIMITS.max_total_mb*1e6){ problem=problem||('Together the files are larger than '+LIMITS.max_total_mb+' MB. Please send fewer pages at once.'); continue; }
    // the file is kept EXACTLY as chosen: nothing is resized or re-compressed here
    const p={id:NEXT_ID++,file:f,url:null,w:null,h:null,warn:"",noPreview:false};
    if(!isPdf(f)&&!isTiff(f)){
      p.url=URL.createObjectURL(f);
      const im=new Image();
      im.onload=()=>{ p.w=im.naturalWidth; p.h=im.naturalHeight;
        if(Math.min(p.w,p.h)<LIMITS.min_short_side_px)
          p.warn="Small picture ("+p.w+" × "+p.h+" px): the writing may not be readable. Move closer and retake it."
        ;render(); };
      im.onerror=()=>{ p.noPreview=true; render(); };
      im.src=p.url;
    } else { p.noPreview=true; }
    PAGES.push(p);
  }
  say(problem); SEND_KEY=null; render();
}
function dropPage(i){ const p=PAGES[i]; if(p&&p.url) URL.revokeObjectURL(p.url); PAGES.splice(i,1); SEND_KEY=null; say(""); render(); }
function movePage(i,d){ const j=i+d; if(j<0||j>=PAGES.length) return; [PAGES[i],PAGES[j]]=[PAGES[j],PAGES[i]]; SEND_KEY=null; render(); }

function render(){
  const n=PAGES.length, anyPdf=PAGES.some(p=>isPdf(p.file));
  $("#pgcount").textContent=n?(n+(n===1?" page":" pages")+" added"):"";
  $("#pages").innerHTML=PAGES.map((p,i)=>{
    const thumb=(p.url&&!p.noPreview)?'<img class="thumb" src="'+p.url+'" alt="Preview of page '+(i+1)+'"/>'
      :'<div class="thumb ph" aria-hidden="true">'+(isPdf(p.file)?"PDF":isTiff(p.file)?"TIFF":"…")+'</div>';
    const dis=LOCKED||SENDING?" disabled":"";
    return '<li class="pg" data-i="'+i+'">'+thumb
      +'<div class="pg-body"><div class="pg-name"><b>Page '+(i+1)+'</b> · '+esc(p.file.name)+'</div>'
      +'<div class="muted">'+mb(p.file.size)+(p.w?(' · '+p.w+' × '+p.h+' px'):'')+'</div>'
      +(p.warn?'<div class="pill warn pg-warn">'+esc(p.warn)+'</div>':'')+'</div>'
      +'<div class="pg-ctl">'
      +'<button class="btn btn-ghost btn-sm" data-act="up" aria-label="Move page '+(i+1)+' up"'+(i===0||dis?" disabled":"")+'>↑</button>'
      +'<button class="btn btn-ghost btn-sm" data-act="down" aria-label="Move page '+(i+1)+' down"'+(i===n-1||dis?" disabled":"")+'>↓</button>'
      +'<button class="btn btn-ghost btn-sm" data-act="del" aria-label="Remove page '+(i+1)+'"'+(dis?" disabled":"")+'>✕</button>'
      +'</div></li>';
  }).join("");
  const g=$("#grouping"); g.hidden=n<2;
  const one=g.querySelector('input[value="one_document"]'), sep=g.querySelector('input[value="separate"]');
  one.disabled=anyPdf||LOCKED||SENDING; sep.disabled=LOCKED||SENDING;
  if(anyPdf){ sep.checked=true; }
  $("#grphint").textContent=anyPdf?"A PDF already holds all of its pages, so these are sent as separate files.":"";
  $("#go").disabled=!n||SENDING||LOCKED;
  for(const b of document.querySelectorAll(".up-actions .btn")) b.disabled=LOCKED||SENDING;
}
$("#pages").addEventListener("click",e=>{
  const b=e.target.closest("button[data-act]"); if(!b) return;
  const i=+b.closest("li").dataset.i, a=b.dataset.act;
  if(a==="up") movePage(i,-1); else if(a==="down") movePage(i,1); else dropPage(i);
});
document.querySelectorAll('#grouping input').forEach(r=>r.addEventListener("change",()=>{ SEND_KEY=null; }));

dz.onclick=()=>{ if(!LOCKED&&!SENDING) fileInput.click(); };
dz.onkeydown=e=>{ if(e.key==="Enter"||e.key===" "){ e.preventDefault(); dz.onclick(); } };
dz.ondragover=e=>{ e.preventDefault(); dz.classList.add("drag"); };
dz.ondragleave=()=>dz.classList.remove("drag");
dz.ondrop=e=>{ e.preventDefault(); dz.classList.remove("drag"); addFiles([...e.dataTransfer.files]); };
$("#pick").onclick=()=>fileInput.click();
$("#cam").onclick=()=>camInput.click();     // on a phone this opens the camera; on a laptop, a file chooser
fileInput.onchange=()=>{ addFiles([...fileInput.files]); fileInput.value=""; };
camInput.onchange=()=>{ addFiles([...camInput.files]); camInput.value=""; };
["#abha","#pref"].forEach(id=>$(id).addEventListener("input",()=>{ SEND_KEY=null; }));

function normAbha(){      // "" = empty, false = not 14 digits, else 14-XXXX-XXXX-XXXX
  const t=$("#abha").value.trim(); if(!t) return "";
  const d=t.replace(/[ -]/g,""); if(!/^[0-9]{14}$/.test(d)) return false;
  return d.slice(0,2)+"-"+d.slice(2,6)+"-"+d.slice(6,10)+"-"+d.slice(10);
}
function newKey(){ return (crypto.randomUUID?crypto.randomUUID():String(Date.now())+Math.random().toString(16).slice(2)).replace(/[^A-Za-z0-9_-]/g,""); }
async function plainError(r){
  if(r.status>=500) return "Something went wrong on our side. Please try again in a moment.";
  try{ const j=await r.json(); if(typeof j.detail==="string"&&j.detail) return j.detail; }catch(e){}
  return "Something is wrong with what was sent. Please check the pages and try again.";
}
function unlockAfterJob(){ LOCKED=false; SENDING=false; SEND_KEY=null; render(); }

$("#go").onclick=async()=>{
  if(SENDING||LOCKED) return;                       // pressed twice: the second press does nothing
  if(!PAGES.length){ say("Please add at least one photo, PDF or scan."); return; }
  const abha=normAbha();
  if(abha===false){ say("The ABHA number must have 14 digits, for example 14-1111-2222-3333."); return; }
  say(""); SENDING=true; render();
  const fd=new FormData();
  fd.append("abha",abha);
  fd.append("grouping",PAGES.length>1&&document.querySelector('input[name=grp]:checked').value==="one_document"?"one_document":"separate");
  if(MATCH) fd.append("patient_ref", $("#pref").value.trim());
  for(const p of PAGES) fd.append("files",p.file,p.file.name);
  SEND_KEY=SEND_KEY||newKey();                       // the same Send retried = the same job
  $("#hint").textContent="sending…";
  let r;
  try{ r=await fetch("api/jobs",{method:"POST",body:fd,headers:{"Idempotency-Key":SEND_KEY}}); }
  catch(e){ say("We could not reach the server. Please check the connection and try again."); $("#hint").textContent=""; SENDING=false; render(); return; }
  if(!r.ok){ say(await plainError(r)); $("#hint").textContent=""; if(r.status<500) SEND_KEY=null; SENDING=false; render(); return; }
  JOB=(await r.json()).job_id; $("#hint").textContent="processing…";
  LOCKED=true; SENDING=false; render();
  $("#emr-card").hidden=false; poll();
};

let RESULTS=[];
async function loadResult(){            // the connector's JSON, shown as-is (UP-S3 placeholder)
  let j;
  try{ const r=await fetch("api/jobs/"+JOB+"/result.json"); if(!r.ok) throw 0; j=await r.json(); }
  catch(e){ $("#resnotice").textContent="The result could not be loaded. Please try again in a moment."; $("#result-card").hidden=false; return; }
  RESULTS=j.results||[];
  const sel=$("#resdoc");
  sel.innerHTML=RESULTS.map((r,i)=>'<option value="'+i+'">'+esc(r.filename||("Document "+(i+1)))+'</option>').join("");
  sel.hidden=RESULTS.length<2; sel.onchange=()=>showResult(+sel.value);
  $("#result-card").hidden=false; showResult(0);
}
function showResult(i){
  const r=RESULTS[i]||{};
  $("#resjson").textContent=JSON.stringify(r,null,2);
  $("#resnotice").textContent=r.notice||"";
  const n=r.needs_check_count||0;
  $("#respill").innerHTML=r.status==="needs_check"||n?'<span class="pill warn">'+n+(n===1?" value needs":" values need")+' a check</span>'
    :r.status==="complete"?'<span class="pill ok">all values accepted</span>':'<span class="pill">'+esc(r.status||"")+'</span>';
  const dl=$("#resdl");
  if(r.document_id){ dl.href="api/documents/"+r.document_id+"/result.json?download=true"; dl.hidden=false; } else dl.hidden=true;
}

function poll(){
  clearTimeout(TIMER);
  TIMER=setTimeout(async()=>{
    const j=await (await fetch("api/jobs/"+JOB)).json();
    renderGrid(j);
    if(j.state==="mismatch"){ showMismatch(j); $("#hint").textContent=""; unlockAfterJob(); return; }
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
      loadResult();
      $("#sent-card").hidden=false;
      $("#sent-card").scrollIntoView({behavior:"smooth"});
    } else if(j.state==="error"){ $("#hint").textContent="job error: "+(j.error||""); unlockAfterJob(); }
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

_UPLOAD_CSS = """
.up-actions{display:flex;flex-wrap:wrap;gap:var(--sp-2);align-items:center;margin-top:var(--sp-3)}
.up-actions .btn{min-height:44px}
.pagelist{list-style:none;margin:var(--sp-3) 0 0;padding:0;display:grid;gap:var(--sp-2)}
.pg{display:grid;grid-template-columns:64px 1fr auto;gap:var(--sp-3);align-items:center;
  background:var(--surface-2);border:1px solid var(--line);border-radius:var(--r-ctl);padding:var(--sp-2) var(--sp-3)}
.pg .thumb{width:64px;height:80px;object-fit:cover;border-radius:6px;border:1px solid var(--line-strong);background:#fff}
.pg .thumb.ph{display:flex;align-items:center;justify-content:center;font-size:var(--fs-12);font-weight:700;color:var(--muted)}
.pg-name{overflow-wrap:anywhere}
.pg-warn{margin-top:var(--sp-1);white-space:normal;border-radius:var(--r-ctl);padding:var(--sp-1) var(--sp-2);font-weight:500}
.pg-ctl{display:flex;gap:6px}
.pg-ctl .btn{min-width:44px;min-height:44px}
.grouping{border:1px solid var(--line);border-radius:var(--r-ctl);margin:var(--sp-3) 0 0;padding:var(--sp-2) var(--sp-3)}
.grouping legend{font-size:var(--fs-12);color:var(--muted);padding:0 var(--sp-1)}
.grouping label{display:block;padding:var(--sp-1) 0}
.res-top{display:flex;flex-wrap:wrap;gap:var(--sp-3);align-items:center;margin-bottom:var(--sp-3)}
.res-top select{min-height:36px;border:1px solid var(--line-strong);border-radius:var(--r-ctl);padding:0 var(--sp-2);background:var(--surface)}
.resjson{margin:0;max-height:440px;overflow:auto;background:var(--surface-2);border:1px solid var(--line);
  border-radius:var(--r-ctl);padding:var(--sp-3);font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  white-space:pre-wrap;overflow-wrap:anywhere}
.up-err{margin-top:var(--sp-3);padding:var(--sp-2) var(--sp-3);color:var(--err);background:var(--err-bg);
  border:1px solid var(--err-line);border-radius:var(--r-ctl)}
@media (max-width:560px){
  .pg{grid-template-columns:56px 1fr}
  .pg .thumb{width:56px;height:70px}
  .pg-ctl{grid-column:1 / -1;justify-content:flex-end}
}
"""

PAGE = _HTML.replace("%%CSS%%", BRAND_CSS + _UPLOAD_CSS).replace("%%HEADER%%", HEADER_HTML).replace("%%ICON%%", UPLOAD_ICON)
