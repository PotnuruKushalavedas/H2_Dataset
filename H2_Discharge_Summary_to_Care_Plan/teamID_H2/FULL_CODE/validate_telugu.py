#!/usr/bin/env python3
"""Input-only structural and Telugu-script checks for H2 predictions.

This validator reads split IDs and source summaries only. It does not read
answer keys, gold examples, or the official evaluator.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
from datetime import date
from pathlib import Path


HERE = Path(__file__).resolve().parent
REQUIRED_LISTS = (
    "medicines", "stop_medicines", "warning_signs", "diet", "activity", "follow_ups",
)
MEDICINE_KEYS = ("name", "dose", "frequency", "timing", "duration", "how_to_take_plain")
FOLLOWUP_KEYS = ("date", "with_whom", "purpose")
SECTION_TITLES = {
    "medicines": "మందులు",
    "stop_medicines": "ఆపాల్సిన మందులు",
    "warning_signs": "వెంటనే వైద్య సహాయం పొందండి, ఒకవేళ",
    "diet": "ఆహారం",
    "activity": "నడక మరియు రోజువారీ పనులు",
    "wound_care": "గాయం సంరక్షణ",
    "other_advice": "ఇతర సూచనలు",
    "follow_ups": "తదుపరి వైద్య తనిఖీలు",
}


def _expected_ids(data_dir: Path, split: str) -> set[str]:
    path = data_dir / ("splits.csv" if split == "all" else f"{split}_ids.csv")
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = csv.DictReader(stream)
        if "summary_id" not in (rows.fieldnames or []):
            raise ValueError(f"{path} has no summary_id column")
        return {row["summary_id"] for row in rows}


def _source_texts(data_dir: Path, expected: set[str]) -> dict[str, str]:
    found = {}
    with (data_dir / "summaries.jsonl").open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            record = json.loads(line)
            summary_id = record["summary_id"]
            if summary_id in expected:
                if summary_id in found:
                    raise ValueError(f"duplicate source ID {summary_id} on line {line_number}")
                found[summary_id] = record["text"]
    missing = expected - found.keys()
    if missing:
        raise ValueError(f"{len(missing)} expected source summaries are missing")
    return found


def _letter_share(text: str) -> float:
    letters = [char for char in text if char.isalpha()]
    if not letters:
        return 0.0
    telugu = sum(0x0C00 <= ord(char) <= 0x0C7F for char in letters)
    return telugu / len(letters)


def _section_bullet_count(text: str, title: str) -> int:
    lines = text.splitlines()
    try:
        start = lines.index(title) + 1
    except ValueError:
        return 0
    count = 0
    for line in lines[start:]:
        if not line.startswith("- "):
            break
        count += 1
    return count


def _schema_errors(record: dict, line_number: int) -> list[str]:
    where = f"line {line_number}"
    errors = []
    for key in ("summary_id", "care_plan", "plain_text_en", "translation", "readability"):
        if key not in record:
            errors.append(f"{where}: missing {key}")
    if errors:
        return errors
    cp = record["care_plan"]
    if not isinstance(cp, dict):
        return [f"{where}: care_plan is not an object"]
    for key in REQUIRED_LISTS:
        if not isinstance(cp.get(key), list):
            errors.append(f"{where}: care_plan.{key} must be a list")
    for key in ("wound_care", "other_advice"):
        if key in cp and not isinstance(cp[key], list):
            errors.append(f"{where}: care_plan.{key} must be a list when present")
    if errors:
        return errors
    for key in ("stop_medicines", "warning_signs", "diet", "activity", "wound_care", "other_advice"):
        if any(not isinstance(item, str) for item in cp.get(key, [])):
            errors.append(f"{where}: care_plan.{key} entries must be strings")
    for index, medicine in enumerate(cp["medicines"]):
        if not isinstance(medicine, dict):
            errors.append(f"{where}: medicine {index} is not an object")
            continue
        for key in MEDICINE_KEYS:
            if key not in medicine or (medicine[key] is not None and not isinstance(medicine[key], str)):
                errors.append(f"{where}: medicine {index} has invalid/missing {key}")
    for index, visit in enumerate(cp["follow_ups"]):
        if not isinstance(visit, dict):
            errors.append(f"{where}: follow-up {index} is not an object")
            continue
        for key in FOLLOWUP_KEYS:
            if key not in visit or (key == "date" and not isinstance(visit[key], str)) \
                    or (key != "date" and visit[key] is not None and not isinstance(visit[key], str)):
                errors.append(f"{where}: follow-up {index} has invalid/missing {key}")
        if isinstance(visit.get("date"), str):
            try:
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", visit["date"]):
                    raise ValueError
                date.fromisoformat(visit["date"])
            except ValueError:
                errors.append(f"{where}: follow-up {index} date must be a real YYYY-MM-DD date")
    translation = record["translation"]
    if not isinstance(translation, dict) or translation.get("language_code") != "te" \
            or not isinstance(translation.get("text"), str):
        errors.append(f"{where}: translation must contain language_code 'te' and text")
    readability = record["readability"]
    if not isinstance(readability, dict) or not isinstance(readability.get("metric"), str):
        errors.append(f"{where}: invalid readability object")
    elif readability.get("score") is not None and (
        isinstance(readability["score"], bool)
        or not isinstance(readability["score"], (int, float))
        or not math.isfinite(float(readability["score"]))
    ):
        errors.append(f"{where}: readability.score must be finite numeric or null")
    if not isinstance(record["plain_text_en"], str):
        errors.append(f"{where}: plain_text_en must be a string")
    return errors


def validate(submission: Path, data_dir: Path, split: str) -> int:
    expected = _expected_ids(data_dir, split)
    sources = _source_texts(data_dir, expected)
    records = {}
    errors = []
    try:
        with submission.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    errors.append(f"line {line_number}: blank JSONL line")
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    errors.append(f"line {line_number}: invalid JSON ({exc})")
                    continue
                if not isinstance(record, dict):
                    errors.append(f"line {line_number}: record must be an object")
                    continue
                errors.extend(_schema_errors(record, line_number))
                summary_id = record.get("summary_id")
                if isinstance(summary_id, str):
                    if summary_id in records:
                        errors.append(f"duplicate prediction ID: {summary_id}")
                    records[summary_id] = record
    except UnicodeDecodeError as exc:
        errors.append(f"submission is not valid UTF-8: {exc}")

    missing = expected - records.keys()
    extra = records.keys() - expected
    if missing:
        errors.append(f"{len(missing)} expected IDs are missing: {sorted(missing)[:5]}")
    if extra:
        errors.append(f"{len(extra)} unexpected IDs are present: {sorted(extra)[:5]}")

    checked = 0
    minimum_script_share = 1.0
    for summary_id in expected & records.keys():
        record = records[summary_id]
        if _schema_errors(record, 0):
            continue
        checked += 1
        cp = record["care_plan"]
        english = record["plain_text_en"]
        telugu = record["translation"]["text"]
        minimum_script_share = min(minimum_script_share, _letter_share(telugu))
        if _letter_share(telugu) < 0.50:
            errors.append(f"{summary_id}: Telugu script is under 50% of letters")
        if any(0x0900 <= ord(char) <= 0x097F for char in telugu):
            errors.append(f"{summary_id}: translation contains Devanagari/Hindi-script characters")
        if english and not (0.4 <= len(telugu) / len(english) <= 3.0):
            errors.append(f"{summary_id}: translation length is outside official 0.4-3.0 ratio")

        for index, medicine in enumerate(cp["medicines"]):
            name = medicine.get("name") or ""
            if not name or name.casefold() not in english.casefold() or name.casefold() not in telugu.casefold():
                errors.append(f"{summary_id}: medicine {index} name missing from English/Telugu text")
                continue
            start = telugu.casefold().find(name.casefold())
            nearby = telugu[start:start + 200]
            for number in re.findall(r"\d+(?:\.\d+)?", medicine.get("dose") or ""):
                if not re.search(r"(?<!\d)" + re.escape(number) + r"(?!\d)", nearby):
                    errors.append(f"{summary_id}: medicine {index} dose number {number} missing near Telugu name")
        for visit in cp["follow_ups"]:
            for language, text in (("English", english), ("Telugu", telugu)):
                if visit["date"] not in text:
                    errors.append(f"{summary_id}: follow-up date missing from {language} text")

        expected_sections = {
            "medicines": len(cp["medicines"]),
            "stop_medicines": len(cp["stop_medicines"]),
            "warning_signs": len(cp["warning_signs"]),
            "diet": len(cp["diet"]),
            "activity": len(cp["activity"]),
            "wound_care": len(cp.get("wound_care", [])),
            "other_advice": len(cp.get("other_advice", [])),
            "follow_ups": len(cp["follow_ups"]),
        }
        for key, count in expected_sections.items():
            if count and _section_bullet_count(telugu, SECTION_TITLES[key]) != count:
                errors.append(f"{summary_id}: Telugu {key} bullet count does not match structured fields")
        for name in cp["stop_medicines"]:
            if name.casefold() not in telugu.casefold():
                errors.append(f"{summary_id}: stopped medicine name missing from Telugu text")
        if cp["stop_medicines"] and "ఆపండి" not in telugu:
            errors.append(f"{summary_id}: explicit Telugu stop wording is missing")

    print(f"Split: {split}; expected IDs: {len(expected)}; unique prediction IDs: {len(records)}")
    print(f"Structurally checked predictions: {checked}; minimum Telugu-script share: {minimum_script_share:.3f}")
    print("Source access: split IDs and summaries only; no answer keys, gold examples, or evaluator.")
    if errors:
        print(f"FAILED: {len(errors)} validation issue(s)")
        for error in errors[:40]:
            print(f"- {error}")
        if len(errors) > 40:
            print(f"- ... {len(errors) - 40} additional issue(s)")
        return 1
    print("PASS: IDs, JSONL/schema, Telugu script, and rendered medicine/follow-up/list consistency.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Input-only validator for Telugu H2 predictions.")
    parser.add_argument("--submission", required=True, type=Path)
    parser.add_argument("--split", choices=("dev", "test", "all"), default="test")
    parser.add_argument("--data_dir", type=Path, default=HERE / "data")
    args = parser.parse_args()
    raise SystemExit(validate(args.submission, args.data_dir, args.split))


if __name__ == "__main__":
    main()
