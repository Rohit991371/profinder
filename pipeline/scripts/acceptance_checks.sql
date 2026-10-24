-- Acceptance checks for doc 03 section 5.3 (database part). Run against the Docker DB after a load:
--   docker exec -i profinder-pipeline-pg psql -U pipeline -d profinder_pipeline < scripts/acceptance_checks.sql
-- Every "expect" line says what a passing result looks like.

\echo '1. auto-accepted candidates that fail a blocking check (expect 0)'
select count(*) from ingest.current_candidate c
where c.release_id is not null and c.decision = 'auto_accept' and c.proposed_status = 'verified'
  and exists (select 1 from jsonb_array_elements(c.checks) e where (e->>'passed')::boolean = false and coalesce((e->>'blocking')::boolean, true));

\echo '2. human-approved candidates with a failed blocking check: each must have an override note (expect only rows you overrode on purpose)'
select c.university_name, c.payload->>'nameZh' as name, c.review_note from ingest.current_candidate c
where c.decision = 'human_approved'
  and exists (select 1 from jsonb_array_elements(c.checks) e where (e->>'passed')::boolean = false and coalesce((e->>'blocking')::boolean, true));

\echo '3. loaded professors without a SourceRecord (expect 0)'
select count(*) from "Professor" p
where p."dataSource" = 'official_faculty_page'
  and not exists (select 1 from "SourceRecord" s where s."entityType" = 'PROFESSOR' and s."entityId" = p.id);

\echo '4. invalid emails on pipeline-loaded professors (expect 0)'
select count(*) from "Professor" p
where p."dataSource" = 'official_faculty_page' and p.email is not null and p.email !~ '^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$';

\echo '5. profile URLs shared by several pipeline-loaded professors = directory pages (expect 0 rows)'
select "profileUrl", count(*) from "Professor" where "dataSource" = 'official_faculty_page' and "profileUrl" is not null
group by 1 having count(*) > 1;

\echo '6. duplicates by university + nameZh among pipeline-loaded professors (expect 0 rows)'
select "universityId", "nameZh", count(*) from "Professor" where "dataSource" = 'official_faculty_page' and "nameZh" is not null
group by 1, 2 having count(*) > 1;

\echo '7. changes recorded per type (every loaded row and every enrich has a ChangeLog row)'
select "changeType", count(*) from "ChangeLog" group by 1 order by 1;

\echo '8. row counts (compare with the numbers before the load) and user tables (expect 0 / unchanged)'
select 'University' as t, count(*) from "University" union all select 'Professor', count(*) from "Professor"
union all select 'Program', count(*) from "Program" union all select 'Scholarship', count(*) from "Scholarship"
union all select 'Student', count(*) from "Student" union all select 'Application', count(*) from "Application"
union all select 'SourceRecord', count(*) from "SourceRecord" union all select 'ProfessorSource', count(*) from "ProfessorSource";

\echo '9. universities that now have a real official URL'
select count(*) filter (where "officialUrl" not ilike '%campuschina.org%') as real_url, count(*) as total from "University";
