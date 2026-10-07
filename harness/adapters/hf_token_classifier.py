"""Adapter for Hugging Face token-classification de-identifiers (Stanford, OBI, KindLab, LabBiomedica).

Tag schemes: IO (Stanford, KindLab), BIO (LabBiomedica), BILOU (OBI). Tag probabilities are
collapsed to entity-type probabilities; P(O) drives thresholding.

Long-text strategies (all return per-token probabilities for every token exactly once):
  author      the model's intended pipeline - Stanford: <=508-token windows cut at ':' ';'
              '.' (MIDRC code); OBI: 64-token cores with 32-token context each side
              (128-token training sequences); others: config `inference.author`
  production  overlapping windows: core `window` tokens + `context` tokens each side;
              predictions are taken only from the core, so every token sees context
  naive       hard cuts every (max_tokens-2) tokens, no context (worst case)
  truncate    first (max_tokens-2) tokens only; the rest is never seen (reported)

Threshold decoding (sweep only): token is PHI if 1 - P(O) >= t, typed by the most probable
entity type. threshold=None = argmax over tags (released behaviour).
"""
from __future__ import annotations

import logging
from typing import Optional

import numpy as np

from harness.adapters.base import BaseModelAdapter, RawOutput
from harness.schema import Prediction, TruncationInfo

log = logging.getLogger(__name__)
BREAK_TOKENS = (":", ";", ".")


