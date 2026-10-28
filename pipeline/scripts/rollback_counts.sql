-- Row counts before/after a load or rollback (read-only). Run:
--   Get-Content scripts/rollback_counts.sql -Raw | docker exec -i profinder-pipeline-pg psql -U pipeline -d profinder_pipeline
SELECT 'University' AS tbl, count(*) FROM "University"
UNION ALL SELECT 'Professor', count(*) FROM "Professor"
UNION ALL SELECT 'Program', count(*) FROM "Program"
UNION ALL SELECT 'Scholarship', count(*) FROM "Scholarship"
UNION ALL SELECT 'ProfessorResearchArea', count(*) FROM "ProfessorResearchArea"
UNION ALL SELECT 'ProfessorSource', count(*) FROM "ProfessorSource"
UNION ALL SELECT 'SourceRecord', count(*) FROM "SourceRecord"
UNION ALL SELECT 'ChangeLog', count(*) FROM "ChangeLog"
UNION ALL SELECT 'Student (must stay 0)', count(*) FROM "Student"
UNION ALL SELECT 'Application (must stay 0)', count(*) FROM "Application"
ORDER BY 1;
