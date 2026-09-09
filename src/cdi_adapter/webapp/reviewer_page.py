from .theme import BRAND_CSS, HEADER_HTML

_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>CareFlow Polyclinic — reviewer</title>
<style>%%CSS%%
.rv2-wrap{max-width:1400px;margin:0 auto;padding:16px}
.rv2-tabs{display:flex;gap:8px;margin-bottom:12px}
.rv2-tabs button{padding:8px 16px;border:1px solid var(--line-strong);background:var(--surface);
  border-radius:8px;cursor:pointer;font-size:var(--fs-13)}
.rv2-tabs button.on{background:var(--blue-600);color:#fff;border-color:var(--blue-600)}
.rv2-id{margin-left:auto;font-size:var(--fs-13);color:var(--muted)}
table.wb{width:100%;border-collapse:collapse;font-size:var(--fs-13);background:var(--surface);
  border:1px solid var(--line);border-radius:10px;overflow:hidden}
table.wb th,table.wb td{padding:9px 12px;text-align:left;border-bottom:1px solid var(--line)}
table.wb th{background:var(--surface-2);font-weight:600}
table.wb tr:hover td{background:var(--surface-2);cursor:pointer}
.pill{display:inline-block;padding:2px 8px;border-radius:999px;font-size:11px;font-weight:600}
.pill.stat{background:#fee2e2;color:#b91c1c}.pill.urgent{background:#ffedd5;color:#c2410c}
.pill.routine{background:#e0e7ff;color:#3730a3}
.ws{display:grid;grid-template-columns:minmax(320px,1fr) minmax(420px,1.2fr);gap:16px;margin-top:12px}
.ws .scan{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:10px;position:sticky;top:12px;height:fit-content}
.ws .scan img{width:100%;border-radius:6px;display:block}
.ws .panel{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:14px}
.el{border:1px solid var(--line);border-radius:9px;padding:10px 12px;margin-bottom:10px}
.el.accept{border-color:#16a34a;background:#f0fdf4}.el.reject{border-color:#dc2626;background:#fef2f2}
.el.correct{border-color:#2563eb;background:#eff6ff}
.el .ft{font-size:11px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}
.el .propose{font-size:var(--fs-14);margin:3px 0 8px}
.el input,.el textarea{width:100%;padding:6px 8px;border:1px solid var(--line-strong);border-radius:7px;
  font:inherit;margin-bottom:6px}
.el .acts{display:flex;gap:6px}
.el .acts button{flex:1;padding:6px;border:1px solid var(--line-strong);background:var(--surface);
  border-radius:7px;cursor:pointer;font-size:12px}
.el .acts button.sel{background:var(--blue-600);color:#fff;border-color:var(--blue-600)}
.bigbtn{width:100%;padding:11px;border:0;border-radius:9px;background:var(--blue-600);color:#fff;
  font-weight:600;cursor:pointer;font-size:var(--fs-14);margin-top:6px}
.bigbtn.ghost{background:var(--surface);color:var(--ink);border:1px solid var(--line-strong)}
.bigbtn:disabled{opacity:.5;cursor:not-allowed}
.vres{margin-top:10px;padding:10px 12px;border-radius:9px;font-size:var(--fs-13)}
.vres.ok{background:#f0fdf4;border:1px solid #16a34a}
.vres.bad{background:#fef2f2;border:1px solid #dc2626}
.vres ul{margin:6px 0 0 16px}
.muted{color:var(--muted)}
pre.bundle{white-space:pre-wrap;font-size:11px;background:var(--surface-2);padding:10px;border-radius:8px;
  max-height:320px;overflow:auto;margin-top:8px}
</style>
</head>
<body>
%%HEADER%%
<div class="rv2-wrap">
  <div class="rv2-tabs">
    <button id="t-wb" class="on" onclick="show('wb')">Workbasket <span id="c-wb"></span></button>
    <button id="t-wl" onclick="show('wl')">My worklist <span id="c-wl"></span></button>
    <span class="rv2-id">reviewer: <b id="me">reviewer</b></span>
  </div>
  <div id="listview"></div>
  <div id="wsview" hidden></div>
</div>
<script>
const $=s=>document.querySelector(s);
const ME="reviewer";
let VIEW="wb", ITEM=null;

function show(v){ VIEW=v; ITEM=null;
  $("#t-wb").classList.toggle("on",v==="wb"); $("#t-wl").classList.toggle("on",v==="wl");
  $("#wsview").hidden=true; $("#listview").hidden=false; loadList(); }

async function loadList(){
  const url = VIEW==="wb" ? "api/reviewer/workbasket" : "api/reviewer/worklist?assignee="+ME;
  const j = await (await fetch(url)).json();
  const st = await (await fetch("api/reviewer/stats")).json();
  $("#c-wb").textContent = "("+(st.by_state.open||0)+")";
  $("#c-wl").textContent = "("+((st.by_state.in_progress||0)+(st.by_state.claimed||0))+")";
  const rows = (j.items||[]).map(it=>`
    <tr onclick="open_item('${it.id}')">
      <td><span class="pill ${it.priority}">${it.priority}</span></td>
      <td>${it.patient_display||''}</td>
      <td>${it.mpi_id||'<span class=muted>id pending</span>'}</td>
      <td>${it.doc_type||''}</td>
      <td>${it.n_elements ?? (it.n_resolved+'/'+it.n_elements)} fields</td>
      <td>${it.state||'open'}${it.bundle_status? ' · '+it.bundle_status:''}</td>
    </tr>`).join("");
  $("#listview").innerHTML = `<table class="wb"><thead><tr>
      <th>Priority</th><th>Patient</th><th>Patient ID</th><th>Type</th><th>Fields</th><th>State</th>
    </tr></thead><tbody>${rows||'<tr><td colspan=6 class=muted>Nothing here.</td></tr>'}</tbody></table>`;
}

async function open_item(id){
  const it = await (await fetch("api/reviewer/items/"+id)).json();
  ITEM = it;
  $("#listview").hidden=true; $("#wsview").hidden=false;
  const claimed = it.state==="in_progress" || it.state==="approved" || it.state==="rejected";
  const els = it.elements.map((e,i)=>elHtml(e,i)).join("");
  $("#wsview").innerHTML = `
   <p><button class="bigbtn ghost" style="width:auto;padding:7px 14px" onclick="show('${VIEW}')">← back</button>
      &nbsp; <b>${it.patient_display}</b> · ${it.doc_type} · <span class="muted">${it.state}</span></p>
   <div class="ws">
     <div class="scan"><img src="${it.original_url}" onerror="this.src='${it.page_image_url}'"/></div>
     <div class="panel" id="panel">
       ${claimed ? "" : `<button class="bigbtn" onclick="claim('${it.id}')">Pick into my worklist</button>`}
       <div id="els" ${claimed?'':'style="opacity:.5;pointer-events:none"'}>${els}</div>
       ${claimed && it.state==='in_progress' ? `
         <button class="bigbtn" id="approve" onclick="approve('${it.id}')">Approve → promote → generate bundle</button>
         <button class="bigbtn ghost" onclick="reject('${it.id}')">Reject item</button>` : ""}
       <div id="vout"></div>
     </div>
   </div>`;
  if(it.validation) renderValidation(it.validation, it.bundle_status, it.id);
}

function elHtml(e,i){
  const cls = e.decision && e.decision!=="pending" ? " "+e.decision : "";
  const val = e.reviewer_text ?? e.proposed_text ?? "";
  const num = e.reviewer_value_num ?? e.proposed_value_num ?? "";
  const unit = e.reviewer_value_unit ?? e.proposed_value_unit ?? "";
  return `<div class="el${cls}" data-id="${e.id}" data-i="${i}">
    <div class="ft">${e.fact_type}${e.proposed_code? ' · '+e.proposed_code : ''}</div>
    <div class="propose">${e.proposed_text||''} ${e.proposed_value_num!=null? '· '+e.proposed_value_num+' '+(e.proposed_value_unit||''):''} ${e.proposed_freq_text? '· '+e.proposed_freq_text:''}</div>
    <input class="rtext" placeholder="corrected text" value="${val.replace(/"/g,'&quot;')}"/>
    <input class="rnum" placeholder="value" value="${num}"/>
    <input class="runit" placeholder="unit (UCUM)" value="${unit}"/>
    <div class="acts">
      <button data-d="accept"  class="${e.decision==='accept'?'sel':''}"  onclick="dec(${i},'accept')">Accept</button>
      <button data-d="correct" class="${e.decision==='correct'?'sel':''}" onclick="dec(${i},'correct')">Correct</button>
      <button data-d="reject"  class="${e.decision==='reject'?'sel':''}"  onclick="dec(${i},'reject')">Reject</button>
    </div>
  </div>`;
}

function dec(i,d){
  const el = document.querySelector('.el[data-i="'+i+'"]');
  el.className = "el "+d;
  el.querySelectorAll('.acts button').forEach(b=>b.classList.toggle('sel', b.dataset.d===d));
  ITEM.elements[i].decision = d;
  save();
}

let saveT=null;
function collect(){
  return [...document.querySelectorAll('.el')].map(el=>{
    const i = +el.dataset.i;
    return {id: el.dataset.id, decision: ITEM.elements[i].decision||'pending',
      reviewer_text: el.querySelector('.rtext').value.trim()||null,
      reviewer_value_num: el.querySelector('.rnum').value.trim()===''?null:parseFloat(el.querySelector('.rnum').value),
      reviewer_value_unit: el.querySelector('.runit').value.trim()||null};
  });
}
function save(){
  clearTimeout(saveT);
  saveT=setTimeout(async ()=>{
    await fetch("api/reviewer/items/"+ITEM.id+"/elements",{method:"POST",
      headers:{'content-type':'application/json'},
      body:JSON.stringify({assignee:ME, elements:collect()})});
  },400);
}
document.addEventListener('input',e=>{ if(e.target.closest('.el')) save(); });

async function claim(id){
  await fetch("api/reviewer/items/"+id+"/claim",{method:"POST",
    headers:{'content-type':'application/json'},body:JSON.stringify({assignee:ME})});
  open_item(id);
}
async function reject(id){
  if(!confirm("Reject this whole item?")) return;
  await fetch("api/reviewer/items/"+id+"/reject",{method:"POST",
    headers:{'content-type':'application/json'},body:JSON.stringify({reviewer:ME})});
  show(VIEW);
}
async function approve(id){
  const btn=$("#approve"); btn.disabled=true; btn.textContent="promoting + generating…";
  await fetch("api/reviewer/items/"+id+"/elements",{method:"POST",
    headers:{'content-type':'application/json'},body:JSON.stringify({assignee:ME,elements:collect()})});
  const r = await fetch("api/reviewer/items/"+id+"/approve",{method:"POST",
    headers:{'content-type':'application/json'},body:JSON.stringify({reviewer:ME})});
  const j = await r.json();
  if(!r.ok){ alert(j.detail||"approve failed"); btn.disabled=false; btn.textContent="Approve → promote → generate bundle"; return; }
  btn.textContent="approved";
  renderValidation(j.validation, j.bundle_status, id, j.promoted);
}

function renderValidation(v, status, id, promoted){
  const errs = (v.issues||[]).filter(x=>x.severity==="error");
  const warns = (v.issues||[]).filter(x=>x.severity==="warning");
  const cls = v.ok ? "ok" : "bad";
  $("#vout").innerHTML = `
    <div class="vres ${cls}">
      <b>${v.ok ? "✓ Bundle conforms" : "✗ Bundle has "+errs.length+" error(s)"}</b>
      — validator: <code>${v.validator}</code>, status: <b>${status}</b>
      ${promoted? ' · canonical rows: '+JSON.stringify(promoted.counts):''}
      ${errs.length? '<ul>'+errs.map(e=>`<li><code>${e.path}</code> ${e.msg}</li>`).join('')+'</ul>':''}
      ${warns.length? '<div class="muted" style="margin-top:6px">'+warns.length+' warning(s): '+warns.slice(0,4).map(w=>w.msg).join('; ')+'</div>':''}
      <button class="bigbtn ghost" style="margin-top:8px" onclick="showBundle('${id}')">view bundle JSON</button>
    </div>`;
}
async function showBundle(id){
  const j = await (await fetch("api/reviewer/items/"+id+"/bundle")).json();
  const pre = document.createElement('pre'); pre.className="bundle";
  pre.textContent = JSON.stringify(j.bundle, null, 1);
  $("#vout").appendChild(pre);
}
show('wb');
</script>
</body></html>"""

REVIEWER_PAGE = _HTML.replace("%%CSS%%", BRAND_CSS).replace("%%HEADER%%", HEADER_HTML)
