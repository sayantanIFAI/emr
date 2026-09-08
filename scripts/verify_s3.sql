\pset pager off
\echo ==== S2  doc_classification  (real Qwen2.5-VL-7B-Instruct) ====
SELECT substr(sd.original_filename,1,30) AS file,
       dc.doc_type, dc.is_handwritten AS hw, dc.language AS lang, dc.confidence AS conf
FROM doc_classification dc
JOIN source_document sd ON sd.id = dc.document_id
ORDER BY sd.original_filename;

\echo
\echo ==== S3  ocr_block  (RapidOCR) - first lab report, first 12 lines ====
SELECT dp.page_no AS pg, ob.reading_order AS ro, round(ob.ocr_conf,2) AS conf,
       ob.bbox, substr(ob.text,1,48) AS text
FROM ocr_block ob
JOIN document_page dp ON dp.id = ob.page_id
JOIN source_document sd ON sd.id = dp.document_id
WHERE sd.original_filename LIKE 'LG-80000_lab%'
ORDER BY dp.page_no, ob.reading_order
LIMIT 12;

\echo
\echo ==== pipeline_run  (S1 -> S2 -> S3) ====
SELECT stage, status, count(*) AS n,
       round(avg(extract(epoch FROM ended_at - started_at))::numeric,1) AS avg_s
FROM pipeline_run
GROUP BY stage, status
ORDER BY stage, status;

\echo
\echo ==== per-document block counts ====
SELECT substr(sd.original_filename,1,30) AS file, dc.doc_type,
       count(ob.id) AS ocr_blocks,
       round(avg(ob.ocr_conf),3) AS mean_conf
FROM source_document sd
JOIN doc_classification dc ON dc.document_id = sd.id
JOIN document_page dp ON dp.document_id = sd.id
LEFT JOIN ocr_block ob ON ob.page_id = dp.id
GROUP BY sd.original_filename, dc.doc_type
ORDER BY sd.original_filename;

\echo
\echo ==== totals ====
SELECT (SELECT count(*) FROM source_document)   AS documents,
       (SELECT count(*) FROM doc_classification) AS classifications,
       (SELECT count(*) FROM ocr_block)          AS ocr_blocks,
       (SELECT count(*) FROM audit_log)          AS audit_events;
