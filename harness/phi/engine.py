"""PHI detection engine - the only entry point applications use.

    engine = PHIEngine(HarnessConfig.load(), registry_store=store)
    response = engine.analyze(DetectionRequest(...))      # DETECT / DEBUG / SCORE
    response = engine.redact(DetectionRequest(...))       # + sanitized / tokenized text
    original = engine.restore(response.output.vault_id, downstream_text)   # inside the trust boundary

Pipeline (one code path for every mode):
    request -> text views (normalizers) -> recognizers via Presidio (evidence, ORIGINAL offsets)
            -> policy.annotate (allow-lists, thresholds) -> reconciliation -> policy.decide
            -> DetectionResponse [-> redaction (separate module) -> TokenVault kept in the engine]

Presidio's role: RecognizerRegistry selects recognizers per language (static + per-request
ad-hoc registry recognizers) and EvidenceAnalyzer invokes them through the NLP-engine pipeline.
EvidenceAnalyzer.analyze_all deliberately skips AnalyzerEngine.analyze's global score threshold
and EntityRecognizer.remove_duplicates (which drops results contained in a same-type result):
both would destroy evidence before Cadence policy sees it.
"""
from __future__ import annotations

import contextlib
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from presidio_analyzer import AnalyzerEngine, EntityRecognizer, RecognizerRegistry, RecognizerResult
from presidio_analyzer.nlp_engine import NoOpNlpEngine

from harness.phi import provenance, telemetry
from harness.phi.config import HarnessConfig, UnsupportedLanguageError
from harness.phi.contracts import (DecisionValue, DetectionEvidence, DetectionRequest, DetectionResponse, FailureInfo, InputInfo,
                                   Mode, NormalizationInfo, NormalizationTrace, Operator, OutputInfo, RedactionRequest,
                                   RedactionResponse, RejectionReason, ResponseSummary, Stamps, TextView, Timings, sha256)
from harness.phi.failsafe import (Deadline, EngineFailure, EngineStartupError, FailureClass, FailurePolicy,
                                  run_with_timeout)
from harness.phi.normalizers import NORMALIZERS, build_views
from harness.phi.policy import annotate, decide
from harness.phi.recognizers import (ConversationalRecognizer, ModelRecognizerAdapter, PHIRecognizer,
                                     PresidioBuiltinRecognizer, RecognitionContext, RegistryRecognizer, RuleFamilyRecognizer)
from harness.phi.recognizers.base import evidence_from_result
from harness.phi.redaction import TokenVault, redact as do_redact
from harness.phi.registry_store import InMemoryRegistryStore, RegistryStore

log = logging.getLogger("harness.phi.engine")


def _debug_raise() -> bool:
    """Developer switch PHI_DEBUG_RAISE=1: re-raise instead of failing closed. NEVER set in production
    (the startup banner of a production deployment must assert it is unset)."""
    import os
    return os.environ.get("PHI_DEBUG_RAISE") == "1"
# Process-wide model cache: (model key, device) -> loaded adapter. Repeated requests and repeated
# PHIEngine instances in one process share weights instead of reloading them.
_ADAPTER_CACHE: dict[tuple[str, str], Any] = {}


def get_adapter(key: str, device: str, load: bool = True) -> Any:
    from harness.adapters.base import ModelStatus
    from harness.adapters.factory import build_adapter
    a = _ADAPTER_CACHE.get((key, device))
    if a is None:
        a = build_adapter(key, device)
        _ADAPTER_CACHE[(key, device)] = a
    if load and a.status != ModelStatus.LOADED and not a.load():
        raise RuntimeError(f"model {key} failed to load: {a.load_error}")
    return a


