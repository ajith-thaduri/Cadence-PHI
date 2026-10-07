"""Real-ASR condition for the Tier-3 Cadence-realistic set.

For each SPOKEN document, every speaker turn is:
  1. synthesised with the offline macOS TTS (`say`, a fixed voice per speaker/language),
  2. passed through a narrow-band TELEPHONE channel (8 kHz, 300-3400 Hz band-pass, additive
     noise at 15-25 dB SNR, mu-law companding),
  3. transcribed by a local Whisper model (openai/whisper-small, pinned revision),
  4. aligned back to the reference turn so each gold PHI span is mapped onto the ASR
     hypothesis. Entities whose words were entirely deleted by ASR are dropped from gold
     (no PHI characters remain to leak) and counted separately.

This produces genuine ASR-engine errors (digit formatting, name substitutions, split/merged
words, dropped punctuation). Limitations: synthetic voices are cleaner than real callers;
no cross-talk; ASR is Whisper-small, not Cadence's production ASR.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import subprocess
import tempfile
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import numpy as np
from scipy import signal
from scipy.io import wavfile

from harness.decision.markup import parse

log = logging.getLogger(__name__)
CACHE = Path(__file__).resolve().parents[2] / "data" / "cadence_realistic" / "asr_cache"   # per-turn ASR output (reproducibility)
from harness.phi.asr import WHISPER_ID, WHISPER_REV  # noqa: E402  single pinned definition
VOICES = {"en": ["Samantha", "Reed (English (US))", "Flo (English (US))", "Rocko (English (US))",
                 "Shelley (English (US))", "Grandpa (English (US))", "Sandy (English (US))", "Eddy (English (US))"],
          "es": ["Paulina", "Eddy (Spanish (Mexico))", "Flo (Spanish (Mexico))", "Reed (Spanish (Mexico))",
                 "Mónica", "Sandy (Spanish (Mexico))", "Grandma (Spanish (Mexico))", "Rocko (Spanish (Mexico))"]}
DIGITS = {"zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
          "seven": "7", "eight": "8", "nine": "9", "cero": "0", "uno": "1", "dos": "2", "tres": "3", "cuatro": "4",
          "cinco": "5", "seis": "6", "siete": "7", "ocho": "8", "nueve": "9"}
_TOK = re.compile(r"[A-Za-zÀ-ÿ'’]+|\d")   # each digit is its own token


def _norm_tokens(text: str) -> list[tuple[int, int, str]]:
    out = []
    for m in _TOK.finditer(text):
        w = m.group().lower().replace("’", "'")
        out.append((m.start(), m.end(), DIGITS.get(w, w)))
    return out


def tts(text: str, voice: str, path: Path) -> None:
    aiff = path.with_suffix(".aiff")
    # text via stdin (-f -): utterances starting with '-' must never be parsed as options
    subprocess.run(["say", "-v", voice, "-o", str(aiff), "-f", "-"], input=text.encode("utf-8"), check=True, capture_output=True)
    subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", str(aiff), str(path)], check=True, capture_output=True)
    aiff.unlink(missing_ok=True)


def telephone_channel(x: np.ndarray, sr: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    x = x.astype(np.float32) / 32768.0
    x8 = signal.resample_poly(x, 1, sr // 8000)
    b, a = signal.butter(4, [300 / 4000, 3400 / 4000], btype="band")
    x8 = signal.lfilter(b, a, x8)
    snr_db = rng.uniform(15, 25)
    p = np.mean(x8 ** 2) + 1e-12
    x8 = x8 + rng.normal(0, np.sqrt(p / 10 ** (snr_db / 10)), size=x8.shape)
    mu = 255.0
    x8 = np.clip(x8, -1, 1)
    x8 = np.sign(x8) * np.log1p(mu * np.abs(x8)) / np.log1p(mu)
    x8 = np.round(x8 * 127) / 127                     # 8-bit quantisation in the companded domain
    x8 = np.sign(x8) * ((1 + mu) ** np.abs(x8) - 1) / mu
    return signal.resample_poly(x8, sr // 8000, 1).astype(np.float32)


from harness.phi.asr import Transcriber  # noqa: E402  (runtime ASR stage; research code reuses it)


def _turns(text: str) -> list[tuple[str, str, int]]:
    """(speaker, utterance, char offset of utterance in text) for 'SPEAKER: words' lines."""
    out, pos = [], 0
    for line in text.split("\n"):
        m = re.match(r"\s*([A-Z][A-Z0-9_ ]{0,20}):\s?", line)
        if m:
            out.append((m.group(1).strip(), line[m.end():], pos + m.end()))
        elif line.strip():
            out.append(("NARRATOR", line, pos))
        pos += len(line) + 1
    return out


SIMILARITY_MIN = 0.6   # protocol deviation D1 (see decision report): garbled entities are not leaked PHI


NUMERIC_WORDS = set(DIGITS) | {
    "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen",
    "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety", "hundred", "thousand",
    "first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth", "eleventh",
    "twelfth", "thirteenth", "fourteenth", "fifteenth", "sixteenth", "seventeenth", "eighteenth", "nineteenth",
    "twentieth", "thirtieth", "diez", "once", "doce", "veinte", "treinta", "cuarenta", "cincuenta", "cien", "mil",
    "january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november",
    "december", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
    "noviembre", "diciembre", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo", "yesterday", "tomorrow", "ayer", "mañana"}


def _words(s: str) -> list[str]:
    return [w.lower() for w in re.findall(r"[A-Za-zÀ-ÿ']+|\d+", s)]


def _is_numeric(s: str) -> bool:
    return any(w.isdigit() or w in NUMERIC_WORDS or re.sub(r"(st|nd|rd|th)$", "", w).isdigit() for w in _words(s))


def surface_similarity(ref_surface: str, hyp_surface: str) -> float:
    """Does the ASR surface still carry the entity's identifying content? (0..1)
    Numeric entities (dates, IDs, phones, street numbers): 1.0 if the hypothesis still contains a
    digit, number/ordinal word, month or weekday - formatting changes ("twenty-eighth" -> "28th") do
    not remove PHI. Name-like entities: best token-level character similarity between reference and
    hypothesis tokens ("Delgado" -> "Volgado" 0.71 kept; "Tanya" -> "How here" 0.0 destroyed)."""
    if _is_numeric(ref_surface):
        return 1.0 if _is_numeric(hyp_surface) else 0.0
    def cands(x: str) -> list[str]:
        ws = _words(x)
        collapsed = "".join(ws)                      # spelled names: "D-E-L-A-C-R-O-I-X" -> "delacroix"
        return [w for w in ws if len(w) >= 2] + ([collapsed] if len(collapsed) >= 2 else [])
    rt, ht = cands(ref_surface), cands(hyp_surface)
    if not rt or not ht:
        return 0.0
    return max(SequenceMatcher(a=a, b=b, autojunk=False).ratio() for a in rt for b in ht)


def repetition_loop(text: str, min_repeats: int = 8) -> bool:
    toks = re.findall(r"\w+", text.lower())
    run = 1
    for x, y in zip(toks, toks[1:]):
        run = run + 1 if x == y else 1
        if run >= min_repeats:
            return True
    return False


def map_entities(ref: str, hyp: str, ents: list[dict]) -> tuple[list[dict], list[dict]]:
    """Map gold spans in the reference utterance onto the ASR hypothesis via token alignment."""
    rt, ht = _norm_tokens(ref), _norm_tokens(hyp)
    sm = SequenceMatcher(a=[t[2] for t in rt], b=[t[2] for t in ht], autojunk=False)
    r2h: dict[int, list[int]] = {}
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op in ("equal", "replace"):
            for i in range(i1, i2):
                if op == "equal":
                    r2h[i] = [j1 + (i - i1)]
                else:   # proportional mapping inside a replace block
                    lo = j1 + int((i - i1) * (j2 - j1) / max(1, i2 - i1))
                    hi = j1 + int((i - i1 + 1) * (j2 - j1) / max(1, i2 - i1))
                    r2h[i] = list(range(lo, max(lo + 1, hi))) if j2 > j1 else []
    mapped, dropped = [], []
    for e in ents:
        idx = [k for k, (s, t, _) in enumerate(rt) if s < e["end"] and t > e["start"]]
        hs = sorted({j for k in idx for j in r2h.get(k, []) if j < len(ht)})
        if not hs:
            dropped.append(e)
            continue
        s, t = ht[hs[0]][0], ht[hs[-1]][1]
        sim = surface_similarity(e["text"], hyp[s:t])
        mapped.append({**e, "start": s, "end": t, "text": hyp[s:t], "ref_text": e["text"], "similarity": round(sim, 3),
                       "garbled": sim < SIMILARITY_MIN,
                       "asr_altered": " ".join(x[2] for x in rt if x[0] < e["end"] and x[1] > e["start"]) !=
                                      " ".join(ht[j][2] for j in hs)})
    return mapped, dropped


def build_asr_document(rec: dict[str, Any], transcriber: Transcriber, work: Path) -> dict[str, Any]:
    clean, ents = parse(rec["text"])
    lang = rec.get("language", "en")
    voices = VOICES["es" if lang in ("es", "mixed") else "en"]
    speakers: dict[str, str] = {}
    out_lines, out_ents, dropped, pos = [], [], [], 0
    seed = int(hashlib.sha256(rec["id"].encode()).hexdigest()[:8], 16)
    for k, (spk, utt, off) in enumerate(_turns(clean)):
        if not utt.strip():
            continue
        voice = speakers.setdefault(spk, voices[(seed + len(speakers)) % len(voices)])
        key = hashlib.sha256(f"{voice}|{seed + k}|{lang}|{WHISPER_REV}|{utt}".encode()).hexdigest()[:24]
        cached = CACHE / f"{key}.txt"
        if cached.exists():
            hyp = cached.read_text(encoding="utf-8")
        else:
            wav = work / f"{rec['id']}_{k}.wav"
            tts(utt, voice, wav)
            sr, x = wavfile.read(wav)
            audio = telephone_channel(x, sr, seed + k)
            hyp = transcriber(audio, sr, {"en": "english", "es": "spanish"}.get(lang))
            CACHE.mkdir(parents=True, exist_ok=True)
            cached.write_text(hyp, encoding="utf-8")
        local = [{**e, "start": e["start"] - off, "end": e["end"] - off} for e in ents
                 if e["start"] >= off and e["end"] <= off + len(utt)]
        m, d = map_entities(utt, hyp, local)
        prefix = f"{spk}: " if spk != "NARRATOR" else ""
        base = pos + len(prefix)
        out_ents += [{**e, "start": e["start"] + base, "end": e["end"] + base} for e in m]
        dropped += d
        line = prefix + hyp
        out_lines.append(line)
        pos += len(line) + 1
    text = "\n".join(out_lines)
    for e in out_ents:
        assert text[e["start"]:e["end"]] == e["text"]
    ref_words = len(clean.split())
    garbled = [e for e in out_ents if e["garbled"]]
    return {"id": rec["id"] + "_asr", "text": text, "entities": [e for e in out_ents if not e["garbled"]],
            "garbled_by_asr": garbled, "dropped_by_asr": dropped, "repetition_loop": repetition_loop(text),
            "metadata": {"doc_type": rec["doc_type"], "language": lang, "speakers": speakers,
                         "asr": f"{WHISPER_ID}@{WHISPER_REV[:7]}", "channel": "8kHz band-pass 300-3400Hz, noise 15-25dB SNR, mu-law",
                         "reference_words": ref_words, "hypothesis_words": len(text.split()),
                         "source_id": rec["id"], "scenario": rec.get("scenario", "")}}
