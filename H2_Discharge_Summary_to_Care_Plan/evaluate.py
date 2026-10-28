#!/usr/bin/env python3
"""H2 - Discharge Summary to Care Plan: official scorer.

    python evaluate.py --submission submission/H2_predictions.jsonl --truth data/dev_fact_key.jsonl [--out score.json]

Standard library + numpy + pandas only. Faithfulness is scored above fluency.
See README.md ("How you're scored") for the full, plain-English description.
"""
from __future__ import annotations

import argparse
import collections
import difflib
import json
import math
import re
import sys
import unicodedata
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

# =============================================================================== constants (repeated in README)
W_MED_RECALL = 0.30      # share of source medicines present in care_plan.medicines
W_DOSE = 0.20            # dose exactly as in the source
W_FREQ = 0.15            # frequency class (once/twice/three/four times a day, SOS, weekly)
W_DURATION = 0.10        # duration (days / till review / lifelong)
W_FOLLOWUP = 0.15        # follow-up dates recalled (exact YYYY-MM-DD)
W_STOP = 0.10            # stop-instructions recalled (weight re-distributed if the source has none)

PENALTY_DROPPED_MED = 1.0     # each source medicine missing from care_plan.medicines
PENALTY_ALTERED_DOSE = 1.0    # each matched medicine whose dose is not exactly the source dose
PENALTY_INVENTED_MED = 1.0    # each care_plan medicine not in the source (incl. stopped medicine listed as 'take',
                              # a name that also contains another drug, or a duplicate entry for the same medicine)
PENALTY_WRONG_STOP = 1.0      # each stop_medicines entry that is an active medicine or not a source stop instruction
PENALTY_INVENTED_FOLLOWUP = 1.0  # each follow-up date not in the source

W_STRUCTURED = 0.80      # per-summary blend: structured part (after penalties, floored at 0)
W_TEXT = 0.20            # per-summary blend: text-faithfulness part
W_TEXT_EN = 0.50         # inside text part: plain_text_en
W_TEXT_TRANSLATION = 0.50  # inside text part: translation.text
TEXT_INVENTED_DRUG_PENALTY = 0.25  # per drug name in the text that appears nowhere in the source summary
NAME_DOSE_WINDOW = 200   # dose numbers must appear within this many characters of the medicine name in the text
MIN_NATIVE_SCRIPT_SHARE = 0.50  # translation: share of letters that must be in the declared language's script
MAX_LATIN_WORD_SHARE = 0.35     # translation: max share of Latin-script words that are not drug names / units / codes
MAX_TOP_WORD_SHARE = 0.15       # translation: max share of the single most frequent native-script word (anti-filler)
TRANSLATION_LENGTH_RATIO = (0.4, 3.0)  # translation length must be within this ratio of plain_text_en length
MAX_SOURCE_COPY_SHARE = 0.50    # text: max share of its words that sit in runs of >= COPY_MIN_RUN words copied in order from the source
COPY_MIN_RUN = 4                # minimum run length (words) counted as copied
MIN_FUNCTION_WORDS = 3          # translation: distinct common words of the language that must appear (where a list exists)
FUZZY_NAME_RATIO = 0.88  # difflib ratio for matching a misspelt medicine name
MAX_FIELD_LEN = 300      # max characters in name/dose/frequency/timing/duration/date/with_whom/stop entries
MAX_TEXT_LEN = 100_000   # max characters in plain_text_en / translation.text / how_to_take_plain etc.
MAX_MEDICINES = 100      # medicines per summary beyond this are ignored (with a warning)

LANG_SCRIPTS = {  # language_code -> unicode block(s) of its script
    "hi": [(0x0900, 0x097F)], "mr": [(0x0900, 0x097F)], "ne": [(0x0900, 0x097F)], "kok": [(0x0900, 0x097F)],
    "bn": [(0x0980, 0x09FF)], "as": [(0x0980, 0x09FF)], "pa": [(0x0A00, 0x0A7F)], "gu": [(0x0A80, 0x0AFF)],
    "or": [(0x0B00, 0x0B7F)], "ta": [(0x0B80, 0x0BFF)], "te": [(0x0C00, 0x0C7F)], "kn": [(0x0C80, 0x0CFF)],
    "ml": [(0x0D00, 0x0D7F)], "ur": [(0x0600, 0x06FF), (0x0750, 0x077F)],
}
HINDI_MONTHS = ["जनवरी", "फ़रवरी", "मार्च", "अप्रैल", "मई", "जून", "जुलाई", "अगस्त", "सितंबर", "अक्टूबर", "नवंबर", "दिसंबर"]
EN_MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"]
REQUIRED_CP_LISTS = ["medicines", "stop_medicines", "warning_signs", "diet", "activity", "follow_ups"]
MED_KEYS = ["name", "dose", "frequency", "timing", "duration", "how_to_take_plain"]
FU_KEYS = ["date", "with_whom", "purpose"]

# names too generic to count as a drug mention in free text
TEXT_SCAN_STOPLIST = {"iron", "calcium", "vitamin d", "vit d", "insulin", "fdc", "asa", "pcm", "att", "akt", "arni", "ufh",
                      "kcl", "lmwh", "ifa", "isdn", "mgso4", "pan", "basal insulin", "plain insulin", "soluble insulin",
                      "short-acting insulin", "rapid-acting insulin", "premix insulin", "premixed insulin", "regular insulin",
                      "human regular insulin", "glargine", "aspart", "mixtard", "30/70 insulin", "insulin 30/70",
                      "vitamin b6", "vit b6", "calcium carbonate", "folic acid", "iron folic acid", "iron and folic acid",
                      "ferrous sulphate", "ferrous sulfate", "metoprolol", "budesonide", "formoterol", "amoxicillin",
                      "amoxycillin", "sacubitril", "valsartan", "vitamin d3", "vit d3", "vitamin b6"}
NEGATION = re.compile(r"\b(avoid|(do not|don't|never) (take|use|buy|give|start)|without (asking|advice))\b[^.;\n]{0,40}$")
NEGATION_BREAK = re.compile(r"\b(skip|forget|miss|stop|stopping|missing|forgetting)\b")
FUNCTION_WORDS = {  # very common words; a real translation contains several of them
    "hi": "है हैं और के की का को में से पर न नहीं या यह लें करें दिन बार तक भी एक हर अगर तो",
    "mr": "आहे आणि व किंवा नाही न हे ही दिवस वेळा घ्या करा पर्यंत मध्ये एक जर तर सकाळी रात्री",
    "ne": "छ र को मा ले हो दिन पटक वा पनि एक गर्नुहोस्",
    "bn": "এবং ও না দিন বার করে করুন খান এই যে থেকে জন্য পর্যন্ত বা একটি আর হবে",
    "gu": "અને છે ના નહીં દિવસ વખત કરો લો માં થી સુધી એક અથવા જો તો",
    "pa": "ਅਤੇ ਹੈ ਦੇ ਦੀ ਦਾ ਨੂੰ ਵਿੱਚ ਤੋਂ ਨਾ ਦਿਨ ਵਾਰ ਲਓ ਕਰੋ ਤੱਕ ਜਾਂ ਇੱਕ",
    "or": "ଏବଂ ଓ ନାହିଁ ଦିନ ଥର କରନ୍ତୁ ଏହି ବା ପର୍ଯ୍ୟନ୍ତ ଏକ",
    "ta": "மற்றும் ஒரு நாள் முறை இல்லை வேண்டும் இந்த அல்லது வரை பிறகு முன் தினமும்",
    "te": "మరియు ఒక రోజు సార్లు లేదా కాదు ఈ వరకు తర్వాత ముందు తీసుకోండి రోజుకు",
    "kn": "ಮತ್ತು ಒಂದು ದಿನ ಬಾರಿ ಅಥವಾ ಇಲ್ಲ ಈ ವರೆಗೆ ನಂತರ ಮೊದಲು ತೆಗೆದುಕೊಳ್ಳಿ",
    "ml": "ഒരു ദിവസം തവണ അല്ലെങ്കിൽ ഇല്ല ഈ വരെ ശേഷം മുമ്പ് കഴിക്കുക",
    "ur": "اور ہے کے کی کا کو میں سے نہ نہیں دن بار لیں کریں تک یا ایک",
}  # 'as' and 'kok' have no list: the function-word check is skipped for them
NUM_WORDS = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7", "eight": "8",
             "nine": "9", "ten": "10", "eleven": "11", "twelve": "12", "fourteen": "14", "fifteen": "15", "twenty": "20",
             "thirty": "30", "a week": "1 week", "a month": "1 month", "a year": "1 year", "fortnight": "2 weeks"}

