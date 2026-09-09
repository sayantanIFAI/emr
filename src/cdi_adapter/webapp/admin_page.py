from .theme import BRAND_CSS, HEADER_HTML

_HTML = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>CareFlow Polyclinic — admin / data inspector</title>
<style>%%CSS%%
.ad{max-width:1400px;margin:0 auto;padding:16px}
.ad h2{font-size:var(--fs-16);margin:18px 0 8px}
.grp{background:var(--surface);border:1px solid var(--line);border-radius:10px;margin-bottom:12px;overflow:hidden}
.grp>.hd{padding:9px 14px;background:var(--surface-2);font-weight:600;font-size:var(--fs-13);
  display:flex;justify-content:space-between}
.grp table{width:100%;border-collapse:collapse;font-size:var(--fs-13)}
.grp td{padding:6px 14px;border-top:1px solid var(--line)}
.grp td.n{text-align:right;font-variant-numeric:tabular-nums;width:90px}
.grp td.t{cursor:pointer;color:var(--blue-600)}
.grp td.t:hover{text-decoration:underline}
.zero{color:var(--muted)} .miss{color:#b91c1c}
.pane{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:12px;margin-bottom:12px}
.pane h3{margin:0 0 8px;font-size:var(--fs-14)}
.tbl{overflow:auto} .tbl table{border-collapse:collapse;font-size:11px;white-space:nowrap}
.tbl th,.tbl td{border:1px solid var(--line);padding:4px 7px;text-align:left;max-width:280px;overflow:hidden;text-overflow:ellipsis}
.tbl th{background:var(--surface-2);position:sticky;top:0}
.plist{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:10px}
.pcard{border:1px solid var(--line);border-radius:9px;padding:10px 12px;cursor:pointer}
.pcard:hover{border-color:var(--blue-600)}
.pcard b{font-size:var(--fs-14)} .pcard .row{font-size:11px;color:var(--muted);margin-top:4px}
.graph section{margin-bottom:10px}
.graph h4{margin:0 0 4px;font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)}
.ok{color:#15803d;font-weight:700}.bad{color:#b91c1c;font-weight:700}
.tabs{display:flex;gap:8px;margin-bottom:12px}
.tabs button{padding:7px 14px;border:1px solid var(--line-strong);background:var(--surface);border-radius:8px;cursor:pointer;font-size:var(--fs-13)}
.tabs button.on{background:var(--blue-600);color:#fff;border-color:var(--blue-600)}
code{background:var(--surface-2);padding:1px 5px;border-radius:4px}
</style></head><body>
%%HEADER%%
<div class="ad">
  <div class="tabs">
    <button id="tb-o" class="on" onclick="tab('o')">Table overview</button>
    <button id="tb-p" onclick="tab('p')">Promoted patients (canonical graph)</button>
  </div>
  <div id="v-o"></div>
  <div id="v-p" hidden></div>
  <div id="inspect"></div>
</div>
<script>
const $=s=>document.querySelector(s);
function tab(t){ $("#tb-o").classList.toggle("on",t==="o"); $("#tb-p").classList.toggle("on",t==="p");
  $("#v-o").hidden=t!=="o"; $("#v-p").hidden=t!=="p"; $("#inspect").innerHTML="";
  t==="o"?loadOverview():loadPatients(); }

async function loadOverview(){
  const j=await (await fetch("api/admin/overview")).json();
  $("#v-o").innerHTML = `<p class="zero">total rows across all tables: <b>${j.total_rows}</b></p>` +
    j.groups.map(g=>`
     <div class="grp"><div class="hd"><span>${g.group}</span><span>${g.group_rows} rows</span></div>
      <table>${g.tables.map(t=>`<tr>
        <td class="t" onclick="showTable('${t.table}')">${t.table}</td>
        <td class="n ${t.missing?'miss':(t.rows?'':'zero')}">${t.missing?'—':t.rows}</td>
      </tr>`).join("")}</table></div>`).join("");
}
async function showTable(name){
  const j=await (await fetch("api/admin/table/"+name+"?limit=25")).json();
  $("#inspect").innerHTML = `<div class="pane"><h3>${name} — latest ${j.count} rows</h3>
    <div class="tbl"><table><thead><tr>${j.columns.map(c=>`<th>${c}</th>`).join("")}</tr></thead>
    <tbody>${j.rows.map(r=>`<tr>${j.columns.map(c=>`<td title="${fmt(r[c])}">${fmt(r[c])}</td>`).join("")}</tr>`).join("")}</tbody>
    </table></div></div>`;
  $("#inspect").scrollIntoView({behavior:"smooth"});
}
function fmt(v){ if(v==null) return ""; if(typeof v==="object") return JSON.stringify(v).slice(0,120); return (""+v).slice(0,120); }

async function loadPatients(){
  const j=await (await fetch("api/admin/patients")).json();
  if(!j.patients.length){ $("#v-p").innerHTML='<p class="zero">No patients promoted to the canonical EMR yet. Approve an item in <code>/reviewer</code>.</p>'; return; }
  $("#v-p").innerHTML = `<div class="plist">${j.patients.map(p=>`
    <div class="pcard" onclick="showPatient('${p.id}')">
      <b>${p.name_full||'Unknown'}</b> <span class="zero">${p.gender||''} ${p.date_of_birth||''}</span>
      <div class="row">mpi ${p.mpi_id||'-'} · id ${p.id.slice(0,8)}</div>
      <div class="row">${p.ids} ids · ${p.encounters} enc · ${p.conditions} cond · ${p.observations} obs · ${p.meds} meds · ${p.labs} labs</div>
      <div class="row">${p.provenance} provenance rows · ${p.bundles} FHIR bundle(s)</div>
    </div>`).join("")}</div>`;
}
async function showPatient(pid){
  const g=await (await fetch("api/admin/patient/"+pid)).json();
  const sec=(title,rows,cols)=> !rows.length ? "" : `<section><h4>${title} (${rows.length})</h4>
    <div class="tbl"><table><thead><tr>${cols.map(c=>`<th>${c}</th>`).join("")}</tr></thead>
    <tbody>${rows.map(r=>`<tr>${cols.map(c=>`<td>${fmt(r[c])}</td>`).join("")}</tr>`).join("")}</tbody></table></div></section>`;
  const b=g.bundles[0];
  $("#inspect").innerHTML = `<div class="pane graph"><h3>${g.patient.name_full} — canonical graph</h3>
    ${sec("Identifiers", g.identifiers, ["system","value","use","assigner"])}
    ${sec("Encounters", g.encounters, ["klass","status","period_start","department"])}
    ${sec("Conditions", g.conditions, ["category","display","code_system","code","clinical_status"])}
    ${sec("Observations", g.observations, ["category","display","code","value_num","value_unit_ucum","value_string"])}
    ${sec("Medication orders", g.medication_orders, ["drug_text","dose_num","dose_unit_ucum","frequency_code","duration_days"])}
    ${sec("Lab results", g.lab_results, ["test_name","code","value_num","value_unit_ucum","abnormal_flag"])}
    ${sec("Diagnostic reports", g.diagnostic_reports, ["category","display","status","conclusion"])}
    ${sec("Procedures", g.procedures, ["display","code","status"])}
    ${sec("Allergies", g.allergies, ["substance_display","category","criticality"])}
    ${sec("Documents", g.documents, ["document_type","title","mime_type","source"])}
    ${sec("AI events", g.ai_events, ["task","model_id","human_review_status","reviewed_by"])}
    ${sec("AI verifications", g.verifications, ["target_kind","decision","reviewer","final_resource_table"])}
    ${sec("Provenance", g.provenance, ["target_table","activity","agent_type","agent_id","model_id"])}
    ${sec("Audit", g.audit, ["actor_user","actor_role","action","resource_table","occurred_at"])}
    <section><h4>Generated ABDM bundle</h4>${ b ? `<p>${b.artifact} · <b>${b.status}</b> ·
      validator <code>${b.validator}</code> · <span class="${b.validation_ok?'ok':'bad'}">${b.validation_ok?'CONFORMS':'HAS ERRORS'}</span>
      · ${b.entries} entries · ${b.ig_package}</p>` : '<p class="zero">no bundle</p>' }</section>
  </div>`;
  $("#inspect").scrollIntoView({behavior:"smooth"});
}
tab('o');
</script></body></html>"""

ADMIN_PAGE = _HTML.replace("%%CSS%%", BRAND_CSS).replace("%%HEADER%%", HEADER_HTML)