class HFTokenClassificationAdapter(BaseModelAdapter):
    supports_scores = True
    filterable = True

    @property
    def default_threshold(self) -> Optional[float]:
        return None

    @property
    def icfg(self) -> dict:
        return self.entry.get("inference", {})

    def max_input_length(self) -> tuple[int, str]:
        return int(self.icfg.get("max_tokens", 512)), "subword_tokens"

    def _load(self) -> None:
        import torch
        from transformers import AutoModelForTokenClassification, AutoTokenizer

        mid, rev = self.entry["model_id"], self.entry["revision"]
        tok_id = self.entry.get("tokenizer_id")
        if tok_id:
            self.tokenizer = AutoTokenizer.from_pretrained(tok_id, add_prefix_space=True)
            self.runtime_notes.append(f"tokenizer loaded from {tok_id} (checkpoint repo ships none)")
        else:
            kw = {"add_prefix_space": True} if "roberta" in str(self.entry.get("architecture", "")).lower() else {}
            self.tokenizer = AutoTokenizer.from_pretrained(mid, revision=rev, **kw)
        if not self.tokenizer.is_fast:
            raise RuntimeError("fast tokenizer required for character offsets")
        model, info = AutoModelForTokenClassification.from_pretrained(mid, revision=rev, output_loading_info=True)
        missing = [k for k in info.get("missing_keys", []) if "classifier" in k]
        if missing:
            raise RuntimeError(f"classifier head not in checkpoint (randomly initialised): {missing}")
        if info.get("unexpected_keys"):
            self.runtime_notes.append(f"unused checkpoint keys: {sorted(info['unexpected_keys'])}")
        self.model = model.to(self.device).eval()
        self._torch = torch
        id2label = {int(k): v for k, v in model.config.id2label.items()}
        scheme = self.icfg.get("tag_scheme", "IO")
        outside = self.mapping.outside_label or "O"
        self.types = sorted({self._split(l, scheme)[1] for l in id2label.values() if l != outside})
        if set(self.types) != set(self.mapping.native_labels):
            raise RuntimeError(f"label mapping {sorted(self.mapping.native_labels)} != model types {self.types}")
        # tag index -> (type index into [O]+types, begins_span)
        self.tag_type = np.zeros(len(id2label), dtype=np.int64)
        self.tag_begin = np.zeros(len(id2label), dtype=bool)
        for i, lab in id2label.items():
            if lab == outside:
                self.tag_type[i] = 0
                continue
            prefix, typ = self._split(lab, scheme)
            self.tag_type[i] = 1 + self.types.index(typ)
            self.tag_begin[i] = prefix in ("B", "U")
        self._break_ids = {self.tokenizer.convert_tokens_to_ids(t) for t in BREAK_TOKENS}
        self._break_ids |= {i for i in (self.tokenizer.convert_tokens_to_ids("Ġ" + t) for t in BREAK_TOKENS)
                            if i != self.tokenizer.unk_token_id}

    @staticmethod
    def _split(label: str, scheme: str) -> tuple[str, str]:
        if scheme in ("BIO", "BILOU") and len(label) > 2 and label[1] == "-":
            return label[0], label[2:]
        return "I", label

    # ---- windowing ------------------------------------------------------------------
    def _windows(self, ids: list[int], word_ids: list[Optional[int]]) -> list[tuple[int, int, int, int]]:
        """(ctx_start, core_start, core_end, ctx_end) token windows."""
        n = len(ids)
        lim = int(self.icfg.get("max_tokens", 512)) - 2
        strat = self.long_text_strategy
        if strat == "truncate":
            e = min(n, lim)
            return [(0, 0, e, e)]
        if strat == "naive":
            return [(s, s, min(n, s + lim), min(n, s + lim)) for s in range(0, max(n, 1), lim)]
        cfg = self.icfg.get("production" if strat == "production" else "author", {})
        if strat == "author" and cfg.get("mode") == "break_tokens":
            return [(s, s, e, e) for s, e in self._break_windows(ids, word_ids, int(cfg.get("window", 508)))]
        win, ctx = int(cfg.get("window", lim)), int(cfg.get("context", 0))
        win = max(16, min(win, lim - 2 * ctx))   # core + both contexts must fit the model window
        if n <= lim and strat == "author":
            return [(0, 0, n, n)]
        out = []
        for s in range(0, max(n, 1), win):
            e = min(n, s + win)
            out.append((max(0, s - ctx), s, e, min(n, e + ctx)))
        return out

    def _break_windows(self, ids, word_ids, window: int) -> list[tuple[int, int]]:
        n = len(ids)
        if n <= 510:
            return [(0, n)]
        out, s = [], 0
        while s < n:
            e = min(s + window, n)
            if e < n:
                cut = None
                for j in range(e - 1, s, -1):
                    if ids[j] in self._break_ids:
                        cut = j + 1
                        break
                if cut is None:
                    cut = e
                    while cut > s + 1 and word_ids[cut] is not None and word_ids[cut] == word_ids[cut - 1]:
                        cut -= 1
                e = cut
            out.append((s, e))
            s = e
        return out

    def infer_raw(self, text: str, threshold: Optional[float] = None) -> RawOutput:
        return self.infer_raw_batch([text])[0]

    def infer_raw_batch(self, texts: list[str], threshold: Optional[float] = None) -> list[RawOutput]:
        torch = self._torch
        tok = self.tokenizer
        encs = [tok(t, add_special_tokens=False, return_offsets_mapping=True) for t in texts]
        jobs, per_text = [], []
        for i, enc in enumerate(encs):
            w = self._windows(enc["input_ids"], enc.word_ids())
            per_text.append(w)
            jobs.extend((i, *x) for x in w)
        results: list[np.ndarray] = []
        for b in range(0, len(jobs), 16):
            chunk = jobs[b:b + 16]
            seqs = [[tok.cls_token_id] + encs[i]["input_ids"][cs:ce] + [tok.sep_token_id] for i, cs, _, _, ce in chunk]
            L = max(len(x) for x in seqs)
            input_ids = torch.full((len(seqs), L), tok.pad_token_id, dtype=torch.long)
            attn = torch.zeros((len(seqs), L), dtype=torch.long)
            for r, x in enumerate(seqs):
                input_ids[r, :len(x)] = torch.tensor(x)
                attn[r, :len(x)] = 1
            with torch.inference_mode():
                logits = self.model(input_ids=input_ids.to(self.device), attention_mask=attn.to(self.device)).logits
            p = logits.float().softmax(-1).cpu().numpy()
            for r, (i, cs, ks, ke, ce) in enumerate(chunk):
                results.append(p[r, 1 + (ks - cs): 1 + (ke - cs)])
        outs, k = [], 0
        lim = int(self.icfg.get("max_tokens", 512))
        for i, enc in enumerate(encs):
            n = len(enc["input_ids"])
            w = per_text[i]
            parts = results[k:k + len(w)]
            k += len(w)
            seen = sum(ke - ks for _, ks, ke, _ in w)
            offsets = np.array(enc["offset_mapping"][:seen], dtype=np.int64).reshape(-1, 2)
            probs = np.concatenate(parts, axis=0) if parts else np.zeros((0, len(self.tag_type)))
            truncated = seen < n
            bounds = [int(offsets[ke - 1, 1]) for _, _, ke, _ in w[:-1] if 0 < ke <= len(offsets)]
            outs.append(RawOutput(payload={"offsets": offsets, "probs": probs}, truncation=TruncationInfo(
                strategy=self.long_text_strategy if n > lim - 2 else "none", max_supported=lim,
                unit="subword_tokens", input_length=n + 2, truncated=truncated,
                visible_char_end=int(offsets[-1, 1]) if truncated and len(offsets) else None,
                num_chunks=len(w), chunk_char_bounds=bounds)))
        return outs

    # ---- decoding -------------------------------------------------------------------
    def decode(self, raw: RawOutput, text: str, threshold: Optional[float]) -> list[Prediction]:
        offsets, probs = raw.payload["offsets"], raw.payload["probs"]
        if len(probs) == 0:
            return []
        n_types = len(self.types) + 1
        type_p = np.zeros((len(probs), n_types))
        np.add.at(type_p.T, self.tag_type, probs.T)
        p_phi = 1.0 - type_p[:, 0]
        if threshold is None:
            tag = probs.argmax(-1)
            ttype = self.tag_type[tag]
            begin = self.tag_begin[tag]
        else:
            ttype = np.where(p_phi >= threshold, 1 + type_p[:, 1:].argmax(-1), 0)
            begin = np.zeros(len(probs), dtype=bool)
        preds, cur = [], None
        for (s, e), t, b, sc in zip(offsets.tolist(), ttype.tolist(), begin.tolist(), p_phi.tolist()):
            if e <= s:
                continue
            if t == 0:
                if cur:
                    preds.append(self._mk(cur, text))
                    cur = None
                continue
            gap = text[cur[1]:s] if cur else ""
            new_word = gap != ""          # B-/U- only starts a new span at a word boundary
            if cur and cur[2] == t and not (b and new_word) and gap.strip() == "":
                cur[1] = e
                cur[3].append(sc)
            else:
                if cur:
                    preds.append(self._mk(cur, text))
                cur = [s, e, t, [sc]]
        if cur:
            preds.append(self._mk(cur, text))
        return preds

    def _mk(self, cur, text: str) -> Prediction:
        s, e, t, scores = cur
        while s < e and text[s].isspace():
            s += 1
        native = self.types[t - 1]
        return Prediction(start=s, end=e, text=text[s:e], native_label=native,
                          canonical_label=self.mapping.to_canonical(native), score=float(np.mean(scores)))