HERE = Path(__file__).resolve().parent


class FormatError(Exception):
    pass


# =============================================================================== text helpers

def ascii_digits(s: str) -> str:
    return "".join(str(unicodedata.digit(ch)) if ch.isdigit() and not ch.isascii() else ch for ch in s)


def norm(s: str) -> str:
    s = (s or "").replace("½", " 0.5 ").replace("¼", " 0.25 ").replace("¾", " 0.75 ")   # before NFKC (which makes '1⁄2')
    s = unicodedata.normalize("NFKC", ascii_digits(s)).lower().replace("\u2044", "/")
    s = re.sub("[\u2010-\u2015\u2212]", "-", s)   # en/em dashes, minus -> '-' ('1–0–1')
    s = re.sub(r"[^\S\n]+", " ", s)          # collapse spaces but keep line breaks
    return re.sub(r" ?\n ?", "\n", s).strip()


_UNITS_W = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9}
_TENS_W = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}


def norm_field(s: str) -> str:
    """for dose/frequency/duration fields: also number words and dotted abbreviations (b.d. -> bd)"""
    s = norm(s)[:MAX_FIELD_LEN * 2]
    s = re.sub(r"\b([a-z])\.([a-z])\.(?:([a-z])\.)?(?:([a-z])\.)?", lambda m: "".join(g for g in m.groups() if g), s)
    s = re.sub(r"\b(" + "|".join(_TENS_W) + r")[- ](" + "|".join(_UNITS_W) + r")\b",
               lambda m: str(_TENS_W[m.group(1)] + _UNITS_W[m.group(2)]), s)
    for w, d in _TENS_W.items():
        s = re.sub(r"\b" + w + r"\b", str(d), s)
    for w, d in NUM_WORDS.items():
        s = re.sub(r"\b" + w + r"\b", d, s)
    return s


_PREFIX = re.compile(r"^(tab(let)?s?\.?|t\.|cap(sule)?s?\.?|c\.|inj(ection)?\.?|syp\.?|syrup|inh(aler)?\.?|mdi|rotacap|"
                     r"e/d|eye drops?|oint(ment)?\.?|sachet|sl)\s+")


