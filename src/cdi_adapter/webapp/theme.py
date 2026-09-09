"""Shared brand system for CareFlow Polyclinic — white + blue, healthcare-clean.

One place for the palette tokens, type scale, spacing, component styles and the
inline-SVG logo, used by both the upload page and the review console.
"""

LOGO_SVG = (
    '<svg width="34" height="34" viewBox="0 0 40 40" fill="none" '
    'xmlns="http://www.w3.org/2000/svg" aria-hidden="true">'
    '<defs><linearGradient id="cfLogo" x1="0" y1="0" x2="40" y2="40" '
    'gradientUnits="userSpaceOnUse">'
    '<stop stop-color="#2f6bff"/><stop offset="1" stop-color="#1d4ed8"/>'
    '</linearGradient></defs>'
    '<rect x="1.5" y="1.5" width="37" height="37" rx="11" fill="url(#cfLogo)"/>'
    '<path d="M7 23.2h5.4l2.3-6.2 3.7 12.4 2.9-9 1.7 3.6H27" stroke="#fff" '
    'stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/>'
    '<path d="M26.5 12.8c3.4 0 6 2.5 6 5.7 0 3.9-4.2 7-9 10.7" stroke="#bcd3ff" '
    'stroke-width="2.2" stroke-linecap="round" opacity="0.9"/>'
    '</svg>'
)

# rendered into <header> on every page
HEADER_HTML = f"""
<header class="cf-header">
  <a class="cf-brand" href="/">
    {LOGO_SVG}
    <span class="cf-wordmark"><b>CareFlow</b> Polyclinic</span>
  </a>
  <nav class="cf-nav">
    <a href="/">Upload</a>
    <a href="/review">Review queue</a>
  </nav>
</header>
"""

