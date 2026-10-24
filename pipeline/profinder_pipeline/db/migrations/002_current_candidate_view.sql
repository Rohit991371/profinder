-- Iteration 5: ingest.candidate keeps the history of every extract run. This view is "what is current":
-- for each (university_name, entity_type) only the candidates of the NEWEST run that produced that entity type.
-- No table is changed; the view can be dropped and recreated freely (use a new 00N file to change it).
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
