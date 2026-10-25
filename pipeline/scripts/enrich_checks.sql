-- Iteration 6.1 acceptance checks for an enrichment load (read-only SELECTs). Run:
--   docker exec -i profinder-pipeline-pg psql -U pipeline -d profinder_pipeline < scripts/enrich_checks.sql
\echo '1. professors enriched by OpenAlex, by dataSource (must only be official_faculty_page / official_import)'
SELECT p."dataSource", count(*) FROM "ChangeLog" c JOIN "Professor" p ON p.id = c."entityId"
WHERE c."changeType" = 'PIPELINE_ENRICHED' AND c."afterJson" LIKE '%"_release": "enr\_%' GROUP BY 1 ORDER BY 1;
\echo '2. fields written by enrichment ChangeLog rows (must be only openAlexId, orcid, publicationCount)'
SELECT k, count(*) FROM "ChangeLog" c, LATERAL json_object_keys(c."afterJson"::json) k
WHERE c."changeType" = 'PIPELINE_ENRICHED' AND c."afterJson" LIKE '%"_release": "enr\_%' AND k NOT LIKE '\_%' GROUP BY 1 ORDER BY 1;
\echo '3. enriched professors without a ProfessorSource(openalex) row (must be 0)'
SELECT count(*) FROM "ChangeLog" c WHERE c."changeType" = 'PIPELINE_ENRICHED' AND c."afterJson" LIKE '%"_release": "enr\_%'
AND NOT EXISTS (SELECT 1 FROM "ProfessorSource" s WHERE s."professorId" = c."entityId" AND s.source = 'openalex');
\echo '4. Professor rows created by an enrichment (must be 0)'
SELECT count(*) FROM "ChangeLog" WHERE "changeType" = 'PIPELINE_CREATED' AND "afterJson" LIKE '%"_release": "enr\_%';
\echo '5. status of enriched professors unchanged by enrichment (must be 0 rows with a status key in the log)'
SELECT count(*) FROM "ChangeLog" c WHERE c."afterJson" LIKE '%"_release": "enr\_%' AND (c."afterJson"::json)::jsonb ? 'dataStatus';
\echo '6. row counts and user tables'
SELECT (SELECT count(*) FROM "Professor") professors, (SELECT count(*) FROM "Student") students, (SELECT count(*) FROM "Application") applications;
