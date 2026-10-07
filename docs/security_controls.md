# Security and privacy controls

Each control lists the code that implements it and the test that proves it. Control references are NIST
SP 800-53 rev. 5, HIPAA 45 CFR 164.312 / 164.514 and NIST SP 800-188.

| control | implementation | verified by |
|---|---|---|
| **Fail closed** (SC-24, SI-17). Every failure class has an action: block, reject or conservative fallback. Raw text is never released on error. | `harness/phi/failsafe.py`, `PHIEngine._guarded` | `tests/test_failsafe.py` |
| **Startup self-test.** Sentinel cases must be redacted, or the engine refuses to serve. | `policy/failure.yaml` `startup_self_test`, `PHIEngine.self_test` | `test_failsafe.py`, model tier `tests/test_model_tier.py` |
| **Supply-chain integrity** (SR-4, SI-7). Model revisions are pinned, the weight files' SHA-256 is pinned, and the engine refuses to serve on mismatch. | `models.yaml` `weights_sha256`, `provenance.weights_sha256` | `test_failsafe.py::test_weight_hash_mismatch_refuses_to_start` |
| **Local inference only.** No inference API is called. Model tests force `HF_HUB_OFFLINE`. | adapters load local snapshots | `tests/test_model_tier.py`, `test_boundaries.py` |
| **No PHI in logs** (SI-11, AU-3). Logs carry allow-listed structured fields and keyed-HMAC text fingerprints. Free-text log arguments are replaced. Failure codes carry exception class names, never messages. | `harness/phi/telemetry.py` | `tests/test_telemetry_privacy.py` |
| **Data minimisation in memory.** A serving engine keeps no model hits after a request (`model_cache.max_entries: 0`, bounded LRU otherwise). Vault stores are bounded and expire. | `recognizers/model.py` `HitCache`, `vault.py` | `test_telemetry_privacy.py`, `test_vault.py` |
| **Tokens are not derived from PHI** (164.514(c)). TOKENIZE numbers tokens sequentially within a scope; PSEUDONYMIZE requires a managed HMAC key, with no fallback. | `harness/phi/redaction` | `tests/test_phi_harness.py` |
| **Re-identification map protected** (SC-28, 164.312(a)(2)(iv)). AES-256-GCM per vault, random nonce, vault ID bound as associated data (detects tampering or swapping), 0600 files, atomic writes. A missing or malformed key stops startup. TTL is enforced. | `harness/phi/vault.py` | `tests/test_vault.py` |
| **Audited re-identification** (AU-2, 164.312(b)). Every vault put, get, restore and expiry is recorded with vault ID, actor, purpose and time, never values. | `vault._Audit`, `PHIEngine.restore` | `test_vault.py` |
| **Token consistency scope.** Document, conversation or member, never global. A missing scope key falls back to document scope (counted). | `policy/redaction.yaml`, `PHIEngine._scope_vault` | `test_vault.py` |
| **Boundary protection** (SC-7). Before egress, an independent deterministic re-scan (no ML) blocks payloads with leaked direct identifiers. If the guard itself fails, the payload is blocked. | `harness/phi/outbound_guard.py` | `tests/test_outbound_guard.py` |
| **Safe model rollout.** In shadow mode a model runs, but its evidence can never change a decision or the output. | `policy.allowed`, `RejectionReason.shadow_mode` | `tests/test_shadow_mode.py` |
| **Continuous monitoring** (SI-4, CA-7). Canaries run through the full path. A Poisson control chart on detection rate per entity × modality alerts on drops and surges. Canary traffic is excluded from the rates. | `harness/phi/monitoring.py`, `policy/monitoring.yaml` | `tests/test_monitoring.py` |
| **Configuration integrity** (CM-6). Strict schema, reference integrity, no silent overrides, environment allow-list, dev-only provenance on generated overlays. | `harness/phi/config/` | `tests/test_config_layered.py` |
| **Separation of evaluation from runtime.** The runtime never imports `harness_eval`, and no evaluation logic runs in the detector. | package boundary | `tests/test_boundaries.py` |
| **Test-data governance.** Manifest roles, hash verification, contamination checks, an append-only ledger, holdout hiding. | `harness_eval/manifests.py`, `ledger.py`, `contamination.py` | `tests/test_eval_core.py` |

| **Operator console** (AC-3, AC-7, SC-8, SI-10). There are three auth modes: `none` is accepted only on loopback, plus token and users (scrypt hashes). Sessions are HMAC-signed, HttpOnly and SameSite=Strict, and mutating requests require the CSRF header. Roles are viewer, operator and admin, and failed logins are rate limited. The API never echoes original text. The console sends a strict CSP, allows no CORS and sends no-store on API responses. Jobs come from an allow-list. Audit records carry no text. | `harness_ui/` | `tests/test_ui_api.py`, `frontend/e2e/` |

## Operational requirements (outside this repository)

- **Keys.** Provision `PHI_VAULT_KEY` (32 random bytes), `PHI_LOG_HMAC_KEY` and, if PSEUDONYMIZE is used,
  `PHI_PSEUDONYM_KEY` from a secrets manager. Rotate them under the key-management policy. Rotating the vault key
  makes existing vaults unreadable by design, so drain or re-encrypt them first.
- **Vault directory.** Use a dedicated encrypted volume with restricted service-account access, and ship
  `audit.jsonl` to the SIEM.
- **Outbound guard.** Call `OutboundGuard.check` on every payload before it leaves the trust boundary. Treat
  `allowed == False` as a hard stop.
- **Monitoring.** Schedule `run_canaries` (every `interval_seconds`) and `check_rates` (every `window_minutes`).
  Alert on any canary failure or rate alert.
