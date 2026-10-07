"""Workbench: analyze / redact one text, outbound-guard check, re-identification, batch redaction."""
from __future__ import annotations

import secrets
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from harness.phi.contracts import DecisionValue, DetectionOptions, DetectionRequest, Mode, RegistryContext
from harness.phi.engine import PHIEngine
from harness_ui.audit import text_fingerprint
from harness_ui.deps import State, require, state
from harness_ui.engines import WhatIfError
from harness_ui.jobs import BatchDoc, JobError, batch_work, parse_upload
from harness_ui.schemas import AnalyzeIn, AnalyzeOut, RestoreIn, RestoreOut
from harness_ui.security import Principal

router = APIRouter(tags=["workbench"])
SPOKEN = {"telephone", "asr", "face_to_face"}
SAFE_META = {"label", "native_label", "match_type", "registry_type", "transformations", "approximate_mapping", "context_hit",
             "shadow_would_accept", "chunks", "single_token"}


def _group(rid: str) -> str:
    if rid.startswith("model_"):
        return "Contextual model"
    if rid.startswith("registry"):
        return "Known details (registry)"
    if rid.startswith("conv"):
        return "Conversation rules"
    return "Pattern rules"


@router.post("/analyze", response_model=AnalyzeOut)
def analyze(body: AnalyzeIn, p: Principal = Depends(require("analyze")), st: State = Depends(state)) -> dict[str, Any]:
    lim = st.settings.limits.max_text_chars
    if len(body.text) > lim:
        raise HTTPException(413, f"text is {len(body.text)} characters; the limit is {lim}")
    try:
        eng, what_if = st.engines.engine_for(body.what_if)
    except WhatIfError as e:
        raise HTTPException(422, str(e)) from None
    except Exception:
        raise HTTPException(503, "the engine is not available - see the Overview page") from None
    cfg = eng.cfg
    profile = body.normalization_profile or ("asr" if body.modality in SPOKEN else "default")
    if profile not in cfg.raw["normalization_profiles"]:
        raise HTTPException(422, f"unknown normalization profile {profile!r}")
    if body.policy_profile not in cfg.raw["policy_profiles"]:
        raise HTTPException(422, f"unknown policy profile {body.policy_profile!r}")
    rid = f"ui_{secrets.token_hex(8)}"
    notices: list[str] = []
    if what_if:
        notices.append("What-if analysis: results use temporary overrides and are not representative of the production "
                       "configuration; the startup self-test was skipped for this configuration.")
    with st.engines.store.scoped(body.known) as ctx:
        req = DetectionRequest(request_id=rid, text=body.text, language="en", modality=body.modality,
                               normalization_profile=profile, policy_profile=body.policy_profile,
                               registry_context=RegistryContext(subject_context_id=ctx) if ctx else None,
                               metadata={"conversation_id": body.conversation_id} if body.conversation_id else {},
                               options=DetectionOptions(mode=Mode.REDACT, include_candidates=body.include_evidence,
                                                        include_text=False, redaction_operator=body.operator))
        with st.engines.using(eng):
            r = eng.redact(req)
        guard = None
        if body.run_guard and r.status == "ok" and r.output.sanitized_text:
            tokens = []
            if r.output.vault_id:
                v = eng.vault_store.get(r.output.vault_id, actor="console", purpose="outbound_guard_token_list")
                tokens = list(v.tokens) if v else []
            g = st.engines.guard()
            res = g.check(r.output.sanitized_text, tokens, request_id=rid,
                          registry_context=RegistryContext(subject_context_id=ctx) if ctx else None)
            guard = {"allowed": res.allowed, "status": res.status,
                     "hits": [{"entity_type": h.entity_type, "start": h.start, "end": h.end, "recognizers": h.recognizers,
                               "blocking": h.blocking} for h in res.hits]}
    if body.known and not body.known.is_empty():
        notices.append("Known details were used for this request only and have been discarded.")
    dets = []
    for d in r.detections:
        dets.append({"id": d.detection_id, "entity_type": d.entity_type, "start": d.original_start, "end": d.original_end,
                     "decision": str(d.decision), "confidence_class": str(d.confidence_class),
                     "decision_score": round(d.decision_score, 4), "threshold": d.threshold, "reason": d.decision_reason,
                     "recognizers": d.contributing_recognizers,
                     "sources": sorted({_group(x) for x in d.contributing_recognizers}),
                     "action": d.redaction_action,
                     "rejected": [{"recognizer_id": x.recognizer_id, "entity_type": x.entity_type, "raw_score": round(x.raw_score, 4),
                                   "score_type": str(x.score_type), "threshold_used": x.threshold_used, "reason": str(x.reason)}
                                  for x in d.rejected_evidence]})
    ev = [{"id": e.evidence_id, "entity_type": e.entity_type, "start": e.original_start, "end": e.original_end,
           "raw_score": round(e.raw_score, 4), "score_type": str(e.score_type), "recognizer_id": e.recognizer_id,
           "source": str(e.source), "view": e.text_view_id, "threshold_used": e.threshold_used,
           "threshold_source": e.threshold_source, "accepted": e.accepted_as_candidate,
           "rejection_reason": str(e.rejection_reason) if e.rejection_reason else None,
           "metadata": {k: v for k, v in e.metadata.items() if k in SAFE_META}} for e in r.evidence]
    final = [d for d in r.detections if d.decision != DecisionValue.KEEP]
    st.audit.record(p.name, p.role, "analyze", request_id=rid, text_hmac=text_fingerprint(body.text), characters=len(body.text),
                    status=r.status, detections=len(final), what_if=what_if, known_details=bool(ctx),
                    guard_allowed=guard["allowed"] if guard else None, entities=r.summary.entities_by_type)
    can_restore = st.settings.features.restore and p.allows(st.settings.roles.restore)
    return {"request_id": rid, "status": r.status, "failure": r.failure.model_dump() if r.failure else None,
            "sanitized_text": r.output.sanitized_text if r.status == "ok" else None, "operator": r.output.operator,
            "vault_id": r.output.vault_id if can_restore else None, "detections": dets, "evidence": ev,
            "summary": r.summary.model_dump(), "timings": r.timings.model_dump(), "guard": guard, "what_if": what_if,
            "notices": notices, "config_sha256": r.config_sha256, "stamps": r.stamps.model_dump() if r.stamps else None}


