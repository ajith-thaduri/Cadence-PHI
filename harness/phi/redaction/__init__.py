"""Redaction: final Detections + ORIGINAL text -> sanitized text. Separate from detection.

  redact(RedactionRequest) -> (RedactionResponse, TokenVault)

- Operates only on ORIGINAL offsets of reconciled (non-overlapping) detections; no recognizer
  ever replaces text, so offsets cannot be corrupted by earlier replacements.
- Replacement is performed by Presidio's AnonymizerEngine (operators: replace / custom / encrypt).
  Conflict resolution is irrelevant because reconciliation already guarantees non-overlap.
- Operators
    REDACT        "[NAME]"            irreversible
    TOKENIZE      "[NAME_1]"          deterministic within the document; reversible via TokenVault (Cadence default)
    PSEUDONYMIZE  "NAME-3f9a2c"       keyed HMAC surrogate (PHI_PSEUDONYM_KEY, required - no fallback); reversible via TokenVault
    ENCRYPT       AES ciphertext      Presidio encrypt operator, key from PHI_ENCRYPTION_KEY; reversible with the key
- The TokenVault (token -> original surface) is returned SEPARATELY and must stay inside the
  protected environment. It is never part of RedactionResponse / DetectionResponse / logs.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
from dataclasses import dataclass, field

from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import ConflictResolutionStrategy, OperatorConfig, RecognizerResult

from harness.phi.failsafe import EngineFailure, FailureClass
from harness.phi.contracts import (DecisionValue, Detection, Operator, RedactionRequest, RedactionResponse, Replacement,
                                   sha256, stable_id)

_ENGINE = AnonymizerEngine()


@dataclass
class TokenVault:
    """Re-identification map. Keep inside the trust boundary; never serialize into model I/O."""
    vault_id: str
    request_id: str
    tokens: dict[str, str] = field(default_factory=dict)                 # token -> first original surface
    occurrences: list[tuple[int, int, str, str]] = field(default_factory=list)  # (out_start, out_end, token, surface) - THIS request
    ids: dict[tuple[str, str], str] = field(default_factory=dict)        # (entity, normalised surface) -> token (scope state)
    counters: dict[str, int] = field(default_factory=dict)               # entity -> last issued N (scope state)

    def restore(self, sanitized_text: str) -> str:
        out, pos = [], 0
        for s, e, tok, surface in sorted(self.occurrences):
            out.append(sanitized_text[pos:s])
            out.append(surface if sanitized_text[s:e] == tok else sanitized_text[s:e])
            pos = e
        out.append(sanitized_text[pos:])
        return "".join(out)

    def restore_tokens(self, model_output: str) -> str:
        """Replace tokens appearing anywhere in downstream (e.g. Bedrock) output. Only tokens issued by
        this vault are replaced (exact string match), so the token format cannot cause false restores."""
        if not self.tokens:
            return model_output
        rx = re.compile("|".join(re.escape(t) for t in sorted(self.tokens, key=len, reverse=True)))
        return rx.sub(lambda m: self.tokens[m.group()], model_output)


def _norm_surface(s: str) -> str:
    return re.sub(r"\W+", "", s.lower())


_TITLES = {"dr", "dra", "mr", "mrs", "ms", "mx", "miss", "prof", "doctor", "nurse"}


def _name_words(s: str) -> frozenset[str]:
    """Comparable words of a name mention: lower-case, possessive and titles removed."""
    s = re.sub(r"['’]s\b", "", s.lower())
    return frozenset(w for w in re.findall(r"[a-záéíóúñü]+", s) if w not in _TITLES and len(w) > 1)


def redact(req: RedactionRequest, scope: "TokenVault | None" = None) -> tuple[RedactionResponse, TokenVault]:
    """scope: the vault of the request's consistency scope (conversation / member). Its numbering continues,
    so a surface already tokenized in the scope keeps its token; the returned vault carries the updated scope
    state and this request's occurrences. None = document scope (fresh numbering)."""
    text = req.original_text
    dets = sorted([d for d in req.detections if d.decision == DecisionValue.REDACT or
                   (d.decision == DecisionValue.REVIEW and req.redact_review)], key=lambda d: d.original_start)
    for a, b in zip(dets, dets[1:]):
        if b.original_start < a.original_end:
            raise ValueError("redaction requires reconciled, non-overlapping detections")
    if scope is not None:
        vault = TokenVault(vault_id=scope.vault_id, request_id=req.request_id, tokens=dict(scope.tokens),
                           ids=dict(scope.ids), counters=dict(scope.counters))
    else:
        vault = TokenVault(vault_id=stable_id("vault", req.request_id, sha256(text)), request_id=req.request_id)
    counters, ids = vault.counters, vault.ids
    coref = set(req.coreference_entities)
    surface_of: dict[str, str] = dict(vault.tokens)          # token -> first surface (scope state + this request)
    ekey = os.environ.get("PHI_ENCRYPTION_KEY")
    needs = {Operator(req.operators_by_entity.get(d.entity_type, req.operator)) for d in dets}
    pkey = os.environ.get("PHI_PSEUDONYM_KEY", "").encode()
    if Operator.PSEUDONYMIZE in needs and len(pkey) < 16:
        # no random fallback: pseudonyms must be stable and keyed with a managed secret (SP 800-188)
        raise EngineFailure(FailureClass.key_unavailable, "redaction", "pseudonym_key_missing")
    if Operator.ENCRYPT in needs and not ekey:
        raise EngineFailure(FailureClass.key_unavailable, "redaction", "encryption_key_missing")

    def value_for(d: Detection, op: Operator) -> str:
        surf = text[d.original_start:d.original_end]
        if op == Operator.REDACT:
            return f"[{d.entity_type}]"
        if op == Operator.TOKENIZE:
            k = (d.entity_type, _norm_surface(surf))
            if k not in ids and d.entity_type in coref:
                words = _name_words(surf)
                if words:     # partial mention of exactly one earlier full name of the same type -> same token
                    owners = {tok for (et, _), tok in ids.items()
                              if et == d.entity_type and words < _name_words(surface_of.get(tok, ""))}
                    if len(owners) == 1:
                        ids[k] = owners.pop()
            if k not in ids:
                counters[d.entity_type] = counters.get(d.entity_type, 0) + 1
                ids[k] = req.token_format.replace("{ENTITY}", d.entity_type).replace("{N}", str(counters[d.entity_type]))
                surface_of.setdefault(ids[k], surf)
            return ids[k]
        if op == Operator.PSEUDONYMIZE:
            h = hmac.new(pkey, f"{d.entity_type}|{_norm_surface(surf)}".encode(), hashlib.sha256).hexdigest()[:8]
            return f"{d.entity_type}-{h}"
        raise ValueError(op)

    results, operators, planned = [], {}, []
    for i, d in enumerate(dets):
        op = Operator(req.operators_by_entity.get(d.entity_type, req.operator))
        etype = f"D{i}"                       # one operator per detection keeps the mapping 1:1
        results.append(RecognizerResult(etype, d.original_start, d.original_end, 1.0))
        if op == Operator.ENCRYPT:
            operators[etype] = OperatorConfig("encrypt", {"key": ekey})
        else:
            val = value_for(d, op)
            operators[etype] = OperatorConfig("replace", {"new_value": val})
        planned.append((d, op))
    res = _ENGINE.anonymize(text=text, analyzer_results=results, operators=operators,
                            conflict_resolution=ConflictResolutionStrategy.REMOVE_INTERSECTIONS,
                            merge_entities_with_spaces=False)
    items = sorted(res.items, key=lambda it: it.start)
    if len(items) != len(planned):
        raise RuntimeError("anonymizer changed the number of spans; refusing to emit unverifiable output")
    reps = []
    for (d, op), it in zip(planned, items):
        surf = text[d.original_start:d.original_end]
        reps.append(Replacement(detection_id=d.detection_id, entity_type=d.entity_type, original_start=d.original_start,
                                original_end=d.original_end, output_start=it.start, output_end=it.end, operator=op,
                                replacement=it.text))
        if op in (Operator.TOKENIZE, Operator.PSEUDONYMIZE):
            vault.tokens.setdefault(it.text, surf)
            vault.occurrences.append((it.start, it.end, it.text, surf))
    resp = RedactionResponse(request_id=req.request_id, sanitized_text=res.text, operator=req.operator,
                             replacements=reps, vault_id=vault.vault_id if vault.occurrences else None,
                             original_sha256=sha256(text))
    return resp, vault
