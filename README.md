# HuntBot 2.0

Python job-discovery, scoring, document-tailoring and application-assistance tools,
with a local FastAPI dashboard. This is a sanitized source snapshot, not a hosted
service. No personal resumes, accounts, browser sessions, API keys or job database
are included. The profile in `config/profile.yml` is fictional.

## Install and launch

Use Python 3.11 or newer. Run these commands from the extracted source directory:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[web,browser,test]'
python -m playwright install chromium
huntbot2 --help
huntbot2 init --project-dir .
huntbot2 doctor --project-dir .
```

`doctor` may warn about missing optional services or documents. This snapshot's
packaging declares only the top-level package, so use an **editable install** and
run from this source directory; a non-editable wheel is not verified.

Launch the dashboard on loopback only:

```bash
export HUNTBOT2_DATA_ROOT="$PWD"
export HUNTBOT2_DB_PATH="$PWD/data/huntbot2.db"
export HUNTBOT2_SUBMIT_POLICY=stop_before_submit
export HUNTBOT2_SCHEDULE_ENABLED=false
export HUNTBOT2_LLM_ENABLED=false
uvicorn huntbot2.web.app:app --host 127.0.0.1 --port 8095
```

Open <http://127.0.0.1:8095>. Keep the dashboard and any browser-control services
private; do not expose them directly to the Internet. Use your own login/password
when creating a dashboard account. Configure your own cockpit authentication
(`HUNTBOT2_COCKPIT_AUTH_TOKEN`, or `HUNTBOT2_COCKPIT_AUTH_USER` and
`HUNTBOT2_COCKPIT_AUTH_PASSWORD`) before allowing other machines to connect.
Do not enable `HUNTBOT2_COCKPIT_AUTH_BYPASS`.

## Configure your own data

- Replace `config/profile.yml` with truthful preferences. Its existing keys form
  the example schema; some optional profile fields are consumed from raw YAML.
- Supply your own `config/master_resume.md` for tailoring. Optional personal
  cover-letter and DOCX templates are not included. Review fallback output: it
  is a draft, not a finished application, and may include `[Your name]`.
- Log into job sites using your own authorized accounts. Browser profiles,
  cookies, learned answers and generated documents are sensitive local data.
- AI integration is optional. Configure your own provider keys in dashboard
  Settings or a private local `.env`. The LLM client supports
  `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` and `OPENROUTER_API_KEY`; choose the
  provider and models appropriate to your account. Never commit credentials.
  AI features may send profile, resume and job text to your selected provider.
- Keep `.env`, databases, browser profiles and generated outputs out of public
  shares. This snapshot intentionally excludes deployment and personal config.

## CLI examples

These names match the CLI in this snapshot (see `huntbot2 --help`):

```bash
huntbot2 discover-demo --project-dir .
huntbot2 shortlist --project-dir .
huntbot2 tailor-demo --project-dir .
huntbot2 queue-list --project-dir .
huntbot2 pipeline-now --project-dir . --dry-run --submit-policy never_submit
```

The demo discovery command uses included HTML fixtures. The pipeline/dashboard
can use live discovery; a dry-run is **not** a guarantee of no network access for
other stages. Inspect configuration before starting a pipeline. Do not run these
commands against another person's data directory.

## Approval and automatic-apply safety

Leave **automatic apply disabled**: keep `HUNTBOT2_SCHEDULE_ENABLED=false`, do
not create enabled calendar entries, and do not use scheduler/calendar “Run now”
or automatic modes. A forced schedule run can bypass the schedule-enabled flag;
calendar automatic mode can change submit policy.

Keep `HUNTBOT2_SUBMIT_POLICY=stop_before_submit` (CLI: `never_submit`) and retain
`--dry-run` for initial review. Live application filling may still interact with
sites even when final submission is blocked. Review every field and document;
AI-generated answers can be wrong.

Final submit requires an allowing policy plus a matching submit token. The web
pipeline also checks a valid human checkpoint before supplying that token. CLI
and other entry points do **not** necessarily enforce the same checkpoint, and
not all displayed pacing/guardrail settings apply to every path. Do not treat
this as an independently audited safety boundary. Before any intentional live
submission, set your own strong private `HUNTBOT2_SUBMIT_TOKEN`; this snapshot
retains a public legacy fallback for compatibility, not as a secure credential.
Never enable allowing policies just to try the UI. Respect site rules and limits.

## Development checks

```bash
python -m compileall -q huntbot2 tests
python -m pytest tests
```

These are instructions, not a claim that browser automation, provider APIs or
live submissions have been tested. No deployment or production-readiness claim
is made.
