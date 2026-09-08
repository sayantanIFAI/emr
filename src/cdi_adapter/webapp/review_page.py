from .theme import BRAND_CSS, HEADER_HTML

_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>CareFlow Polyclinic — review queue</title>
<style>%%CSS%%</style>
</head>
<body>
%%HEADER%%
<div class="rv-wrap">
  <aside class="rv-list" id="list"></aside>
  <section class="rv-detail" id="detail">
    <p class="empty">Select a held fact on the left to review it against the source document.</p>
  </section>
</div>
<script>
const $=s=>document.querySelector(s);
let TASKS=[], CUR=null;

async function load(){
  const j=await (await fetch("api/review/tasks")).json();
  TASKS=j.tasks||[];
  const n=TASKS.reduce((a,t)=>a+t.facts.length,0);
  let html='<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">'
    +'<b>Held facts</b><span class="muted" style="font-size:12px">'+n+' in '+TASKS.length+' task(s)</span></div>';
  if(!TASKS.length){ html+='<p class="empty">Queue is empty — every fact is governed.</p>'; }
  else html+=TASKS.map(t=>
    '<div class="rv-task"><h3>'+esc(t.kind)+(t.doc_type?" · "+esc(t.doc_type):"")
    +' <span class="pill">p'+t.priority+'</span></h3>'
    +t.facts.map(f=>
      '<div class="rv-fact" data-id="'+f.fact_id+'" onclick="pick(\''+f.fact_id+'\')">'
      +'<div><b>'+esc(f.fact_type)+'</b> — '+esc(f.text||"")+'</div>'
      +'<div style="margin-top:4px"><span class="pill '+(f.confidence<0.6?'err':f.confidence<0.85?'warn':'')+'">conf '+f.confidence.toFixed(2)+'</span> '
      +(f.code?'<span class="pill blue">'+sys(f.code_system)+' '+f.code+'</span>':'<span class="pill warn">uncoded</span>')
      +'</div></div>').join("")
    +'</div>').join("");
  $("#list").innerHTML=html;
}

async function pick(fid){
  CUR=fid;
  document.querySelectorAll(".rv-fact").forEach(e=>e.classList.toggle("sel", e.dataset.id===fid));
  const d=await (await fetch("api/review/facts/"+fid)).json();
  const bb=(d.evidence_bbox_union&&d.evidence_bbox_union[0])||null;
  const W=600;
  let overlay="";
  if(bb && d.page_width){
    const s=W/d.page_width;
    overlay='<div class="bbox" style="left:'+(bb[0]*s)+'px;top:'+(bb[1]*s)+'px;width:'+((bb[2]-bb[0])*s)+'px;height:'+((bb[3]-bb[1])*s)+'px"></div>';
  }
  const md=d.medication_detail||{};
  let rows='<tr><th>Fact type</th><td>'+esc(d.fact_type)+'</td></tr>'
    +'<tr><th>Text (as read)</th><td><b>'+esc(d.local_text||"")+'</b></td></tr>'
    +'<tr><th>OCR span</th><td>'+((d.extracted_text||[]).map(esc).join("<br>")||'<span class="muted">—</span>')+'</td></tr>'
    +'<tr><th>OCR blocks</th><td>'+((d.ocr_blocks||[]).map(b=>esc(b.text)+' <span class="muted">('+b.conf.toFixed(2)+')</span>').join("<br>")||'—')+'</td></tr>'
    +'<tr><th>Proposed code</th><td>'+(d.code?'<code class="k">'+sys(d.code_system)+'</code> '+d.code+' — '+esc(d.code_display||""):'<span class="muted">uncoded (local only)</span>')+'</td></tr>'
    +'<tr><th>Value</th><td class="tab-nums">'+(d.value_num!=null?d.value_num+" "+(d.value_unit_ucum||""):esc(d.value_text||"—"))+' '+(d.abnormal_flag?'<span class="pill warn">'+d.abnormal_flag+'</span>':'')+'</td></tr>';
  if(d.medication_detail) rows+='<tr><th>Dose / freq / route</th><td>'
    +((md.dose_num!=null?md.dose_num:(md.strength_num!=null?md.strength_num:'?'))+' '+(md.dose_unit_ucum||md.strength_unit||'')
      +' · '+(md.frequency_code||md.frequency_per_day||'?')+' · '+(md.route||'?'))+'</td></tr>';
  rows+='<tr><th>Confidence</th><td class="tab-nums">'+d.confidence_overall.toFixed(3)+'</td></tr>'
    +'<tr><th>Rule findings</th><td>'+findings(d.review_note)+'</td></tr>';

  $("#detail").innerHTML=
    '<div style="display:flex;gap:22px;flex-wrap:wrap">'
    +'<div class="imgwrap" style="width:'+W+'px">'
    +(d.page_image_url?'<img src="'+d.page_image_url+'" style="width:'+W+'px" alt="source page"/>':'<p class="empty">no page image</p>')
    +overlay+'</div>'
    +'<div style="flex:1;min-width:320px">'
    +'<table class="data">'+rows+'</table>'
    +'<label class="fld">Corrected text (optional)</label>'
    +'<input type="text" id="c_text" value="'+esc(d.local_text||"")+'"/>'
    +'<div style="display:flex;gap:8px;margin:8px 0">'
    +'<input type="text" id="c_val" placeholder="value" value="'+(d.value_num!=null?d.value_num:'')+'" style="width:90px"/>'
    +'<input type="text" id="c_unit" placeholder="unit (UCUM)" value="'+esc(d.value_unit_ucum||'')+'" style="width:120px"/>'
    +'<input type="text" id="c_code" placeholder="code" value="'+esc(d.code||'')+'" style="width:110px"/>'
    +'<input type="text" id="c_sys" placeholder="system" value="'+esc(d.code_system||'')+'" style="flex:1"/></div>'
    +'<div style="display:flex;gap:10px;margin-top:8px">'
    +'<button class="btn btn-ok" onclick="decide(\'accept\')">Accept as-is</button>'
    +'<button class="btn btn-primary" onclick="decide(\'correct\')">Save correction</button>'
    +'<button class="btn btn-danger" onclick="decide(\'reject\')">Reject</button></div>'
    +'<p class="muted" id="msg" style="margin-top:10px"></p></div></div>';
}

function findings(note){
  if(!note) return '<span class="muted">none</span>';
  return note.split(";").map(s=>{
    s=s.trim();
    const sev=/\[blocker\]/.test(s)?'blocker':/\[warn\]/.test(s)?'warn':'';
    return '<div class="finding '+sev+'">'+esc(s)+'</div>';
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
  if(r.ok){ await load(); $("#detail").innerHTML='<p class="empty">Saved. Pick the next fact.</p>'; }
}
const sys=s=>!s?'':s.includes("snomed")?"SNOMED":s.includes("loinc")?"LOINC":s.includes("icd")?"ICD-10":s;
const esc=s=>String(s==null?"":s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
load();
</script>
</body>
</html>
"""

REVIEW_PAGE = _HTML.replace("%%CSS%%", BRAND_CSS).replace("%%HEADER%%", HEADER_HTML)
