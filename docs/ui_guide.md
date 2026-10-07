# Console guide

The PHI Engine Console is a web interface to the detection and redaction engine. Everything runs on this
machine; text is never sent to an external service.

## For everyone

### Redact text

1. Open **Redact text**.
2. Paste the text, or choose **Open .txt**. To see how it works, choose **Example**, which uses only fabricated data.
3. Under **What kind of text is this?**, pick the closest match. Choose *Phone call transcript* for speech-to-text, so
   that spoken numbers and dates are understood.
4. Under **How should identifiers be replaced?**, pick a style:

   | style | looks like | can it be reversed? |
   |---|---|---|
   | Numbered placeholders (default) | `[NAME_1]`, `[PHONE_1]` | yes, by authorised staff |
   | Category labels | `[NAME]` | no |
   | Keyed pseudonyms | `NAME-3f9a2c` | yes, by authorised staff |
   | Encrypted values | ciphertext | with the key |

5. Choose **Redact**, or press Ctrl/⌘ + Enter.

**Reading the result:**

- **Ready to share / Do not share yet.** A second, independent scanner re-checks the redacted copy. If it still
  finds an identifier, do not share the text. Review it by hand, or switch to *Category labels*.
- **Not processed.** If any check could not complete, the console produces **no** redacted copy. This is
  fail-closed behaviour: never share the original instead.
- **Redacted copy.** Copy or download it.
- **Highlighted original.** Your text with every finding marked and labelled. Click a finding to see why it was
  found. A dashed outline means the finding is flagged for review; it is still redacted. Switch on **Mask values**
  when someone else can see your screen.
- **Findings.** The same findings as a table.

**Known details (optional).** If you know who the text is about, enter their name, date of birth, IDs and so on.
The engine then finds those values reliably, including nicknames and spoken forms. The details are used for that
one request and then discarded.

### Batch files

Use **Batch files** to redact many documents at once.

1. Drop files onto the page:
   - `.txt`: one document per file.
   - `.jsonl`: one `{"text": "...", "id": "...", "modality": "..."}` record per line.
   - `.json`: a single object, or a list of them.
2. Choose **Redact** and wait for the progress bar to finish.
3. Download the zip:
   - `sanitized/`: the redacted text only.
   - `detections.jsonl`: positions and types, without any text.
   - `summary.csv`: counts per document.
4. Check `summary.csv` for any document that was **not** processed.

### Simple and Technical views

The switch at the top right changes how much detail you see:

- **Simple** shows plain-language results.
- **Technical** adds scores, thresholds and the sources they came from, recognizer IDs, timings, the evidence
  table, the conversation ID, and **what-if analysis**. What-if lets you try temporary configuration overrides for
  your own request only. The results are marked as what-if, and the deployed configuration never changes.

## Assurance pages

| page | what it is for |
|---|---|
| **Checks & evaluations** | Run the behavioural checklist, the prompt suite, dataset evaluations, performance tests, release gates, canaries, the monitoring baseline, the system card, or the test suite. Each job shows the **exact command**, so anyone can reproduce it from a terminal, plus a live log. Evaluating held-out *test* data needs the admin role and a written reason, and is recorded. |
| **Results** | Every stored run, with headline numbers and details: per-entity recall with confidence intervals, checklist breakdown, latency tables. Per-span files containing text are never shown. |
| **Release readiness** | The latest go/no-go decision and the verdict and reasons for each gate (G1–G9). |
| **Monitoring** | Run the canaries; see detection-rate alerts against the baseline, plus counters. Counts only, never text. |
| **Configuration** | Read-only view of what counts as PHI, recognizers, models (including whether their weight hashes are pinned), thresholds and the resolved configuration file. Also: validate every environment. **Reload engine** (admin only) re-reads `configs/phi` after a reviewed change has been deployed. |
| **Activity & audit** | The evaluation ledger, and (admin only) the console audit trail: who did what, with IDs and counts but never text. |

## For administrators

### Install and start

```bash
uv pip install --python .venv/bin/python -e '.[ui]'     # or: pip install -e '.[ui]'
scripts/build_ui.sh                                     # needs Node.js >= 20; builds frontend/ into harness_ui/static
python -m harness_ui serve                              # http://127.0.0.1:8765
```

Useful flags: `--port`, `--env dev|test|prod` (the engine configuration environment) and `--no-warm`. The engine
loads in the background at startup and runs its self-test; the header badge shows *starting*, *ready* or *failed*.
If the engine cannot start, the console refuses to process text and the Overview page shows the reason.

### Settings: `configs/ui/ui.yaml`

All settings are validated strictly at startup. `python -m harness_ui check` prints the effective settings.

