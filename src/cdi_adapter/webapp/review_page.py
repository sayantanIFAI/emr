REVIEW_PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>CDI-Adapter · review queue</title>
<style>
  :root{--bg:#0f1420;--card:#161d2e;--line:#28324a;--fg:#e6ebf5;--mut:#93a1bd;
        --accent:#5b9dff;--ok:#3fd08a;--warn:#f5b642;--err:#ff6b6b;}
  *{box-sizing:border-box;} body{margin:0;background:var(--bg);color:var(--fg);
    font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif;}
  header{padding:16px 22px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;align-items:center}
  h1{margin:0;font-size:16px;font-weight:650} a{color:var(--accent)}
  .wrap{display:grid;grid-template-columns:340px 1fr;gap:0;height:calc(100vh - 55px)}
  .list{border-right:1px solid var(--line);overflow:auto;padding:12px}
  .detail{overflow:auto;padding:18px 22px}
  .task{margin-bottom:14px}
  .task h3{font-size:12px;color:var(--mut);text-transform:uppercase;letter-spacing:.05em;margin:0 0 6px}
  .fact{padding:8px 10px;border:1px solid var(--line);border-radius:8px;margin-bottom:6px;cursor:pointer;background:#121a2b}
  .fact:hover{border-color:var(--accent)}
  .fact.sel{border-color:var(--accent);background:#16233c}
  .pill{font-size:10.5px;padding:1px 7px;border-radius:20px;border:1px solid var(--line);color:var(--mut)}
  .pill.err{color:var(--err);border-color:#5f2222}.pill.warn{color:var(--warn);border-color:#5f4a1f}
  .imgwrap{position:relative;display:inline-block;border:1px solid var(--line);border-radius:8px;overflow:hidden;max-width:100%}
  .imgwrap img{display:block;max-width:100%}
  .bbox{position:absolute;border:2px solid var(--accent);background:rgba(91,157,255,.15);box-shadow:0 0 0 9999px rgba(0,0,0,.35)}
  table{width:100%;border-collapse:collapse;font-size:13px;margin:10px 0}
  th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}
  th{color:var(--mut);width:150px;font-weight:600}
  input[type=text]{width:100%;padding:7px 9px;background:#0d1322;color:var(--fg);border:1px solid var(--line);border-radius:6px}
  button{padding:9px 16px;border:0;border-radius:8px;font-weight:650;cursor:pointer;font-size:13px}
  .b-accept{background:var(--ok);color:#04140c}.b-reject{background:var(--err);color:#170404}
  .b-correct{background:var(--accent);color:#04101f}
  .finding{font-size:12px;padding:4px 8px;border-radius:6px;background:#0d1322;border:1px solid var(--line);margin:3px 0}
  .finding.blocker{border-color:#5f2222;color:#ffb0b0}.finding.warn{border-color:#5f4a1f;color:#ffd98a}
  .muted{color:var(--mut)} code.k{color:var(--accent)}
</style>
</head>
<body>
<header>
  <h1>Review queue — held clinical facts</h1>
  <div><a href="/">← upload</a> &nbsp; <span id="count" class="muted"></span> &nbsp;
    <button class="b-correct" onclick="load()">Refresh</button></div>
</header>
<div class="wrap">
  <div class="list" id="list"></div>
  <div class="detail" id="detail"><p class="muted">Select a fact on the left.</p></div>
</div>
<script>
const $=s=>document.querySelector(s);
let TASKS=[], CUR=null;

async function load(){
  const r=await fetch("api/review/tasks"); const j=await r.json(); TASKS=j.tasks;
  const n=TASKS.reduce((a,t)=>a+t.facts.length,0);
  $("#count").textContent = n+" fact(s) in "+TASKS.length+" task(s)";
  $("#list").innerHTML = TASKS.map(t=>`
    <div class="task">
      <h3>${t.kind} · ${t.doc_type||""} <span class="muted">p${t.priority}</span></h3>
      ${t.facts.map(f=>`
        <div class="fact" data-id="${f.fact_id}" onclick="pick('${f.fact_id}')">
          <div><b>${esc(f.fact_type)}</b> — ${esc(f.text||"")}</div>
          <div style="margin-top:4px">
            <span class="pill ${f.confidence<0.6?'err':f.confidence<0.85?'warn':''}">conf ${f.confidence.toFixed(2)}</span>
            ${f.code?`<span class="pill">${short(f.code_system)} ${f.code}</span>`:'<span class="pill warn">uncoded</span>'}
          </div>
        </div>`).join("")}
    </div>`).join("") || '<p class="muted">Queue is empty — every fact is governed. 🎉</p>';
}

async function pick(fid){
  CUR=fid;
  document.querySelectorAll(".fact").forEach(e=>e.classList.toggle("sel", e.dataset.id===fid));
  const d=await (await fetch("api/review/facts/"+fid)).json();
  const bb=(d.evidence_bbox_union&&d.evidence_bbox_union[0])||null;
  const scale = 620; // display width px target
  let overlay="";
  if(bb && d.page_width){
    const s=scale/d.page_width;
    overlay=`<div class="bbox" style="left:${bb[0]*s}px;top:${bb[1]*s}px;width:${(bb[2]-bb[0])*s}px;height:${(bb[3]-bb[1])*s}px"></div>`;
  }
  const md=d.medication_detail||{};
  $("#detail").innerHTML=`
    <div style="display:flex;gap:22px;flex-wrap:wrap">
      <div class="imgwrap" style="width:${scale}px">
        ${d.page_image_url?`<img src="${d.page_image_url}" style="width:${scale}px"/>`:'<p class="muted" style="padding:20px">no page image</p>'}
        ${overlay}
      </div>
      <div style="flex:1;min-width:320px">
        <table>
          <tr><th>Fact type</th><td>${esc(d.fact_type)}</td></tr>
          <tr><th>Text (as read)</th><td><b>${esc(d.local_text||"")}</b></td></tr>
          <tr><th>OCR span</th><td>${(d.extracted_text||[]).map(esc).join("<br>")||'<span class="muted">—</span>'}</td></tr>
          <tr><th>OCR blocks</th><td>${(d.ocr_blocks||[]).map(b=>esc(b.text)+` <span class="muted">(${b.conf.toFixed(2)})</span>`).join("<br>")||'—'}</td></tr>
          <tr><th>Proposed code</th><td>${d.code?`<code class="k">${short(d.code_system)}</code> ${d.code} — ${esc(d.code_display||"")}`:'<span class="muted">uncoded (local_only)</span>'}</td></tr>
          <tr><th>Value</th><td>${d.value_num!=null?d.value_num+" "+(d.value_unit_ucum||""):esc(d.value_text||"—")} ${d.abnormal_flag?`<span class="pill warn">${d.abnormal_flag}</span>`:""}</td></tr>
          ${d.medication_detail?`<tr><th>Dose / freq / route</th><td>${md.dose_num??md.strength_num??'?'} ${md.dose_unit_ucum||md.strength_unit||''} · ${md.frequency_code||md.frequency_per_day||'?'} · ${md.route||'?'}</td></tr>`:''}
          <tr><th>Confidence</th><td>${d.confidence_overall.toFixed(3)}</td></tr>
          <tr><th>Rule findings</th><td>${findingsHtml(d.review_note)}</td></tr>
        </table>
        <div style="margin-top:6px">
          <label class="muted">Corrected text (optional)</label>
          <input type="text" id="c_text" value="${esc(d.local_text||"")}"/>
          <div style="display:flex;gap:8px;margin:8px 0">
            <input type="text" id="c_val" placeholder="value" value="${d.value_num!=null?d.value_num:''}" style="width:90px"/>
            <input type="text" id="c_unit" placeholder="unit (UCUM)" value="${esc(d.value_unit_ucum||'')}" style="width:120px"/>
            <input type="text" id="c_code" placeholder="code" value="${esc(d.code||'')}" style="width:110px"/>
            <input type="text" id="c_sys" placeholder="system" value="${esc(d.code_system||'')}" style="flex:1"/>
          </div>
        </div>
        <div style="display:flex;gap:10px;margin-top:8px">
          <button class="b-accept" onclick="decide('accept')">Accept as-is</button>
          <button class="b-correct" onclick="decide('correct')">Save correction</button>
          <button class="b-reject" onclick="decide('reject')">Reject</button>
        </div>
        <p class="muted" id="msg" style="margin-top:10px"></p>
      </div>
    </div>`;
}

function findingsHtml(note){
  if(!note) return '<span class="muted">none</span>';
  return note.split(";").map(s=>{
    s=s.trim(); const sev=/\[blocker\]/.test(s)?'blocker':/\[warn\]/.test(s)?'warn':'';
    return `<div class="finding ${sev}">${esc(s)}</div>`;
  }).join("");
}

async function decide(action){
  const body={action, reviewer:"reviewer"};
  if(action==="correct"){
    body.corrections={
      local_text:$("#c_text").value||undefined,
      value_num:$("#c_val").value!==""?Number($("#c_val").value):undefined,
      value_unit_ucum:$("#c_unit").value||undefined,
      code:$("#c_code").value||undefined,
      code_system:$("#c_sys").value||undefined,
      code_status:$("#c_code").value?"bound":undefined
    };
  }
  const r=await fetch("api/facts/"+CUR+"/review",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
  $("#msg").textContent = r.ok ? ("saved: "+action) : ("error: "+await r.text());
  if(r.ok){ await load(); $("#detail").innerHTML='<p class="muted">Saved. Pick the next fact.</p>'; }
}
const short=s=>!s?'':s.includes("snomed")?"SNOMED":s.includes("loinc")?"LOINC":s.includes("icd")?"ICD-10":s;
const esc=s=>String(s==null?"":s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
load();
</script>
</body>
</html>
"""
