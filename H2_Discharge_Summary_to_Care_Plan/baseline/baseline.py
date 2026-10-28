#!/usr/bin/env python3
"""H2 baseline: regex extraction of the medication list + template plain text.

Deliberately simple and honest:
  * reads only the discharge-medication section, follow-up dates, STOP lines and warning lines;
  * does NOT map brands, does NOT handle tapers / split insulin doses / half tablets specially;
  * translation is an English copy (language_code 'hi') -> it FAILS the script check, so the
    translation part of the text score is 0. Replace it with a real Indian-language version.

Usage (from the package folder):
    python baseline/baseline.py --split dev  --out dev_predictions.jsonl
    python baseline/baseline.py --split test                 # -> submission/H2_predictions.jsonl
"""
import argparse
import json
import re
from datetime import date, datetime
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent.parent
MED_HEADINGS = ["DISCHARGE MEDICATIONS", "Rx on discharge", "Medications at Discharge", "TREATMENT ADVISED ON DISCHARGE",
                "ADV ON DISCHARGE"]
WARN_HEADINGS = ["RETURN TO EMERGENCY", "Review SOS if", "Danger signs", "COME BACK TO THE HOSPITAL", "Report immediately"]
FU_HEADINGS = ["FOLLOW-UP", "F/U", "Follow-up", "REVIEW", "Follow up"]
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
FREQ_RE = re.compile(r"\b(OD|BD|TDS|QID|HS|SOS)\b|(\d-\d-\d)|once weekly|once a week")
DOSE_RE = re.compile(r"(\d+(?:\.\d+)?(?:,\d{3})?)\s*(mg|mcg|units|U|IU|ml|mL|puffs|tabs|tab|drop)\b")
DUR_RE = re.compile(r"(x\s*\d+\s*(?:/\s*\d+|days?|wks?|weeks?|months?|yrs?|years?|d)\b|for \d+ \w+|lifelong|life long|"
                    r"till (?:next )?review|till the next review|until the next visit|till next visit)", re.I)


def section(lines, heads):
    for i, ln in enumerate(lines):
        if any(ln.strip().startswith(h) for h in heads):
            out = []
            for l2 in lines[i + 1:]:
                if not l2.strip():
                    if out:
                        break
                    continue
                out.append(l2)
            return out
    return []


def parse_date(s):
    s = s.strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    m = re.match(r"(\d{1,2})-([A-Za-z]{3})-(\d{2})$", s)
    if m:
        return date(2000 + int(m.group(3)), MONTHS[m.group(2).lower()], int(m.group(1)))
    m = re.match(r"(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)\s+(\d{4})$", s)
    if m:
        return date(int(m.group(3)), MONTHS[m.group(2)[:3].lower()], int(m.group(1)))
    return None


DATE_RE = re.compile(r"\d{1,2}[/.\-]\d{1,2}[/.\-]\d{4}|\d{1,2}-[A-Za-z]{3}-\d{2}|\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+\s+\d{4}")


def parse_med_line(line):
    line = re.sub(r"^\s*(\d+[.)]|[-•*])\s*", "", line).strip()
    name = re.split(r"\s\d", line, maxsplit=1)[0]
    d = DOSE_RE.search(line)
    f = FREQ_RE.search(line)
    du = DUR_RE.search(line)
    return dict(name=name.strip(), dose=f"{d.group(1)} {d.group(2)}" if d else "", frequency=f.group(0) if f else "",
                timing="", duration=du.group(0) if du else "", how_to_take_plain=line)


def extract(text):
    lines = text.split("\n")
    meds, stops = [], []
    sec = section(lines, MED_HEADINGS)
    for ln in sec:
        if ln.startswith("+"):
            continue
        if ln.startswith("|"):
            cells = [c.strip() for c in ln.strip("|").split("|")]
            if cells[0] in ("No", "") or len(cells) < 6:
                continue
            meds.append(dict(name=cells[1], dose=cells[2], frequency=cells[4], timing=cells[6] if len(cells) > 6 else "",
                             duration=cells[5], how_to_take_plain=" ".join(cells[1:])))
            continue
        if "STOP" in ln.upper() and ln.upper().lstrip("-•* ").startswith("STOP"):
            continue
        if re.match(r"\s*(\d+[.)]|[-•*])\s", ln):
            meds.append(parse_med_line(ln))
    for ln in lines:
        m = re.match(r"\s*(?:[-•*]\s*)?(?:STOP|Stop)\s+((?:Tab|T|Cap|Inj)\..*)", ln)
        if m:
            stops.append(re.sub(r"\(.*", "", m.group(1)).strip())
    stop_sec = section(lines, ["MEDICINES TO BE STOPPED", "PLEASE STOP"])
    stops += [re.sub(r"\(.*", "", re.sub(r"^\s*[-•*]\s*", "", x)).strip() for x in stop_sec]
    warn = [re.sub(r"^\s*[-•*]\s*", "", x).strip() for x in section(lines, WARN_HEADINGS)]
    fus = []
    for ln in section(lines, FU_HEADINGS):
        for ds in DATE_RE.findall(ln):
            d = parse_date(ds)
            if d:
                fus.append(dict(date=d.isoformat(), with_whom="", purpose=re.sub(r"^\s*[-•*]\s*", "", ln).strip()))
    return dict(medicines=meds, stop_medicines=stops, warning_signs=warn, diet=[], activity=[], follow_ups=fus)


def plain_text(cp):
    out = ["YOUR MEDICINES"]
    for m in cp["medicines"]:
        out.append(f"- {m['name']} {m['dose']} {m['frequency']} {m['duration']}".strip())
    if cp["stop_medicines"]:
        out.append("STOP these medicines: " + ", ".join(cp["stop_medicines"]))
    out.append(f"WARNING SIGNS: {len(cp['warning_signs'])} listed in your summary - read them with your nurse.")
    out.append("VISITS: " + "; ".join(f["date"] for f in cp["follow_ups"]))
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["dev", "test", "all"], default="test")
    ap.add_argument("--out", default=None)
    ap.add_argument("--data_dir", default=str(HERE / "data"))
    a = ap.parse_args()
    data = Path(a.data_dir)
    if a.split == "all":
        ids = set(pd.read_csv(data / "splits.csv")["summary_id"])
    else:
        ids = set(pd.read_csv(data / f"{a.split}_ids.csv")["summary_id"])
    out = Path(a.out) if a.out else HERE / "submission" / "H2_predictions.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with open(data / "summaries.jsonl", encoding="utf-8") as f, open(out, "w", encoding="utf-8") as g:
        for line in f:
            rec = json.loads(line)
            if rec["summary_id"] not in ids:
                continue
            cp = extract(rec["text"])
            txt = plain_text(cp)
            g.write(json.dumps(dict(summary_id=rec["summary_id"], care_plan=cp, plain_text_en=txt,
                                    translation=dict(language_code="hi", text=txt),  # English copy: scores 0 on translation
                                    readability=dict(metric="none", score=None)), ensure_ascii=False) + "\n")
            n += 1
    print(f"wrote {n} predictions to {out}")


if __name__ == "__main__":
    main()
