"""Background jobs: evaluation / check commands (subprocess) and batch redaction (in process).

Every CLI job type is an ALLOW-LISTED command template with validated parameters - the console can never run an
arbitrary command. Each job records the exact command lines (shown in the UI, copy-paste reproducible), its
user, timing, exit code and log. Job state is persisted to <jobs_dir>/index.jsonl; jobs that were running when
the console stopped are marked `interrupted` on the next start.

Batch jobs hold the original documents in memory only while they are processed; what is written to disk is the
sanitized text, detection offsets / types / decisions and counts.
"""
from __future__ import annotations

import csv
import io
import json
import os
import queue
import re
import secrets
import signal
import subprocess
import sys
import threading
import time
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from harness.paths import ROOT
from harness.phi.contracts import DecisionValue, DetectionOptions, DetectionRequest, Mode, Operator

SAFE_NAME = re.compile(r"[^A-Za-z0-9_.\-]+")


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------- job type registry
class _P(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChecklistP(_P):
    tier: Literal["fast", "model", "all"] = "fast"


class SuiteP(_P):
    tier: Literal["fast", "model"] = "fast"
    regress: bool = True


class EvaluateP(_P):
    datasets: list[str] = Field(min_length=1, max_length=10)
    model: Literal["default", "none"] = "default"


class PerfP(_P):
    check: Literal["latency", "throughput", "soak", "coldstart", "determinism", "all"] = "latency"
    duration: int = Field(60, ge=10, le=7200)
    n: int = Field(20, ge=3, le=500)
    device: Literal["cpu", "mps", "cuda"] = "cpu"
    model: Literal["default", "none"] = "default"


class GatesP(_P):
    with_model: bool = True
    perf_run: Optional[str] = Field(None, pattern=r"^[A-Za-z0-9_.\-]+$")


class NoP(_P):
    pass


@dataclass(frozen=True)
class JobSpec:
    title: str
    description: str
    params: type[_P]
    role: str = "run_jobs"                   # key in settings.roles
    kind: Optional[str] = None                # results/<kind>/ subfolder the job writes (for the run browser)
    est: str = ""


SPECS: dict[str, JobSpec] = {
    "checklist": JobSpec("Behavioural checklist", "Template tests: every PHI format must be fully redacted, robust to "
                         "case / punctuation / fillers, and clinical sentences must survive (gate G5).", ChecklistP,
                         kind="checklist", est="~10 s fast, ~1 min model"),
    "suite": JobSpec("Prompt suite", "215 curated prompts with expected PHI; optional regression check against the "
                     "reviewed baseline (gate G4).", SuiteP, kind="suite", est="~5 s fast, ~2 min model"),
    "evaluate": JobSpec("Dataset evaluation", "Score the engine on a registered dataset (residual PHI, recall, "
                        "precision, attribution). Test datasets are admin-only and ledgered.", EvaluateP, kind="phi",
                        est="1-5 min"),
    "perf": JobSpec("Performance", "Latency percentiles, throughput, soak, cold start, determinism (gate G8).", PerfP,
                    kind="perf", est="1-60 min"),
    "gates": JobSpec("Release gates", "Run gates G1-G9 and produce the go / no-go decision.", GatesP, kind="gates",
                     est="~3 min"),
    "canaries": JobSpec("Canaries", "Run the configured monitoring canaries through the full engine path.", NoP,
                        est="~10 s"),
    "monitoring_baseline": JobSpec("Monitoring baseline", "Recompute expected detection rates from dev data.", NoP,
                                   est="~1 min"),
    "cards": JobSpec("System card", "Regenerate docs/system_card.md and dataset datasheets.", NoP, est="~10 s"),
    "selftest": JobSpec("Test suite", "Run the automated test suite (fast tier).", NoP, est="~30 s"),
}


def build_commands(jtype: str, p: Any, engine_env: str, out: str, test_data: bool) -> list[list[str]]:
    py = ["python", "-m"]
    if jtype == "checklist":
        return [py + ["harness_eval.checklist", "run", "--tier", p.tier, "--env", engine_env, "--output", out]]
    if jtype == "suite":
        model = ["--model", "none"] if p.tier == "fast" else []
        cmds = [py + ["harness_eval.suite", "run", "--env", engine_env, *model, "--output", out]]
        if p.regress:
            base = "baseline_fast.json" if p.tier == "fast" else "baseline.json"
            cmds.append(py + ["harness_eval.suite", "regress", "--env", engine_env, *model, "--run", out, "--baseline", base])
        return cmds
    if jtype == "evaluate":
        model = ["--model", "none"] if p.model == "none" else []
        return [py + ["harness.evaluate", "--config", "configs/phi/phi.yaml", "--env", engine_env,
                      "--dataset", ",".join(p.datasets), *model, "--out", out]]
    if jtype == "perf":
        model = ["--model", "none"] if p.model == "none" else []
        return [py + ["harness_eval.perf", p.check, "--env", engine_env, "--device", p.device, "--duration", str(p.duration),
                      "--n", str(p.n), *model, "--output", out]]
    if jtype == "gates":
        extra = (["--with-model"] if p.with_model else []) + (["--perf", f"results/perf/{p.perf_run}"] if p.perf_run else [])
        return [py + ["harness_eval.gates", "run", "--env", engine_env, *extra, "--output", out]]
    if jtype == "canaries":
        return [py + ["harness_eval.monitoring", "canaries", "--env", engine_env]]
    if jtype == "monitoring_baseline":
        return [py + ["harness_eval.monitoring", "baseline", "--env", engine_env]]
    if jtype == "cards":
        return [py + ["harness_eval.cards", "all"]]
    if jtype == "selftest":
        return [py + ["pytest", "-q", "-p", "no:cacheprovider"]]
    raise ValueError(jtype)


# ---------------------------------------------------------------------------- job record
@dataclass
class Job:
    id: str
    type: str
    title: str
    params: dict[str, Any]
    commands: list[list[str]]
    user: str
    created: str = field(default_factory=now)
    status: str = "queued"
    started: Optional[str] = None
    finished: Optional[str] = None
    exit_code: Optional[int] = None
    output: Optional[str] = None
    run_ref: Optional[dict[str, str]] = None
    progress: Optional[dict[str, int]] = None
    error: Optional[str] = None
    reason: Optional[str] = None
    _proc: Optional[subprocess.Popen] = field(default=None, repr=False)
    _cancel: bool = field(default=False, repr=False)
    _work: Optional[Callable[["Job"], None]] = field(default=None, repr=False)

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "type": self.type, "title": self.title, "status": self.status, "params": self.params,
                "commands": [" ".join(c) for c in self.commands], "created": self.created, "started": self.started,
                "finished": self.finished, "exit_code": self.exit_code, "user": self.user, "output": self.output,
                "run_ref": self.run_ref, "progress": self.progress, "error": self.error}