BRAND_CSS = """
:root{
  --blue-700:#1e40af; --blue-600:#1d4ed8; --blue-500:#2f6bff; --blue-100:#dbe6ff;
  --blue-50:#eef4ff; --blue-tint:#f5f8ff;
  --page:#f4f7fc; --surface:#ffffff; --surface-2:#f7f9fd;
  --ink:#0f1b34; --muted:#5b6b8c; --faint:#93a1bd;
  --line:#e4eaf4; --line-strong:#cfd9ec;
  --ok:#0f9d58; --ok-bg:#e9f7ef; --ok-line:#bfe6cf;
  --warn:#b7791f; --warn-bg:#fdf5e6; --warn-line:#f0dcae;
  --err:#d64545; --err-bg:#fdeeee; --err-line:#f2cccc;
  --ring:0 0 0 3px rgba(29,78,216,.30);
  --sh-sm:0 1px 2px rgba(16,42,90,.05), 0 1px 3px rgba(16,42,90,.06);
  --sh-md:0 4px 12px rgba(16,42,90,.08), 0 2px 4px rgba(16,42,90,.05);
  --r-pill:999px; --r-ctl:10px; --r-card:14px;
  --fs-12:12px; --fs-13:13px; --fs-14:14px; --fs-16:16px; --fs-20:20px; --fs-26:26px;
  --sp-1:4px; --sp-2:8px; --sp-3:12px; --sp-4:16px; --sp-5:20px; --sp-6:24px; --sp-8:32px;
  --font:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
}
*{box-sizing:border-box}
html,body{margin:0}
body{
  background:var(--page); color:var(--ink); font-family:var(--font);
  font-size:var(--fs-14); line-height:1.55; -webkit-font-smoothing:antialiased;
}
.tab-nums{font-variant-numeric:tabular-nums}
a{color:var(--blue-600);text-decoration:none}
a:hover{text-decoration:underline}

/* header */
.cf-header{
  display:flex;align-items:center;justify-content:space-between;
  padding:12px 24px;background:var(--surface);border-bottom:1px solid var(--line);
  position:sticky;top:0;z-index:20;
}
.cf-brand{display:flex;align-items:center;gap:10px;color:var(--ink)}
.cf-brand:hover{text-decoration:none}
.cf-wordmark{font-size:var(--fs-16);letter-spacing:.2px;color:var(--muted)}
.cf-wordmark b{color:var(--ink);font-weight:700}
.cf-nav{display:flex;gap:18px;font-size:var(--fs-13)}
.cf-nav a{color:var(--muted);padding:6px 4px;border-radius:8px}
.cf-nav a:hover{color:var(--blue-600);text-decoration:none}

/* layout */
.cf-main{max-width:1000px;margin:0 auto;padding:24px 24px 64px}
.cf-sub{color:var(--muted);font-size:var(--fs-13);margin:2px 0 0}

/* card */
.card{
  background:var(--surface);border:1px solid var(--line);border-radius:var(--r-card);
  box-shadow:var(--sh-sm);padding:20px;margin-bottom:18px;
}
.card > h2{margin:0 0 4px;font-size:var(--fs-16);font-weight:650}
.card > .hint{color:var(--muted);font-size:var(--fs-13);margin:0 0 14px}

/* dropzone */
.dropzone{
  border:2px dashed var(--line-strong);background:var(--blue-tint);
  border-radius:16px;padding:34px 20px;text-align:center;cursor:pointer;
  transition:border-color .15s ease, background .15s ease;
}
.dropzone:hover,.dropzone:focus-visible{border-color:var(--blue-500);background:var(--blue-50);outline:none}
.dropzone.drag{border-color:var(--blue-600);background:var(--blue-100)}
.dropzone svg{display:block;margin:0 auto 10px}
.dropzone .dz-title{font-weight:600}
.dropzone .dz-sub{color:var(--muted);font-size:var(--fs-13);margin-top:4px}
.dz-files{margin-top:12px;font-size:var(--fs-13);color:var(--muted)}

/* inputs */
label.fld{display:block;font-size:var(--fs-12);color:var(--muted);margin:14px 0 4px;
  text-transform:uppercase;letter-spacing:.05em}
input[type=text]{
  width:100%;padding:9px 12px;font-size:var(--fs-14);color:var(--ink);
  background:var(--surface);border:1px solid var(--line-strong);border-radius:var(--r-ctl);
}
input[type=text]:focus-visible{outline:none;border-color:var(--blue-500);box-shadow:var(--ring)}

/* buttons */
.btn{
  display:inline-flex;align-items:center;gap:8px;min-height:40px;padding:9px 18px;
  font-size:var(--fs-14);font-weight:650;border-radius:var(--r-ctl);border:1px solid transparent;
  cursor:pointer;transition:background .12s ease, box-shadow .12s ease;
}
.btn:focus-visible{outline:none;box-shadow:var(--ring)}
.btn[disabled]{opacity:.5;cursor:default}
.btn-primary{background:var(--blue-600);color:#fff}
.btn-primary:hover{background:var(--blue-700)}
.btn-ghost{background:var(--surface);color:var(--ink);border-color:var(--line-strong)}
.btn-ghost:hover{background:var(--surface-2)}
.btn-ok{background:var(--ok);color:#fff}
.btn-danger{background:var(--err);color:#fff}
.btn-sm{min-height:32px;padding:5px 12px;font-size:var(--fs-13)}

/* pills */
.pill{display:inline-flex;align-items:center;gap:5px;font-size:var(--fs-12);font-weight:600;
  padding:2px 9px;border-radius:var(--r-pill);border:1px solid var(--line-strong);
  color:var(--muted);background:var(--surface-2);white-space:nowrap}
.pill.blue{color:var(--blue-700);background:var(--blue-50);border-color:var(--blue-100)}
.pill.ok{color:var(--ok);background:var(--ok-bg);border-color:var(--ok-line)}
.pill.warn{color:var(--warn);background:var(--warn-bg);border-color:var(--warn-line)}
.pill.err{color:var(--err);background:var(--err-bg);border-color:var(--err-line)}

/* progress rows */
.prow{display:flex;align-items:center;gap:10px;padding:10px 0;border-bottom:1px solid var(--line);font-size:var(--fs-13)}
.prow:last-child{border-bottom:0}
.prow .fn{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.prow .ico{width:18px;text-align:center;color:var(--faint)}
.prow .ico.ok{color:var(--ok)} .prow .ico.err{color:var(--err)}
.spin{width:13px;height:13px;border:2px solid var(--line-strong);border-top-color:var(--blue-600);
  border-radius:50%;display:inline-block;animation:cfspin .8s linear infinite}
@keyframes cfspin{to{transform:rotate(360deg)}}
@media (prefers-reduced-motion:reduce){.spin{animation-duration:0s}}

/* patient banner */
.patient{display:flex;align-items:center;gap:16px;background:var(--blue-50);
  border:1px solid var(--blue-100);border-radius:var(--r-card);padding:16px 18px}
.avatar{width:46px;height:46px;border-radius:12px;background:var(--blue-600);color:#fff;
  display:flex;align-items:center;justify-content:center;font-weight:700;font-size:var(--fs-16)}
.patient .pid{font-size:var(--fs-20);font-weight:700;letter-spacing:.4px}
.patient .meta{color:var(--muted);font-size:var(--fs-13);margin-top:2px}
.patient .meta b{color:var(--ink);font-weight:600}

/* details / bundle cards */
details.bundle{border:1px solid var(--line);border-radius:12px;margin-top:10px;background:var(--surface)}
details.bundle > summary{cursor:pointer;padding:12px 14px;display:flex;gap:9px;align-items:center;flex-wrap:wrap;font-weight:600}
details.bundle > summary::-webkit-details-marker{display:none}
details.bundle .body{padding:0 14px 14px}
code.k{color:var(--blue-700);font-weight:600}

/* tables */
table.data{width:100%;border-collapse:collapse;font-size:var(--fs-12);margin:8px 0 12px}
table.data th,table.data td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}
table.data th{color:var(--muted);font-weight:600}

pre.json{background:#0d1836;color:#dce7ff;border-radius:10px;padding:12px;overflow:auto;
  max-height:440px;font-size:var(--fs-12);line-height:1.5;
  font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}

.muted{color:var(--muted)} .err-txt{color:var(--err)}
.empty{color:var(--muted);text-align:center;padding:22px}

/* page title */
.cf-pagetitle{max-width:1000px;margin:0 auto;padding:20px 24px 4px}
.cf-pagetitle h1{margin:0;font-size:var(--fs-20);font-weight:700;letter-spacing:.2px}
.cf-pagetitle .cf-sub{margin-top:3px}

/* Generate EMR — stage tabs + grid */
.emr-tabs{display:flex;gap:6px;flex-wrap:wrap;margin:2px 0 14px}
.emr-tab{display:flex;align-items:center;gap:7px;padding:7px 12px;border:1px solid var(--line);
  border-radius:var(--r-pill);font-size:var(--fs-13);font-weight:600;color:var(--muted);
  background:var(--surface-2);cursor:default}
.emr-tab.active{color:var(--blue-700);background:var(--blue-50);border-color:var(--blue-100)}
.emr-tab.done{color:var(--ok);background:var(--ok-bg);border-color:var(--ok-line)}
.emr-tab .dot{width:8px;height:8px;border-radius:50%;background:currentColor;opacity:.55}
.emr-tab.done .dot{opacity:1}

.emr-grid{width:100%;border-collapse:collapse;font-size:var(--fs-13)}
.emr-grid th{font-size:var(--fs-12);color:var(--muted);font-weight:600;text-align:center;padding:6px 4px}
.emr-grid th.doc{text-align:left}
.emr-grid td{padding:8px 4px;border-top:1px solid var(--line);text-align:center;vertical-align:middle}
.emr-grid td.doc{text-align:left}
.emr-grid td.doc .fn{font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:280px}
.emr-grid td.doc .sub{color:var(--muted);font-size:var(--fs-12)}
.cell{display:inline-flex;align-items:center;justify-content:center;width:24px;height:24px;border-radius:7px}
.cell.pending{color:var(--faint)}
.cell.running{color:var(--blue-600)}
.cell.done{color:var(--ok);background:var(--ok-bg)}
.cell.error{color:var(--err);background:var(--err-bg)}
.tick{width:15px;height:15px}

/* inline extraction editor: image left, table right */
.editor{display:grid;grid-template-columns:minmax(300px,460px) 1fr;gap:20px;align-items:start}
.editor .scan{position:sticky;top:70px}
.editor .imgwrap{width:100%}
.doc-switch{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px}
.doc-switch button{padding:6px 12px;border:1px solid var(--line);border-radius:var(--r-pill);
  background:var(--surface-2);font-size:var(--fs-13);font-weight:600;color:var(--muted);cursor:pointer}
.doc-switch button.active{color:var(--blue-700);background:var(--blue-50);border-color:var(--blue-100)}
.ftable{width:100%;border-collapse:collapse;font-size:var(--fs-13)}
.ftable th{font-size:var(--fs-12);color:var(--muted);font-weight:600;text-align:left;padding:6px 8px;border-bottom:1px solid var(--line)}
.ftable td{padding:5px 8px;border-bottom:1px solid var(--line);vertical-align:middle}
.ftable tr.drop{opacity:.4;text-decoration:line-through}
.ftable tr:hover td{background:var(--blue-tint)}
.ftable input[type=text]{padding:5px 7px;font-size:var(--fs-13);border-radius:7px}
.ftable input.sm{width:70px}
.ftable .conf{font-size:var(--fs-12);color:var(--muted)}
.chk{width:16px;height:16px;accent-color:var(--blue-600);cursor:pointer}
@media (max-width:860px){.editor{grid-template-columns:1fr}.editor .scan{position:static}}

/* review console */
.rv-wrap{display:grid;grid-template-columns:minmax(320px,600px) 1fr;gap:0;min-height:calc(100vh - 58px)}
.rv-list{border-right:1px solid var(--line);overflow:auto;padding:16px;background:var(--surface)}
.rv-detail{overflow:auto;padding:20px 24px}
.rv-task h3{font-size:var(--fs-12);color:var(--muted);text-transform:uppercase;letter-spacing:.05em;margin:14px 0 6px}
.rv-fact{padding:9px 11px;border:1px solid var(--line);border-radius:10px;margin-bottom:6px;cursor:pointer;background:var(--surface)}
.rv-fact:hover{border-color:var(--blue-500)}
.rv-fact.sel{border-color:var(--blue-600);background:var(--blue-50)}
.imgwrap{position:relative;display:inline-block;border:1px solid var(--line);border-radius:10px;overflow:hidden;max-width:100%;background:var(--surface-2)}
.imgwrap img{display:block;max-width:100%}
.bbox{position:absolute;border:2px solid var(--blue-600);background:rgba(29,78,216,.12);
  box-shadow:0 0 0 9999px rgba(15,27,52,.45)}
.finding{font-size:var(--fs-12);padding:4px 8px;border-radius:6px;background:var(--surface-2);
  border:1px solid var(--line);margin:3px 0}
.finding.blocker{border-color:var(--err-line);color:var(--err);background:var(--err-bg)}
.finding.warn{border-color:var(--warn-line);color:var(--warn);background:var(--warn-bg)}
@media (max-width:820px){.rv-wrap{grid-template-columns:1fr}.rv-list{border-right:0;border-bottom:1px solid var(--line)}}
"""

UPLOAD_ICON = (
    '<svg width="34" height="34" viewBox="0 0 24 24" fill="none" '
    'stroke="#2f6bff" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
    '<path d="M12 16V4M12 4l-4 4M12 4l4 4"/>'
    '<path d="M4 14v4a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-4"/></svg>'
)
