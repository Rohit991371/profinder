-- Iteration 5.4: QA sample bookkeeping (doc 02 section 5.3). Additive only; the view is recreated so it exposes the new columns.
ALTER TABLE ingest.candidate ADD COLUMN IF NOT EXISTS qa_sample boolean NOT NULL DEFAULT false;
ALTER TABLE ingest.candidate ADD COLUMN IF NOT EXISTS qa_result text CHECK (qa_result IS NULL OR qa_result IN ('ok','error'));

CREATE OR REPLACE VIEW ingest.current_candidate AS
WITH run_rank AS (
    SELECT c.university_name, c.entity_type, c.run_id, max(r.started_at) AS started_at
    FROM ingest.candidate c
    JOIN ingest.run r USING (run_id)
    GROUP BY c.university_name, c.entity_type, c.run_id
), newest AS (
    SELECT DISTINCT ON (university_name, entity_type) university_name, entity_type, run_id
    FROM run_rank
    ORDER BY university_name, entity_type, started_at DESC, run_id
)
SELECT c.*
FROM ingest.candidate c
JOIN newest n
  ON n.run_id = c.run_id
 AND n.entity_type = c.entity_type
 AND n.university_name IS NOT DISTINCT FROM c.university_name;
