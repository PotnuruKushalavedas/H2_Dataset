#!/usr/bin/env python3
"""Conservative, reference-driven H2 discharge-plan generator.

This implementation reads medicines only from the discharge-prescription section,
uses the supplied drug and abbreviation references, and emits the official JSONL
schema. It uses only the Python standard library.

Examples (run from this folder):
    python improved_solution.py --split dev --out dev_predictions_improved.jsonl
    python improved_solution.py --split test
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
from datetime import date, datetime
from pathlib import Path


HERE = Path(__file__).resolve().parent
MED_HEADINGS = (
    "DISCHARGE MEDICATIONS", "RX ON DISCHARGE", "MEDICATIONS AT DISCHARGE",
    "TREATMENT ADVISED ON DISCHARGE", "ADV ON DISCHARGE",
)
STOP_HEADINGS = ("MEDICINES TO BE STOPPED", "MEDICATIONS TO BE STOPPED", "PLEASE STOP")
SECTION_BREAKS = (
    "INSTRUCTIONS", "ADVICE ON DISCHARGE", "DIET", "ACTIVITY", "WOUND CARE",
    "REPORT IMMEDIATELY", "DANGER SIGNS", "RETURN TO EMERGENCY", "FOLLOW-UP",
    "FOLLOW UP", "F/U", "IN CASE OF EMERGENCY", "MEDICINES TO BE STOPPED",
    "MEDICATIONS TO BE STOPPED", "PLEASE STOP", "GENERAL ADVICE", "ADVICE",
    "ADV", "WARNING SIGNS", "REVIEW SOS IF",
)
DATE_RE = re.compile(
    r"(?<!\d)(?:\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|"
    r"\d{1,2}-[A-Za-z]{3}-\d{2,4}|\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+\s+\d{4})(?!\d)",
    re.I,
)
MONTHS = {name.lower(): number for number, name in enumerate(
    ("January", "February", "March", "April", "May", "June", "July", "August",
     "September", "October", "November", "December"), 1
)}
MONTHS.update({name[:3].lower(): number for number, name in enumerate(
    ("January", "February", "March", "April", "May", "June", "July", "August",
     "September", "October", "November", "December"), 1
)})
FREQ_RE = re.compile(
    r"\b(?:OD|BD|TDS|TID|QID|QID|HS|SOS|PRN|WEEKLY)\b|"
    r"\b(?:once\s+(?:daily|a day|weekly|a week)|twice\s+(?:daily|a day)|"
    r"three times(?:\s+a day|\s+daily)?|four times(?:\s+a day|\s+daily)?)\b|"
    r"\b\d+\s*/\s*week\b",
    re.I,
)
DURATION_RE = re.compile(
    r"\b(?:x\s*)?\d+(?:\.\d+)?\s*(?:/\s*(?:7|52|12)|days?|d\b|weeks?|wks?|w\b|"
    r"months?|mos?|years?|yrs?|yr\b)\b|"
    r"\b(?:life[\s-]*long|lifelong|till(?:\s+the|\s+next)?\s+(?:next\s+)?review|"
    r"till(?:\s+the|\s+next)?\s+(?:next\s+)?visit|"
    r"until(?:\s+the|\s+next)?\s+(?:next\s+)?(?:review|visit|appointment))\b",
    re.I,
)
DOSE_RE = re.compile(
    r"(?<![\w.])(\d+(?:,\d{3})*(?:\.\d+)?(?:\s*-\s*\d+(?:\.\d+)?){1,3})\s*(units?|u|iu)\b|"
    r"(?<![\w.])(\d+(?:,\d{3})*(?:\.\d+)?(?:/\d+(?:\.\d+)?)?)\s*"
    r"(mg|mcg|µg|ug|iu|units?|u|ml|mL|puffs?|tabs?|tablets?|caps?|capsules?|drops?|%)\b",
    re.I,
)
COUNT_RE = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*(tabs?|tablets?|caps?|capsules?|puffs?|drops?)\b", re.I)
SLOT_DOSE_RE = re.compile(r"(?<![\d/])(\d+(?:\.\d+)?(?:\s*-\s*\d+(?:\.\d+)?){1,2})\s*(?:units?|u)\b", re.I)
SLOT_RE = re.compile(r"(?<![\d/])\d+(?:\s*-\s*\d+){1,3}(?![\d/])")
HALF_SLOT_RE = re.compile(r"(?<![\d/])(?:½|1\s*/\s*2|\d+)(?:\s*-\s*(?:½|1\s*/\s*2|\d+)){2,3}(?![\d/])", re.I)
HALF_TABLET_RE = re.compile(
    r"\bhalf\b.{0,20}\b(?:tablet|tabs?)\b|(?:½|1\s*/\s*2)\s*(?:tablet|tabs?)\b|"
    r"(?:½|1\s*/\s*2)\s*-\s*0\s*-\s*(?:½|1\s*/\s*2)",
    re.I,
)
DATE_FORMATS = ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y-%m-%d", "%Y/%m/%d",
                "%d/%m/%y", "%d-%m-%y", "%d.%m.%y")


def norm_words(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def build_drug_index(rows: list[dict[str, str]]) -> tuple[list[dict], dict[str, dict]]:
    entries = []
    by_generic = {}
    for row in rows:
        generic = row["generic_name"].strip()
        brands = [part.strip() for part in row.get("common_brands", "").split(";") if part.strip()]
        aliases = [generic, *brands]
        aliases.extend(part.strip() for part in row.get("also_known_as", "").split(";") if part.strip())
        normalized = sorted({norm_words(alias) for alias in aliases if norm_words(alias)},
                            key=len, reverse=True)
        entry = {
            "generic": generic,
            "brands": brands,
            "aliases": normalized,
            "strengths": row.get("typical_strengths", ""),
            "available": row.get("used_at_discharge", "").strip().casefold() == "yes",
        }
        entries.append(entry)
        by_generic[generic] = entry
    entries.sort(key=lambda item: max(map(len, item["aliases"]), default=0), reverse=True)
    return entries, by_generic


def _find_matches(text: str, entries: list[dict]) -> list[tuple[int, int, dict, str]]:
    normalized = norm_words(text)
    found = []
    for entry in entries:
        for alias in entry["aliases"]:
            match = re.search(r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])", normalized)
            if match:
                found.append((match.start(), match.end(), entry, alias))
    found.sort(key=lambda item: (-(item[1] - item[0]), item[0]))
    selected = []
    used = set()
    for start, end, entry, alias in found:
        if entry["generic"] in used:
            continue
        if any(start < prev_end and end > prev_start for prev_start, prev_end, _, _ in selected):
            continue
        selected.append((start, end, entry, alias))
        used.add(entry["generic"])
    return sorted(selected, key=lambda item: item[0])


def _heading_index(lines: list[str], headings: tuple[str, ...], start: int = 0) -> int | None:
    normalized = tuple(h.casefold() for h in headings)
    for index in range(start, len(lines)):
        text = lines[index].strip().casefold().lstrip("-: ")
        if any(text.startswith(heading.casefold()) for heading in normalized):
            return index
    return None


def _is_break(line: str) -> bool:
    text = line.strip().casefold().lstrip("-•* \ufffd")
    text = re.sub(r"^\d+[.)]\s*", "", text)
    return any(re.match(r"^" + re.escape(heading.casefold()) + r"(?:\b|$)", text)
               for heading in SECTION_BREAKS)


def _medication_lines(lines: list[str]) -> list[str]:
    start = _heading_index(lines, MED_HEADINGS)
    if start is None:
        return []
    out = []
    for line in lines[start + 1:]:
        if line.strip() and _is_break(line):
            break
        if line.strip():
            out.append(line)
    return out


def _is_stop_line(line: str) -> bool:
    return bool(re.match(r"^\s*(?:[-•*]\s*)?(?:STOP|PLEASE STOP)\b", line, re.I))


def _surface_brand(line: str, entry: dict) -> str | None:
    low = line.casefold()
    for brand in entry["brands"]:
        if re.search(r"(?<![a-z0-9])" + re.escape(brand.casefold()) + r"(?![a-z0-9])", low):
            return brand
    return None


def _display_name(entry: dict, line: str) -> str:
    generic = entry["generic"]
    brand = _surface_brand(line, entry)
    display = generic[0].upper() + generic[1:]
    return f"{display} ({brand})" if brand else display


def _strength_dose(line: str, entry: dict) -> str | None:
    if entry["generic"].startswith("insulin "):
        breakfast = re.search(r"(\d+(?:\.\d+)?)\s*(?:units?|u)\s+before breakfast", line, re.I)
        dinner = re.search(r"(\d+(?:\.\d+)?)\s*(?:units?|u)\s+before dinner", line, re.I)
        if breakfast and dinner:
            return f"{breakfast.group(1)} units before breakfast, {dinner.group(1)} units before dinner"
    match = SLOT_DOSE_RE.search(line)
    if match:
        slots = re.sub(r"\s+", "", match.group(1))
        return f"{slots} units"

    candidates = list(DOSE_RE.finditer(line))
    measured = []
    for match in candidates:
        if match.group(1):
            amount = re.sub(r"\s+", "", match.group(1))
            measured.append((match.start(), f"{amount} {match.group(2).lower()}"))
        elif match.group(3):
            amount = match.group(3).replace(" ", "")
            unit = match.group(4).lower().replace("µg", "mcg").replace("ug", "mcg")
            unit = {"u": "units", "unit": "units", "ml": "ml", "iu": "IU",
                    "tab": "tablet", "tabs": "tablets", "cap": "capsule",
                    "caps": "capsules", "puff": "puff", "drop": "drop"}.get(unit, unit)
            rendered = f"{amount} {unit}"
            if unit == "%":
                rendered = f"{amount}%"
            measured.append((match.start(), rendered))
    count = COUNT_RE.search(line)
    if count and count.group(2).casefold().startswith("puff"):
        return f"{count.group(1)} puffs"
    if "then" in line.casefold() and len(measured) > 1:
        return ", then ".join(item[1] for item in measured)
    if measured:
        mass = [item for item in measured if re.search(r"\b(?:mg|mcg|iu|units?|ml)\b", item[1], re.I)]
        if HALF_TABLET_RE.search(line) and mass:
            amount, unit = re.match(r"(\d+(?:,\d{3})*(?:\.\d+)?)\s+([a-z]+)", mass[0][1], re.I).groups()
            half_value = float(amount.replace(",", "")) / 2
            half_text = f"{half_value:g} {unit}"
            return f"half tablet of {mass[0][1]} ({half_text})"
        if len(mass) > 1:
            return " + ".join(item[1] for item in mass)
        return (mass or measured)[0][1]

    strength_text = entry.get("strengths", "")
    strength_nums = []
    for amount, unit in re.findall(r"(?<!\d)(\d+(?:,\d{3})*(?:\.\d+)?)\s*(mg|mcg|IU|units?|ml|%)",
                                   strength_text, re.I):
        strength_nums.append((amount.replace(",", ""), unit.lower()))
    for number in re.findall(r"(?<![\d/])\d+(?:\.\d+)?(?![\d/])", line):
        for amount, unit in strength_nums:
            if math.isclose(float(number), float(amount), rel_tol=0, abs_tol=0.0001):
                rendered_unit = "IU" if unit == "iu" else unit
                return f"{number} {rendered_unit}"

    if count:
        amount = count.group(1)
        unit = count.group(2).casefold()
        unit = "tablet" if unit.startswith("tab") else "capsule" if unit.startswith("cap") else unit.rstrip("s")
        return f"{amount} {unit}"
    return None


def _frequency(line: str) -> str | None:
    code = re.search(r"\b(OD|BD|TDS|TID|QID|HS|SOS|PRN|WEEKLY)\b", line, re.I)
    if code:
        value = code.group(1).upper()
        return {"TID": "TDS", "PRN": "SOS"}.get(value, value)
    fractional_slots = HALF_SLOT_RE.search(line)
    if fractional_slots and re.search(r"½|1\s*/\s*2", fractional_slots.group(0)):
        count = sum(1 for part in re.split(r"\s*-\s*", fractional_slots.group(0))
                    if part.strip() not in {"0", "0.0"})
        return {1: "OD", 2: "BD", 3: "TDS", 4: "QID"}.get(count)
    slot = SLOT_RE.search(line)
    if slot and re.match(r"\s*(?:min(?:utes?)?|hours?|hrs?)\b", line[slot.end():slot.end() + 14], re.I):
        slot = None
    if slot:
        value = re.sub(r"\s+", "", slot.group(0))
        return value
    meal_slots = set(re.findall(
        r"\b(breakfast|bf|lunch|dinner|supper|bedtime)\b", line, re.I
    ))
    if len(meal_slots) > 1:
        return {2: "BD", 3: "TDS", 4: "QID"}.get(len(meal_slots))
    if re.search(r"\bbefore meals?\b", line, re.I):
        return "TDS"
    match = FREQ_RE.search(line)
    if not match:
        if re.search(r"\b(?:every\s+)?(?:one|once)\s+(?:a\s+)?day\b", line, re.I):
            return "OD"
        return None
    value = re.sub(r"\s+", " ", match.group(0)).strip().casefold()
    if "weekly" in value or "/week" in value or "a week" in value:
        return "once weekly"
    if "once" in value:
        return "OD"
    if "twice" in value:
        return "BD"
    if "three" in value:
        return "TDS"
    if "four" in value:
        return "QID"
    return value.upper()


def _timing(line: str) -> str | None:
    low = line.casefold()
    if re.search(r"\b(?:at\s+)?bedtime\b|\bbefore bed\b", low):
        return "at bedtime"
    if re.search(r"(?<!\w)(?:30|half|½|1/2)\s*(?:minutes?|mins?|min|hrs?|hr|hours?)", low):
        if "before breakfast" in low and re.search(r"(?:\band\b|&)\s*(?:before\s+)?dinner\b", low):
            return "30 minutes before breakfast and dinner"
        if "before breakfast" in low:
            return "30 minutes before breakfast"
        if "before meals" in low or "before meal" in low:
            return "30 minutes before meals"
    if re.search(r"\b(?:empty stomach|on empty stomach|before food|before meals?)\b", low):
        if "empty stomach" in low:
            return "on an empty stomach"
        if re.search(r"\b(?:15|15-minute|15 min)\b", low):
            return "15 minutes before meals"
        return "before food"
    if "before breakfast" in low:
        return "before breakfast"
    if "after breakfast" in low:
        return "after breakfast"
    if "after lunch" in low:
        return "after lunch"
    if "after dinner" in low:
        return "after dinner"
    if "evening meal" in low:
        return "with the evening meal"
    if re.search(r"\b(?:after food|pc)\b", low):
        return "after food"
    if re.search(r"\b(?:with food|with meals?)\b", low):
        return "with food"
    return None


def _duration(line: str) -> str | None:
    matches = list(DURATION_RE.finditer(line))
    if not matches:
        return None
    dose_steps = len(list(DOSE_RE.finditer(line)))
    if "then" in line.casefold() and len(matches) > 1 and dose_steps > 1:
        return ", then ".join(re.sub(r"\s+", " ", match.group(0)).strip() for match in matches)
    return re.sub(r"\s+", " ", matches[0].group(0)).strip()


def _parse_date(value: str) -> date | None:
    text = value.strip().replace(",", "")
    text = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", text, flags=re.I)
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    match = re.fullmatch(r"(\d{1,2})-([A-Za-z]{3})-(\d{2,4})", text)
    if match:
        month = MONTHS.get(match.group(2).casefold())
        year = int(match.group(3))
        year = year + (2000 if year < 100 else 0)
        if month:
            try:
                return date(year, month, int(match.group(1)))
            except ValueError:
                return None
    match = re.fullmatch(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", text)
    if match:
        month = MONTHS.get(match.group(2).casefold())
        if month:
            try:
                return date(int(match.group(3)), month, int(match.group(1)))
            except ValueError:
                return None
    return None


def _line_values(line: str) -> list[str]:
    value = re.sub(r"^\s*(?:[-•*]\s*|\d+[.)]\s*)", "", line).strip()
    if value.startswith("|"):
        cells = [cell.strip() for cell in value.strip("|").split("|")]
        value = " ".join(cell for index, cell in enumerate(cells) if index != 0 and cell)
    return [value] if value else []


def _route_instruction(line: str) -> str | None:
    if re.search(r"\b(?:SC|S/C|subcutaneous)\b", line, re.I):
        return "inject under the skin"
    if re.search(r"\b(?:SL|sublingual)\b", line, re.I):
        return "keep under the tongue to dissolve"
    if re.search(r"\b(?:inhalation|inhaled|inh)\b", line, re.I):
        return "inhale"
    if re.search(r"\b(?:eye|eyes|ophthalmic)\b", line, re.I):
        return "put in the eye"
    if re.search(r"\b(?:topical|apply to skin|apply locally)\b", line, re.I):
        return "apply to the skin"
    if re.search(r"\b(?:PO|oral|by mouth)\b", line, re.I):
        return "take by mouth"
    return None


def _parse_medicines(lines: list[str], drug_entries: list[dict]) -> tuple[list[dict], list[str]]:
    medicines = []
    stops = []
    seen = set()
    for line in lines:
        for source_line in _line_values(line):
            matches = _find_matches(source_line, drug_entries)
            if _is_stop_line(source_line):
                if matches:
                    _, _, entry, _ = matches[0]
                    name = _display_name(entry, source_line)
                    dose = _strength_dose(source_line, entry)
                    stops.append(f"{name} {dose}".strip() if dose else name)
                continue
            if not matches:
                continue
            _, _, entry, _ = matches[0]
            if not entry["available"] or entry["generic"] in seen:
                continue
            seen.add(entry["generic"])
            name = _display_name(entry, source_line)
            dose = _strength_dose(source_line, entry)
            frequency = _frequency(source_line)
            medicines.append({
                "name": name,
                "dose": dose,
                "frequency": frequency,
                "timing": _timing(source_line),
                "duration": _duration(source_line),
                "how_to_take_plain": None,
                "_route": _route_instruction(source_line),
                "_source": source_line,
                "_generic": entry["generic"],
            })
    return medicines, stops


def _advice_sections(lines: list[str]) -> dict[str, list[str]]:
    result = {"warning_signs": [], "diet": [], "activity": [], "wound_care": [], "other_advice": []}
    mode = "other_advice"
    instructions_started = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        clean = re.sub(r"^\s*(?:[-•*]\s*|\d+[.)]\s*)", "", stripped).strip()
        low = clean.casefold().rstrip(":")
        header = clean.casefold().strip().rstrip(" :-")
        if header in {"instructions", "advice on discharge", "general advice", "adv", "advice", "other instructions"}:
            instructions_started = True
            mode = "other_advice"
            continue
        if re.match(r"^(?:report immediately|danger signs|warning signs|review sos if|return to emergency|come back to the hospital)\b", low):
            mode = "warning_signs"
            instructions_started = True
            continue
        if re.match(r"^(?:follow[- ]?up|f/u)\b", low):
            mode = "follow_ups"
            instructions_started = True
            continue
        if low.rstrip(" :-") == "review":
            mode = "follow_ups"
            instructions_started = True
            continue
        if re.match(r"^(?:diet)\s*:", low):
            mode = "diet"
            instructions_started = True
            continue
        if re.match(r"^(?:activity|physical activity)\s*:", low):
            mode = "activity"
            instructions_started = True
            continue
        if re.match(r"^(?:wound care|wound)\s*:", low):
            mode = "wound_care"
            instructions_started = True
            continue
        if re.match(r"^(?:others?|other advice)\s*:", low):
            mode = "other_advice"
            instructions_started = True
            continue
        if re.match(r"^(?:in case of emergency|for any emergency)\b", low):
            result["other_advice"].append(clean)
            continue
        if re.match(r"^(?:dr\.|doctor\b)", clean, re.I):
            mode = "done"
            continue
        if mode == "done":
            continue
        if mode == "follow_ups" or (mode == "other_advice" and not instructions_started):
            continue
        if clean.endswith(":") and len(clean.split()) <= 4:
            continue
        result.setdefault(mode, []).append(clean)
    return result


def _followups(lines: list[str]) -> list[dict]:
    index = _heading_index(lines, ("FOLLOW-UP", "FOLLOW UP", "F/U"))
    if index is None:
        index = next((i for i, line in enumerate(lines)
                      if re.fullmatch(r"\s*REVIEW\s*[:\-]?\s*", line, re.I)), None)
    if index is None:
        return []
    out = []
    for line in lines[index + 1:]:
        if _is_break(line) or re.match(r"^\s*(?:Dr\.|Doctor\b)", line, re.I):
            break
        clean = re.sub(r"^\s*(?:[-•*]\s*|\d+[.)]\s*)", "", line).strip()
        dates = []
        for match in DATE_RE.finditer(clean):
            parsed = _parse_date(match.group(0))
            if parsed:
                dates.append((match.start(), match.end(), parsed.isoformat()))
        if not dates:
            continue
        for start, end, iso in dates:
            before = clean[:start].strip(" -:;")
            after = clean[end:].strip(" -:;")
            body = " ".join(part for part in (before, after) if part)
            who_match = None
            for chunk in re.split(r"\s+-\s+|\s+in\s+", body, flags=re.I):
                parenthetical = re.search(r"\(([^()]*)\)", chunk)
                if parenthetical and re.search(r"\bOPD\b", parenthetical.group(1), re.I):
                    who_match = parenthetical
                    continue
                matches = list(re.finditer(
                    r"\b(?!(?:on|at|in)\b)([A-Za-z][A-Za-z&/'-]*(?:\s+(?!(?:on|at|in)\b)"
                    r"(?:[A-Za-z][A-Za-z&/'-]*|\([^()]*\))){0,5}\s+"
                    r"(?:OPD|clinic|centre|center|dept\.?|laboratory|lab))\b", chunk, re.I
                ))
                if matches:
                    who_match = matches[-1]
            with_whom = (who_match.group(1).strip() if who_match else None)
            purpose = body
            if who_match:
                department = with_whom
                purpose = re.sub(re.escape(department), "", body, count=1, flags=re.I).strip(" -:;")
            purpose = re.sub(r"\s+", " ", purpose)
            purpose = re.sub(r"^\s*(?:on|at|in)\s+", "", purpose, flags=re.I)
            purpose = re.sub(r"\s*\((?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\)", "", purpose, flags=re.I)
            purpose = re.sub(r"\(\s*\)", "", purpose)
            purpose = re.sub(r"\s+-\s+on\s+(?=bring\b)", " - ", purpose, flags=re.I)
            purpose = re.sub(r"\breview with reports\s*-\s*bring reports:\s*",
                             "Review with reports: ", purpose, flags=re.I)
            purpose = re.sub(r"(?:[,;:]\s*)?\b(?:on|at|in)\b(?:\s*\b(?:on|at|in)\b)*\s*[.,;:]*\s*$",
                             "", purpose, flags=re.I)
            purpose = purpose.strip(" -:;,.")
            out.append({"date": iso, "with_whom": with_whom, "purpose": purpose or None})
    return out


def _continue_previous(text: str) -> str | None:
    match = re.search(r"(?im)^\s*(?:[-•*]\s*)?continue previous medications?\b[^\n]*", text)
    return match.group(0).strip(" -*•") if match else None


def _human_frequency(value: str | None) -> str:
    if not value:
        return ""
    low = value.casefold()
    if "sos" in low or "prn" in low:
        return "only when needed"
    if "weekly" in low or "/week" in low:
        return "once a week"
    if re.search(r"\b(?:od|hs)\b", low):
        return "once a day"
    if re.search(r"\bbd\b", low):
        return "twice a day"
    if re.search(r"\b(?:tds|tid)\b", low):
        return "three times a day"
    if re.search(r"\bqid\b", low):
        return "four times a day"
    slots = re.match(r"(\d+)-(\d+)-(\d+)", value)
    if slots:
        return f"{slots.group(1)}-{slots.group(2)}-{slots.group(3)} each day"
    return value


def _human_duration(value: str | None) -> str:
    if not value:
        return ""
    low = value.casefold()
    if "life" in low:
        return "for life"
    if "review" in low or "visit" in low:
        return "until your next check-up"
    if "then" in low:
        steps = []
        for match in DURATION_RE.finditer(value):
            step = _human_duration(re.sub(r"^x\s*", "", match.group(0), flags=re.I))
            if step:
                steps.append(step.removeprefix("for "))
        if len(steps) > 1:
            return "for " + ", then ".join(steps)
    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:/\s*(7|52|12)|days?|d\b|weeks?|wks?|w\b|months?|mos?|years?|yrs?)", low)
    if not match:
        return value
    number = match.group(1)
    unit = match.group(2)
    if unit == "7":
        return f"for {number} days"
    if unit == "52":
        days = float(number) * 7
        return f"for {int(days) if days.is_integer() else days:g} days"
    if unit == "12":
        return f"for {number} months"
    unit_text = match.group(0)[len(number):].strip()
    return f"for {number} {unit_text}"


def _dose_instructions(med: dict) -> str:
    chunks = []
    if med["dose"]:
        chunks.append(med["dose"])
    if med["frequency"]:
        chunks.append(_human_frequency(med["frequency"]))
    if med["timing"] and med["timing"].casefold() not in (med["dose"] or "").casefold():
        chunks.append(med["timing"])
    if med["duration"]:
        chunks.append(_human_duration(med["duration"]))
    if med.get("_route"):
        chunks.append(med["_route"])
    return ", ".join(chunks)


def _frequency_te(value: str | None) -> str:
    if not value:
        return ""
    low = value.casefold()
    if "sos" in low or "prn" in low:
        return "అవసరమైనప్పుడు మాత్రమే"
    if "weekly" in low or "/week" in low:
        return "వారానికి ఒకసారి"
    for code, count in (("qid", 4), ("tds", 3), ("tid", 3), ("bd", 2), ("od", 1), ("hs", 1)):
        if re.search(rf"\b{code}\b", low):
            if code == "hs":
                return "రాత్రి నిద్రపోయే ముందు"
            unit = "సారి" if count == 1 else "సార్లు"
            return f"రోజుకు {count} {unit}"
    slots = re.match(r"(\d+)-(\d+)-(\d+)", value)
    if slots:
        return f"ఉదయం {slots.group(1)}, మధ్యాహ్నం {slots.group(2)}, రాత్రి {slots.group(3)}"
    return ""


def _timing_te(value: str | None) -> str:
    if not value:
        return ""
    low = value.casefold()
    if "30 minutes before breakfast and dinner" in low:
        return "అల్పాహారం మరియు రాత్రి భోజనానికి 30 నిమిషాల ముందు"
    if "30 minutes before breakfast" in low:
        return "అల్పాహారానికి 30 నిమిషాల ముందు"
    if "30 minutes before meals" in low:
        return "భోజనానికి 30 నిమిషాల ముందు"
    if "at bedtime" in low or "before bed" in low:
        return "రాత్రి నిద్రపోయే ముందు"
    if "before_bf_dinner" in low:
        return "అల్పాహారం మరియు రాత్రి భోజనానికి ముందు"
    if "before_meals_15" in low:
        return "భోజనానికి 15 నిమిషాల ముందు"
    if "before_meals_30" in low:
        return "భోజనానికి 30 నిమిషాల ముందు"
    if "empty_stomach" in low:
        return "ఖాళీ కడుపుతో"
    if "before_breakfast" in low:
        return "అల్పాహారానికి ముందు"
    if "after_breakfast" in low:
        return "అల్పాహారం తర్వాత"
    if "after_lunch" in low:
        return "మధ్యాహ్న భోజనం తర్వాత"
    if "after_dinner" in low:
        return "రాత్రి భోజనం తర్వాత"
    if "with_evening_meal" in low:
        return "సాయంత్రం భోజనంతో"
    if "before_food" in low:
        return "భోజనానికి ముందు"
    if "after_food" in low:
        return "భోజనం తర్వాత"
    if "at_10pm" in low:
        return "రాత్రి 10 గంటలకు"
    return ""


def _duration_te(value: str | None) -> str:
    if not value:
        return ""
    low = value.casefold()
    if "life" in low or "lifelong" in low:
        return "జీవితాంతం"
    if "review" in low or "visit" in low:
        return "తదుపరి వైద్య పరీక్ష వరకు"
    match = re.search(
        r"(\d+(?:\.\d+)?)\s*(?:/\s*(7|52|12)|days?|d\b|weeks?|wks?|w\b|months?|mos?|years?|yrs?)",
        low,
    )
    if not match:
        return ""
    number, unit = match.groups()
    if unit == "7" or re.match(r"(?:days?|d\b)", low[match.end(1):].strip()):
        unit_te = "రోజులు"
    elif unit == "52":
        number, unit_te = str(int(float(number) * 7)), "రోజులు"
    elif unit == "12" or re.match(r"(?:months?|mos?)", low[match.end(1):].strip()):
        unit_te = "నెలలు"
    elif re.match(r"(?:weeks?|wks?|w\b)", low[match.end(1):].strip()):
        unit_te = "వారాలు"
    else:
        unit_te = "సంవత్సరాలు"
    if float(number) == 1:
        unit_te = {
            "రోజులు": "రోజు",
            "నెలలు": "నెల",
            "వారాలు": "వారం",
            "సంవత్సరాలు": "సంవత్సరం",
        }[unit_te]
    return f"{number} {unit_te}"


def _dose_te(value: str | None) -> str:
    if not value:
        return ""
    text = value
    for pattern, replacement in (
        (r"\bbefore breakfast\b", "అల్పాహారానికి ముందు"),
        (r"\bbefore dinner\b", "రాత్రి భోజనానికి ముందు"),
        (r"\bafter breakfast\b", "అల్పాహారం తర్వాత"),
        (r"\bafter dinner\b", "రాత్రి భోజనం తర్వాత"),
        (r"\bafter lunch\b", "మధ్యాహ్న భోజనం తర్వాత"),
        (r"\bafter food\b", "భోజనం తర్వాత"),
        (r"\bwith food\b", "భోజనంతో"),
        (r"\bbefore food\b", "భోజనానికి ముందు"),
    ):
        text = re.sub(pattern, replacement, text, flags=re.I)
    return text


def _route_te(value: str | None) -> str:
    if not value:
        return ""
    low = value.casefold()
    if "inject under the skin" in low:
        return "చర్మం కింద ఇంజెక్షన్‌గా ఇవ్వండి"
    if "under the tongue" in low:
        return "నాలుక కింద ఉంచి కరిగేలా చేయండి"
    if "inhale" in low:
        return "ఇన్హేలర్ ద్వారా పీల్చండి"
    if "put in the eye" in low:
        return "కంటిలో వేయండి"
    if "apply to the skin" in low:
        return "చర్మంపై రాయండి"
    if "take by mouth" in low:
        return "నోటి ద్వారా తీసుకోండి"
    return ""


def _expand_glossary(text: str, glossary: list[dict[str, str]] | None) -> str:
    if not glossary:
        return text
    patient_facing = {
        "afb", "att", "bd", "bp", "dvt", "fbs", "fwb", "glv", "hs", "lft",
        "nwb", "od", "ogtt", "opd", "pc", "pft", "ppbs", "pwb", "qid",
        "rom", "sc", "sl", "smbg", "sos", "tds",
    }
    meanings: dict[str, set[str]] = {}
    for row in glossary:
        abbreviation = row.get("abbreviation", "").strip()
        plain = row.get("plain_language", "").strip()
        if abbreviation and plain:
            meanings.setdefault(abbreviation.casefold(), set()).add(plain)
    for abbreviation, values in sorted(meanings.items(), key=lambda item: -len(item[0])):
        if abbreviation not in patient_facing or len(values) != 1:
            continue
        plain = next(iter(values))
        if "/" in plain or len(abbreviation) < 2:
            continue
        text = re.sub(rf"\b{re.escape(abbreviation)}\b", plain, text, flags=re.I)
    return text


def _plain_clause(text: str) -> str:
    text = text.strip().rstrip(".")
    rewrites = (
        (r"Excessive PV bleeding\s*\(>\s*1 pad soaked per hour\)\s*/\s*passing clots",
         "Very heavy vaginal bleeding, such as soaking more than 1 pad in an hour, or passing blood clots"),
        (r"Foul smelling lochia\s*/\s*vaginal discharge", "Bad-smelling vaginal fluid or discharge"),
        (r"Painful, red, hard swelling of the breast with fever",
         "A painful, firm, red breast that is swollen, together with a fever"),
        (r"Severe headache, blurring of vision or pain in upper abdomen",
         "Strong head pain, unclear sight, or pain high in the belly"),
        (r"Persistent low mood\s*/\s*hopelessness\s*/\s*insomnia\s*/\s*thoughts of harming self or baby",
         "Ongoing sadness, hopeless feelings, sleep trouble, or thoughts of hurting yourself or your baby"),
        (r"Baby:\s*poor feeding\s*/\s*lethargy\s*/\s*fever\s*/\s*yellowish skin",
         "For your baby, seek help if feeding is poor, the baby is unusually sleepy, has a fever, or looks yellow"),
        (r"Any bleeding\s*-\s*gums, urine, black or red stools, vomiting blood",
         "Blood from the gums or in urine, black or red bowel movements, or blood in vomit"),
        (r"Bleeding from any site\s*\(gums\s*/\s*urine\s*/\s*black or red stools\s*/\s*blood vomiting\)",
         "Blood from the gums or in urine, black or red bowel movements, or blood in vomit"),
        (r"Early ambulation\s*-\s*walk in the house several times a day",
         "Walk inside your home several times each day"),
        (r"Nutritious diet\s*\+\s*extra meal daily;\s*GLV, milk, dal;\s*plenty of oral fluids",
         "Eat nourishing food and add one meal each day; include green leafy vegetables, milk, and dal, and drink plenty of fluids"),
        (r"Nutritious diet with 1 extra meal a day\s*-\s*green leafy vegetables, milk, dal;\s*plenty of fluids",
         "Eat nourishing food and add one meal each day; include green leafy vegetables, milk, and dal, and drink plenty of fluids"),
        (r"High protein diet\s*-\s*dal\s*/\s*eggs\s*/\s*milk\s*/\s*(paneer|soya)(?:\s*-\s*for healing)?",
         r"Choose protein-rich foods such as dal, eggs, milk, and \1 to support healing"),
        (r"Low salt diet\s*-\s*less than 5 g salt per day;\s*avoid pickles, papad, namkeen",
         "Keep salt below 5 g each day; do not eat pickles, papad, or namkeen"),
        (r"Salt\s*<\s*5 g/day\s*-\s*no pickle\s*/\s*papad\s*/\s*namkeen",
         "Keep salt below 5 g each day; avoid pickles, papad, and namkeen"),
        (r"Avoid lifting weight\s*>\s*5 kg x 4 weeks",
         "For 4 weeks, do not lift anything heavier than 5 kg"),
        (r"No heavy lifting\s*\(more than 5 kg\)\s*for 4 weeks",
         "For 4 weeks, do not lift anything heavier than 5 kg"),
        (r"No smoking\s*/\s*no tobacco in any form|Stop smoking and all forms of tobacco",
         "Do not smoke or use tobacco in any form"),
        (r"Hypoglycaemia\s*-\s*sweating\s*/\s*tremors\s*/\s*confusion:\s*3 tsp glucose or sugar in water immediately and report;\s*if drowsy, nothing orally\s*-\s*bring to hospital",
         "If you are sweaty, shaky, or confused from low blood sugar, take 3 tsp of sugar or glucose in water and seek care; if drowsy, take nothing by mouth and go to hospital"),
        (r"Low sugar symptoms\s*\(sweating, shakiness, confusion\):\s*take 3 tsp sugar or glucose in water at once and report;\s*if drowsy, give nothing by mouth\s*-\s*bring to hospital",
         "If you are sweaty, shaky, or confused from low blood sugar, take 3 tsp of sugar or glucose in water and seek care; if drowsy, take nothing by mouth and go to hospital"),
        (r"Diabetic diet\s*-\s*avoid sugar, sweets, jaggery, honey and fruit juices",
         "For diabetes, avoid sugar, sweets, jaggery, honey, and fruit juice"),
        (r"Small, frequent meals\s*-\s*do not skip any meal|Small frequent meals;\s*do not skip meals",
         "Eat small meals often, and do not miss a meal"),
        (r"Adequate oral fluids\s*-\s*2 to 3 litres per day",
         "Drink 2 to 3 litres of fluids each day"),
        (r"Check blood sugar 4 times a day \(before meals and at bedtime\) and maintain a chart",
         "Measure blood sugar 4 times daily, before meals and at bedtime, and record the results"),
        (r"Insulin technique taught\s*-\s*rotate injection sites \(abdomen/thigh\);\s*store insulin in fridge, not freezer",
         "Use the injection method you were shown; alternate sites on the abdomen and thigh, and store insulin in a refrigerator, not the freezer"),
        (r"Injection technique explained:\s*rotate sites \(abdomen\s*/\s*thigh\);\s*keep insulin in fridge \(not freezer\)",
         "Use the injection method you were shown; alternate sites on the abdomen and thigh, and store insulin in a refrigerator, not the freezer"),
        (r"Do not stop insulin on sick days;\s*check sugar every 4 hours and contact hospital",
         "When ill, continue insulin, check sugar every 4 hours, and contact the hospital"),
        (r"Always carry sugar or glucose with you|Carry glucose\s*/\s*sugar at all times",
         "Keep sugar or glucose with you at all times"),
        (r"Foot care:\s*daily inspection, footwear always, avoid walking barefoot",
         "Check your feet every day, wear footwear, and do not walk barefoot"),
        (r"Blood sugar above 300 mg/dl on two readings|Sugar\s*>\s*300 mg/dl on 2 readings",
         "If two readings show blood sugar above 300 mg/dl"),
        (r"Vomiting, pain in abdomen, fast breathing or drowsiness",
         "Get help for vomiting, belly pain, rapid breathing, or unusual sleepiness"),
        (r"Vomiting\s*/\s*pain abdomen\s*/\s*rapid breathing\s*/\s*drowsiness",
         "Get help for vomiting, belly pain, rapid breathing, or unusual sleepiness"),
        (r"Salt\s*<\s*5 g/day\s*-\s*no pickle\s*/\s*papad\s*/\s*namkeen",
         "Keep salt below 5 g each day; avoid pickles, papad, and namkeen"),
        (r"Total fluids max 1\.5 litres/day \(incl\. water\s*/\s*tea\s*/\s*milk\s*/\s*dal\)",
         "Have no more than 1.5 litres of fluids each day, including water, tea, milk, and dal"),
        (r"Gradually increase walking as tolerated",
         "Add more walking little by little, as you are able"),
        (r"Daily morning weight\s*-\s*maintain record",
         "Weigh yourself each morning and write the result down"),
        (r"Do not take painkillers from the chemist without doctor's advice",
         "Ask your doctor before taking any pain medicine bought from a chemist"),
        (r"DM diet:\s*no sugar\s*/\s*sweets\s*/\s*jaggery\s*/\s*honey\s*/\s*fruit juice",
         "For diabetes, avoid sugar, sweets, jaggery, honey, and fruit juice"),
        (r"Daily foot care\s*-\s*inspect feet, always wear footwear, no barefoot walking",
         "Check your feet every day, wear footwear, and do not walk barefoot"),
        (r"SMBG QID \(pre-meals \+ bedtime\)\s*-\s*maintain sugar chart",
         "Check your blood sugar four times each day, before meals and at bedtime, and keep a record"),
        (r"Home sugar readings QID \(pre-meals \+ bedtime\)\s*-\s*maintain sugar chart",
         "Check your blood sugar four times each day, before meals and at bedtime, and keep a record"),
        (r"Keep the wound clean and dry",
         "Clean the incision and keep it dry"),
        (r"Keep wound clean and dry",
         "Clean the incision and keep it dry"),
        (r"Do not remove the dressing;\s*it will be changed at review",
         "Leave the dressing in place; it will be changed at your check-up"),
        (r"Knee bending and quadriceps exercises as taught by physiotherapist,\s*3-4 times a day",
         "Do the knee-bending and thigh exercises shown by the physiotherapist, 3-4 times daily"),
        (r"Knee ROM \+ quadriceps exercises as taught by physio,\s*3-4 times/day",
         "Do the knee and thigh-strengthening movements shown by the physiotherapist, 3-4 times daily"),
        (r"Ice pack over the knee for 15-20 min,\s*3-4 times a day",
         "Place a cold pack on the knee for 15-20 minutes, 3-4 times daily"),
        (r"No squatting\s*/\s*cross-legged sitting;\s*western toilet only",
         "Avoid squatting or sitting with crossed legs; use a Western-style toilet"),
        (r"Avoid squatting and sitting cross-legged;\s*use western toilet",
         "Do not squat or sit with crossed legs; use a Western-style toilet"),
        (r"Mobilise with walker\s*-\s*FWB as tolerated",
         "Use the walker and put weight on the leg as much as you can tolerate"),
        (r"Walk with walker support;\s*full weight bearing as tolerated",
         "Walk using the support of your walker and bear weight as you are able"),
        (r"High fibre diet\s*\+\s*plenty of fluids\s*\(avoid constipation\)",
         "Choose fibre-rich foods and drink plenty of fluids to help prevent constipation"),
        (r"High-fibre diet with plenty of oral fluids to avoid constipation",
         "Eat foods rich in fibre and drink plenty of fluids to help prevent constipation"),
        (r"No heavy lifting\s*\(more than 5 kg\)\s*for 6 weeks",
         "For 6 weeks, avoid lifting anything heavier than 5 kg"),
        (r"Avoid lifting weight\s*>\s*5 kg x 6 weeks",
         "For 6 weeks, do not lift items weighing over 5 kg"),
        (r"Do not apply oil,\s*turmeric or powder on the wound",
         "Keep oil, turmeric, and powder off the wound"),
        (r"Nothing\s*\(oil\s*/\s*haldi\s*/\s*powder\)\s*to be applied on wound",
         "Do not put oil, turmeric, or powder on the wound"),
        (r"Splint wound with pillow on coughing\s*/\s*sneezing;\s*avoid straining",
         "Hold a pillow over the wound when coughing or sneezing, and avoid straining"),
        (r"May bathe after 48 hours;\s*pat the wound dry with a clean cloth",
         "Bathing is allowed after 48 hours; gently dry the wound with a clean cloth"),
        (r"Perineal hygiene\s*-\s*wash with clean water after toilet;\s*change pads every 4-6 hours",
         "After using the toilet, rinse the area with clean water and change pads every 4-6 hours"),
        (r"Sitz bath\s*\(warm water\)\s*twice daily for 1 week",
         "Soak in a warm-water sitz bath twice a day for 1 week"),
        (r"Contraception to be discussed at postnatal visit;\s*spacing of at least 2 years advised",
         "Discuss birth spacing at the postnatal visit; the advised gap is at least 2 years"),
        (r"Feeling low or hopeless most of the time,\s*unable to sleep,\s*or thoughts of harming self or baby",
         "Seek help for ongoing sadness or hopelessness, difficulty sleeping, or thoughts of harming yourself or the baby"),
        (r"Heavy bleeding per vaginum\s*\(soaking more than 1 pad in an hour\)\s*or passing clots",
         "Get urgent help for very heavy vaginal bleeding (over 1 pad soaked in an hour) or blood clots"),
        (r"Baby not feeding well,\s*very sleepy,\s*has fever,\s*or yellow skin",
         "Get help if your baby feeds poorly, is unusually sleepy, has a fever, or develops yellow skin"),
        (r"Heart-healthy diet\s*-\s*more vegetables,\s*fruits and whole grains;\s*avoid fried food and excess oil",
         "For heart health, choose vegetables, fruit, and whole grains, and limit fried foods and excess oil"),
        (r"Antiplatelets\s*/\s*anticoagulant not to be stopped without cardiologist's advice\s*\(incl\.\s*dental\s*/\s*minor procedures\)",
         "Do not stop antiplatelet or blood-thinning medicines without the cardiologist's advice, including before dental or minor procedures"),
        (r"Chest pain not relieved after 3 SL tablets taken 5 min apart",
         "Get urgent help if chest pain continues after 3 tablets under the tongue, taken 5 minutes apart"),
        (r"Chest pain\s*-\s*sit,\s*1 tab SL;\s*repeat q5min up to 3 tabs",
         "For chest pain, sit down and take 1 tablet under the tongue; repeat every 5 minutes, up to 3 tablets"),
        (r"Increase in sputum quantity or change to yellow/green colour",
         "Seek advice if you produce more sputum or it turns yellow or green"),
        (r"Increased sputum\s*/\s*sputum turning yellow or green",
         "Contact your care team if sputum increases or becomes yellow or green"),
        (r"Wheeze\s*/\s*breathlessness not settling with reliever inhaler",
         "Get help if wheezing or breathlessness does not improve with your reliever inhaler"),
        (r"Wheeze or breathlessness not relieved by the reliever inhaler",
         "Seek help if the reliever inhaler does not ease wheezing or breathlessness"),
        (r"No exposure to biomass\s*\(chulha\)\s*smoke\s*/\s*dust",
         "Stay away from cooking-fire smoke and dust"),
        (r"Avoid dust,\s*smoke and cold air\s*\(asthma triggers\)",
         "Keep away from dust, smoke, and cold air that can trigger asthma"),
        (r"Inhaler technique taught;\s*to use with spacer",
         "Use the inhaler with the spacer, following the technique you were shown"),
        (r"Pursed-lip breathing and daily walking as tolerated",
         "Practice breathing out through pursed lips and walk each day as you are able"),
        (r"Slab to be kept dry\s*-\s*nothing to be inserted under slab",
         "Keep the cast dry and do not put anything underneath it"),
        (r"Numbness,\s*tingling,\s*bluish colour or severe swelling of the operated hand",
         "Get medical help for numbness, tingling, blue colour, or marked swelling in the operated hand"),
        (r"Keep hand elevated above heart level;\s*move fingers frequently",
         "Raise your hand above your heart and move your fingers often"),
        (r"Bath allowed after 48 h\s*-\s*pat wound dry with clean cloth",
         "You may bathe after 48 hours; gently dry the incision with a clean cloth"),
        (r"Support the wound with a pillow while coughing or sneezing;\s*avoid straining",
         "Hold a pillow against the incision when you cough or sneeze, and do not strain"),
        (r"Walk around the house several times a day",
         "Walk inside your home several times each day"),
        (r"Soft, easily digestible diet for 1 week, then normal diet",
         "For 1 week, choose soft foods that are easy to digest; after that, return to your usual food"),
        (r"Wound site redness\s*/\s*swelling\s*/\s*pus or discharge",
         "Redness, swelling, pus, or fluid coming from the wound"),
        (r"Severe\s*/\s*increasing abdominal pain",
         "Strong or worsening pain in your belly"),
        (r"Persistent vomiting or unable to take food and fluids orally",
         "Repeated vomiting or being unable to eat or drink"),
        (r"Distension of abdomen with no passage of stool or gas",
         "A swollen belly when no stool or gas is passing"),
        (r"Weight gain of more than 2 kg in 3 days",
         "Weight rising by over 2 kg within 3 days"),
        (r"Breathlessness at rest or on lying flat",
         "Trouble breathing while resting or lying flat"),
        (r"Increasing pedal oedema \(swelling of feet\s*/\s*legs\)",
         "More swelling in your feet or legs"),
        (r"Urine output markedly reduced",
         "A large drop in how much urine you pass"),
        (r"Giddiness, fainting or loss of consciousness",
         "Dizziness, fainting, or becoming unconscious"),
        (r"Palpitations\s*-\s*fast\s*/\s*irregular heart beat",
         "A racing or uneven heartbeat"),
        (r"Fluid restriction\s*-\s*not more than 1\.5 L/day including water, tea, milk, dal",
         "Limit all fluids, including water, tea, milk, and dal, to 1.5 L each day"),
        (r"Gradual increase in walking as tolerated",
         "Add more walking little by little, as you are able"),
        (r"Check weight every morning and keep a record",
         "Weigh yourself each morning and write the result down"),
        (r"Strictly no alcohol",
         "Do not drink alcohol"),
        (r"Daily ATT without missing doses\s*-\s*full course to be completed",
         "Take your TB medicines each day, do not miss doses, and finish the full course"),
        (r"Cover mouth while coughing;\s*spit sputum into a covered container;\s*keep room well ventilated",
         "When coughing, cover your mouth, collect sputum in a covered container, and keep the room well ventilated"),
        (r"Household contacts\s*-\s*TB screening at nearest PHC",
         "People living with you should get TB screening at the nearest health centre"),
        (r"Orange-red urine expected with rifampicin \(harmless\)",
         "Rifampicin may turn urine orange-red; this is expected and harmless"),
        (r"Exclusive breast feeding x 6 months",
         "Feed the baby only breast milk for 6 months"),
        (r"Baby's immunisation as per the card given",
         "Follow the baby's vaccination dates on the card provided"),
        (r"Family planning counselling at PNC visit\s*-\s*spacing >= 2 years",
         "Discuss family planning at the postnatal visit; the stated interval is at least 2 years"),
        (r"Report immediately to Casualty in case of",
         "Go to the hospital right away if"),
        (r"For any emergency report to the Casualty\s*/\s*ER of Chandrapuri District Hospital \(open 24 hours\)",
         "In an emergency, contact the 24-hour emergency department at Chandrapuri District Hospital"),
        (r"In case of emergency contact Casualty, Chandrapuri District Hospital \(24x7\)",
         "For an emergency, contact the 24-hour emergency department at Chandrapuri District Hospital"),
        (r"Review with reports", "Bring your reports for a check-up"),
        (r"Please bring:", "Take these results with you:"),
        (r"OPD", "clinic"),
    )
    for pattern, replacement in rewrites:
        text = re.sub(pattern, replacement, text, flags=re.I)
    text = re.sub(r"\bBreathlessness or difficulty in breathing\b", "Trouble breathing", text, flags=re.I)
    text = re.sub(r"\bBreathlessness / difficulty in breathing\b", "Trouble breathing", text, flags=re.I)
    text = re.sub(r"\bFever\s*>\s*", "A temperature above ", text, flags=re.I)
    text = re.sub(r"\bFever above\b", "A temperature higher than", text, flags=re.I)
    text = re.sub(r"\bWound site redness\b", "Redness around the wound", text, flags=re.I)
    text = re.sub(r"\bReport immediately\b", "Go to the hospital at once", text, flags=re.I)
    text = re.sub(r"\bWt gain\b", "Weight increase", text, flags=re.I)
    text = re.sub(r"\bGiddiness\b", "Dizziness", text, flags=re.I)
    text = re.sub(r"\bLOC\b", "loss of consciousness", text, flags=re.I)
    text = re.sub(r"\bPV\b", "vaginal", text, flags=re.I)
    text = re.sub(r"\bSOS\b", "when needed", text, flags=re.I)
    text = re.sub(r"\bTDS\b", "three times a day", text, flags=re.I)
    text = re.sub(r"\bBD\b", "twice a day", text, flags=re.I)
    text = re.sub(r"\bOD\b", "once a day", text, flags=re.I)
    text = re.sub(r"\bLFT\b", "liver tests", text, flags=re.I)
    text = re.sub(r"\bAFB\b", "sputum test", text, flags=re.I)
    text = re.sub(r"\bSMBG\b", "home sugar readings", text, flags=re.I)
    text = re.sub(r"\bGLV\b", "green leafy vegetables", text, flags=re.I)
    text = re.sub(r"\s+", " ", text)
    for abbr, expansion in (("DVT", "a blood clot in a leg vein"), ("BP", "blood pressure"),
                            ("OPD", "clinic"), ("POD", "day after surgery"), ("GLV", "green leafy vegetables")):
        text = re.sub(rf"\b{abbr}\b", expansion, text, flags=re.I)
    return text


def _telugu_clause(text: str, glossary: list[dict[str, str]] | None = None) -> str:
    text = _expand_glossary(_plain_clause(text), glossary).strip().rstrip(".")
    low = text.casefold()
    if "avoid sugar" in low and all(term in low for term in ("jaggery", "honey", "fruit juice")):
        return "చక్కెర, స్వీట్లు, బెల్లం, తేనె మరియు పండ్ల రసాలు తీసుకోకండి"
    if "alternate sites" in low and "insulin" in low and "freezer" in low:
        return "ఇన్సులిన్ ఇంజెక్షన్ ఇచ్చే చోటును పొట్ట లేదా తొడపై మార్చుతూ ఇవ్వండి; ఇన్సులిన్‌ను ఫ్రిజ్‌లో ఉంచండి, ఫ్రీజర్‌లో కాదు"
    if "feet" in low and "footwear" in low and "barefoot" in low:
        return "పాదాలను ప్రతిరోజూ పరిశీలించండి, ఎల్లప్పుడూ పాదరక్షలు ధరించండి; చెప్పులు లేకుండా నడవకండి"
    if "75 g" in low and "sugar test" in low and "glucose" in low:
        return "గ్లూకోజ్ తాగిన తర్వాత 75 g చక్కెర పరీక్ష చేయించుకోండి"
    if "sugar test before eating" in low and "after a meal" in low:
        return "భోజనానికి ముందు రక్తంలో చక్కెర పరీక్ష, భోజనం చేసిన 2 గంటల తర్వాత మరోసారి పరీక్ష చేయించుకోండి"
    if "home sugar readings" in low or "smbg chart" in low:
        return "రక్తంలో చక్కెర కొలతల పట్టికతో తదుపరి వైద్య తనిఖీకి రండి"
    if "sugar chart" in low:
        return "రక్తంలో చక్కెర కొలతల పట్టికతో తదుపరి వైద్య తనిఖీకి రండి"
    if ("hpe" in low or "histopathology" in low) and "report" in low:
        return "హిస్టోపాథాలజీ పరీక్షా నివేదికతో తదుపరి వైద్య తనిఖీకి రండి"
    if "bring your reports" in low or "review with reports" in low:
        tests = re.search(
            r"(?:bring reports?:|bring your reports for a check-up:|take these results with you:)\s*(.+)$",
            text, re.I,
        )
        if tests:
            return f"పరీక్షా నివేదికలు తీసుకుని తదుపరి వైద్య తనిఖీకి రండి: {tests.group(1)}"
        return "పరీక్షా నివేదికలు తీసుకుని తదుపరి వైద్య తనిఖీకి రండి"
    if ("lft" in low or "liver tests" in low) and "review" in low:
        return "కాలేయ పనితీరు పరీక్షలు చేయించుకుని తదుపరి వైద్య తనిఖీకి హాజరుకండి"
    if "repeat cxr" in low or "repeat chest x-ray" in low:
        return "CXR (ఛాతీ ఎక్స్-రే) పరీక్షను మళ్లీ చేయించుకోండి"
    if "physiotherapy" in low:
        return "ఫిజియోథెరపీ సెషన్‌కు హాజరుకండి"
    if "rft" in low or "renal function" in low:
        if "electrolyte" in low:
            return "మూత్రపిండాల పనితీరు మరియు రక్తంలోని ఎలక్ట్రోలైట్స్ పరీక్షలు చేయించుకోండి"
        return "మూత్రపిండాల పనితీరు పరీక్ష చేయించుకోండి"
    if "creatinine" in low and "electrolyte" in low:
        return "రక్తంలోని క్రియాటినిన్ మరియు ఎలక్ట్రోలైట్స్ పరీక్షలు చేయించుకోండి"
    if "inr" in low and "test" in low:
        return "INR రక్త పరీక్ష చేయించుకోండి"
    fever_value = re.search(
        r"(?:temperature|fever)\s*(?:higher than|above|>)?\s*(\d+(?:\.\d+)?\s*F(?:\s*\(\s*\d+(?:\.\d+)?\s*C\s*\))?)",
        text, re.I,
    )
    if "chandrapuri district hospital" in low and "emergency" in low:
        return "అత్యవసర పరిస్థితిలో Chandrapuri District Hospital అత్యవసర విభాగాన్ని (24 గంటలు) సంప్రదించండి"
    if "temperature" in low or "fever" in low:
        if fever_value:
            return f"జ్వరం {fever_value.group(1)} కంటే ఎక్కువైతే వెంటనే వైద్య సహాయం పొందండి"
        if "chills" in low:
            return "అధిక జ్వరం లేదా చలి వణుకులు ఉంటే వెంటనే వైద్య సహాయం పొందండి"
        if "3 days" in low:
            return "డిశ్చార్జ్ తర్వాత 3 రోజులకు మించి జ్వరం కొనసాగినా లేదా మళ్లీ వచ్చినా వైద్య సహాయం పొందండి"
        return "జ్వరం ఉంటే వెంటనే వైద్య సహాయం పొందండి"
    if "hypogly" in low or ("sugar" in low and ("sweaty" in low or "shaky" in low or "tremor" in low)):
        return ("రక్తంలో చక్కెర తగ్గినప్పుడు చెమటలు, వణుకు లేదా గందరగోళం ఉంటే, 3 చిన్న చెంచాల చక్కెర లేదా గ్లూకోజ్‌ను "
                "నీటిలో తీసుకుని వెంటనే ఆసుపత్రికి వెళ్లండి. మత్తుగా ఉంటే నోటి ద్వారా ఏమీ ఇవ్వకండి; ఆసుపత్రికి తీసుకెళ్లండి")
    if "blood sugar above 300" in low or "sugar > 300" in low:
        return "రెండు రక్త చక్కెర పరీక్షల్లోనూ 300 mg/dl కంటే ఎక్కువగా ఉంటే వైద్య బృందాన్ని సంప్రదించండి"
    if "coughing up blood" in low or "cough" in low and "blood" in low:
        return "దగ్గినప్పుడు రక్తం వస్తే వెంటనే ఆసుపత్రికి వెళ్లండి"
    if "chest pain" in low:
        if "3" in low and ("tablet" in low or "dose" in low):
            return "నాలుక కింద పెట్టే 3 మాత్రలు, ప్రతి 5 నిమిషాలకు ఒకటి తీసుకున్నా ఛాతి నొప్పి తగ్గకపోతే అత్యవసర సహాయం పొందండి"
        return "ఛాతి నొప్పి, బరువుగా అనిపించడం లేదా చెమటలు వస్తే వెంటనే ఆసుపత్రికి వెళ్లండి"
    if "breath" in low or "wheez" in low or "shortness" in low:
        if "reliever inhaler" in low:
            return "ఉపశమన ఇన్హేలర్ వాడినా శ్వాస ఇబ్బంది లేదా పిల్లికూత తగ్గకపోతే వైద్య సహాయం పొందండి"
        if "sentence" in low:
            return "శ్వాస ఇబ్బందివల్ల పూర్తి వాక్యాలు మాట్లాడలేకపోతే వెంటనే ఆసుపత్రికి వెళ్లండి"
        if "lying flat" in low or "lying down" in low:
            return "విశ్రాంతిలో లేదా పడుకున్నప్పుడు శ్వాస తీసుకోవడం కష్టమైతే వెంటనే సహాయం పొందండి"
        return "శ్వాస తీసుకోవడంలో ఇబ్బంది ఉంటే వెంటనే వైద్య సహాయం పొందండి"
    if "wound" in low or "incision" in low or "dressing" in low:
        if "redness" in low or "pus" in low or "discharge" in low:
            return "గాయం చుట్టూ ఎర్రదనం, వాపు, చీము లేదా ద్రవం వస్తే వెంటనే ఆసుపత్రికి వెళ్లండి"
        if "bleeding" in low or "soakage" in low:
            return "గాయం నుంచి రక్తం వచ్చినా లేదా కట్టు తడిసిపోయినా వెంటనే వైద్య సహాయం పొందండి"
        if "remove" in low or "leave the dressing" in low:
            return "కట్టును తీసేయకండి; వైద్య పరీక్ష సమయంలో దాన్ని మారుస్తారు"
        if "oil" in low or "turmeric" in low or "powder" in low:
            return "గాయంపై నూనె, పసుపు లేదా పొడి వేయకండి"
        if "clean" in low or "dry" in low:
            return "గాయాన్ని శుభ్రంగా, పొడిగా ఉంచండి"
    if "bleeding" in low or "blood from" in low or "blood in" in low or "haemoptysis" in low:
        if "pad" in low or "vaginal" in low or "per vaginum" in low:
            return "ఒక గంటలో ఒకటి కంటే ఎక్కువ ప్యాడ్లు తడిసేంత రక్తస్రావం లేదా రక్తపు గడ్డలు వస్తే అత్యవసర సహాయం పొందండి"
        if "gum" in low or "urine" in low or "stool" in low or "vomit" in low:
            return "చిగుళ్ల నుంచి లేదా మూత్రంలో రక్తం, నల్లగా లేదా ఎర్రగా మలం, లేదా వాంతిలో రక్తం కనిపిస్తే వెంటనే ఆసుపత్రికి వెళ్లండి"
        if "cough" in low:
            return "దగ్గినప్పుడు రక్తం వస్తే వెంటనే ఆసుపత్రికి వెళ్లండి"
        return "రక్తస్రావం కనిపిస్తే వెంటనే వైద్య సహాయం పొందండి"
    if "clot" in low or ("calf" in low and "swelling" in low):
        return "కాల్ఫ్‌లో నొప్పి లేదా కాలు వాపు ఉంటే వెంటనే వైద్య సహాయం పొందండి; ఇది రక్తం గడ్డకట్టిన లక్షణం కావచ్చు"
    if "scrot" in low:
        return "వృషణ సంచిలో వాపు లేదా తీవ్రమైన నొప్పి ఉంటే వెంటనే ఆసుపత్రికి వెళ్లండి"
    if "urine" in low or "urinary" in low or "micturition" in low:
        if "unable" in low or "inability" in low or "pass urine" in low:
            return "మూత్రం పోయలేకపోతే వెంటనే వైద్య సహాయం పొందండి"
        if "reduced" in low or "decrease" in low or "drop" in low:
            return "మూత్రం పరిమాణం బాగా తగ్గితే వైద్య సహాయం పొందండి"
        if "burning" in low or "pain" in low:
            return "మూత్రం పోసేటప్పుడు మంట లేదా నొప్పి ఉంటే వైద్య సహాయం పొందండి"
        if "orange" in low or "rifampicin" in low:
            return "Rifampicin వల్ల మూత్రం నారింజ-ఎరుపు రంగులోకి మారవచ్చు; ఇది సాధారణంగా కనిపించే మార్పు"
    if "jaundice" in low or ("yellow" in low and "eye" in low):
        return "కళ్లు పసుపు రంగులోకి మారినా లేదా మళ్లీ మళ్లీ వాంతులు, ఆకలి లేకపోవడం ఉంటే TB మందులు ఆపి వెంటనే ఆసుపత్రికి వెళ్లండి"
    if "dizz" in low or "faint" in low or "consciousness" in low or "giddiness" in low:
        return "తల తిరగడం, మూర్ఛ రావడం లేదా స్పృహ కోల్పోవడం జరిగితే వెంటనే వైద్య సహాయం పొందండి"
    if "palpitation" in low or "heartbeat" in low or "heart beat" in low:
        return "గుండె వేగంగా లేదా అసమానంగా కొట్టుకుంటే వెంటనే వైద్య సహాయం పొందండి"
    if "weakness" in low and ("face" in low or "arm" in low or "speech" in low):
        return "ముఖం, చేయి లేదా కాలులో అకస్మాత్తుగా బలహీనత వచ్చినా లేదా మాట తడబడినా వెంటనే ఆసుపత్రికి వెళ్లండి"
    if "convulsion" in low or "seizure" in low or "fit" in low:
        return "మూర్ఛ లేదా ఫిట్స్ వస్తే వెంటనే ఆసుపత్రికి వెళ్లండి"
    if "headache" in low or "vision" in low or "epigastric" in low or "upper abdomen" in low:
        return "తీవ్రమైన తలనొప్పి, చూపు మసకబారడం లేదా పై కడుపులో నొప్పి ఉంటే వెంటనే వైద్య సహాయం పొందండి"
    if "baby" in low and ("feed" in low or "sleepy" in low or "yellow" in low or "fever" in low):
        return "శిశువు సరిగా పాలు తాగకపోయినా, చాలా నిద్రమత్తుగా ఉన్నా, జ్వరం వచ్చినా లేదా చర్మం పసుపుగా కనిపించినా వైద్య సహాయం పొందండి"
    if "hopeless" in low or "harming yourself" in low or "harming self" in low or "low mood" in low:
        return "ఎక్కువకాలం దిగులు లేదా నిరాశగా ఉండటం, నిద్రపట్టకపోవడం, మీకు లేదా శిశువుకు హాని చేయాలనే ఆలోచనలు వస్తే వెంటనే సహాయం పొందండి"
    if "breast" in low and ("pain" in low or "swelling" in low or "fever" in low):
        return "జ్వరంతో పాటు రొమ్ము నొప్పిగా, ఎర్రగా లేదా వాపుగా ఉంటే వైద్య సహాయం పొందండి"
    if "vaginal discharge" in low or "lochia" in low or "vaginal fluid" in low:
        return "దుర్వాసనతో కూడిన యోని స్రావం ఉంటే వైద్య సహాయం పొందండి"
    if ("abdominal" in low or "abdomen" in low or "belly" in low or "stomach" in low) and any(
        term in low for term in ("pain", "stool", "gas", "distension", "swollen", "vomit", "unable to eat")
    ):
        if "stool" in low or "gas" in low or "distension" in low or "swollen" in low:
            return "మలం లేదా వాయువు వెళ్లకుండా కడుపు ఉబ్బితే వెంటనే వైద్య సహాయం పొందండి"
        if "vomit" in low or "unable to eat" in low or "not able to take orally" in low:
            return "మళ్లీ మళ్లీ వాంతులు వచ్చినా లేదా తినడం, తాగడం సాధ్యం కాకపోయినా వైద్య సహాయం పొందండి"
        return "తీవ్రమైన లేదా పెరుగుతున్న కడుపు నొప్పి ఉంటే వెంటనే ఆసుపత్రికి వెళ్లండి"
    if re.search(r"\b150/100\b", low):
        return "ఇంట్లో కొలిచిన రక్తపోటు 150/100 mmHg లేదా అంతకంటే ఎక్కువగా ఉంటే వైద్య బృందానికి తెలియజేయండి"
    if "weight" in low and ("2 kg" in low or "gain" in low or "increase" in low or "rising" in low):
        return "3 రోజుల్లో బరువు 2 kg కంటే ఎక్కువ పెరిగితే వైద్య బృందానికి తెలియజేయండి"
    if "rash" in low or "itch" in low:
        return "శరీరమంతా దద్దుర్లు లేదా దురద వస్తే వైద్య సహాయం పొందండి"
    if "blur" in low or "colour" in low or "color" in low:
        return "చూపు మసకబారినా లేదా రంగులను గుర్తించడం కష్టమైనా వైద్య బృందానికి తెలియజేయండి"
    if "emergency" in low:
        return "అత్యవసర పరిస్థితిలో వెంటనే సమీప ఆసుపత్రిని సంప్రదించండి"
    if "walking" in low or "walk " in low or "walker's" in low or "walker" in low or "ambulation" in low:
        if "walker" in low:
            if "non-weight" in low or "nwb" in low:
                return "ఆపరేషన్ చేసిన కాలిపై బరువు వేయకండి; వాకర్ లేదా క్రచెస్‌తో నడవండి"
            if "toe-touch" in low or "partial" in low or "pwb" in low:
                return "వాకర్ లేదా క్రచెస్‌తో నడవండి; ఆపరేషన్ చేసిన కాలి వేళ్లను మాత్రమే నేలపై తాకించండి"
            return "వాకర్ సహాయంతో, మీకు సాధ్యమైనంత బరువు వేస్తూ నడవండి"
        if "10-15" in low:
            return "మొదటి 2 వారాలు సమతల ప్రదేశంలో రోజుకు 2 సార్లు 10-15 నిమిషాలు నడవండి; మెట్లు ఎక్కువగా ఎక్కకండి"
        if "home" in low or "house" in low:
            return "రోజుకు పలుమార్లు ఇంట్లో నడవండి"
        return "మీకు సాధ్యమైనంతగా నడకను క్రమంగా పెంచండి"
    if "lift" in low or "heavy" in low:
        limit = re.search(r"(?:more than|over|>)\s*(\d+\s*kg)", text, re.I)
        weeks = re.search(r"(\d+)\s*weeks?", low)
        amount = limit.group(1) if limit else "5 kg"
        return f"{weeks.group(1) + ' వారాల పాటు ' if weeks else ''}{amount} కంటే ఎక్కువ బరువు ఎత్తకండి"
    if "smok" in low or "tobacco" in low:
        return "ధూమపానం చేయకండి; ఏ రూపంలోనూ పొగాకు వాడకండి"
    if "inhaler" in low and "spacer" in low:
        return "మీకు చూపిన విధంగా స్పేసర్‌తో ఇన్హేలర్ వాడండి"
    if "dust" in low or "smoke" in low or "cold air" in low or "trigger" in low or "chulha" in low:
        return "దుమ్ము, పొగ మరియు చల్లని గాలికి దూరంగా ఉండండి"
    if "breathing" in low and ("exercise" in low or "pursed" in low or "deep breath" in low):
        return "చెప్పిన విధంగా శ్వాస వ్యాయామాలు చేయండి"
    if "pillow" in low or "cough" in low and "sneez" in low:
        return "దగ్గినప్పుడు లేదా తుమ్మినప్పుడు గాయంపై దిండును ఆనించి, ఒత్తిడి పెట్టకండి"
    if "dressing" in low:
        return "కట్టును తీసేయకండి; వైద్య పరీక్ష సమయంలో దాన్ని మారుస్తారు"
    if "wound" in low or "incision" in low:
        if "clean" in low or "dry" in low:
            return "గాయాన్ని శుభ్రంగా, పొడిగా ఉంచండి"
        if "oil" in low or "turmeric" in low or "haldi" in low or "powder" in low:
            return "గాయంపై నూనె, పసుపు లేదా పొడి వేయకండి"
    if "oil" in low or "turmeric" in low or "haldi" in low or "powder" in low:
        return "గాయంపై నూనె, పసుపు లేదా పొడి వేయకండి"
    if "breastfeeding" in low or "breast feed" in low or "breast milk" in low:
        return "శిశువుకు 6 నెలల పాటు తల్లిపాలు మాత్రమే ఇవ్వండి"
    if ("infant" in low or "baby" in low) and ("immunis" in low or "vaccin" in low) and ("6 week" in low or "6-week" in low):
        return "శిశువుకు 6 వారాల టీకాలు వేయించండి"
    if "immunis" in low or "vaccin" in low:
        return "ఇచ్చిన టీకాల కార్డు ప్రకారం శిశువుకు టీకాలు వేయించండి"
    if "family planning" in low or "contraception" in low or "spacing" in low or "birth spacing" in low:
        return "ప్రసవానంతర సందర్శనలో కుటుంబ నియంత్రణ గురించి చర్చించండి; కనీసం 2 సంవత్సరాల విరామం పాటించండి"
    if "tb screening" in low or "screened for tb" in low:
        return "మీతో నివసించే కుటుంబ సభ్యులు సమీప ఆరోగ్య కేంద్రంలో TB పరీక్ష చేయించుకోవాలి"
    if "rifampicin" in low and ("contraceptive" in low or "condom" in low or "iucd" in low):
        return "Rifampicin గర్భనిరోధక మాత్రల ప్రభావాన్ని తగ్గించవచ్చు; కండోమ్ లేదా IUCD గురించి వైద్యుడిని అడగండి"
    if "insulin" in low and ("sick" in low or "ill" in low):
        return "అనారోగ్యంగా ఉన్నప్పుడు కూడా ఇన్సులిన్ ఆపకండి; ప్రతి 4 గంటలకు చక్కెర పరీక్షించి ఆసుపత్రిని సంప్రదించండి"
    if "insulin" in low and ("fridge" in low or "refrigerator" in low):
        return "ఇన్సులిన్ ఇంజెక్షన్ స్థలాలను మార్చండి; ఇన్సులిన్‌ను ఫ్రీజర్‌లో కాకుండా ఫ్రిజ్‌లో ఉంచండి"
    if ("sugar or glucose" in low or "glucose / sugar" in low) and ("carry" in low or "keep" in low):
        return "చక్కెర లేదా గ్లూకోజ్‌ను ఎల్లప్పుడూ మీ వెంట ఉంచుకోండి"
    if "blood sugar" in low and ("4 times" in low or "four times" in low or "qid" in low):
        return "భోజనానికి ముందు మరియు నిద్రకు ముందు సహా రోజుకు 4 సార్లు రక్త చక్కెర పరీక్షించి, ఫలితాలను రాయండి"
    if "blood pressure" in low and ("chart" in low or "monitor" in low):
        return "ఇంట్లో రోజుకు 2 సార్లు రక్తపోటు కొలిచి, పట్టికలో రాయండి"
    if "tb medicines" in low or "tb medicine" in low or "att" in low:
        return "TB మందులను ప్రతిరోజూ మోతాదు మానకుండా తీసుకుని, సూచించిన పూర్తి కోర్సు ముగించండి"
    if "fluids" in low or "litre" in low or "litres" in low:
        quantity = re.search(
            r"(\d+(?:\.\d+)?\s*(?:to|-)\s*\d+(?:\.\d+)?\s*litres?|\d+(?:\.\d+)?\s*litres?)",
            text, re.I,
        )
        if quantity and ("max" in low or "limit" in low or "no more" in low):
            amount = re.sub(r"\s+to\s+", "–", quantity.group(1), flags=re.I)
            return f"రోజుకు మొత్తం ద్రవాలు {amount} మించకుండా తీసుకోండి"
        if quantity:
            amount = re.sub(r"\s+to\s+", "–", quantity.group(1), flags=re.I)
            return f"రోజుకు {amount} ద్రవాలు తాగండి"
        return "తగినంత ద్రవాలు తాగండి"
    if "protein" in low:
        return "పప్పు, గుడ్లు, పాలు మరియు పనీర్ వంటి ప్రోటీన్ ఉన్న ఆహారం తీసుకోండి"
    if "fibre" in low or "fiber" in low:
        return "మలబద్ధకం రాకుండా పీచు పదార్థాలు ఉన్న ఆహారం తిని, తగినంత ద్రవాలు తాగండి"
    if "extra meal" in low or "nourishing" in low or "nutritious" in low:
        return "పోషకమైన ఆహారం తినండి; రోజుకు ఒక అదనపు భోజనం తీసుకుని, ఆకుకూరలు, పాలు, పప్పు చేర్చండి"
    if "heart" in low and "diet" in low:
        return "గుండె ఆరోగ్యం కోసం కూరగాయలు, పండ్లు, తృణధాన్యాలు తీసుకోండి; వేయించిన ఆహారం, అధిక నూనె తగ్గించండి"
    if "low-fat" in low or "low fat" in low:
        return "తక్కువ కొవ్వు ఉన్న ఆహారం తీసుకోండి; వేయించిన పదార్థాలు, నూనె, నెయ్యి, ఎర్ర మాంసం తగ్గించండి"
    if "salt" in low and ("5 g" in low or "pickles" in low or "papad" in low):
        return "రోజుకు ఉప్పు 5 g కంటే తక్కువగా తీసుకోండి; ఊరగాయలు, పాపడ్, నమ్కీన్ తగ్గించండి"
    if "small" in low and "meal" in low:
        return "చిన్న పరిమాణంలో తరచుగా తినండి; భోజనం మానేయకండి"
    if "normal diet" in low:
        return "మీకు తట్టుకునే సాధారణ ఆహారం తీసుకోండి"
    if ("diet" in low or "soft foods" in low) and ("soft" in low or "digestible" in low):
        return "1 వారం మెత్తగా, సులభంగా జీర్ణమయ్యే ఆహారం తీసుకోండి; తర్వాత సాధారణ ఆహారం తీసుకోండి"
    if "rest" in low or "household work" in low:
        return "తగినంత విశ్రాంతి తీసుకోండి; 6 వారాల పాటు తేలికపాటి ఇంటి పనులు మాత్రమే చేయండి"
    if "driv" in low:
        weeks = re.search(r"(\d+)\s*weeks?", low)
        return f"{weeks.group(1) + ' వారాల పాటు ' if weeks else ''}వాహనం నడపకండి"
    if "kegel" in low or "pelvic floor" in low:
        return "పెల్విక్ ఫ్లోర్ (Kegel) వ్యాయామాలు రోజూ చేయండి"
    if "sitz bath" in low:
        return "1 వారం పాటు రోజుకు 2 సార్లు గోరువెచ్చని నీటితో సిట్జ్ బాత్ చేయండి"
    if "perineal" in low or ("pad" in low and "toilet" in low):
        return "మరుగుదొడ్డి ఉపయోగించిన తర్వాత శుభ్రమైన నీటితో కడగండి; ప్రతి 4-6 గంటలకు ప్యాడ్ మార్చండి"
    if ("weight" in low or "weigh" in low) and ("morning" in low or "record" in low):
        return "ప్రతి ఉదయం బరువు చూసి నమోదు చేయండి"
    if "feet or legs" in low or "pedal oedema" in low:
        return "పాదాలు లేదా కాళ్ల వాపు పెరిగితే వైద్య సహాయం పొందండి"
    if "alcohol" in low:
        return "మద్యం తాగకండి"
    if "painkiller" in low or "pain medicine" in low or "chemist" in low or "nsaid" in low:
        return "వైద్యుడిని అడగకుండా నొప్పి మందులు కొనకండి లేదా వాడకండి"
    if "anticoagulant" in low or "blood thinner" in low or "blood-thinner" in low or "antiplatelet" in low:
        return "రక్తం గడ్డకట్టకుండా చేసే మందు మోతాదును స్వయంగా మార్చకండి లేదా ఆపకండి; కొత్త మందు మొదలుపెట్టే ముందు కార్డియాలజిస్ట్‌ను అడగండి"
    if "steroid" in low and "sugar" in low:
        return "స్టెరాయిడ్ మందుల కోర్సు సమయంలో రోజూ రక్త చక్కెర పరీక్షించండి"
    if "foot" in low or "barefoot" in low:
        return "పాదాలను రోజూ పరిశీలించండి, పాదరక్షలు ధరించండి; చెప్పులు లేకుండా నడవకండి"
    if "numbness" in low or "tingling" in low or "bluish" in low or "blue colour" in low:
        if "hand" in low or "finger" in low:
            return "ఆపరేషన్ చేసిన చేతిలో మొద్దుబారడం, సూదులు గుచ్చినట్లు అనిపించడం, నీలం రంగు లేదా తీవ్రమైన వాపు ఉంటే వైద్య సహాయం పొందండి"
        return "ఆపరేషన్ చేసిన కాలులో మొద్దుబారడం, సూదులు గుచ్చినట్లు అనిపించడం, నీలం రంగు లేదా తీవ్రమైన వాపు ఉంటే వైద్య సహాయం పొందండి"
    if "fall" in low or "rug" in low or "loose mat" in low:
        return "పడిపోకుండా రాత్రి దీపం ఉంచండి, జారే చాపలు తొలగించండి, నడిచేటప్పుడు ఆధారం వాడండి"
    if "sputum" in low or "phlegm" in low:
        if "yellow" in low or "green" in low or "increase" in low or "quantity" in low:
            return "కఫం ఎక్కువైనా లేదా పసుపు/ఆకుపచ్చ రంగులోకి మారినా వైద్యుడికి తెలియజేయండి"
        if "intensive phase" in low or "end of ip" in low:
            return "చెప్పిన చికిత్స దశ ముగిసినప్పుడు కఫం పరీక్ష చేయించండి"
        return "చెప్పిన సమయంలో కఫం పరీక్ష చేయించండి"
    if "vomit" in low or "vomiting" in low:
        return "మళ్లీ మళ్లీ వాంతులు వచ్చినా లేదా తినడం, తాగడం సాధ్యం కాకపోయినా వైద్య సహాయం పొందండి"
    if "normal diet" in low and ("soft" in low or "digestible" in low):
        return "1 వారం మెత్తగా, సులభంగా జీర్ణమయ్యే ఆహారం తీసుకోండి; ఆ తర్వాత సాధారణ ఆహారం తీసుకోండి"
    if "healthy" in low and "weight" in low:
        return "చక్కెర మరియు స్వీట్లు తగ్గించి, ఆరోగ్యకరమైన బరువును 유지ించండి"
    if "orange-red urine" in low and "rifampicin" in low:
        return "Rifampicin వల్ల మూత్రం నారింజ-ఎరుపు రంగులోకి మారవచ్చు; ఇది సాధారణంగా కనిపించే మార్పు"
    if "ocp" in low and ("rifampicin" in low or "iucd" in low):
        return "Rifampicin గర్భనిరోధక మాత్రల ప్రభావాన్ని తగ్గిస్తుంది; కండోమ్ లేదా IUCD గురించి వైద్యుడిని అడగండి"
    if "work" in low and ("resume" in low or "office" in low or "desk" in low):
        weeks = re.search(r"(\d+)\s*weeks?", low)
        return f"{weeks.group(1) + ' వారాల తర్వాత ' if weeks else ''}ఆఫీసు లేదా డెస్క్ పని తిరిగి ప్రారంభించవచ్చు"
    if "blood thinner" in low and "new medicine" in low:
        return "రక్తం పలుచబరిచే మందు మోతాదును స్వయంగా మార్చకండి; కొత్త మందు మొదలుపెట్టే ముందు వైద్యుడికి చెప్పండి"
    if "eye" in low and "urine" in low and "yellow" in low:
        return "కళ్లు లేదా మూత్రం పసుపు రంగులోకి మారినా, వాంతులు లేదా ఆకలి లేకపోవడం ఉన్నా TB మందులు ఆపి ఆసుపత్రికి వెళ్లండి"
    if "urine" in low and "orange" in low:
        return "TB మందుల వల్ల మూత్రం నారింజ-ఎరుపు రంగులోకి మారవచ్చు"
    if "exercise" in low or "quadriceps" in low or ("knee" in low and "bend" in low):
        return "ఫిజియోథెరపిస్ట్ చూపిన వ్యాయామాలను రోజుకు 3-4 సార్లు చేయండి"
    if "ice" in low or "cold pack" in low or "cryotherapy" in low:
        return "మోకాలిపై 15-20 నిమిషాలు, రోజుకు 3-4 సార్లు ఐస్ ప్యాక్ పెట్టండి"
    if "thigh-strengthening" in low or ("physiotherapist" in low and "movements" in low):
        return "ఫిజియోథెరపిస్ట్ చూపిన మోకాలి మరియు తొడ బలపరిచే వ్యాయామాలను రోజుకు 3-4 సార్లు చేయండి"
    if "raise your hand" in low and "heart" in low or "hand elevated" in low:
        return "విశ్రాంతి సమయంలో చేతిని గుండె కంటే ఎత్తులో ఉంచి, వేళ్లను తరచుగా కదపండి"
    if "prednisolone" in low and "sugar" in low:
        return "Prednisolone మందుల కోర్సు సమయంలో రోజూ రక్త చక్కెర పరీక్షించండి"
    if "elevat" in low or "above heart" in low:
        if "hand" in low or "finger" in low:
            return "విశ్రాంతి సమయంలో చేతిని గుండె కంటే ఎత్తులో ఉంచి, వేళ్లను తరచుగా కదపండి"
        return "విశ్రాంతి సమయంలో కాలును దిండ్లపై ఎత్తుగా ఉంచి, కాలి వేళ్లను కదపండి"
    if "cast" in low or "slab" in low or "plaster" in low:
        return "ప్లాస్టర్‌ను పొడిగా ఉంచండి; దాని కింద ఏ వస్తువూ పెట్టకండి"
    if "ogtt" in low or "glucose tolerance" in low:
        return "చెప్పిన తేదీన 75 g OGTT (గ్లూకోజ్ సహన పరీక్ష) చేయించుకోండి"
    if "suture" in low or "staple" in low:
        return "గాయం కుట్లు లేదా స్టేపుల్స్ తొలగించి, గాయాన్ని పరీక్షించించుకోండి"
    if "postnatal" in low or re.search(r"\bpnc\b", low):
        return "ప్రసవం తర్వాత 6 వారాలకు వైద్య తనిఖీకి రండి"
    if "sputum" in low and ("test" in low or "examination" in low):
        return "చెప్పిన దశ ముగిసినప్పుడు కఫం పరీక్ష చేయించండి"
    if "lipid profile" in low:
        return "ఉపవాసంతో లిపిడ్ ప్రొఫైల్ రక్త పరీక్ష చేయించండి"
    if "spirometry" in low or "pft" in low:
        return "ఊపిరితిత్తుల పనితీరు పరీక్ష (స్పైరోమెట్రీ) చేయించండి"
    if "2d echo" in low or "echo" in low:
        return "2D ఎకో పరీక్ష చేయించుకుని వైద్య తనిఖీకి రండి"
    if "hba1c" in low:
        return "HbA1c పరీక్ష చేయించుకుని వైద్య తనిఖీకి రండి"
    if "x-ray" in low or "xray" in low:
        return "X-ray తీసుకుని వైద్య తనిఖీకి రండి"
    if "wound check" in low or "wound inspection" in low:
        return "గాయం పరీక్ష చేయించుకోండి"
    if "review" in low or "check-up" in low or "check x-ray" in low or "report" in low or "bring" in low:
        tests = re.search(
            r"(?:bring reports?:|bring your reports for a check-up:|take these results with you:)\s*(.+)$",
            text, re.I,
        )
        if tests:
            test_names = tests.group(1)
            for pattern, replacement in (
                (r"\bweight chart\b", "బరువు పట్టిక"),
                (r"\burine routine\b", "మూత్ర పరీక్ష"),
                (r"\belectrolytes\b", "ఎలక్ట్రోలైట్స్"),
            ):
                test_names = re.sub(pattern, replacement, test_names, flags=re.I)
            return f"తదుపరి వైద్య తనిఖీకి పరీక్షా నివేదికలు వెంట తీసుకురండి: {test_names}"
        detail = re.sub(r"^(?:review|check-up|follow-up)\b\s*[:,-]?\s*", "", text, flags=re.I).strip()
        return f"తదుపరి వైద్య తనిఖీకి హాజరుకండి: {detail}" if detail else "తదుపరి వైద్య తనిఖీకి హాజరుకండి"
    return text


def _telugu_department(value: str) -> str:
    low = value.casefold()
    mappings = (
        ("pulmonary", "ఊపిరితిత్తుల వైద్య క్లినిక్"),
        ("diabetes", "మధుమేహ వైద్య క్లినిక్"), ("dm clinic", "మధుమేహ వైద్య క్లినిక్"),
        ("cardiology", "గుండె వైద్య క్లినిక్"), ("medicine", "మెడిసిన్ క్లినిక్"),
        ("obg", "స్త్రీ మరియు ప్రసూతి వైద్య క్లినిక్"), ("gynaecology", "స్త్రీ వైద్య క్లినిక్"),
        ("paediatric", "పిల్లల వైద్య క్లినిక్"), ("immunisation", "టీకాల క్లినిక్"),
        ("orthopaedics", "ఎముకల వైద్య క్లినిక్"), ("ortho", "ఎముకల వైద్య క్లినిక్"),
        ("central lab", "కేంద్ర ప్రయోగశాల"), ("laboratory", "ఆసుపత్రి ప్రయోగశాల"),
        (" lab", "ప్రయోగశాల"),
        ("surgery", "శస్త్రచికిత్స క్లినిక్"), ("chest", "ఛాతీ/శ్వాసకోశ క్లినిక్"),
        ("tb", "TB క్లినిక్"), ("dots", "DOTS కేంద్రం"),
        ("physiotherapy", "ఫిజియోథెరపీ విభాగం"),
    )
    for token, translated in mappings:
        if token in low:
            return translated
    return "వైద్య క్లినిక్" if value else ""


def _telugu_followup_detail(visit: dict, glossary: list[dict[str, str]] | None) -> str:
    department = _telugu_department((visit.get("with_whom") or "").strip())
    purpose = (visit.get("purpose") or "").strip()
    if purpose:
        purpose_te = _telugu_clause(purpose, glossary)
    else:
        purpose_te = "తదుపరి వైద్య తనిఖీ"
    return " ".join(part for part in (department, purpose_te) if part)


def _render_telugu(cp: dict, glossary: list[dict[str, str]] | None = None) -> str:
    lines = [
        "నా సంరక్షణ ప్రణాళిక",
        "క్రింది మందులు మరియు సూచనలను మీ ఆసుపత్రి బృందం చెప్పిన విధంగా పాటించండి.",
    ]
    if cp["medicines"]:
        lines.extend(["", "మందులు"])
        for med in cp["medicines"]:
            parts = [
                _dose_te(med.get("dose")),
                _frequency_te(med.get("frequency")),
                "" if med.get("timing") and med["timing"].casefold() in
                (med.get("dose") or "").casefold() else _timing_te(med.get("timing")),
                _duration_te(med.get("duration")),
            ]
            instruction = ", ".join(part for part in parts if part)
            route = _route_te(med.get("how_to_take_plain"))
            if instruction and route:
                lines.append(f"- {med['name']}: {instruction}; {route}.")
            elif instruction:
                lines.append(f"- {med['name']}: {instruction} వాడండి.")
            elif route:
                lines.append(f"- {med['name']}: {route}.")
            else:
                lines.append(f"- {med['name']}: సారాంశంలో చెప్పిన విధంగా వాడండి.")
    if cp["continue_previous_medications"]:
        lines.extend(["", "గతంలో వాడుతున్న మందులను మునుపటిలాగే కొనసాగించండి."])
    if cp["stop_medicines"]:
        lines.extend(["", "ఆపాల్సిన మందులు"])
        lines.extend(f"- {_dose_te(name)} — ఈ మందును ఆపండి." for name in cp["stop_medicines"])
    for title, key in (
        ("వెంటనే వైద్య సహాయం పొందండి, ఒకవేళ", "warning_signs"),
        ("ఆహారం", "diet"),
        ("నడక మరియు రోజువారీ పనులు", "activity"),
        ("గాయం సంరక్షణ", "wound_care"),
        ("ఇతర సూచనలు", "other_advice"),
    ):
        if cp.get(key):
            lines.extend(["", title])
            lines.extend(f"- {_telugu_clause(item, glossary)}." for item in cp[key])
    if cp["follow_ups"]:
        lines.extend(["", "తదుపరి వైద్య తనిఖీలు"])
        for visit in cp["follow_ups"]:
            lines.append(f"- {visit['date']}: {_telugu_followup_detail(visit, glossary)}.")
    if cp["continue_previous_text"]:
        condition = cp["continue_previous_text"].casefold()
        if "hypothyroidism" in condition:
            advice = "థైరాయిడ్ మందులను మునుపటిలాగే కొనసాగించండి"
        elif "t2dm" in condition or "diabetes" in condition:
            advice = "మధుమేహం కోసం గతంలో వాడుతున్న మందులను మునుపటిలాగే కొనసాగించండి"
        elif "htn" in condition or "hypertension" in condition:
            advice = "రక్తపోటు కోసం గతంలో వాడుతున్న మందులను మునుపటిలాగే కొనసాగించండి"
        elif "bph" in condition:
            advice = "ప్రోస్టేట్ సమస్య కోసం గతంలో వాడుతున్న మందులను మునుపటిలాగే కొనసాగించండి"
        elif "glaucoma" in condition:
            advice = "గ్లాకోమా కోసం గతంలో వాడుతున్న మందులను మునుపటిలాగే కొనసాగించండి"
        elif "dyslipidaemia" in condition:
            advice = "కొలెస్ట్రాల్ కోసం గతంలో వాడుతున్న మందులను మునుపటిలాగే కొనసాగించండి"
        else:
            advice = "గతంలో వాడుతున్న మందులను మునుపటిలాగే కొనసాగించండి"
        lines.extend(["", advice + "."])
    return "\n".join(lines)


def _syllables(word: str) -> int:
    value = re.sub(r"[^a-z]", "", word.casefold())
    if not value:
        return 0
    groups = re.findall(r"[aeiouy]+", value)
    count = len(groups)
    if value.endswith("e") and count > 1 and not value.endswith(("le", "ye")):
        count -= 1
    return max(1, count)


def readability_grade(text: str) -> float | None:
    words = re.findall(r"\b[A-Za-z]+\b", text)
    sentences = max(1, len(re.findall(r"[.!?]+", text)))
    if not words:
        return None
    syllables = sum(_syllables(word) for word in words)
    return round(0.39 * (len(words) / sentences) + 11.8 * (syllables / len(words)) - 15.59, 2)


def _render_english(cp: dict) -> str:
    lines = ["MY CARE PLAN", "", "MEDICINES"]
    for med in cp["medicines"]:
        instruction = med["how_to_take_plain"] or _dose_instructions(med)
        line = f"- {med['name']}"
        if instruction:
            line += f": {instruction}."
        lines.append(line)
    if cp["continue_previous_medications"]:
        lines.extend(["", "Continue your previous medicines in the same way as before."])
    if cp["stop_medicines"]:
        lines.extend(["", "MEDICINES TO STOP"])
        lines.extend(f"- Stop taking {name}." for name in cp["stop_medicines"])
    for title, key in (("GET MEDICAL HELP NOW IF", "warning_signs"), ("FOOD", "diet"),
                       ("ACTIVITY", "activity"), ("WOUND CARE", "wound_care"),
                       ("OTHER INSTRUCTIONS", "other_advice")):
        if cp[key]:
            lines.extend(["", title])
            lines.extend(f"- {_plain_clause(item)}." for item in cp[key])
    if cp["follow_ups"]:
        lines.extend(["", "FOLLOW-UP VISITS"])
        for visit in cp["follow_ups"]:
            detail = " ".join(part for part in (visit["with_whom"], visit["purpose"]) if part)
            lines.append(f"- {visit['date']}" + (f": {_plain_clause(detail)}." if detail else "."))
    if cp["continue_previous_text"]:
        lines.extend(["", _plain_clause(cp["continue_previous_text"]) + "."])
    return "\n".join(lines)


def extract(text: str, drug_entries: list[dict]) -> dict:
    lines = text.splitlines()
    meds, stops = _parse_medicines(_medication_lines(lines), drug_entries)

    stop_start = _heading_index(lines, STOP_HEADINGS)
    if stop_start is not None:
        for line in lines[stop_start + 1:]:
            if _is_break(line):
                break
            med_lines = _line_values(line)
            for med_line in med_lines:
                matches = _find_matches(med_line, drug_entries)
                if matches:
                    _, _, entry, _ = matches[0]
                    name = _display_name(entry, med_line)
                    dose = _strength_dose(med_line, entry)
                    stops.append(f"{name} {dose}".strip() if dose else name)

    advice = _advice_sections(lines)
    stop_seen = set()
    unique_stops = []
    for item in stops:
        key = norm_words(item)
        if key and key not in stop_seen:
            stop_seen.add(key)
            unique_stops.append(item)
    previous = _continue_previous(text)

    cp = {
        "medicines": meds,
        "stop_medicines": unique_stops,
        "warning_signs": advice["warning_signs"],
        "diet": advice["diet"],
        "activity": advice["activity"],
        "wound_care": advice["wound_care"],
        "other_advice": advice["other_advice"],
        "follow_ups": _followups(lines),
        "continue_previous_medications": previous is not None,
        "continue_previous_text": previous,
    }
    for med in cp["medicines"]:
        med["how_to_take_plain"] = _dose_instructions(med) or None
        med.pop("_source", None)
        med.pop("_route", None)
        med.pop("_generic", None)
    return cp


def predict(
    summary_id: str,
    text: str,
    drug_entries: list[dict],
    glossary: list[dict[str, str]] | None = None,
) -> dict:
    cp = extract(text, drug_entries)
    english = _render_english(cp)
    telugu = _render_telugu(cp, glossary)
    return {
        "summary_id": summary_id,
        "care_plan": cp,
        "plain_text_en": english,
        "translation": {"language_code": "te", "text": telugu},
        "readability": {"metric": "flesch_kincaid_grade", "score": readability_grade(english)},
    }


def _load_ids(data: Path, split: str) -> set[str]:
    if split == "all":
        rows = read_csv(data / "splits.csv")
    else:
        rows = read_csv(data / f"{split}_ids.csv")
    return {row["summary_id"] for row in rows}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Generate source-faithful H2 care plans.")
    parser.add_argument("--split", choices=("dev", "test", "all"), default="test")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--data_dir", type=Path, default=HERE / "data")
    args = parser.parse_args(argv)
    data = args.data_dir
    ids = _load_ids(data, args.split)
    drug_entries, _ = build_drug_index(read_csv(data / "drug_reference.csv"))
    glossary = read_csv(data / "abbreviation_glossary.csv")
    output = args.out or HERE / "submission" / "H2_predictions.jsonl"
    output.parent.mkdir(parents=True, exist_ok=True)
    written = set()
    count = 0
    with (data / "summaries.jsonl").open(encoding="utf-8") as source, output.open("w", encoding="utf-8", newline="\n") as target:
        for line_number, line in enumerate(source, 1):
            record = json.loads(line)
            summary_id = record["summary_id"]
            if summary_id not in ids:
                continue
            if summary_id in written:
                raise ValueError(f"duplicate summary_id in summaries.jsonl: {summary_id}")
            prediction = predict(summary_id, record["text"], drug_entries, glossary)
            target.write(json.dumps(prediction, ensure_ascii=False) + "\n")
            written.add(summary_id)
            count += 1
    missing = ids - written
    if missing:
        raise ValueError(f"{len(missing)} requested summaries were not found; first missing id: {sorted(missing)[0]}")
    print(f"Wrote {count} predictions to {output}")


if __name__ == "__main__":
    main()
