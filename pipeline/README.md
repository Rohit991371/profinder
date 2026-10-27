# ProFinder data pipeline

A Python pipeline that collects **sourced, validated** data about Chinese universities, computing faculty, programmes and scholarships and loads it into the ProFinder database **additively** — it adds rows and fills empty or placeholder fields, never deletes, never overwrites real values, and puts everything uncertain in front of a human first.

- Official page or no value: every fact must be on the university's own domain and found in the fetched page text.
- Dates and money are written only after a person approved them.
- Professors are never created from OpenAlex; OpenAlex only adds ids to existing official-sourced professors.
- Polite crawling: `robots.txt`, per-host delay that adapts to slow sites, a circuit breaker, a 30-day cache.
- Dry-run by default; real writes need an approved release bundle, `--apply` and an explicit `CONFIRM_LOAD=<release id>`.

Read **[OWNER-GUIDE.md](OWNER-GUIDE.md)** for architecture, running, review and approval, rollback and limitations. Design background: [docs/01-ARCHITECTURE.md](docs/01-ARCHITECTURE.md) and [docs/02-VERIFICATION-POLICY.md](docs/02-VERIFICATION-POLICY.md).

## Quick start

```bash
cd pipeline
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt -r requirements-render.txt
playwright install chromium
cp .env.example .env                                    # set database URLs and API keys locally; never commit
python -m pytest tests -q
python -m profinder_pipeline doctor
python -m profinder_pipeline init-db                    # creates only the schema `ingest`

python -m profinder_pipeline batch run --only "Tsinghua University"      # fetch -> extract -> match for one university
python -m profinder_pipeline release build                               # draft release bundle + needs_review.csv
python -m profinder_pipeline review import --file output/releases/<folder>/needs_review.csv --apply   # after a person filled the CSV
python -m profinder_pipeline release approve --release <id> --by "<name>"
python -m profinder_pipeline load --release <id>                         # dry-run (default)
CONFIRM_LOAD=<id> python -m profinder_pipeline load --release <id> --apply
```

## Commands

| Group | Commands |
|---|---|
| Setup | `init-db`, `app-schema` and `seed-snapshot` (local test database only), `doctor` |
| Collect | `registry …`, `discover …`, `fetch …` |
| Extract | `extract run / show / site-learn / programs / report` |
| Match | `match universities / run / show` |
| Release | `release build / verify / qa-status / approve / import / list`, `review export / import` |
| Write | `load`, `rollback` |
| Extras | `enrich …` (OpenAlex ids), `audit legacy`, `batch run / status / report` |

`python -m profinder_pipeline <command> --help` shows the options of each command.

## Layout

`config/` (universities, allowed domains, per-site extraction rules, placeholders) · `profinder_pipeline/` (code by stage) · `scripts/` (scope guard, packer, SQL checks) · `tests/` · `docker/` (local Postgres) · `docs/`. The pipeline's working data (`inputs/`, `output/`, schema `ingest`) is not committed.

## Safety

Secrets only in environment variables or GitHub Secrets; logs show host names only. Hosts whose name contains `prod` are refused unless the owner sets an explicit allow variable. This repository part adds files only: `python scripts/check_scope.py` verifies that a change touches nothing outside `pipeline/**` and `.github/workflows/pipeline-*.yml`.

This is an engineering policy, not legal advice: confirm PIPL/GDPR requirements for publishing professors' public contact details.
