# Rollback check on PostgreSQL (open item c) — run once before the handover, Docker DB only

Rollback was only tested on a sqlite stand-in. This is the one round trip on the real engine. Nothing here touches anything but the Docker DB.
Replace `<ID>` with a loaded release id or an `enr_...` enrichment id. PowerShell syntax; Git Bash: `export VAR=value`.

1. Backup:  `docker exec profinder-pipeline-pg pg_dump -U pipeline profinder_pipeline > backup_before_rollback_check.sql`
2. Counts before (save the output):
   `docker exec -it profinder-pipeline-pg psql -U pipeline -d profinder_pipeline -c "select (select count(*) from \"Professor\") prof, (select count(*) from \"ProfessorSource\") psrc, (select count(*) from \"SourceRecord\") srec, (select count(*) from \"ChangeLog\") clog, (select count(*) from \"ProfessorResearchArea\") pra, (select count(*) from \"Student\") stu, (select count(*) from \"Application\") app;"`
3. Dry-run:  `python -m profinder_pipeline rollback --release <ID>`   (for an enrichment: `python -m profinder_pipeline enrich rollback --enrich <ID>`)
   Read the `plan:` line and the `kept:` lines.
4. Apply:   `$env:CONFIRM_ROLLBACK = "<ID>"` then the same command with `--apply`.
5. Counts again (same query): the entity tables are back to their pre-load numbers; `Student` and `Application` unchanged; `ChangeLog` has MORE rows (the `PIPELINE_ROLLED_BACK` rows: nothing is deleted from the log except unreviewed proposals of that release).
6. Run the rollback dry-run a second time: `plan:` must be empty. Then load again (dry-run first) to prove the round trip: the plan equals the first load.
7. If anything differs: `psql ... < backup_before_rollback_check.sql` into a fresh DB, and paste the plan lines back.
