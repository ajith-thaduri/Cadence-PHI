"""Jobs: launch allow-listed evaluation / check commands, follow their logs, download batch outputs."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response

from harness_eval import manifests
from harness_ui.deps import State, current, require, state
from harness_ui.jobs import SPECS, JobError, zip_dir
from harness_ui.schemas import JobIn
from harness_ui.security import Principal

router = APIRouter(tags=["jobs"])


@router.get("/job-types")
def job_types(p: Principal = Depends(current), st: State = Depends(state)) -> dict[str, Any]:
    ds = manifests.load().get("datasets", {})
    return {
        "types": [{"type": k, "title": s.title, "description": s.description, "estimate": s.est,
                   "params_schema": s.params.model_json_schema(), "allowed": p.allows(getattr(st.settings.roles, s.role))}
                  for k, s in SPECS.items()],
        "datasets": [{"id": k, "role": v.get("role"), "phi_class": v.get("phi_class"), "modality": v.get("modality"),
                      "allowed": v.get("role") == "dev" or (v.get("role") == "test" and p.allows(st.settings.roles.run_test_data_jobs))}
                     for k, v in ds.items() if v.get("role") in ("dev", "test")],
    }


@router.get("/jobs")
def list_jobs(p: Principal = Depends(current), st: State = Depends(state)) -> list[dict[str, Any]]:
    return [j.public() for j in st.jobs.list()[:300]]


@router.post("/jobs")
def submit(body: JobIn, p: Principal = Depends(require("run_jobs")), st: State = Depends(state)) -> dict[str, Any]:
    test_data = False
    if body.type == "evaluate":
        names = body.params.get("datasets") or []
        try:
            roles = {d: manifests.role(d) for d in names}
        except Exception:
            raise HTTPException(422, "unknown dataset") from None
        if any(r not in ("dev", "test") for r in roles.values()):
            raise HTTPException(422, "only dev and test datasets can be evaluated from the console")
        test_data = any(r == "test" for r in roles.values())
        if test_data:
            if not p.allows(st.settings.roles.run_test_data_jobs):
                raise HTTPException(403, f"evaluating test data needs the {st.settings.roles.run_test_data_jobs} role")
            if not (body.reason and len(body.reason.strip()) >= 10):
                raise HTTPException(422, "a reason (at least 10 characters) is required to access test data; it is recorded")
    try:
        job = st.jobs.submit(body.type, body.params, p.name, test_data=test_data, reason=body.reason)
    except JobError as e:
        raise HTTPException(422, str(e)) from None
    st.audit.record(p.name, p.role, "job_submit", job_id=job.id, job_type=job.type, test_data=test_data,
                    reason=(body.reason or None))
    return job.public()


@router.get("/jobs/{jid}")
def get_job(jid: str, p: Principal = Depends(current), st: State = Depends(state)) -> dict[str, Any]:
    j = st.jobs.jobs.get(jid)
    if j is None:
        raise HTTPException(404, "no such job")
    return j.public()


@router.get("/jobs/{jid}/log")
def job_log(jid: str, offset: int = 0, p: Principal = Depends(current), st: State = Depends(state)) -> dict[str, Any]:
    if jid not in st.jobs.jobs:
        raise HTTPException(404, "no such job")
    text, nxt = st.jobs.log(jid, max(offset, 0))
    return {"text": text, "offset": nxt, "status": st.jobs.jobs[jid].status}


@router.post("/jobs/{jid}/cancel")
def cancel(jid: str, p: Principal = Depends(require("run_jobs")), st: State = Depends(state)) -> dict[str, Any]:
    if jid not in st.jobs.jobs:
        raise HTTPException(404, "no such job")
    j = st.jobs.cancel(jid)
    st.audit.record(p.name, p.role, "job_cancel", job_id=jid)
    return j.public()


@router.get("/jobs/{jid}/download")
def download(jid: str, p: Principal = Depends(require("batch")), st: State = Depends(state)) -> Response:
    j = st.jobs.jobs.get(jid)
    if j is None or j.type != "batch" or j.status != "succeeded":
        raise HTTPException(404, "no downloadable output for this job")
    d = st.jobs.dir / jid / "output"
    if not d.exists():
        raise HTTPException(404, "output no longer available")
    st.audit.record(p.name, p.role, "batch_download", job_id=jid)
    return Response(zip_dir(d), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="redacted_{jid}.zip"'})


@router.get("/jobs/{jid}/summary")
def batch_summary(jid: str, p: Principal = Depends(require("batch")), st: State = Depends(state)) -> dict[str, Any]:
    import csv
    j = st.jobs.jobs.get(jid)
    f = st.jobs.dir / jid / "output" / "summary.csv"
    if j is None or j.type != "batch" or not f.exists():
        raise HTTPException(404, "no batch summary")
    with open(f, newline="") as fh:
        return {"rows": list(csv.DictReader(fh))}