class EvidenceAnalyzer(AnalyzerEngine):
    def analyze_all(self, text: str, language: str, ad_hoc_recognizers: Optional[list[EntityRecognizer]] = None,
                    allow: Optional[set[str]] = None, context_enhancement: bool = False,
                    timings: Optional[dict[str, float]] = None, deadline: Optional[Deadline] = None,
                    model_budget_s: Optional[float] = None) -> list[RecognizerResult]:
        recs = self.registry.get_recognizers(language=language, entities=None, all_fields=True,
                                             ad_hoc_recognizers=ad_hoc_recognizers)
        if allow is not None:
            recs = [r for r in recs if r.name in allow]
        nlp = self.nlp_engine.process_text(text, language)
        out: list[RecognizerResult] = []
        for r in recs:
            stage = f"recognizer:{r.name}"
            if deadline is not None:
                deadline.check(stage)
            t0 = time.perf_counter()
            is_model = r.name.startswith("model_")
            call = (lambda r=r: r.analyze(text=text, entities=r.supported_entities, nlp_artifacts=nlp) or [])
            if is_model and deadline is not None:
                budget = min(deadline.remaining_s(), model_budget_s or deadline.remaining_s())
                res = run_with_timeout(call, budget, stage, FailureClass.model_error)
            else:
                try:
                    res = call()
                except EngineFailure:
                    raise
                except Exception as exc:  # noqa: BLE001 - exception TEXT may contain PHI; only its class name is kept
                    if _debug_raise():
                        raise
                    raise EngineFailure(FailureClass.model_error if is_model else FailureClass.recognizer_error,
                                        stage, f"recognizer_exception:{type(exc).__name__}") from None
            if timings is not None:
                timings[r.name] = timings.get(r.name, 0.0) + (time.perf_counter() - t0) * 1000
            out += res
        if context_enhancement:
            out = self._enhance_using_context(text, out, nlp, recs)
        return out


@dataclass
class Collected:
    views: list[TextView]
    traces: list[NormalizationTrace]
    normalizers: list[str]
    evidence: list[DetectionEvidence]
    recognizers_run: list[str]
    language: str
    per_recognizer_ms: dict[str, float] = field(default_factory=dict)
    normalization_ms: float = 0.0
    recognizer_ms: float = 0.0
    model_ms: float = 0.0