@router.post("/restore", response_model=RestoreOut)
def restore(body: RestoreIn, p: Principal = Depends(require("restore")), st: State = Depends(state)) -> dict[str, Any]:
    """Re-identify tokens in downstream text (e.g. an LLM answer). Audited; only tokens issued by that vault."""
    if not st.settings.features.restore:
        raise HTTPException(403, "re-identification is disabled in configs/ui/ui.yaml")
    eng: PHIEngine = st.engines.base()
    try:
        out = eng.restore(body.vault_id, body.text, actor=f"console:{p.name}", purpose="console_reidentify")
    except KeyError:
        st.audit.record(p.name, p.role, "restore", ok=False, vault_id=body.vault_id)
        raise HTTPException(404, "unknown or expired vault") from None
    v = eng.vault_store.get(body.vault_id, actor="console", purpose="restore_count")
    replaced = sum(body.text.count(t) for t in (v.tokens if v else {}))
    st.audit.record(p.name, p.role, "restore", vault_id=body.vault_id, replaced=replaced)
    return {"text": out, "replaced": replaced}


@router.post("/batch")
async def batch(files: list[UploadFile] = File(...), modality: str = Form("typed"), operator: str = Form(""),
                policy_profile: str = Form("cadence_default"), p: Principal = Depends(require("batch")),
                st: State = Depends(state)) -> dict[str, Any]:
    lim = st.settings.limits
    if modality not in {"typed", "note", "dictation", "telephone", "face_to_face", "asr", "imported_text"}:
        raise HTTPException(422, "unknown modality")
    if operator and operator not in {"REDACT", "TOKENIZE", "PSEUDONYMIZE", "ENCRYPT"}:
        raise HTTPException(422, "unknown operator")
    if policy_profile not in st.engines.base_cfg.raw["policy_profiles"]:
        raise HTTPException(422, "unknown policy profile")
    docs: list[BatchDoc] = []
    total = 0
    for f in files:
        data = await f.read(lim.max_upload_mb * 2 ** 20 + 1)
        total += len(data)
        if total > lim.max_upload_mb * 2 ** 20:
            raise HTTPException(413, f"upload exceeds {lim.max_upload_mb} MB")
        try:
            docs += parse_upload(f.filename or "document.txt", data, modality, lim.max_text_chars)
        except (JobError, ValueError) as e:
            raise HTTPException(422, str(e) if isinstance(e, JobError) else f"{f.filename}: invalid JSON") from None
        if len(docs) > lim.max_batch_files:
            raise HTTPException(413, f"more than {lim.max_batch_files} documents")
    if not docs:
        raise HTTPException(422, "no documents found in the upload")
    st.engines.base()
    options = {"modality": modality, "operator": operator or None, "policy_profile": policy_profile}
    out_tpl = str(st.jobs.dir / "{id}" / "output")
    job = st.jobs.submit_work("batch", "Batch redaction", {**options, "documents": len(docs)}, p.name,
                              lambda j: batch_work(st.engines, docs, options, st.jobs.dir / j.id / "output")(j), out_tpl)
    st.audit.record(p.name, p.role, "batch_submit", job_id=job.id, documents=len(docs), characters=sum(len(d.text) for d in docs))
    return job.public()