def norm_name(s: str) -> str:
    s = norm(s)
    for _ in range(2):
        s = _PREFIX.sub("", s)
    s = re.sub(r"\([^)]*\d[^)]*\)", " ", s)              # strength labels in brackets
    s = re.sub(r"\b\d{1,7}(\.\d{1,4})?\s*(mg|mcg|g|ml|iu|units?|%|k)?\b", " ", s)
    s = re.sub(r"[^a-z0-9+/\- ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _ref_rows(path: Path):
    df = pd.read_csv(path, dtype=str).fillna("")
    for _, row in df.iterrows():
        g = row["generic_name"].strip().lower()
        ns = {g} | {p.strip() for p in g.split(" + ")}
        ns |= {b.strip().lower() for b in row["common_brands"].split(";") if b.strip()}
        ns |= {a.strip().lower() for a in row.get("also_known_as", "").split(";") if a.strip()}
        yield g, ns


def load_drug_reference(path: Path) -> dict:
    """generic -> set of normalised names (for structured matching); raw lower-case names kept under key (g, 'raw')."""
    names = {}
    for g, ns in _ref_rows(path):
        names[g] = {norm_name(n) for n in ns if norm_name(n)} | {n for n in ns if len(n) >= 4}
    return names


# Lookup-only list of common Indian drugs that are NOT in drug_reference.csv (never prescribed in this dataset).
# Used only to recognise invented medicines (in names and in free text); none of them is a source drug.
NON_REFERENCE_DRUGS = [
    "domperidone", "pan-d", "pan d", "razo-d", "hydrochlorothiazide", "hctz", "chlorthalidone", "teneligliptin", "voglibose",
    "fenofibrate", "olmesartan", "cilnidipine", "levocetirizine", "cetirizine", "ranitidine", "famotidine", "omeprazole",
    "esomeprazole", "chlorzoxazone", "serratiopeptidase", "thiocolchicoside", "nimesulide", "mefenamic acid", "dicyclomine",
    "drotaverine", "warfarin", "ivabradine", "trimetazidine", "nebivolol", "glipizide", "gliclazide", "pioglitazone",
    "isosorbide mononitrate", "nicorandil", "ranolazine", "metolazone", "eplerenone", "metoprolol tartrate", "atenolol",
    "ciprofloxacin", "ofloxacin", "norfloxacin", "nitrofurantoin", "cefpodoxime", "linezolid",
    "theophylline", "doxofylline", "deriphyllin", "ambroxol", "levosalbutamol", "pregabalin", "gabapentin", "methylcobalamin",
    "ursodeoxycholic acid", "sucralfate", "itopride", "rifaximin", "albendazole", "ivermectin", "dexamethasone",
    "methylprednisolone", "deflazacort", "clonazepam", "alprazolam", "zolpidem", "insulin detemir", "insulin degludec",
    "insulin lispro", "dulaglutide", "semaglutide", "liraglutide", "cinnarizine", "betahistine",
]


def _add_non_reference(lex):
    for n in NON_REFERENCE_DRUGS:
        if n not in lex:              # never shadows a reference drug name
            lex[n] = {"non-reference:" + n}


FOREIGN_SKIP = {"iron", "calcium", "insulin", "vitamin d", "vit d", "vitamin d3", "vit d3", "vitamin b6", "vit b6", "fdc", "att",
                "akt", "ifa", "pan", "asa", "pcm", "folic acid", "basal insulin", "plain insulin"}


def load_foreign_lexicon(path: Path) -> dict:
    """lexicon for medicine/stop NAMES: every reference name (generic, components, brands, aliases) except truly
    generic words. Multi-word names are NOT split into single words, so salt / release qualifiers
    ('pantoprazole sodium', 'amlodipine besylate', 'metoprolol succinate ER') never look like another drug."""
    lex: dict = {}
    for g, ns in _ref_rows(path):
        for n in ns:
            if len(n) >= 4 and n not in FOREIGN_SKIP:
                lex.setdefault(n, set()).add(g)
    _add_non_reference(lex)
    return lex


def load_lexicon(path: Path) -> dict:
    """free-text drug lexicon: name -> set(generic). Used for 'invented drug' checks."""
    lex: dict = {}
    for g, ns in _ref_rows(path):
        for n in ns:
            if len(n) >= 4 and n not in TEXT_SCAN_STOPLIST:
                lex.setdefault(n, set()).add(g)
    _add_non_reference(lex)
    return lex


_LEX_CACHE: dict = {}


def _lex_re(lex):
    key = id(lex)
    if key not in _LEX_CACHE:
        names = sorted(lex, key=len, reverse=True)
        _LEX_CACHE[key] = re.compile(r"(?<![a-z0-9])(" + "|".join(re.escape(n) for n in names) + r")(?![a-z0-9])")
    return _LEX_CACHE[key]


def scan_mentions(text: str, lex: dict, skip_negated=False):
    """-> list of sets of generics, one set per drug-name mention (a name may map to several generics)."""
    t = norm(text)
    out = []
    for m in _lex_re(lex).finditer(t):
        if skip_negated:
            before = t[max(0, m.start() - 60):m.start()]
            neg = NEGATION.search(before)
            if neg and not NEGATION_BREAK.search(before[neg.start():]):
                continue
        out.append(lex[m.group(1)])
    return out


def mention_spans(text_norm: str, lex: dict):
    return [(m.start(), m.end()) for m in _lex_re(lex).finditer(text_norm)]


def scan_text_drugs(text: str, lex: dict) -> set:
    found = set()
    for s in scan_mentions(text, lex):
        found |= s
    return found


# =============================================================================== matching

def match_generic(student_name: str, generics: list, names: dict, taken=()):
    """Return the generic (from `generics`) that a student medicine name refers to, or None.
    Best = lowest rank (exact < contains < contained < fuzzy), then longest matching name, then a generic not yet taken."""
    s = norm_name(student_name)
    if not s:
        return None
    best, best_rank = None, (9, 0, 1)
    for g in generics:
        for n in names.get(g, {g}):
            if not n:
                continue
            if s == n:
                rank = 0
            elif len(n) >= 4 and re.search(r"(?<![a-z0-9])" + re.escape(n) + r"(?![a-z0-9])", s):
                rank = 1
            elif len(s) >= 5 and re.search(r"(?<![a-z0-9])" + re.escape(s) + r"(?![a-z0-9])", n):
                rank = 2
            elif len(n) >= 5 and max(difflib.SequenceMatcher(None, s, n).ratio(),
                                     max((difflib.SequenceMatcher(None, w, n).ratio() for w in s.split()), default=0)) >= FUZZY_NAME_RATIO:
                rank = 3
            else:
                continue
            key = (rank, -len(n), 1 if g in taken else 0)
            if key < best_rank:
                best, best_rank = g, key
    return best


def foreign_drug(name: str, allowed: set, lex: dict) -> bool:
    """True if the name mentions a reference drug that is none of the `allowed` source generics
    (e.g. 'Amlodipine / Telmisartan' when telmisartan is not a source medicine)."""
    return any(not (s & allowed) for s in scan_mentions(name, lex))


# =============================================================================== dose / frequency / duration

UNIT_FAMILY = {
    "mg": ("mass", 1.0), "milligram": ("mass", 1.0), "milligrams": ("mass", 1.0),
    "mcg": ("mass", 0.001), "µg": ("mass", 0.001), "ug": ("mass", 0.001), "microgram": ("mass", 0.001), "micrograms": ("mass", 0.001),
    "g": ("mass", 1000.0), "gm": ("mass", 1000.0), "gram": ("mass", 1000.0), "grams": ("mass", 1000.0),
    "ml": ("vol", 1.0), "millilitre": ("vol", 1.0), "milliliter": ("vol", 1.0), "millilitres": ("vol", 1.0),
    "u": ("unit", 1.0), "unit": ("unit", 1.0), "units": ("unit", 1.0), "iu": ("unit", 1.0), "i.u.": ("unit", 1.0),
    "puff": ("puff", 1.0), "puffs": ("puff", 1.0),
    "drop": ("drop", 1.0), "drops": ("drop", 1.0),
    "tab": ("tablet", 1.0), "tabs": ("tablet", 1.0), "tablet": ("tablet", 1.0), "tablets": ("tablet", 1.0),
    "cap": ("capsule", 1.0), "caps": ("capsule", 1.0), "capsule": ("capsule", 1.0), "capsules": ("capsule", 1.0),
    "rotacap": ("capsule", 1.0), "rotacaps": ("capsule", 1.0),
    "sachet": ("sachet", 1.0), "sachets": ("sachet", 1.0),
}
_NUM = r"(\d{1,7}(?:,\d{3}){0,2}(?:\.\d{1,4})?k?|\d{1,3}/\d{1,3}|0?\.\d{1,4})"
_UNIT_RE = "|".join(sorted((re.escape(u) for u in UNIT_FAMILY), key=len, reverse=True))
_PAIR_RE = re.compile(_NUM + r"((?:\s{0,3}-\s{0,3}(?:" + _NUM[1:-1] + r")){0,4})\s{0,3}(" + _UNIT_RE + r")(?![a-z])")


def to_num(t: str) -> float:
    t = t.replace(",", "")
    if t.endswith("k"):
        return float(t[:-1]) * 1000
    if "/" in t:
        a, b = t.split("/")
        return float(a) / float(b) if float(b) else float("nan")
    return float(t)


def dose_pairs(s: str):
    """-> list of (family, value_in_base_unit); handles '16-0-10 units'."""
    s = norm_field(s)
    out = []
    for m in _PAIR_RE.finditer(s):
        fam, mult = UNIT_FAMILY[m.group(3)]
        nums = [m.group(1)] + [x.strip() for x in m.group(2).split("-") if x.strip()]
        for t in nums:
            # '24/26 mg' or '6/200 mcg' = two component strengths; '1/2 tab' = a half
            parts = t.split("/") if ("/" in t and fam == "mass") else [t]
            for t2 in parts:
                try:
                    v = to_num(t2)
                except ValueError:
                    continue
                if v > 0:
                    out.append((fam, round(v * mult, 6)))
    return out


def bare_numbers(s: str):
    return [to_num(x) for x in re.findall(r"(?<![\d.])(\d{1,7}(?:,\d{3}){0,2}(?:\.\d{1,4})?k?)(?![\d])", norm_field(s))]


HALF_RE = re.compile(r"(\bhalf\b|\b1/2\b|\b0\.5\s*(tab|tablet))")


def _fam_val(v, u):
    fam, mult = UNIT_FAMILY[u.lower()] if u.lower() in UNIT_FAMILY else UNIT_FAMILY[u.lower().rstrip("s")]
    return fam, round(float(v) * mult, 6)


def _allowed(alts):
    allowed = {}
    for a in alts:
        for v, u in a["values"]:
            fam, val = _fam_val(v, u)
            allowed.setdefault(fam, set()).add(val)
    return allowed


def _in(x, vals):
    return any(abs(x - v) <= 1e-6 * max(1, v) for v in vals)


MAX_PHRASE = re.compile(r"\b(max(imum)?|not more than|no more than|up ?to|upto|at most)\b\.?\s*:?\s*\d[^;,()\n]{0,40}")


def strip_maxima(s: str, source_amounts=()) -> str:
    """'2 puffs (max 8 puffs/day)' -> '2 puffs ( )' when the source itself states 'max 8 puffs/day'.
    A max / up-to phrase with any amount the source does not state is KEPT, so its amount counts as a dose."""
    def repl(m):
        pairs = dose_pairs(m.group(0))
        if pairs and all(any(fa == fb and _in(va, {vb}) for fb, vb in source_amounts) for fa, va in pairs):
            return " "
        return m.group(0)
    return MAX_PHRASE.sub(repl, norm_field(s))


ALT_OPTION = re.compile(r"\b(double|triple|twice the dose|extra dose|or more|increase)\b")


def _source_extra_amounts(truth_med):
    """amounts written in the source's own extra instructions / SOS text (e.g. 'max 8 puffs/day') are allowed."""
    txt = " ; ".join((truth_med.get("extra_instructions_as_written") or []) + [truth_med.get("sos_indication_as_written") or ""])
    return dose_pairs(txt)


def dose_ok(truth_med: dict, dose_texts: list, name_texts: list) -> bool:
    alts = truth_med.get("dose_alternatives") or []
    if not alts:
        return True  # e.g. ointment: no numeric dose in source
    src_amounts = _source_extra_amounts(truth_med)
    if any(ALT_OPTION.search(norm_field(x)) for x in dose_texts):
        return False     # alternative / escalating dose options not in the source
    dose_texts = [strip_maxima(x, src_amounts) for x in dose_texts]
    dose_txt = " ; ".join(dose_texts)
    pairs = dose_pairs(dose_txt + " ; " + " ; ".join(name_texts))
    if not dose_pairs(dose_txt):
        # bare numbers in the dose field ("650") take the source unit
        u = alts[0]["values"][0][1]
        pairs += [_fam_val(v, u) for v in bare_numbers(dose_txt)]
    half = bool(HALF_RE.search(norm(dose_txt)))
    allowed = _allowed(alts)
    # counts (tablets, capsules, puffs, drops, sachets): when the source dose is not a count, only '1' (or '½' for a
    # half-tablet prescription) may appear - '2 tablets' of a 500 mg prescription is a doubled dose
    has_half = any(a.get("requires_half") for a in alts)
    for fam, val in pairs:
        if fam in COUNT_FAMILIES and fam not in allowed and not (_in(val, {1.0}) or (has_half and _in(val, {0.5}))):
            return False
    for a in alts:
        need = [_fam_val(v, u) for v, u in a["values"]]
        fam = need[0][0]
        got = {val for f, val in pairs if f == fam}
        if a.get("requires_half") and not half:
            continue
        if all(_in(val, got) for _, val in need) and all(_in(g, allowed[fam]) for g in got):
            return True
    return False


SLOT_WORDS = [r"morning|breakfast|\bam\b", r"afternoon|lunch|noon|midday", r"evening|dinner|supper", r"night|bed ?time|nightly"]


def freq_class(s: str):
    s = norm_field(s)
    if not s:
        return None
    if re.search(r"\b(sos|prn|as needed|when needed|if needed|when required|as required|only if|only when|if required)\b", s):
        return "SOS"
    if re.search(r"week|/wk\b|\bweekly\b|every (monday|tuesday|wednesday|thursday|friday|saturday|sunday)", s):
        return "WEEKLY"
    m = re.search(r"(?<![\d/.])(\d{1,3}(?:\.\d{1,2})?)\s*-\s*(\d{1,3}(?:\.\d{1,2})?)\s*-\s*(\d{1,3}(?:\.\d{1,2})?)(?:\s*-\s*(\d{1,3}(?:\.\d{1,2})?))?(?![\d/])", s)
    if m:
        n = sum(1 for g in m.groups() if g is not None and float(g) > 0)
        if 1 <= n <= 4:
            return n
    hrs = r"(hours?|hrs?|h)\b"
    if re.search(r"\b(qid|qds)\b|four times|4 times|\b4\s*x\b|every 6 " + hrs + r"|6[- ]hourly|\bq6h\b", s):
        return 4
    if re.search(r"\b(tds|tid)\b|three times|thrice|3 times|\b3\s*x\b|every 8 " + hrs + r"|8[- ]hourly|\bq8h\b", s):
        return 3
    if re.search(r"\b(bd|bid)\b|twice|two times|2 times|\b2\s*x\b|every 12 " + hrs + r"|12[- ]hourly|\bq12h\b", s):
        return 2
    if re.search(r"\b(od|hs|qd)\b|once|one time|1 time|\b1\s*x\b|every 24 " + hrs + r"|\bq24h\b", s):
        return 1      # an explicit 'once' wins over time words ('once daily at night after dinner')
    if re.search(r"daily|every day|per day|a day|every (morning|evening|night)|nightly|at (bed ?time|night)", s):
        n = sum(1 for p in SLOT_WORDS if re.search(p, s))
        return n if n >= 2 else 1
    n = sum(1 for p in SLOT_WORDS if re.search(p, s))
    return n or None


TRUTH_FREQ = {"OD": 1, "HS": 1, "BD": 2, "TDS": 3, "QID": 4, "SOS": "SOS", "WEEKLY": "WEEKLY"}


def dur_parse(s: str):
    s = norm_field(s)[:MAX_FIELD_LEN]
    s = re.sub(r"\b\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b", " ", s)   # calendar dates are not durations
    if not s:
        return None, []
    vals = []
    for m in re.finditer(r"(\d{1,4})\s*/\s*(7|52|12)\b", s):
        n, b = int(m.group(1)), m.group(2)
        vals.append(n * {"7": 1, "52": 7, "12": 30}[b])
    s2 = re.sub(r"(\d{1,4})\s*/\s*(7|52|12)\b", " ", s)
    for m in re.finditer(r"(\d{1,4}(?:\.\d{1,2})?)\s*(days?|d\b|weeks?|wks?|w\b|months?|mo\b|years?|yrs?|yr\b)", s2):
        n, u = float(m.group(1)), m.group(2)
        mult = 1 if u.startswith("d") else 7 if u.startswith("w") else 30 if u.startswith("mo") else 365 if u.startswith("y") else 1
        vals.append(int(round(n * mult)))
    if vals:
        return "days", vals
    if re.search(r"life ?-?long|for life|lifetime|rest of (your|his|her|the) life|long[- ]term|permanent|always", s):
        return "lifelong", []
    if re.search(r"(till|until|untill|upto|up to)\b.{0,25}\b(review|visit|follow|check|appointment|next|see|doctor (says|tells|asks|advises))", s) \
            or "next review" in s:
        return "till_review", []
    return None, []


def _close(a, b):
    """exact below 4 weeks; from 28 days +-2 days or 7% (so 12 weeks ~ 3 months, 26 weeks ~ 6 months)"""
    return abs(a - b) <= (max(2, 0.07 * b) if b >= 28 else 0)


def duration_ok(truth_med: dict, dur_texts: list, other_texts: list) -> bool:
    """All matched entries must agree with the source duration (no hedging with several values)."""
    t = truth_med.get("duration_type")
    if t in (None, "none"):
        return True
    parsed = [dur_parse(d) for d in dur_texts]
    kinds = {k for k, _ in parsed if k}
    vals = [v for _, vs in parsed for v in vs]
    if t in ("lifelong", "till_review"):
        return kinds == {t}
    if kinds != {"days"}:
        return False
    target = truth_med.get("duration_days")
    if truth_med.get("taper"):
        steps = [st["duration_days"] for st in truth_med["taper"]]
        ok_vals = set(steps) | {target}
        _, v2 = dur_parse(" ; ".join(other_texts))
        allv = vals + v2
        if not all(any(_close(v, x) for x in ok_vals) for v in vals):
            return False
        if any(_close(v, target) for v in allv) or _close(sum(vals), target):
            return True
        rem = list(allv)
        for sd in steps:
            if sd in rem:
                rem.remove(sd)
            else:
                return False
        return True
    return all(_close(v, target) for v in vals) or (len(vals) > 1 and _close(sum(vals), target) and len(dur_texts) == 1)


# =============================================================================== text checks

def _num_strs(v: str, unit: str = ""):
    f = float(v)
    out = {v}
    if f.is_integer():
        out.add(str(int(f)))
        if f >= 1000:
            out.add(f"{int(f):,}")
    if unit == "mg" and f >= 1000 and (f / 1000).is_integer():
        out.add(f"{int(f / 1000)} g")      # '1 g' for 1000 mg
    return out


def date_strings(d: date):
    dd, mm = f"{d.day:02d}", f"{d.month:02d}"
    yy = str(d.year)[2:]
    out = {d.isoformat(), f"{dd}/{mm}/{d.year}", f"{dd}-{mm}-{d.year}", f"{d.day}/{d.month}/{d.year}",
           f"{d.day}-{d.month}-{d.year}", f"{dd}.{mm}.{d.year}", f"{dd}/{mm}/{yy}", f"{dd}-{mm}-{yy}", f"{d.day}/{d.month}/{yy}",
           f"{d.year}/{mm}/{dd}", f"{d.year}.{mm}.{dd}"}
    suf = "th" if 11 <= d.day <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(d.day % 10, "th")
    mon, mon3 = EN_MONTHS[d.month - 1], EN_MONTHS[d.month - 1][:3]
    for day in (str(d.day), dd, f"{d.day}{suf}"):
        out |= {f"{day} {mon} {d.year}", f"{day} {mon3} {d.year}", f"{day}-{mon3}-{d.year}", f"{day}-{mon3}-{str(d.year)[2:]}",
                f"{day} {mon}, {d.year}", f"{day} {mon3}, {d.year}",
                f"{mon} {day}, {d.year}", f"{mon3} {day}, {d.year}",
                f"{day} {HINDI_MONTHS[d.month - 1]} {d.year}", f"{day} {HINDI_MONTHS[d.month - 1].replace('़', '')} {d.year}"}
    return {norm(x) for x in out}


COPY_FILLER = {"ok", "a", "an", "the", "and", "or", "of", "to", "is", "in", "on", "at", "for", "with", "by", "it", "so", "yes",
               "uh", "um", "pls", "please", "-", "x"}


def shingle_copy_share(text: str, source: str, k: int = COPY_MIN_RUN) -> float:
    """share of the text's content words that lie in runs of >= k words copied in order from the source.
    Short filler words are ignored on both sides, so inserting 'ok' every few words does not hide a copy."""
    a = [w for w in re.findall(r"\w+", norm(text)) if w not in COPY_FILLER]
    b = [w for w in re.findall(r"\w+", norm(source)) if w not in COPY_FILLER]
    if len(a) < k or len(b) < k:
        return 0.0
    src = {tuple(b[i:i + k]) for i in range(len(b) - k + 1)}
    covered = [False] * len(a)
    for i in range(len(a) - k + 1):
        if tuple(a[i:i + k]) in src:
            for j in range(i, i + k):
                covered[j] = True
    return sum(covered) / len(a)


COUNT_FAMILIES = {"tablet", "capsule", "puff", "drop", "sachet"}


def text_score(text: str, truth: dict, names: dict, lex: dict, source: str = "", flex: dict | None = None):
    t = norm(text)[:MAX_TEXT_LEN]
    items, hits = 0, 0
    spans = mention_spans(t, flex if flex is not None else lex)
    for med in truth["medicines"]:
        items += 1
        g = med["generic_name"]
        cand = set(names.get(g, {g}))
        if med.get("brand_as_written"):
            cand |= {norm_name(med["brand_as_written"]), med["brand_as_written"].lower()}
        pos = []
        for n in cand:
            if len(n) < 3:
                continue
            for m in re.finditer(r"(?<![a-z0-9])" + re.escape(n) + r"(?![a-z0-9])", t):
                pos.append(m.start())
        if not pos:
            continue
        alts = med.get("dose_alternatives") or []
        if not alts:
            hits += 1
            continue
        allowed = _allowed(alts)
        src_amounts = _source_extra_amounts(med)
        # a different amount of the same unit right after the medicine name (up to the end of the line, the next drug
        # name, or NAME_DOSE_WINDOW characters) = altered dose in the text
        altered = False
        for p in pos:
            line_end = t.find("\n", p)
            end = min(line_end if line_end >= 0 else len(t), p + NAME_DOSE_WINDOW)
            nxt = [a_ for a_, _ in spans if p + 3 < a_ < end]
            seg = strip_maxima(t[p: min([end] + nxt)], src_amounts)
            for fam, val in dose_pairs(seg):
                if fam in allowed and not _in(val, allowed[fam]) and not (fam in COUNT_FAMILIES and _in(val, {1.0})):
                    altered = True
        if altered:
            continue
        ok = False
        for a in alts:
            need = [_num_strs(v, u) for v, u in a["values"]]
            for p in pos:
                win = t[max(0, p - NAME_DOSE_WINDOW): p + NAME_DOSE_WINDOW]
                if a.get("requires_half") and not HALF_RE.search(win) and "half" not in win and "आधी" not in win:
                    continue
                if all(any(re.search(r"(?<![\d.,])" + re.escape(x.lower()) + r"(?![\d])", win) for x in alt) for alt in need):
                    ok = True
                    break
            if ok:
                break
        hits += ok
    for fu in truth["follow_ups"]:
        items += 1
        if any(re.search(r"(?<![0-9])" + re.escape(ds) + r"(?![0-9])", t) for ds in date_strings(date.fromisoformat(fu["date"]))):
            hits += 1
    coverage = hits / items if items else 1.0
    mentioned = set(truth.get("drugs_mentioned_anywhere") or []) | {m["generic_name"] for m in truth["medicines"]} | \
        {m["generic_name"] for m in truth["stopped_medicines"]}
    invented = sorted({min(s) for s in scan_mentions(text, lex, skip_negated=True) if not (s & mentioned)})
    score = max(0.0, coverage - TEXT_INVENTED_DRUG_PENALTY * len(invented))
    copied = shingle_copy_share(text, source) if source else 0.0
    if copied > MAX_SOURCE_COPY_SHARE:
        score = 0.0
    return score, coverage, invented, copied


def _is_mark_or_letter(ch):
    return unicodedata.category(ch)[0] in ("L", "M")


def native_share(text: str, lang: str) -> float:
    blocks = LANG_SCRIPTS.get(lang, [])
    letters = [ch for ch in text if _is_mark_or_letter(ch)]
    if not letters:
        return 0.0
    nat = sum(1 for ch in letters if any(lo <= ord(ch) <= hi for lo, hi in blocks))
    return nat / len(letters)


ALLOWED_LATIN = {"mg", "mcg", "ml", "units", "unit", "iu", "puff", "puffs", "drop", "drops", "tab", "tabs", "tablet", "tablets",
                 "opd", "ecg", "inr", "lft", "cbc", "hb", "hba1c", "fbs", "ppbs", "tsh", "afb", "dots", "ogtt", "hpe", "pft", "echo",
                 "review", "spo2", "bp", "tb", "copd", "dka", "lscs", "iucd", "sachet", "capsule", "x-ray", "ray"}


def translation_check(tr_text: str, lang: str, en_text: str, lex: dict):
    """-> (ok, details). Guards against English copies, look-alike letters, ciphers and filler."""
    tr_text = unicodedata.normalize("NFKC", tr_text or "")
    share = native_share(tr_text, lang)
    blocks = LANG_SCRIPTS.get(lang, [])
    words = re.findall(r"[^\s,.;:()\[\]!?\"'।॥-]+", tr_text)
    lexnames = {w for n in lex for w in n.split()}

    def is_native(ch):
        return any(lo <= ord(ch) <= hi for lo, hi in blocks)
    latin = []
    for w in words:
        letters = [ch for ch in w if _is_mark_or_letter(ch)]
        if not letters or all(is_native(ch) for ch in letters):
            continue
        if any(not is_native(ch) and not ch.isascii() for ch in letters):
            latin.append(w)          # letters of another script (Cyrillic look-alikes, other Indic scripts, ...)
        elif re.fullmatch(r"[A-Za-z][A-Za-z0-9/+\-]*", w) and w.lower() not in ALLOWED_LATIN and w.lower() not in lexnames and len(w) > 2:
            latin.append(w)
    native = [w for w in words if any(is_native(ch) for ch in w)]
    latin_share = len(latin) / max(1, len(words))
    top_share = (collections.Counter(native).most_common(1)[0][1] / len(native)) if len(native) >= 20 else 0.0
    ratio = len(tr_text) / max(1, len(en_text)) if en_text else 1.0
    fw = set(FUNCTION_WORDS.get(lang, "").split())
    n_fw = len(fw & set(native)) if fw else None
    ok = (share >= MIN_NATIVE_SCRIPT_SHARE and latin_share <= MAX_LATIN_WORD_SHARE and top_share <= MAX_TOP_WORD_SHARE
          and TRANSLATION_LENGTH_RATIO[0] <= ratio <= TRANSLATION_LENGTH_RATIO[1]
          and (n_fw is None or n_fw >= MIN_FUNCTION_WORDS))
    return ok, dict(native_share=round(share, 3), latin_word_share=round(latin_share, 3), top_word_share=round(top_share, 3),
                    length_ratio=round(ratio, 3), function_words_found=n_fw)


# =============================================================================== validation

def load_jsonl(path: Path, what: str) -> list:
    if not path.exists():
        raise FormatError(f"{what} file not found: {path}")
    rows = []
    try:
        with open(path, encoding="utf-8-sig") as f:
            for i, line in enumerate(f, 1):
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError as e:
                    raise FormatError(f"{what} line {i} is not valid JSON ({e.msg}). Each line must be one JSON object.")
                if not isinstance(obj, dict):
                    raise FormatError(f"{what} line {i} must be a JSON object, got {type(obj).__name__}.")
                try:
                    json.dumps(obj, ensure_ascii=False).encode("utf-8")
                except UnicodeEncodeError:
                    raise FormatError(f"{what} line {i} contains an invalid character (lone UTF-16 surrogate). "
                                      "Remove broken characters / emoji fragments and save as UTF-8.")
                rows.append(obj)
    except UnicodeDecodeError:
        raise FormatError(f"{what} file is not UTF-8 text. Save it as UTF-8 (json.dumps(..., ensure_ascii=False) is fine).")
    return rows


def _req(obj, key, typ, where, allow_none=False, maxlen=MAX_FIELD_LEN):
    if key not in obj:
        raise FormatError(f"{where}: missing required key '{key}'.")
    v = obj[key]
    if v is None and allow_none:
        return v
    if not isinstance(v, typ) or (typ is not bool and isinstance(v, bool) and typ in (int, float)):
        tn = typ.__name__ if isinstance(typ, type) else "/".join(t.__name__ for t in typ)
        raise FormatError(f"{where}: key '{key}' must be {tn}, got {type(v).__name__}.")
    if isinstance(v, str) and len(v) > maxlen:
        raise FormatError(f"{where}: key '{key}' is {len(v)} characters long (max {maxlen}). Keep fields short and specific.")
    return v


def validate_submission(rows: list) -> dict:
    out = {}
    for i, r in enumerate(rows, 1):
        where = f"submission line {i}"
        sid = _req(r, "summary_id", str, where)
        where = f"submission line {i} (summary_id={sid})"
        if sid in out:
            raise FormatError(f"duplicate summary_id '{sid}' (line {i}). Submit exactly one line per summary.")
        cp = _req(r, "care_plan", dict, where)
        for k in REQUIRED_CP_LISTS:
            _req(cp, k, list, where + " care_plan")
        if len(cp["medicines"]) > MAX_MEDICINES:
            print(f" warning: {where} has {len(cp['medicines'])} medicines; only the first {MAX_MEDICINES} are scored",
                  file=sys.stderr)
            cp["medicines"] = cp["medicines"][:MAX_MEDICINES]
        for j, m in enumerate(cp["medicines"]):
            w2 = f"{where} care_plan.medicines[{j}]"
            if not isinstance(m, dict):
                raise FormatError(f"{w2} must be an object with keys {MED_KEYS}.")
            for k in MED_KEYS:
                _req(m, k, str, w2, allow_none=True, maxlen=MAX_TEXT_LEN if k == "how_to_take_plain" else MAX_FIELD_LEN)
        for j, s in enumerate(cp["stop_medicines"]):
            if not isinstance(s, str) or len(s) > MAX_FIELD_LEN:
                raise FormatError(f"{where} care_plan.stop_medicines[{j}] must be a string (medicine name, <= {MAX_FIELD_LEN} chars).")
        for k in ("warning_signs", "diet", "activity"):
            for j, s in enumerate(cp[k]):
                if not isinstance(s, str) or len(s) > MAX_TEXT_LEN:
                    raise FormatError(f"{where} care_plan.{k}[{j}] must be a string.")
        for j, fu in enumerate(cp["follow_ups"]):
            w2 = f"{where} care_plan.follow_ups[{j}]"
            if not isinstance(fu, dict):
                raise FormatError(f"{w2} must be an object with keys {FU_KEYS}.")
            for k in FU_KEYS:
                _req(fu, k, str, w2, allow_none=(k != "date"), maxlen=MAX_TEXT_LEN if k == "purpose" else MAX_FIELD_LEN)
            try:
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", fu["date"]):
                    raise ValueError
                date.fromisoformat(fu["date"])
            except ValueError:
                raise FormatError(f"{w2}.date must be a real date in YYYY-MM-DD format, got '{fu['date']}'.")
        _req(r, "plain_text_en", str, where, maxlen=MAX_TEXT_LEN)
        tr = _req(r, "translation", dict, where)
        lc = _req(tr, "language_code", str, where + " translation").strip().lower()
        if lc not in LANG_SCRIPTS:
            raise FormatError(f"{where}: translation.language_code '{lc}' not allowed. Use one of {sorted(LANG_SCRIPTS)}.")
        tr["language_code"] = lc
        _req(tr, "text", str, where + " translation", maxlen=MAX_TEXT_LEN)
        rd = _req(r, "readability", dict, where)
        _req(rd, "metric", str, where + " readability")
        sc = _req(rd, "score", (int, float), where + " readability", allow_none=True)
        if sc is not None and (isinstance(sc, bool) or not math.isfinite(float(sc))):
            raise FormatError(f"{where}: readability.score must be a finite number or null.")
        out[sid] = r
    return out


# =============================================================================== scoring

def score_one(pred: dict, truth: dict, names: dict, lex: dict, source: str = "", flex: dict | None = None) -> dict:
    flex = flex if flex is not None else lex
    cp = pred["care_plan"]
    tmeds = truth["medicines"]
    tgens = [m["generic_name"] for m in tmeds]
    multi_ok = {m["generic_name"] for m in tmeds if m.get("taper") or m.get("slot_doses")}  # may be split over entries
    stop_gens = [s["generic_name"] for s in truth["stopped_medicines"]]
    matched = {g: [] for g in tgens}
    invented = []
    for m in cp["medicines"]:
        nm = m.get("name") or ""
        g = match_generic(nm, tgens, names, taken={k for k, v in matched.items() if v})
        if g is None or foreign_drug(nm, set(tgens), flex):
            invented.append(nm)
        elif matched[g] and g not in multi_ok:
            invented.append(nm + " (duplicate entry)")
        else:
            matched[g].append(m)
    n = len(tmeds)
    rec, dos, frq, dur_hits, dur_n = 0, 0, 0, 0, 0
    altered, dropped = [], []
    for tm in tmeds:
        g = tm["generic_name"]
        ms = matched[g]
        has_dur = tm.get("duration_type") not in (None, "none")
        dur_n += has_dur
        if not ms:
            dropped.append(g)
            continue
        rec += 1
        d_ok = dose_ok(tm, [x.get("dose") or "" for x in ms], [x.get("name") or "" for x in ms])
        dos += d_ok
        if not d_ok:
            altered.append(g)
        fc = {freq_class(x.get("frequency") or "") for x in ms}
        frq += fc == {TRUTH_FREQ[tm["frequency_code"]]}
        if has_dur:
            dur_hits += duration_ok(tm, [x.get("duration") or "" for x in ms],
                                    [(x.get("dose") or "") + " " + (x.get("frequency") or "") for x in ms])
    med_recall = rec / n if n else 1.0
    dose_acc = dos / n if n else 1.0
    freq_acc = frq / n if n else 1.0
    dur_acc = dur_hits / dur_n if dur_n else 1.0
    tdates = {f["date"] for f in truth["follow_ups"]}
    sdates = {f["date"] for f in cp["follow_ups"]}
    fu_recall = len(tdates & sdates) / len(tdates) if tdates else 1.0
    invented_fu = sorted(sdates - tdates)
    wrong_stop, stop_hit = [], set()
    for s in cp["stop_medicines"]:
        g = match_generic(s, stop_gens, names)
        if g is not None and not foreign_drug(s, set(stop_gens), flex):
            stop_hit.add(g)
        else:
            wrong_stop.append(s)
    w = dict(med=W_MED_RECALL, dose=W_DOSE, freq=W_FREQ, dur=W_DURATION, fu=W_FOLLOWUP, stop=W_STOP)
    comp = dict(med=med_recall, dose=dose_acc, freq=freq_acc, dur=dur_acc, fu=fu_recall)
    if stop_gens:
        comp["stop"] = len(stop_hit) / len(stop_gens)
    else:
        w.pop("stop")
    structured = sum(w[k] * comp[k] for k in comp) / sum(w.values())
    penalties = (PENALTY_DROPPED_MED * len(dropped) + PENALTY_ALTERED_DOSE * len(altered) + PENALTY_INVENTED_MED * len(invented)
                 + PENALTY_WRONG_STOP * len(wrong_stop) + PENALTY_INVENTED_FOLLOWUP * len(invented_fu))
    structured_after = max(0.0, structured - penalties)
    en_text = pred.get("plain_text_en") or ""
    en, en_cov, en_inv, en_copy = text_score(en_text, truth, names, lex, source, flex)
    tr = pred["translation"]
    tr_ok, tr_info = translation_check(tr.get("text") or "", tr.get("language_code"), en_text, lex)
    if tr_ok:
        trs, tr_cov, tr_inv, tr_copy = text_score(tr.get("text") or "", truth, names, lex, source, flex)
    else:
        trs, tr_cov, tr_inv, tr_copy = 0.0, 0.0, [], 0.0
    text = W_TEXT_EN * en + W_TEXT_TRANSLATION * trs
    total = W_STRUCTURED * structured_after + W_TEXT * text
    return dict(summary_id=truth["summary_id"], score=total, structured_before_penalty=structured, penalties=penalties,
                structured_after_penalty=structured_after, text_score=text, med_recall=med_recall, dose_acc=dose_acc,
                freq_acc=freq_acc, duration_acc=dur_acc, followup_recall=fu_recall,
                stop_recall=comp.get("stop"), n_dropped=len(dropped), n_altered_dose=len(altered), n_invented_med=len(invented),
                n_wrong_stop=len(wrong_stop), n_invented_followup=len(invented_fu), text_en_coverage=en_cov,
                text_translation_coverage=tr_cov, translation_ok=tr_ok, translation_checks=tr_info,
                source_copy_share_en=round(en_copy, 3), invented_drugs_in_text=sorted(set(en_inv) | set(tr_inv)),
                dropped=dropped, altered_dose=altered, invented=invented, wrong_stop=wrong_stop, invented_followup=invented_fu)


def load_sources(truth_path: Path):
    """source texts for the verbatim-copy check: data/summaries.jsonl next to evaluate.py (optional)."""
    p = HERE / "data" / "summaries.jsonl"
    if not p.exists():
        print("\n !!! WARNING: data/summaries.jsonl not found next to evaluate.py -> the verbatim-copy check is DISABLED."
              "\n !!! Run evaluate.py from the package folder (official scoring always has it).\n", file=sys.stderr)
        return {}
    return {json.loads(l)["summary_id"]: json.loads(l)["text"] for l in open(p, encoding="utf-8")}


def evaluate(sub_path: Path, truth_path: Path, drug_ref: Path):
    truth_rows = load_jsonl(truth_path, "truth")
    rows = load_jsonl(sub_path, "submission")
    if not rows:
        raise FormatError("submission is empty. Write one JSON line per summary_id in data/test_ids.csv.")
    preds = validate_submission(rows)
    names = load_drug_reference(drug_ref)
    lex = load_lexicon(drug_ref)
    flex = load_foreign_lexicon(drug_ref)
    sources = load_sources(truth_path)
    per, missing = [], []
    truth_ids = {t["summary_id"] for t in truth_rows}
    for t in truth_rows:
        p = preds.get(t["summary_id"])
        if p is None:
            missing.append(t["summary_id"])
            per.append(dict(summary_id=t["summary_id"], score=0.0, missing=True))
            continue
        per.append(score_one(p, t, names, lex, sources.get(t["summary_id"], ""), flex))
    df = pd.DataFrame(per)
    scored = df[~df["missing"].eq(True)] if "missing" in df else df

    def m(col):
        return float(scored[col].mean()) if len(scored) and col in scored else float("nan")
    res = dict(
        primary_score=float(df["score"].mean()),
        primary_score_range="0 (worst) to 1 (best); higher is better",
        n_truth=len(truth_rows), n_scored=int(len(scored)), n_missing=len(missing), missing_ids_first10=missing[:10],
        n_extra_ids_ignored=len(set(preds) - truth_ids),
        mean_structured_before_penalty=m("structured_before_penalty"), mean_structured_after_penalty=m("structured_after_penalty"),
        mean_text_score=m("text_score"), med_recall=m("med_recall"), dose_accuracy=m("dose_acc"), frequency_accuracy=m("freq_acc"),
        duration_accuracy=m("duration_acc"), followup_date_recall=m("followup_recall"),
        stop_recall=float(scored["stop_recall"].dropna().mean()) if len(scored) and scored["stop_recall"].notna().any() else None,
        total_dropped_medicines=int(scored["n_dropped"].sum()) if len(scored) else 0,
        total_altered_doses=int(scored["n_altered_dose"].sum()) if len(scored) else 0,
        total_invented_medicines=int(scored["n_invented_med"].sum()) if len(scored) else 0,
        total_wrong_stops=int(scored["n_wrong_stop"].sum()) if len(scored) else 0,
        total_invented_followups=int(scored["n_invented_followup"].sum()) if len(scored) else 0,
        summaries_with_any_hard_penalty=int((scored["penalties"] > 0).sum()) if len(scored) else 0,
        text_en_coverage=m("text_en_coverage"), text_translation_coverage=m("text_translation_coverage"),
        translations_failing_script_check=int((~scored["translation_ok"].astype(bool)).sum()) if len(scored) else 0,
        readability_reported_mean=None,
        constants=dict(W_MED_RECALL=W_MED_RECALL, W_DOSE=W_DOSE, W_FREQ=W_FREQ, W_DURATION=W_DURATION, W_FOLLOWUP=W_FOLLOWUP,
                       W_STOP=W_STOP, PENALTY_DROPPED_MED=PENALTY_DROPPED_MED, PENALTY_ALTERED_DOSE=PENALTY_ALTERED_DOSE,
                       PENALTY_INVENTED_MED=PENALTY_INVENTED_MED, PENALTY_WRONG_STOP=PENALTY_WRONG_STOP,
                       PENALTY_INVENTED_FOLLOWUP=PENALTY_INVENTED_FOLLOWUP, W_STRUCTURED=W_STRUCTURED, W_TEXT=W_TEXT,
                       W_TEXT_EN=W_TEXT_EN, W_TEXT_TRANSLATION=W_TEXT_TRANSLATION,
                       TEXT_INVENTED_DRUG_PENALTY=TEXT_INVENTED_DRUG_PENALTY, NAME_DOSE_WINDOW=NAME_DOSE_WINDOW,
                       COPY_MIN_RUN=COPY_MIN_RUN, MIN_FUNCTION_WORDS=MIN_FUNCTION_WORDS, MAX_MEDICINES=MAX_MEDICINES,
                       MIN_NATIVE_SCRIPT_SHARE=MIN_NATIVE_SCRIPT_SHARE, MAX_LATIN_WORD_SHARE=MAX_LATIN_WORD_SHARE,
                       MAX_TOP_WORD_SHARE=MAX_TOP_WORD_SHARE, TRANSLATION_LENGTH_RATIO=list(TRANSLATION_LENGTH_RATIO),
                       MAX_SOURCE_COPY_SHARE=MAX_SOURCE_COPY_SHARE, FUZZY_NAME_RATIO=FUZZY_NAME_RATIO,
                       MAX_FIELD_LEN=MAX_FIELD_LEN, MAX_TEXT_LEN=MAX_TEXT_LEN),
    )
    rs = [p["readability"].get("score") for sid, p in preds.items() if sid in truth_ids]
    rs = [x for x in rs if isinstance(x, (int, float)) and not isinstance(x, bool)]
    if rs:
        res["readability_reported_mean"] = float(np.mean(rs))
    res["per_summary"] = [{k: (None if isinstance(v, float) and math.isnan(v) else v) for k, v in r.items()} for r in per]
    return res, missing


def main(argv=None):
    ap = argparse.ArgumentParser(description="H2 care-plan faithfulness scorer")
    ap.add_argument("--submission", required=True, type=Path)
    ap.add_argument("--truth", required=True, type=Path)
    ap.add_argument("--out", default=Path("score.json"), type=Path)
    ap.add_argument("--drug_ref", default=HERE / "data" / "drug_reference.csv", type=Path,
                    help="data/drug_reference.csv (brand -> generic mapping); default: next to evaluate.py")
    ap.add_argument("--summary_only", action="store_true",
                    help="omit per-summary details from the JSON (organisers use this when returning hidden-test scores)")
    a = ap.parse_args(argv)
    try:
        if not a.drug_ref.exists():
            raise FormatError(f"drug reference not found at {a.drug_ref}. Pass --drug_ref data/drug_reference.csv")
        res, missing = evaluate(a.submission, a.truth, a.drug_ref)
    except FormatError as e:
        print(f"\nSUBMISSION FORMAT ERROR: {e}\nSee README.md section 'What to submit' for the exact format.\n", file=sys.stderr)
        sys.exit(2)
    print("=" * 66)
    print(" H2 Discharge Summary -> Care Plan : faithfulness score")
    print("=" * 66)
    print(f" PRIMARY SCORE (0-1, higher is better) : {res['primary_score']:.4f}")
    print(f" summaries scored / in truth            : {res['n_scored']} / {res['n_truth']}")
    if missing:
        print(f" !!! {len(missing)} summaries MISSING from your submission (scored 0). First: {missing[:5]}")
    if res["n_extra_ids_ignored"]:
        print(f" note: {res['n_extra_ids_ignored']} submitted ids are not in the truth file and were ignored")
    print("-" * 66)
    for k in ["mean_structured_before_penalty", "mean_structured_after_penalty", "mean_text_score", "med_recall",
              "dose_accuracy", "frequency_accuracy", "duration_accuracy", "followup_date_recall", "stop_recall",
              "text_en_coverage", "text_translation_coverage"]:
        v = res[k]
        print(f" {k:<38}: {'n/a' if v is None else f'{v:.4f}'}")
    print("-" * 66)
    for k in ["total_dropped_medicines", "total_altered_doses", "total_invented_medicines", "total_wrong_stops",
              "total_invented_followups", "summaries_with_any_hard_penalty", "translations_failing_script_check"]:
        print(f" {k:<38}: {res[k]}")
    rm = res["readability_reported_mean"]
    print(f" readability (reported, not scored)     : {'n/a' if rm is None else f'{rm:.2f}'}")
    print("=" * 66)
    if a.summary_only:
        res.pop("per_summary", None)
    a.out.write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8", errors="replace")
    print(f" full report written to {a.out}")


if __name__ == "__main__":
    main()