class PHIEngine:
    def __init__(self, cfg: HarnessConfig, device: str = "cpu", registry_store: Optional[RegistryStore] = None,
                 adapters: Optional[dict[str, Any]] = None, load_all_models: bool = False,
                 model_caches: Optional[dict[str, dict]] = None, lazy_models: bool = False,
                 self_test: Optional[bool] = None) -> None:
        """load_all_models=True also builds models disabled in the config (evaluation superset).
        lazy_models=True defers loading weights until a text misses the model cache."""
        telemetry.install()
        self.cfg = cfg
        try:
            self.failure_policy = FailurePolicy.from_config(cfg.raw.get("failure"))
        except ValueError as exc:
            raise EngineStartupError(f"invalid failure policy: {exc}") from None
        self.weight_hashes: dict[str, Optional[str]] = {}
        self.store = registry_store or InMemoryRegistryStore()
        self.adapters = adapters if adapters is not None else {}
        self._injected_adapters = set(self.adapters)
        # trust boundary: the vault store holds re-identification maps (never serialized into responses or logs);
        # encrypted_file without its key is a startup failure - the engine must not serve without it
        from harness.phi.vault import make_store
        try:
            self.vault_store = make_store(cfg.raw)
        except EngineFailure as f:
            raise EngineStartupError(f"vault store unavailable: {f.code}") from None
        self._scope_locks: dict[str, threading.Lock] = {}
        self._scope_locks_guard = threading.Lock()
        LANGS = cfg.recognizer_languages              # this phase: ["en"]
        self.langs = LANGS
        self.registry = RecognizerRegistry(supported_languages=LANGS)
        self.static_ids: list[str] = []
        self.registry_specs: dict[str, dict] = {}
        self.models: dict[str, ModelRecognizerAdapter] = {}
        # evaluation passes its own (persistent) caches; a serving engine gets bounded ones from the config
        self._own_caches = model_caches is None
        self.model_caches = model_caches if model_caches is not None else {}
        cache_max = int((cfg.raw.get("model_cache") or {}).get("max_entries", 0))
        from harness.phi.recognizers.conversational import ConvParams
        from harness.phi.recognizers.labelled import LabelledValueRecognizer
        from harness.phi.recognizers.patterns import PatternRecognizer
        from harness.shield.registry import RegistryParams
        conv_params = ConvParams.from_config(cfg.raw.get("contextual_params"))
        reg_section = cfg.raw.get("registry") or {}
        self.kind_entities = reg_section.get("kind_entities") or None
        self.registry_params = RegistryParams.from_config(reg_section.get("matching")) if reg_section.get("matching") else None
        for rid, spec in cfg.raw["recognizers"].items():
            ver = str(spec.get("version", "1"))
            if "kinds" in spec:
                self.registry_specs[rid] = spec
                continue
            for lang in LANGS:
                if spec.get("type") == "pattern":
                    r: PHIRecognizer = PatternRecognizer(rid, spec, lang)
                elif spec.get("type") == "labelled":
                    r = LabelledValueRecognizer(rid, spec, lang)
                elif "family" in spec:
                    r = RuleFamilyRecognizer(rid, spec["family"], lang, ver, emits=spec.get("emits"))
                elif spec.get("contextual"):
                    r = ConversationalRecognizer(rid, lang, ver, function=spec.get("function"), emits=spec.get("emits"),
                                                 params=conv_params)
                elif "presidio_class" in spec:
                    r = PresidioBuiltinRecognizer(rid, spec["presidio_class"], spec["entity_map"], lang, ver)
                else:
                    raise ValueError(f"recognizer {rid}: cannot tell its type from the config")
                self.registry.add_recognizer(r)
            self.static_ids.append(rid)
        for key, spec in cfg.raw["models"].items():
            if not (spec.get("enabled") or load_all_models):
                continue
            if key not in self.adapters:
                self.adapters[key] = get_adapter(key, device, load=not lazy_models)
            from harness.phi.recognizers.model import HitCache
            cache = self.model_caches.setdefault(key, HitCache(cache_max) if self._own_caches else {})
            for lang in LANGS:   # one adapter (weights) shared by every language instance
                m = ModelRecognizerAdapter(key, self.adapters[key], spec.get("candidate_floor", 0.1), lang, cache)
                self.registry.add_recognizer(m)
            self.models[key] = m
            expected = spec.get("weights_sha256")
            if expected and key in self._injected_adapters:
                self.weight_hashes[key] = "injected"      # caller-supplied adapter (test double): not loaded from disk here
            elif expected:                    # supply-chain integrity: verify pinned weights before serving
                actual = provenance.weights_sha256(self.adapters[key])
                if actual != expected:
                    raise EngineStartupError(f"model {key}: weight hash mismatch (expected {expected[:12]}..., "
                                             f"got {(actual or 'unavailable')[:12]}...)")
                self.weight_hashes[key] = actual
        self.analyzer = EvidenceAnalyzer(registry=self.registry, supported_languages=LANGS, log_decision_process=False,
                                         nlp_engine=NoOpNlpEngine(models=[{"lang_code": l, "model_name": "noop"} for l in LANGS]))
        st = (cfg.raw.get("failure") or {}).get("startup_self_test") or {}
        if self_test if self_test is not None else bool(st.get("required", False)):
            self.self_test(st.get("cases") or [])

    # ------------------------------------------------------------------------ startup self-test
    def self_test(self, cases: list[dict[str, Any]]) -> None:
        """Run sentinel cases (conspicuously fake values) through the full pipeline; refuse to serve
        if any expected detection is missing or any stage fails (ML Test Score 'check before serving')."""
        for i, case in enumerate(cases):
            req = DetectionRequest(request_id=f"selftest_{i}", text=case["text"], language=case.get("language", "en"),
                                   normalization_profile=case.get("normalization_profile", "default"))
            resp = self._guarded(req, self.cfg, None, redact=True)
            if resp.status != "ok":
                raise EngineStartupError(f"startup self-test case {i} failed closed ({resp.failure.code if resp.failure else '?'})")
            red = resp.output.sanitized_text or ""
            for exp in case.get("must_redact", []):
                if exp in red:
                    raise EngineStartupError(f"startup self-test case {i}: sentinel value not redacted")
            got = {d.entity_type for d in resp.detections if d.decision != DecisionValue.KEEP}
            missing = set(case.get("entities", [])) - got
            if missing:
                raise EngineStartupError(f"startup self-test case {i}: missing entity types {sorted(missing)}")
        telemetry.event(log, "phi.self_test", self_test="passed", count=len(cases))

    # ------------------------------------------------------------------------ stages
    def _allowed_ids(self, req: DetectionRequest, cfg: HarnessConfig, include_disabled: bool) -> set[str]:
        ids = set(self.static_ids) | set(self.registry_specs) | {f"model_{k}" for k in self.models}
        if include_disabled:
            return ids
        out = set()
        for rid in ids:
            if rid.startswith("model_"):
                k = rid.removeprefix("model_")
                if k in cfg.enabled_models and (req.enabled_models is None or k in req.enabled_models):
                    out.add(rid)
            elif cfg.recognizer_enabled(rid) and (req.enabled_recognizers is None or rid in req.enabled_recognizers):
                out.add(rid)
        return out

    def collect(self, req: DetectionRequest, include_disabled: bool = False, cfg: Optional[HarnessConfig] = None,
                deadline: Optional[Deadline] = None) -> Collected:
        """Run normalizers and recognizers. include_disabled=True gathers evidence from every
        configured recognizer / loaded model / normalizer (evaluation superset for ablations)."""
        cfg = cfg or self.cfg
        lang = cfg.resolve_language(req.language)
        t0 = time.perf_counter()
        norms = list(NORMALIZERS) if include_disabled else cfg.normalizers_for(req.normalization_profile)
        views, traces = build_views(req.text, norms)
        t_norm = (time.perf_counter() - t0) * 1000
        try:
            reg, versions = self.store.resolve(req.registry_context)
        except Exception:  # noqa: BLE001
            raise EngineFailure(FailureClass.registry_unavailable, "registry_resolve", "registry_unavailable") from None
        ctx = RecognitionContext(req.request_id, lang, reg, versions)
        allow = self._allowed_ids(req, cfg, include_disabled)
        feed = set(cfg.raw.get("normalized_views_feed") or ["rule", "contextual_rule", "registry"])
        timings: dict[str, float] = {}
        evidence: dict[tuple, DetectionEvidence] = {}
        ctx_enh = bool(cfg.raw.get("presidio", {}).get("context_enhancement"))
        for view in views:
            adhoc = [RegistryRecognizer(rid, s["kinds"], reg, versions, lang, str(s.get("version", "1")),
                                        kind_entities=self.kind_entities, params=self.registry_params)
                     for rid, s in self.registry_specs.items() if reg and rid in allow]
            if view.view_id == "original":
                ids = allow
            else:
                ids = set()
                for rid in allow:
                    spec = cfg.raw["recognizers"].get(rid, {})
                    if rid.startswith("model_"):
                        continue          # models always read the original text
                    if ("registry" in feed and rid in self.registry_specs) or \
                       ("contextual_rule" in feed and spec.get("contextual")) or \
                       ("rule" in feed and rid in self.static_ids and not spec.get("contextual")):
                        ids.add(rid)
            results = self.analyzer.analyze_all(view.text, lang, adhoc, ids, ctx_enh and view.view_id == "original", timings,
                                                deadline, self.failure_policy.deadline_model_ms / 1000.0)
            for r in results:
                ev = evidence_from_result(r, ctx, view)
                if ev is None:
                    continue
                key = (ev.recognizer_id, ev.entity_type, ev.original_start, ev.original_end, ev.rule_id)
                if key not in evidence:           # identical evidence from a derived view adds nothing
                    evidence[key] = ev
        evs = sorted(evidence.values(), key=lambda e: (e.original_start, e.original_end, e.recognizer_id, e.evidence_id))
        model_ms = sum(v for k, v in timings.items() if k.startswith("model_"))
        return Collected(views, traces, norms, evs, sorted(timings), lang, timings, t_norm,
                         sum(timings.values()) - model_ms, model_ms)

    # ------------------------------------------------------------------------ public API
    def analyze(self, req: DetectionRequest, cfg: Optional[HarnessConfig] = None,
                collected: Optional[Collected] = None) -> DetectionResponse:
        return self._guarded(req, cfg or self.cfg, collected, redact=req.options.mode == Mode.REDACT)

    def redact(self, req: "DetectionRequest | RedactionRequest", cfg: Optional[HarnessConfig] = None,
               collected: Optional[Collected] = None) -> "DetectionResponse | RedactionResponse":
        """RedactionRequest -> RedactionResponse: redact already-decided detections (detection and
        redaction stay separate). DetectionRequest -> DetectionResponse: analyze + redact in one call."""
        if isinstance(req, RedactionRequest):
            try:
                rresp, vault = do_redact(req)
                if rresp.vault_id:
                    self._store_vault(vault)
            except EngineFailure as f:
                return self._blocked_redaction(req, f)
            except Exception:  # noqa: BLE001
                return self._blocked_redaction(req, EngineFailure(FailureClass.redaction_error, "redaction", "redaction_error"))
            telemetry.event(log, "phi.redact", replacements=len(rresp.replacements), operator=str(rresp.operator))
            telemetry.count("redactions_total")
            return rresp
        return self._guarded(req, cfg or self.cfg, collected, redact=True)

    @staticmethod
    def operators_by_entity(cfg: HarnessConfig) -> dict[str, Operator]:
        """redaction.per_entity > catalog default_action (the policy-profile operator is the fallback)."""
        out = {e: Operator(spec["default_action"]) for e, spec in cfg.raw["entities"].items() if spec.get("default_action")}
        out.update({e: Operator(o) for e, o in ((cfg.raw.get("redaction") or {}).get("per_entity") or {}).items()})
        return out

    # ------------------------------------------------------------------------ fail-closed guard
    def _guarded(self, req: DetectionRequest, cfg: HarnessConfig, collected: Optional[Collected], redact: bool) -> DetectionResponse:
        pol = self.failure_policy
        telemetry.count("requests_total")
        if len(req.text) > pol.max_input_chars:
            return self._blocked(req, cfg, EngineFailure(FailureClass.oversize_input, "input_guard", "input_too_large"),
                                 pol.action_for(FailureClass.oversize_input))
        try:
            cfg.resolve_language(req.language)
        except UnsupportedLanguageError:
            return self._blocked(req, cfg, EngineFailure(FailureClass.unsupported_language, "input_guard",
                                                         "unsupported_language"), pol.action_for(FailureClass.unsupported_language))
        try:
            resp = self._run(req, cfg, collected, redact, Deadline(pol.deadline_total_ms))
        except EngineFailure as f:
            action = pol.action_for(f.failure_class)
            if action == "conservative_fallback" and f.failure_class in (FailureClass.model_error, FailureClass.timeout) \
                    and cfg.enabled_models:
                try:
                    fb = cfg.copy()
                    fb.only_models([])
                    resp = self._run(req, fb, None, redact, Deadline(pol.deadline_total_ms))
                    resp = resp.model_copy(update={"status": "fallback", "failure": self._failure_info(f, action)})
                    telemetry.count("requests_fallback_total")
                    telemetry.event(log, "phi.fallback", request_id=req.request_id, failure_class=f.failure_class.value,
                                    stage=f.stage, code=f.code)
                    return resp
                except EngineFailure as f2:
                    f = f2
                except Exception:  # noqa: BLE001
                    f = EngineFailure(FailureClass.internal_error, "fallback", "internal_error")
            return self._blocked(req, cfg, f, "block" if action == "conservative_fallback" else action)
        except Exception as exc:  # noqa: BLE001 - never surface exception text (class name only)
            if _debug_raise():
                raise
            return self._blocked(req, cfg, EngineFailure(FailureClass.internal_error, "engine",
                                                         f"internal_error:{type(exc).__name__}"), "block")
        telemetry.observe_latency("total", resp.timings.total_ms)
        telemetry.observe_latency("model", resp.timings.model_ms)
        if req.metadata.get("canary") is True:          # canary traffic is kept out of the detection-rate baselines
            telemetry.count("canary_requests_total")
        else:
            telemetry.count("requests_ok_total")
            mod = req.modality if isinstance(req.modality, str) else getattr(req.modality, "value", "unknown")
            telemetry.count(f"chars_total{{modality={mod}}}", len(req.text))
            for et, n in resp.summary.entities_by_type.items():
                telemetry.count(f"detections_total{{entity={et},modality={mod}}}", n)
        telemetry.event(log, "phi.request", request_id=req.request_id, text_hmac=telemetry.keyed_hash(req.text),
                        text_length=len(req.text), status=resp.status, detections=resp.summary.entities_detected,
                        candidates=resp.summary.candidates, total_ms=resp.timings.total_ms,
                        config_sha256=resp.config_sha256, code_commit=provenance.code_commit())
        return resp

    @staticmethod
    def _failure_info(f: EngineFailure, action: str) -> FailureInfo:
        return FailureInfo(failure_class=f.failure_class.value, stage=f.stage, code=f.code, action=action,
                           retryable=f.retryable)

    def _stamps(self, cfg: HarnessConfig) -> Stamps:
        return Stamps(code_commit=provenance.code_commit(), code_fingerprint=provenance.code_fingerprint(),
                      config_sha256=cfg.sha256(), catalog_version=str(cfg.raw.get("catalog_version", "legacy")),
                      models={k: {"revision": (self.adapters[k].entry.get("revision") if k in self.adapters else None),
                                  "weights_sha256": self.weight_hashes.get(k),
                                  "mode": str(cfg.raw["models"].get(k, {}).get("mode", "production"))}
                              for k in cfg.enabled_models})

    def _blocked(self, req: DetectionRequest, cfg: HarnessConfig, f: EngineFailure, action: str) -> DetectionResponse:
        telemetry.count(f"requests_blocked_total{{class={f.failure_class.value}}}")
        telemetry.event(log, "phi.blocked", request_id=req.request_id, failure_class=f.failure_class.value,
                        code=f.code, action=action, text_length=len(req.text))
        return DetectionResponse(
            request_id=req.request_id, policy_version=cfg.raw.get("engine", {}).get("policy_version", "cadence-policy"),
            config_sha256=cfg.sha256(),
            input=InputInfo(text_sha256=sha256(req.text), text_length=len(req.text), language=req.language,
                            resolved_language="", modality=req.modality),
            normalization=NormalizationInfo(profile=req.normalization_profile, normalizers_applied=[], traces=[], text_views=[],
                                            mapping_status="ok"),
            detections=[], evidence=[], output=OutputInfo(mode=req.options.mode),
            summary=ResponseSummary(entities_detected=0, entities_by_type={}, decisions={}, recognizers_used=[],
                                    recognizers_disabled=[], models_used=[], candidates=0, candidates_accepted=0,
                                    candidates_rejected=0, overlap_groups=0),
            timings=Timings(), status="blocked", failure=self._failure_info(f, action), stamps=self._stamps(cfg))

    def _blocked_redaction(self, req: RedactionRequest, f: EngineFailure) -> RedactionResponse:
        telemetry.count(f"redactions_blocked_total{{class={f.failure_class.value}}}")
        telemetry.event(log, "phi.redact_blocked", request_id=req.request_id, failure_class=f.failure_class.value, code=f.code)
        return RedactionResponse(request_id=req.request_id, status="blocked", failure=self._failure_info(f, "block"),
                                 sanitized_text="", operator=req.operator, replacements=[], vault_id=None,
                                 original_sha256=sha256(req.original_text))

    def _store_vault(self, vault: TokenVault) -> None:
        self.vault_store.put(vault)

    def _scope_vault(self, req: DetectionRequest, cfg: HarnessConfig) -> tuple[Optional[TokenVault], Optional[str]]:
        """(existing scope vault or a fresh one, scope lock key) for the request's consistency scope."""
        from harness.phi.vault import scope_key, scoped_vault_id
        scope = (cfg.raw.get("redaction") or {}).get("consistency_scope", "document")
        if scope == "document":
            return None, None
        key = scope_key(scope, dict(req.metadata), req.registry_context.subject_context_id if req.registry_context else None)
        if key is None:
            telemetry.count("vault_scope_fallback")
            return None, None
        vid = scoped_vault_id(scope, key)
        return self.vault_store.get(vid) or TokenVault(vault_id=vid, request_id=req.request_id), vid

    def _scope_lock(self, vid: Optional[str]) -> "threading.Lock | contextlib.nullcontext":
        if vid is None:
            return contextlib.nullcontext()
        with self._scope_locks_guard:
            return self._scope_locks.setdefault(vid, threading.Lock())

    def restore(self, vault_id: str, text: str, actor: str = "caller", purpose: str = "reidentify_downstream_output") -> str:
        """Re-identify tokens in downstream text. Only inside the protected environment; audited."""
        v = self.vault_store.get(vault_id, actor=actor, purpose=purpose)
        if v is None:
            raise KeyError("unknown or expired vault_id")
        return v.restore_tokens(text)

    def _run(self, req: DetectionRequest, cfg: HarnessConfig, collected: Optional[Collected], redact: bool,
             deadline: Optional[Deadline] = None) -> DetectionResponse:
        t_start = time.perf_counter()
        precollected = collected is not None
        col = collected or self.collect(req, cfg=cfg, deadline=deadline)
        if deadline is not None:
            deadline.check("policy")
        norms_active = cfg.normalizers_for(req.normalization_profile)
        opts = req.options
        t0 = time.perf_counter()
        ann = annotate(col.evidence, cfg, req, norms_active)
        t_pol = (time.perf_counter() - t0) * 1000
        t0 = time.perf_counter()
        dets, groups = decide(req.request_id, req.text, ann, cfg, req.policy_profile, include_text=opts.include_text)
        t_rec = (time.perf_counter() - t0) * 1000
        out = OutputInfo(mode=opts.mode)
        t_red = 0.0
        if redact:
            t0 = time.perf_counter()
            pol = cfg.policy(req.policy_profile)
            op = Operator(opts.redaction_operator or pol.get("operator", "TOKENIZE"))
            per_entity = {} if opts.redaction_operator else self.operators_by_entity(cfg)
            red_cfg = cfg.raw.get("redaction") or {}
            scope_vault, scope_id = self._scope_vault(req, cfg)
            with self._scope_lock(scope_id):
                if scope_id is not None:                      # re-read under the lock: another turn may have advanced it
                    scope_vault = self.vault_store.get(scope_id) or scope_vault
                try:
                    rresp, vault = do_redact(RedactionRequest(request_id=req.request_id, original_text=req.text, detections=dets,
                                                              operator=op, operators_by_entity=per_entity,
                                                              redact_review=pol.get("redact_review", True),
                                                              token_format=red_cfg.get("token_format", "[{ENTITY}_{N}]"),
                                                              coreference_entities=red_cfg.get("name_coreference", [])),
                                             scope=scope_vault)
                except EngineFailure:
                    raise
                except Exception:  # noqa: BLE001
                    raise EngineFailure(FailureClass.redaction_error, "redaction", "redaction_error") from None
                if rresp.vault_id:
                    self._store_vault(vault)
            out = OutputInfo(mode=Mode.DEBUG if opts.mode == Mode.DEBUG else Mode.REDACT, sanitized_text=rresp.sanitized_text,
                             tokenized_text=rresp.sanitized_text if op in (Operator.TOKENIZE, Operator.PSEUDONYMIZE) else None,
                             operator=op.value, vault_id=rresp.vault_id)
            t_red = (time.perf_counter() - t0) * 1000
        if opts.include_candidates:
            evidence_out = list(ann)
        elif opts.include_rejected:
            evidence_out = [e for e in ann if not e.accepted_as_candidate]
        else:
            evidence_out = []
        if opts.include_text:
            evidence_out = [e.model_copy(update={"text": req.text[e.original_start:e.original_end]}) for e in evidence_out]
        final = [d for d in dets if d.decision != DecisionValue.KEEP]
        by_type: dict[str, int] = {}
        for d in final:
            by_type[d.entity_type] = by_type.get(d.entity_type, 0) + 1
        decisions: dict[str, int] = {}
        for d in dets:
            decisions[d.decision] = decisions.get(d.decision, 0) + 1
        covered = [(d.original_start, d.original_end) for d in final]
        shadow_only = sum(1 for e in ann if e.rejection_reason == RejectionReason.shadow_mode and e.metadata.get("shadow_would_accept")
                          and e.text_view_id == "original"
                          and not any(s < e.original_end and e.original_start < t for s, t in covered))
        if shadow_only:
            telemetry.count("shadow_only_spans", shadow_only)
        enabled_ids = self._allowed_ids(req, cfg, include_disabled=False)
        all_ids = set(self.static_ids) | set(self.registry_specs) | {f"model_{k}" for k in cfg.raw["models"]}
        summary = ResponseSummary(
            entities_detected=len(final), entities_by_type=dict(sorted(by_type.items())), decisions=decisions,
            recognizers_used=sorted(enabled_ids), recognizers_disabled=sorted(all_ids - enabled_ids),
            models_used=sorted(r.removeprefix("model_") for r in enabled_ids if r.startswith("model_")),
            candidates=len(ann), candidates_accepted=sum(1 for e in ann if e.accepted_as_candidate),
            candidates_rejected=sum(1 for e in ann if not e.accepted_as_candidate),
            overlap_groups=sum(1 for g in groups if len(g.accepted) + len(g.rejected) > 1), shadow_only_spans=shadow_only)
        trace = None
        if opts.include_trace:
            trace = [{"stage": "normalization", "normalizers": col.normalizers, "views": [v.view_id for v in col.views],
                      "ms": round(col.normalization_ms, 3)}]
            trace += [{"stage": "recognizer", "recognizer": k, "ms": round(v, 3),
                       "evidence": sum(1 for e in ann if e.recognizer_id == k)} for k, v in sorted(col.per_recognizer_ms.items())]
            trace += [{"stage": "policy", "accepted": summary.candidates_accepted, "rejected": summary.candidates_rejected},
                      {"stage": "reconciliation", "groups": len(groups), "detections": len(dets)}]
        approx = any(v.mapping_quality == "approximate" for v in col.views)
        resp = DetectionResponse(
            request_id=req.request_id, policy_version=cfg.raw.get("engine", {}).get("policy_version", "cadence-policy"),
            config_sha256=cfg.sha256(),
            input=InputInfo(text_sha256=sha256(req.text), text_length=len(req.text), language=req.language,
                            resolved_language=col.language, modality=req.modality),
            normalization=NormalizationInfo(
                profile=req.normalization_profile, normalizers_applied=norms_active, traces=col.traces,
                text_views=[{"view_id": v.view_id, "transformation_ids": v.transformation_ids,
                             "mapping_quality": v.mapping_quality} for v in col.views],
                mapping_status="approximate_views_evidence_only" if approx else "ok"),
            detections=dets if (opts.include_rejected or opts.mode in (Mode.DEBUG, Mode.SCORE)) else final,
            evidence=evidence_out, output=out, summary=summary, trace=trace,
            timings=Timings(normalization_ms=round(col.normalization_ms, 3), recognizer_ms=round(col.recognizer_ms, 3),
                            model_ms=round(col.model_ms, 3), reconciliation_ms=round(t_rec, 3), policy_ms=round(t_pol, 3),
                            redaction_ms=round(t_red, 3),
                            total_ms=round((time.perf_counter() - t_start) * 1000 + ((col.normalization_ms + col.recognizer_ms +
                                                                                    col.model_ms) if precollected else 0.0), 3),
                            per_recognizer_ms={k: round(v, 3) for k, v in col.per_recognizer_ms.items()}),
            stamps=self._stamps(cfg))
        return resp