class JobError(ValueError):
    pass


class JobManager:
    def __init__(self, jobs_dir: Path, max_concurrent: int, engine_env: str) -> None:
        self.dir = jobs_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.engine_env = engine_env
        self.jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._q: "queue.Queue[str]" = queue.Queue()
        self._load()
        for _ in range(max_concurrent):
            threading.Thread(target=self._worker, daemon=True, name="ui-job-worker").start()

    # persistence -----------------------------------------------------------------------------------------
    def _index(self) -> Path:
        return self.dir / "index.jsonl"

    def _persist(self, j: Job) -> None:
        with self._lock, open(self._index(), "a", encoding="utf-8") as f:
            f.write(json.dumps({**j.public(), "commands": j.commands, "reason": j.reason}) + "\n")

    def _load(self) -> None:
        if not self._index().exists():
            return
        for line in self._index().read_text().splitlines():
            try:
                d = json.loads(line)
            except ValueError:
                continue
            j = Job(id=d["id"], type=d["type"], title=d["title"], params=d["params"], commands=d["commands"],
                    user=d["user"], created=d["created"], status=d["status"], started=d.get("started"),
                    finished=d.get("finished"), exit_code=d.get("exit_code"), output=d.get("output"),
                    run_ref=d.get("run_ref"), progress=d.get("progress"), error=d.get("error"), reason=d.get("reason"))
            self.jobs[j.id] = j
        for j in self.jobs.values():
            if j.status in ("queued", "running"):
                j.status, j.finished = "interrupted", now()
                self._persist(j)

    # submission ------------------------------------------------------------------------------------------
    def submit(self, jtype: str, params: dict[str, Any], user: str, test_data: bool = False,
               reason: Optional[str] = None) -> Job:
        if jtype not in SPECS:
            raise JobError(f"unknown job type {jtype!r}")
        spec = SPECS[jtype]
        try:
            p = spec.params.model_validate(params)
        except ValidationError as e:
            raise JobError("invalid parameters: " + "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors())) from None
        jid = f"{datetime.now():%Y%m%d_%H%M%S}_{secrets.token_hex(3)}"
        out = f"results/{spec.kind}/ui_{jid}" if spec.kind else None
        cmds = build_commands(jtype, p, self.engine_env, out or "", test_data)
        j = Job(id=jid, type=jtype, title=spec.title, params=p.model_dump(), commands=cmds, user=user, output=out,
                run_ref={"kind": spec.kind, "name": f"ui_{jid}"} if spec.kind else None, reason=reason)
        return self._enqueue(j)

    def submit_work(self, jtype: str, title: str, params: dict[str, Any], user: str, work: Callable[[Job], None],
                    output: str) -> Job:
        jid = f"{datetime.now():%Y%m%d_%H%M%S}_{secrets.token_hex(3)}"
        j = Job(id=jid, type=jtype, title=title, params=params, commands=[], user=user, output=output.replace("{id}", jid))
        j._work = work
        return self._enqueue(j)

    def _enqueue(self, j: Job) -> Job:
        (self.dir / j.id).mkdir(parents=True, exist_ok=True)
        with self._lock:
            self.jobs[j.id] = j
        self._persist(j)
        self._q.put(j.id)
        return j

    # execution -------------------------------------------------------------------------------------------
    def _worker(self) -> None:
        while True:
            j = self.jobs.get(self._q.get())
            if j is None or j.status != "queued":
                continue
            j.status, j.started = "running", now()
            self._persist(j)
            log = self.dir / j.id / "log.txt"
            try:
                if j._work is not None:
                    j._work(j)
                    j.exit_code = 0
                else:
                    j.exit_code = self._run_commands(j, log)
                j.status = "cancelled" if j._cancel else ("succeeded" if j.exit_code == 0 else "failed")
            except Exception as e:  # noqa: BLE001 - class name only: messages may echo input
                j.status, j.error = "failed", type(e).__name__
            finally:
                j._work = None                      # drop references to in-memory documents
                j.finished = now()
                self._persist(j)

    def _run_commands(self, j: Job, log: Path) -> int:
        env = {**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "PYTHONUNBUFFERED": "1",
               "PHI_ENV": self.engine_env}
        with open(log, "a", encoding="utf-8") as fh:
            for cmd in j.commands:
                if j._cancel:
                    return -1
                fh.write(f"$ {' '.join(cmd)}\n")
                fh.flush()
                argv = [sys.executable, *cmd[1:]]           # 'python' -> this interpreter
                j._proc = subprocess.Popen(argv, cwd=ROOT, env=env, stdout=fh, stderr=subprocess.STDOUT,
                                           stdin=subprocess.DEVNULL, start_new_session=True)
                rc = j._proc.wait()
                j._proc = None
                fh.write(f"\n[exit code {rc}]\n")
                fh.flush()
                if rc != 0 and j.type != "suite":
                    return rc
                if rc != 0:                                 # suite: regress exit 1 = regressions found
                    return rc
        return 0

    def cancel(self, jid: str) -> Job:
        j = self.jobs[jid]
        if j.status == "queued":
            j.status, j.finished = "cancelled", now()
            self._persist(j)
        elif j.status == "running":
            j._cancel = True
            if j._proc is not None:
                try:
                    os.killpg(j._proc.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        return j

    def log(self, jid: str, offset: int = 0, limit: int = 65536) -> tuple[str, int]:
        p = self.dir / jid / "log.txt"
        if not p.exists():
            return "", offset
        with open(p, "rb") as f:
            f.seek(offset)
            chunk = f.read(limit)
        return chunk.decode("utf-8", errors="replace"), offset + len(chunk)

    def list(self) -> list[Job]:
        return sorted(self.jobs.values(), key=lambda j: j.created, reverse=True)


# ---------------------------------------------------------------------------- batch redaction
@dataclass
class BatchDoc:
    name: str
    text: str
    modality: str = "typed"


def parse_upload(filename: str, data: bytes, default_modality: str, max_chars: int) -> list[BatchDoc]:
    """.txt -> one document; .jsonl -> one per line ({"text": ..., "id"?, "modality"?}); .json -> object or list."""
    base = SAFE_NAME.sub("_", Path(filename).stem)[:80] or "document"
    try:
        s = data.decode("utf-8")
    except UnicodeDecodeError:
        raise JobError(f"{filename}: not UTF-8 text") from None
    suffix = Path(filename).suffix.lower()
    docs: list[BatchDoc] = []
    if suffix in (".txt", ".text", ".md", ""):
        docs = [BatchDoc(base, s, default_modality)]
    elif suffix in (".jsonl", ".json"):
        rows = [json.loads(x) for x in s.splitlines() if x.strip()] if suffix == ".jsonl" else json.loads(s)
        rows = rows if isinstance(rows, list) else [rows]
        for i, r in enumerate(rows):
            if not isinstance(r, dict) or not isinstance(r.get("text") or r.get("original_text"), str):
                raise JobError(f"{filename}: record {i + 1} has no 'text' field")
            rid = SAFE_NAME.sub("_", str(r.get("id") or r.get("request_id") or f"{base}_{i + 1:04d}"))[:80]
            docs.append(BatchDoc(rid, r.get("text") or r.get("original_text"), str(r.get("modality") or default_modality)))
    else:
        raise JobError(f"{filename}: unsupported file type (use .txt, .jsonl or .json)")
    for d in docs:
        if len(d.text) > max_chars:
            raise JobError(f"{d.name}: {len(d.text)} characters exceeds the limit of {max_chars}")
    return docs


def batch_work(engines, docs: list[BatchDoc], options: dict[str, Any], out_dir: Path) -> Callable[[Job], None]:
    def run(job: Job) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "sanitized").mkdir(exist_ok=True)
        eng = engines.base()
        rows, used = [], set()
        job.progress = {"done": 0, "total": len(docs)}
        with open(out_dir / "detections.jsonl", "w", encoding="utf-8") as det_f:
            for i, d in enumerate(docs):
                if job._cancel:
                    break
                name = d.name
                while name in used:
                    name = f"{d.name}_{i}"
                used.add(name)
                prof = options.get("normalization_profile") or ("asr" if d.modality in ("telephone", "asr", "face_to_face") else "default")
                try:
                    req = DetectionRequest(request_id=f"ui_batch_{job.id}_{i}", text=d.text, modality=d.modality,
                                           normalization_profile=prof, policy_profile=options.get("policy_profile", "cadence_default"),
                                           options=DetectionOptions(mode=Mode.REDACT, redaction_operator=options.get("operator")))
                    with engines.using(eng):
                        r = eng.redact(req)
                    status, n_chars = r.status, len(d.text)
                    final = [x for x in r.detections if x.decision != DecisionValue.KEEP]
                    if r.status == "ok":
                        (out_dir / "sanitized" / f"{name}.txt").write_text(r.output.sanitized_text or "", encoding="utf-8")
                    for x in final:
                        det_f.write(json.dumps({"document": name, "entity_type": x.entity_type, "start": x.original_start,
                                                "end": x.original_end, "decision": str(x.decision),
                                                "confidence": str(x.confidence_class),
                                                "recognizers": x.contributing_recognizers}) + "\n")
                    by = r.summary.entities_by_type if r.status == "ok" else {}
                    rows.append({"document": name, "status": status, "characters": n_chars, "detections": len(final),
                                 "failure": (r.failure.code if r.failure else ""), **{f"n_{k}": v for k, v in by.items()}})
                except Exception as e:  # noqa: BLE001 - one bad document must not stop the batch; class name only
                    rows.append({"document": name, "status": "error", "characters": len(d.text), "detections": 0,
                                 "failure": type(e).__name__})
                job.progress = {"done": i + 1, "total": len(docs)}
        docs.clear()                                   # originals are released as soon as the batch ends
        cols = ["document", "status", "characters", "detections", "failure"]
        cols += sorted({k for r in rows for k in r} - set(cols))
        with open(out_dir / "summary.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, cols, restval=0)
            w.writeheader()
            w.writerows(rows)
        cfg = engines.base_cfg
        (out_dir / "manifest.json").write_text(json.dumps({
            "job_id": job.id, "created": job.created, "user": job.user, "options": options, "documents": len(rows),
            "ok": sum(r["status"] == "ok" for r in rows), "config_sha256": cfg.sha256(),
            "catalog_version": cfg.raw.get("catalog_version"), "engine_env": cfg.raw.get("environment"),
            "contents": "sanitized/<document>.txt (sanitized text only), detections.jsonl (offsets into the ORIGINAL "
                        "text, types, decisions - no text), summary.csv (counts)"}, indent=1))
    return run


def zip_dir(d: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(d.rglob("*")):
            if p.is_file() and p.name not in ("log.txt",):
                z.write(p, p.relative_to(d))
    return buf.getvalue()