| setting | meaning |
|---|---|
| `host`, `port` | Bind address. With `auth.mode: none` the console **refuses to start** on anything other than a loopback address. |
| `engine_env` | Engine configuration environment (`configs/phi/environments/`). |
| `auth.mode` | `none`: single local user. `token`: shared secret from `$PHI_UI_TOKEN`, at least 24 characters. `users`: named accounts with roles. |
| `roles.*` | Minimum role for each action. Roles are viewer < operator < admin. Defaults: analyse, batch and run jobs need **operator**; test-data evaluations, re-identification and the audit trail need **admin**. |
| `features.restore` | Set to `false` to remove re-identification from the console entirely. |
| `features.what_if` | Enables or disables what-if analysis. |
| `limits.*` | Text size, upload size, batch size, number of concurrent jobs. |
| `storage.*` | Where job logs, batch outputs and the audit trail are written. |

Environment variable overrides: `PHI_UI_HOST`, `PHI_UI_PORT`, `PHI_UI_ENV`, `PHI_UI_DEVICE`, `PHI_UI_AUTH_MODE`.
Secrets come only from the environment:

- `PHI_UI_TOKEN`: the access token in `token` mode.
- `PHI_UI_SESSION_SECRET`: at least 32 characters. If it is unset, sessions end when the console restarts.
- The engine's own keys (`PHI_VAULT_KEY`, `PHI_LOG_HMAC_KEY`, ...).

### Accounts (`auth.mode: users`)

```bash
python -m harness_ui user add alice --role admin       # prompts for a password (12+ characters); scrypt-hashed
python -m harness_ui user add bob --role operator
python -m harness_ui user disable bob                  # takes effect on bob's next request
python -m harness_ui user list
```

`configs/ui/users.yaml` is written with mode 0600 and is git-ignored.

### Serving on a network

Use `auth.mode: users` or `token`, and put the console behind a TLS-terminating reverse proxy, for example nginx
or an internal load balancer:

- The session cookie is marked `Secure` automatically on https (`cookie_secure: auto`).
- The console does not trust proxy headers.
- Limit access to the internal network.

### Data handling

- **No original text in responses.** API responses contain offsets, decisions and the redacted text, never the
  original. The browser highlights the text it already holds.
- **Only sanitised output on disk.** Batch outputs keep redacted text, offsets and counts; originals are held in
  memory only while a batch runs.
- **No text in the audit trail.** `results/ui_audit.jsonl` and the server logs record user, action, IDs, counts and
  a keyed hash of the text, never the text.
- **Hardened responses.** Every response carries a strict Content-Security-Policy: all assets are local, there are
  no CDNs and no CORS. API responses are `Cache-Control: no-store`.
- **CSRF protection.** State-changing requests need the `X-PHI-UI` header together with `SameSite=Strict`
  cookies. Failed sign-ins are rate limited.
- **Restricted jobs.** Jobs run from an allow-list of commands with validated parameters; the console cannot run
  arbitrary commands.

### Troubleshooting

| symptom | what to do |
|---|---|
| "the web UI has not been built" | Run `scripts/build_ui.sh`. |
| The engine badge shows *failed* | Read the reason on the Overview page. Typical causes: a weight-hash mismatch (the model files changed), a missing vault key in `prod`, or a failed self-test after a configuration change. The console stays fail-closed until the cause is fixed and you choose **Reload engine** (or restart). |
| "refusing to start: auth.mode 'none' is only allowed on a loopback host" | Set `auth.mode` to `token` or `users`, or bind to 127.0.0.1. |
| A job shows `interrupted` | The console stopped while the job was running. Run it again. |

## For developers

```
harness_ui/            FastAPI service (never imported by the runtime engine - tests/test_boundaries.py)
  settings.py          strict settings model + env overrides + unsafe-config refusal
  security.py          auth modes, signed sessions, scrypt, CSRF header, rate limit
  engines.py           production engine, what-if engines (LRU), per-request scoped registry, outbound guard
  jobs.py              allow-listed job registry (subprocess) + batch redaction (in process)
  results.py           read-only run browser (summary files only, path-safe)
  api/                 routes: system, analyze (workbench/batch/restore), jobs, insight (runs/config/monitoring/docs)
frontend/              React 19 + TypeScript + Mantine 8 + TanStack Query + React Router (Vite)
  src/api/             fetch client, hooks, types (schema.d.ts generated from the OpenAPI schema)
  src/pages/           one file per page
  e2e/                 Playwright tests against a real console
```

- **Types.** After changing an API model, run `npm run gen:api` in `frontend/`. It regenerates `openapi.json` and
  `src/api/schema.d.ts`.
- **Development.** Start `python -m harness_ui serve --port 8765`, then run `npm run dev` in `frontend/`. Vite on
  :5173 proxies `/api` to the console.
- **Tests.**
  - `pytest tests/test_ui_api.py`: API privacy, auth, roles, CSRF, jobs and path safety.
  - `npm test`: unit tests.
  - `npm run e2e`: Playwright with the local Chrome. It starts a console on :8766 with the `test` engine environment.
- **New job type.** Add a `JobSpec` and a parameter model in `harness_ui/jobs.py`, add its command in
  `build_commands`, and add a form in `frontend/src/pages/Checks.tsx`.
