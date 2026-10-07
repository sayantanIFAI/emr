"""The admin upload screen: one admin signs in, adds photos / scans / PDFs, and reads the result JSON.

No patient look-up, no reviewer or FHIR links: those screens are closed unless the owner switches
them on (webapp/surface.py). The page's script is the upload screen's own (limits from
``GET api/upload/limits``, the same Send retried gives the same job, nothing is resized in the browser).
"""
from .page import _UPLOAD_CSS
from .theme import BRAND_CSS, HEADER_HTML, UPLOAD_ICON

# the same header without the links to the screens that are closed
_HEADER = HEADER_HTML[:HEADER_HTML.index("<nav")] + "</header>" + chr(10)

_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Prescription reader — admin upload</title>
<style>%%CSS%%</style>
<style>
#camdlg{border:none;border-radius:16px;padding:16px;width:min(96vw,760px);box-shadow:0 20px 60px rgba(0,0,0,.35)}
#camdlg::backdrop{background:rgba(15,23,42,.6)}
#camdlg video{width:100%;max-height:70vh;border-radius:12px;background:#000;display:block}
.camrow{display:flex;gap:10px;margin-top:12px;flex-wrap:wrap;align-items:center}
.jobbox{border:1px solid var(--line,#e2e8f0);border-radius:12px;padding:12px 14px;margin-top:12px}
.jobbox h3{margin:0 0 6px;font-size:15px;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.jobbox .jerr{color:var(--danger,#b42318);font-size:14px;margin:4px 0}
.sumtbl{margin:18px 0 0}
.sumtbl h3{font-size:15px;margin:0 0 6px;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.sumtbl table{width:100%;border-collapse:collapse;font-size:14px}
.sumtbl th,.sumtbl td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--line,#e4eaf4);vertical-align:top}
.sumtbl tbody th{width:30%;color:var(--muted,#5b6b8c);font-weight:600}
.sumtbl thead th{color:var(--muted,#5b6b8c);font-weight:600;font-size:12.5px;text-transform:uppercase;letter-spacing:.3px}
.sumtbl .none{color:var(--faint,#93a1bd)}
.sumtbl .note{margin:6px 0 0;font-size:13px;color:var(--muted,#5b6b8c)}
.sumtbl .tbox{overflow-x:auto}
.sumjson{margin:22px 0 6px;font-size:15px}
</style>
</head>
<body>
%%HEADER%%
<div class="cf-pagetitle">
  <h1>Prescription reader</h1>
  <p class="cf-sub">Upload photos, scans or PDFs of prescriptions. Each one is read and the result is shown below as JSON. Values marked <b>needs a check</b> are doubtful: nothing is guessed.</p>
</div>
<main class="cf-main">
  <div class="card" id="form-card">
    <h2>Upload</h2>
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
    <div style="margin-top:16px;display:flex;gap:10px;align-items:center">
      <button class="btn btn-primary" id="go">Read prescriptions</button>
      <span id="hint" class="muted"></span>
    </div>
  </div>

  <div class="card" id="emr-card" hidden>
    <h2>Progress</h2>
    <p class="muted" style="margin:0">You can keep adding prescriptions while earlier ones are being read.</p>
    <div id="jobs"></div>
  </div>

  <div class="card" id="result-card" hidden>
    <h2>Result</h2>
    <p class="hint" id="resnotice"></p>
    <div class="res-top">
      <select id="resdoc" aria-label="Document" hidden></select>
      <span id="respill"></span>
      <a class="btn btn-ghost btn-sm" id="resdl" href="#" download>Download JSON</a>
    </div>
    <div id="summary" aria-live="polite"></div>
    <h3 class="sumjson">Result (JSON)</h3>
    <pre class="resjson" id="resjson" tabindex="0" aria-label="Result JSON"></pre>
  </div>
  <p class="muted" style="text-align:center"><a href="/status">System status</a></p>
</main>

<dialog id="camdlg" aria-label="Camera">
  <video id="camvideo" autoplay playsinline muted></video>
  <div class="camrow">
    <button class="btn btn-primary" type="button" id="camshot">Capture</button>
    <button class="btn btn-ghost" type="button" id="camswitch" hidden>Switch camera</button>
    <button class="btn btn-ghost" type="button" id="camdone">Done</button>
    <span class="muted" id="camcount" aria-live="polite"></span>
  </div>
</dialog>

<script>

const $=s=>document.querySelector(s);
const STAGES=["ingest","classify","ocr","extract","terminology","validate"];
const STAGE_LABEL={ingest:"Ingest",classify:"Classify",ocr:"OCR",extract:"Extract",terminology:"Terminology",validate:"Validate"};
const TICK='<svg class="tick" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg>';
let TIMER=null,JOBS=[],BATCH=0;

const dz=$("#dz"),fileInput=$("#files"),camInput=$("#camera");
// What the server will accept (settings, GET api/upload/limits). The server checks everything
// again: this only lets the screen say it sooner. Defaults are used only if that call fails.
let LIMITS={max_files:10,max_file_mb:50,max_total_mb:150,min_short_side_px:600};
const OK_TYPES=["application/pdf","image/png","image/jpeg","image/tiff"];
const OK_EXT=/\.(pdf|png|jpe?g|tiff?)$/i;
let PAGES=[],NEXT_ID=1,SEND_KEY=null,SENDING=false;

function limitText(){ return "JPG, PNG, TIFF or PDF · up to "+LIMITS.max_files+" files · each up to "+LIMITS.max_file_mb+" MB"; }
$("#dzsub").textContent=limitText();
fetch("api/upload/limits").then(r=>r.ok?r.json():null).then(j=>{ if(j){ LIMITS=j; $("#dzsub").textContent=limitText(); render(); } }).catch(()=>{});

function say(msg){ const e=$("#uperr"); e.hidden=!msg; e.textContent=msg||""; }
function mb(n){ return n<1e6 ? Math.max(1,Math.round(n/1e3))+" KB" : (n/1e6).toFixed(1).replace(/\.0$/,"")+" MB"; }
function isPdf(f){ return f.type==="application/pdf"||/\.pdf$/i.test(f.name); }
function isTiff(f){ return f.type==="image/tiff"||/\.tiff?$/i.test(f.name); }

function addFiles(list){
  if(SENDING) return;
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
    const dis=SENDING?" disabled":"";
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
  one.disabled=anyPdf||SENDING; sep.disabled=SENDING;
  if(anyPdf){ sep.checked=true; }
  $("#grphint").textContent=anyPdf?"A PDF already holds all of its pages, so these are sent as separate files.":"";
  $("#go").disabled=!n||SENDING;
  for(const b of document.querySelectorAll(".up-actions .btn")) b.disabled=SENDING;
}
$("#pages").addEventListener("click",e=>{
  const b=e.target.closest("button[data-act]"); if(!b) return;
  const i=+b.closest("li").dataset.i, a=b.dataset.act;
  if(a==="up") movePage(i,-1); else if(a==="down") movePage(i,1); else dropPage(i);
});
document.querySelectorAll('#grouping input').forEach(r=>r.addEventListener("change",()=>{ SEND_KEY=null; }));

dz.onclick=()=>{ if(!SENDING) fileInput.click(); };
dz.onkeydown=e=>{ if(e.key==="Enter"||e.key===" "){ e.preventDefault(); dz.onclick(); } };
dz.ondragover=e=>{ e.preventDefault(); dz.classList.add("drag"); };
dz.ondragleave=()=>dz.classList.remove("drag");
dz.ondrop=e=>{ e.preventDefault(); dz.classList.remove("drag"); addFiles([...e.dataTransfer.files]); };
$("#pick").onclick=()=>fileInput.click();
// A phone or tablet (touch screen): the device's own camera app via <input capture> - full-resolution photo,
// autofocus, flash. A computer: a live camera view in this page (getUserMedia needs https or localhost).
const TOUCH=window.matchMedia&&matchMedia("(pointer: coarse)").matches;
const CAN_LIVE=!!(navigator.mediaDevices&&navigator.mediaDevices.getUserMedia&&window.isSecureContext&&window.HTMLDialogElement);
$("#cam").onclick=()=>{ if(TOUCH||!CAN_LIVE) camInput.click(); else openCamera(); };
let STREAM=null,CAMS=[],CAMI=0,SHOTS=0;
const camVideo=$("#camvideo"),camDlg=$("#camdlg");
function stopStream(){ if(STREAM){ STREAM.getTracks().forEach(t=>t.stop()); STREAM=null; } camVideo.srcObject=null; }
async function startStream(deviceId){
  stopStream();
  const size={width:{ideal:3840},height:{ideal:2160}};
  STREAM=await navigator.mediaDevices.getUserMedia({audio:false,video:deviceId?{deviceId:{exact:deviceId},...size}:{facingMode:{ideal:"environment"},...size}});
  camVideo.srcObject=STREAM; await camVideo.play().catch(()=>{});
}
async function openCamera(){
  say(""); SHOTS=0; $("#camcount").textContent="";
  try{
    await startStream();
    CAMS=(await navigator.mediaDevices.enumerateDevices()).filter(d=>d.kind==="videoinput");
    $("#camswitch").hidden=CAMS.length<2;
    camDlg.showModal();
  }catch(e){
    stopStream();
    say(e&&e.name==="NotAllowedError"?"The camera is blocked for this page. Allow it in the browser's address bar, or use Choose files."
       :"No camera could be opened on this device. Use Choose files instead.");
  }
}
$("#camshot").onclick=()=>{
  if(!camVideo.videoWidth) return;
  const cv=document.createElement("canvas"); cv.width=camVideo.videoWidth; cv.height=camVideo.videoHeight;
  cv.getContext("2d").drawImage(camVideo,0,0);
  cv.toBlob(b=>{ if(!b) return; const d=new Date(), z=n=>String(n).padStart(2,"0");
    const f=new File([b],"camera-"+d.getFullYear()+z(d.getMonth()+1)+z(d.getDate())+"-"+z(d.getHours())+z(d.getMinutes())+z(d.getSeconds())+".jpg",{type:"image/jpeg"});
    addFiles([f]); SHOTS++; $("#camcount").textContent=SHOTS+(SHOTS===1?" page":" pages")+" added"; },"image/jpeg",0.95);
};
$("#camswitch").onclick=async()=>{ if(CAMS.length<2) return; CAMI=(CAMI+1)%CAMS.length; try{ await startStream(CAMS[CAMI].deviceId); }catch(e){ say("That camera could not be opened."); } };
$("#camdone").onclick=()=>camDlg.close();
camDlg.addEventListener("close",stopStream);
fileInput.onchange=()=>{ addFiles([...fileInput.files]); fileInput.value=""; };
camInput.onchange=()=>{ addFiles([...camInput.files]); camInput.value=""; };

function newKey(){ return (crypto.randomUUID?crypto.randomUUID():String(Date.now())+Math.random().toString(16).slice(2)).replace(/[^A-Za-z0-9_-]/g,""); }
async function plainError(r){
  if(r.status>=500) return "Something went wrong on our side. Please try again in a moment.";
  try{ const j=await r.json(); if(typeof j.detail==="string"&&j.detail) return j.detail; }catch(e){}
  return "Something is wrong with what was sent. Please check the pages and try again.";
}
$("#go").onclick=async()=>{
  if(SENDING) return;                               // pressed twice while the files are still going up: nothing
  if(!PAGES.length){ say("Please add at least one photo, PDF or scan."); return; }
  say(""); SENDING=true; render();
  const sent=PAGES.slice();
  const fd=new FormData();
  fd.append("grouping",sent.length>1&&document.querySelector('input[name=grp]:checked').value==="one_document"?"one_document":"separate");
  for(const p of sent) fd.append("files",p.file,p.file.name);
  SEND_KEY=SEND_KEY||newKey();                       // the same Send retried = the same job
  $("#hint").textContent="sending…";
  let r;
  try{ r=await fetch("api/jobs",{method:"POST",body:fd,headers:{"Idempotency-Key":SEND_KEY}}); }
  catch(e){ say("We could not reach the server. Please check the connection and try again."); $("#hint").textContent=""; SENDING=false; render(); return; }
  if(!r.ok){ say(await plainError(r)); $("#hint").textContent=""; if(r.status<500) SEND_KEY=null; SENDING=false; render(); return; }
  const id=(await r.json()).job_id;
  // accepted: it is read in the background. The form is free again straight away for the next prescription.
  JOBS.unshift({id,label:"Batch "+(++BATCH),names:sent.map(p=>p.file.name),j:null,finished:false,err:""});
  for(const p of sent) if(p.url) URL.revokeObjectURL(p.url);
  PAGES=[]; SEND_KEY=null; SENDING=false; $("#hint").textContent=""; render();
  $("#emr-card").hidden=false; renderJobs(); poll();
};

let RESULTS=[];                         // {label, r}: newest first, across every batch
async function loadResult(job){         // the connector's JSON, shown as-is (UP-S3 placeholder)
  let j;
  try{ const r=await fetch("api/jobs/"+job.id+"/result.json"); if(!r.ok) throw 0; j=await r.json(); }
  catch(e){ $("#resnotice").textContent="The result of "+job.label+" could not be loaded. Please try again in a moment."; $("#result-card").hidden=false; return; }
  const got=(j.results||[]).map((r,i)=>({label:job.label+" · "+(r.filename||("Document "+(i+1))),r}));
  RESULTS=got.concat(RESULTS);
  const sel=$("#resdoc");
  sel.innerHTML=RESULTS.map((x,i)=>'<option value="'+i+'">'+esc(x.label)+'</option>').join("");
  sel.hidden=RESULTS.length<2; sel.onchange=()=>showResult(+sel.value);
  $("#result-card").hidden=false; sel.value="0"; showResult(0);
}

// ---- the four tables shown above the JSON: patient, doctor, doctor booking, lab tests ----------------
// Only what the result JSON says: a value that is not on the page is shown as such, never filled in.
function vget(v){ return (v&&typeof v==="object"&&"value" in v)?v:{value:null,status:"absent",reason:null}; }
function vcell(v){
  v=vget(v); const none=v.value===null||v.value===undefined||v.value==="";
  let t=none?'<span class="none">not on the page</span>':esc(typeof v.value==="boolean"?(v.value?"yes":"no"):v.value);
  if(!none&&v.status==="needs_check") t+=' <span class="pill warn" title="'+esc(v.reason||"")+'">needs a check</span>';
  return t;
}
function rowsTable(id,title,rows,note){
  return '<section class="sumtbl" id="'+id+'"><h3>'+title+'</h3><div class="tbox"><table><tbody>'
    +rows.map(r=>'<tr><th scope="row">'+esc(r[0])+'</th><td>'+r[1]+'</td></tr>').join("")
    +'</tbody></table></div>'+(note?'<p class="note">'+note+'</p>':'')+'</section>';
}
function plural(n,u){ return n+" "+u+(n===1?"":"s"); }
function bookingOf(r){
  const f=r.follow_up||{}, text=f.text||f.value||"";
  const doc=vget((r.doctor||{}).name).value;
  const tests=(r.lab_tests||[]).filter(t=>t.status!=="rejected").map(t=>t.as_written||t.text).filter(Boolean);
  let needed, when="", cls="ok";
  if(!text){ needed="No follow-up is written on the page"; cls=""; }
  else if(f.kind==="as_needed"){ needed="Only if needed"; when="as needed"; cls=""; }
  else{
    needed="Yes"; cls="warn";
    if(f.kind==="interval"&&f.interval_value!=null){
      const a=f.interval_value, b=f.interval_value_max, u=f.interval_unit||"";
      when="after "+(b!=null&&b!==a?a+"–"+b+" "+u+"s":plural(a,u));
    } else if(f.kind==="date"&&f.date){ when="on "+f.date; }
    else when="time not clear: please read the note";
  }
  const bring=/report|result|test|investigation/i.test(text);
  return {needed,cls,when,text,doc,tests,bring,status:f.status};
}
function summaryHtml(r){
  r=r||{}; const P=r.patient||{}, D=r.doctor||{}, C=D.clinic||{};
  const patient=rowsTable("tbl-patient","Patient details",[
    ["Name",vcell(P.name)],["Age",vcell(P.age_text)],["Date of birth",vcell(P.dob)],["Sex",vcell(P.sex)],
    ["Patient ID (MRN)",vcell(P.mrn)],["Phone",vcell(P.phone)],["Address",vcell(P.address)]]);
  const doctor=rowsTable("tbl-doctor","Doctor details",[
    ["Name",vcell(D.name)],["Registration no.",vcell(D.reg_no)],["Department",vcell(D.department)],
    ["Designation",vcell(D.designation)],["Qualification",vcell(D.qualification)],
    ["Clinic",vcell(C.name)],["Clinic phone",vcell(C.phone)],["Clinic address",vcell(C.address)],
    ["Stamp on the page",vcell(D.stamp_present)],["Signature on the page",vcell(D.signature_present)]],
    "Stamp and signature are a visual guess by the model and are not verified.");
  const b=bookingOf(r);
  const booking=rowsTable("tbl-booking","Doctor booking",[
    ["Booking needed",'<span class="pill '+b.cls+'">'+esc(b.needed)+'</span>'+(b.status==="needs_check"&&b.text?' <span class="pill warn">needs a check</span>':"")],
    ["With",b.doc?esc(b.doc):'<span class="none">doctor not read</span>'],
    ["When",b.when?esc(b.when):'<span class="none">—</span>'],
    ["As written",b.text?esc(b.text):'<span class="none">—</span>'],
    ["Bring to the visit",b.bring&&b.tests.length?esc("Results of: "+b.tests.join(", ")):b.bring?"Reports (as written)":'<span class="none">nothing written</span>']],
    b.needed==="Yes"?"The date is not guessed: it is counted from the day of the visit written on the prescription.":"");
  const allLabs=r.lab_tests||[];
  const isUnrec=t=>/^not a recognised test name/.test(t.reason||"");
  const isUnconf=t=>/^not confirmed on the page/.test(t.reason||"");
  const labs=allLabs.filter(t=>t.status!=="rejected"&&!isUnrec(t)&&!isUnconf(t));
  const unrec=allLabs.filter(t=>t.status!=="rejected"&&isUnrec(t));
  const unconf=allLabs.filter(t=>t.status!=="rejected"&&isUnconf(t));
  const dropped=allLabs.filter(t=>t.status==="rejected");
  const prep=r.lab_preparation||[];
  const labRows=labs.map((t,i)=>{
    const ctx=(t.context||[]).map(c=>esc(c.text)+' <span class="none">('+esc(c.kind)+')</span>').join("<br>");
    const code=t.code?esc(t.code_display||t.code)+' <span class="none">'+esc(t.code_system||"")+" "+esc(t.code)+'</span>':'<span class="none">not matched to a standard test</span>';
    const pr=(t.preparation||[]).length?t.preparation.map(esc).join("<br>"):'<span class="none">none written</span>';
    const st=t.status==="accepted"?'<span class="pill ok">read</span>':t.status==="rejected"?'<span class="pill err">rejected</span>'
      :'<span class="pill warn" title="'+esc(t.reason||"")+'">needs a check</span>';
    return '<tr><td>'+(i+1)+'</td><td>'+esc(t.as_written||t.text||"")+'</td><td>'+code+'</td><td>'+pr+'</td><td>'+(ctx||'<span class="none">—</span>')+'</td><td>'+st+'</td></tr>';
  }).join("");
  const droppedNote=dropped.length?'<p class="note">Left out because they look like medicines, not tests: '+dropped.map(t=>esc(t.as_written||t.text||"")).join("; ")+'.</p>':"";
  const unrecNote=unrec.length?'<p class="note">Read from the page but not recognised as a test name, so not listed above (please check the page): '+unrec.map(t=>esc(t.as_written||t.text||"")).join("; ")+'.</p>':"";
  const unconfNote=unconf.length?'<p class="note">The reader suggested these, but nothing on the page supports them, so they are not listed as tests (check the page): '+unconf.map(t=>esc(t.as_written||t.text||"")).join("; ")+'.</p>':"";
  const prepOnly=!labs.length&&prep.length?'<p class="note">Preparation written on the page: '+prep.map(p=>esc(p.text)).join("; ")+'</p>':"";
  const lab='<section class="sumtbl" id="tbl-labs"><h3>Lab tests <span class="pill">'+labs.length+'</span></h3>'
    +(labs.length?'<div class="tbox"><table><thead><tr><th>#</th><th>Test (as written)</th><th>Standard name</th><th>Preparation</th><th>Why (linked to)</th><th>Check</th></tr></thead><tbody>'+labRows+'</tbody></table></div>'
      :'<p class="none" style="margin:4px 0">No lab test is written on this page.</p>')+prepOnly+unrecNote+unconfNote+droppedNote+'</section>';
  return patient+doctor+booking+lab;
}

function showResult(i){
  const r=(RESULTS[i]||{}).r||{};
  $("#summary").innerHTML=summaryHtml(r);
  $("#resjson").textContent=JSON.stringify(r,null,2);
  $("#resnotice").textContent=r.notice||"";
  const n=r.needs_check_count||0;
  $("#respill").innerHTML=r.status==="needs_check"||n?'<span class="pill warn">'+(n?n+(n===1?" value needs":" values need")+' a check':'needs a check')+'</span>'
    :r.status==="complete"?'<span class="pill ok">all values accepted</span>':'<span class="pill">'+esc(r.status||"")+'</span>';
  const dl=$("#resdl");
  if(r.document_id){ dl.href="api/documents/"+r.document_id+"/result.json?download=true"; dl.hidden=false; } else dl.hidden=true;
}

function poll(){
  clearTimeout(TIMER);
  const active=JOBS.filter(x=>!x.finished);
  if(!active.length){ if(!SENDING) $("#hint").textContent=""; return; }
  if(!SENDING) $("#hint").textContent=active.length+(active.length===1?" batch":" batches")+" being read…";
  TIMER=setTimeout(async()=>{
    await Promise.all(active.map(refreshJob));
    renderJobs(); poll();
  }, 1500);
}
async function refreshJob(job){
  let j;
  try{ const r=await fetch("api/jobs/"+job.id); if(!r.ok) return; j=await r.json(); }catch(e){ return; }   // a blip: try again next time
  job.j=j;
  if(j.state==="done"||j.state==="review"){ job.finished=true; await loadResult(job); }
  else if(j.state==="error"||j.state==="mismatch"){
    job.finished=true;
    job.err=j.error||j.documents.filter(d=>d.error).map(d=>d.filename+": "+d.error.replace(/^rescan: /,"")).join(" ")||"Processing stopped. Please try again.";
  }
}

function jobGrid(j){
  let h='<tr><th class="doc">Document</th>'+STAGES.map(s=>'<th>'+STAGE_LABEL[s]+'</th>').join("")+'<th>Done</th></tr>';
  h+=j.documents.map(d=>{
    const cells=STAGES.map(s=>{
      const st=d.stages[s]||"pending";
      const inner=st==="done"?TICK:st==="running"?'<span class="spin"></span>':st==="error"?'✕':'·';
      return '<td><span class="cell '+st+'">'+inner+'</span></td>';
    }).join("");
    const done=d.status==="done";
    return '<tr><td class="doc"><div class="fn">'+esc(d.filename)+'</div><div class="sub">'
      +(d.doc_type?esc(d.doc_type):'')+(d.facts?' · '+d.facts+' facts':'')+(d.seconds?' · '+d.seconds+'s':'')+'</div>'
      +(d.error?'<div class="sub" style="color:var(--danger,#b42318)">'+esc(d.error.replace(/^rescan: /,""))+'</div>':'')+'</td>'
      +cells+'<td>'+(done?'<span class="cell done">'+TICK+'</span>':d.status==="error"?'<span class="cell error">✕</span>':'<span class="cell running"><span class="spin"></span></span>')+'</td></tr>';
  }).join("");
  return h;
}
function renderJobs(){
  $("#jobs").innerHTML=JOBS.map(job=>{
    const j=job.j, running=!job.finished;
    const state=job.err?'<span class="pill warn">stopped</span>':running?(j&&j.state==="running"?'<span class="pill">reading…</span>':'<span class="pill">waiting…</span>'):'<span class="pill ok">done</span>';
    return '<div class="jobbox"><h3>'+esc(job.label)+' '+state+'<span class="muted" style="font-weight:400">'+esc(job.names.join(", "))+'</span></h3>'
      +(job.err?'<div class="jerr" role="alert">'+esc(job.err)+'</div>':'')
      +(j?'<div style="overflow-x:auto"><table class="emr-grid">'+jobGrid(j)+'</table></div>':'<div class="muted">sent — waiting to start…</div>')+'</div>';
  }).join("");
}

function esc(s){ return String(s==null?"":s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
</script>
</body>
</html>
"""

ADMIN_PAGE = _HTML.replace("%%CSS%%", BRAND_CSS + _UPLOAD_CSS).replace("%%HEADER%%", _HEADER).replace("%%ICON%%", UPLOAD_ICON)
