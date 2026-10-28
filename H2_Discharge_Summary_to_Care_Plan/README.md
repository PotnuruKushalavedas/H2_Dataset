# H2 — Discharge Summary to Patient-Understandable Care Plan

**Scenario.** Chandrapuri District Hospital is a fictional government district hospital. It sends patients home with a discharge summary. The summary is full of abbreviations ("T. Glycomet 500 BD PC x 1/12"), dosage tables and follow-up instructions. Many families cannot read it, and many do not read English. Missed doses and avoidable readmissions follow.

**Your task.** Convert a clinical discharge summary into a **faithful, plain-language care plan** that the patient and their family can follow. You must not add, drop or change any medical instruction. Produce:

- a structured care plan: medicines (what, how much, when, how long), warning signs that mean "return to hospital immediately", diet and activity advice, and follow-up appointments;
- the same plan in English **plus one Indian language** of your choice;
- a readability score;
- a one-page "fridge sheet" (judged in your demo, not by the scorer).

Faithfulness is scored above fluency. Stretch goals (optional): a spoken audio version and a 7-day medicine timetable.

> **In short**
>
> - **You get:** 2,000 synthetic discharge summaries, a drug reference, an abbreviation glossary and 50 reference care plans. Of the 2,000 summaries, 300 are `test` (answer keys hidden), 200 are `dev` (answer keys public) and 1,500 are `unlabelled`.
> - **You build:** a tool that turns each summary into a faithful, plain-language care plan, in English plus one Indian language.
> - **You submit:** one file, `submission/H2_predictions.jsonl`, inside your team zip `teamID_H2.zip`. It has one line for each of the 300 test summaries. You also include all your code and a `README.md` (see **What to submit**).
> - **How you're scored:** `evaluate.py` gives each test summary a score from 0 (worst) to 1 (best) and averages them. Faithfulness counts most: a dropped, invented or changed medicine, dose or follow-up date costs heavily.
> - **Main rule:** never add, drop or change a medical instruction. In the translation, keep medicine names, doses and numbers in Latin script with Western digits (see "How to fill the fields").

## Synthetic data notice

All 2,000 discharge summaries are **synthetic**. The patients, doctors, UHIDs (hospital ID numbers) and IP numbers (admission numbers) are invented. The hospital is fictional.

As an approved exception, **real generic drug names and real, common Indian brand names** are used so the task is realistic. The drug reference and glossary are **simplified for this hackathon; not a clinical reference.** Do not use them to treat anyone.

> All healthcare data is synthetic. No team may use real patient data. Every healthcare solution must be positioned as clinician-assisting, not clinician-replacing, and must include a safety/escalation path. Teams that present an autonomous diagnostic system without a human-in-the-loop will lose points on the Responsibility criterion.

**About the 50 reference examples.** The problem statement mentions "gold-standard human-written" versions. The 50 reference care plans here were **written and reviewed for this hackathon by AI agents**. The organisers approved this in place of clinicians. This is how they were made:

- An AI agent wrote them from hand-authored phrase tables.
- Separate AI reviewer agents checked each of the 50 line by line for faithfulness to its source summary.
- Two AI Hindi reviewers checked the Hindi versions.
- After corrections, a second AI reviewer re-checked all 50 in English and Hindi.
- An AI "physician + pharmacist" reviewer checked the medicine regimens.

They are **not** human-written. Treat them as good examples, not as the only correct wording.

## What's in the box

A `.jsonl` file (JSON Lines) holds one JSON object per line. A "fact key" is the answer key for one summary.

| File | Rows | Description |
|---|---|---|
| `data/summaries/<summary_id>.txt` | 2,000 files | One discharge summary per file (plain text). |
| `data/summaries.jsonl` | 2,000 | The same summaries as JSON lines: `{summary_id, text}`. |
| `data/splits.csv` | 2,000 | `summary_id, split`. The split is `test` (300), `dev` (200) or `unlabelled` (1,500). |
| `data/test_ids.csv` | 300 | **The summaries you must submit predictions for.** Their answer keys are hidden. |
| `data/dev_ids.csv` | 200 | Summaries whose answer keys are public. Use them for development and local scoring. |
| `data/dev_fact_key.jsonl` | 200 | The answer key (fact key) for each dev summary. The hidden test key has the same format. |
| `data/abbreviation_glossary.csv` | 257 | `abbreviation, expansion, plain_language, category`. |
| `data/drug_reference.csv` | 81 | `generic_name, common_brands (';'-separated), dose_forms, typical_strengths, drug_class, plain_language_purpose, also_known_as, used_at_discharge`. |
| `data/gold_examples/` | 50 folders | The reference examples (all from the dev set). See the note below the table. |
| `sample_submission.jsonl` | 300 | A **valid** submission for the test ids, in the exact format `evaluate.py` expects. It is the baseline's output. |
| `evaluate.py` | – | The official scorer. Organisers run this same file against the hidden test key. |
| `baseline/baseline.py` | – | A simple regex baseline. It writes a valid submission for dev or test. |

**Inside `data/gold_examples/`:**

- Each folder `<summary_id>/` holds four files:
    - `source_summary.txt`;
    - `care_plan_en.txt` (English fridge sheet);
    - `care_plan_hi.txt` (Hindi);
    - `care_plan.json` (one submission line).
- `gold_examples.jsonl` holds all 50 in submission format.
- `index.csv` lists them.

The 1,500 `unlabelled` summaries have no answer key. Use them for few-shot prompting (showing an AI model a few examples in the prompt), testing your parser, or self-training.

## Data dictionary

### `summaries.jsonl` / `summaries/*.txt`

| Column | Type | Description | Example | Missing? |
|---|---|---|---|---|
| summary_id | string | Random ID. It carries no information. | `H2S-00WCDYNM` | no |
| text | string | The full discharge summary. | see any `.txt` file | no |

Every summary has these parts:

- a header: patient name, age/sex, UHID, IP number, DOA (date of admission), DOD (date of discharge), consultant, department;
- Diagnosis, brief history, hospital course and (if any) procedure;
- key investigations and condition at discharge;
- **discharge medications** (as a table or a list);
- advice: diet, activity, wound care and other;
- warning signs ("Review SOS if…");
- dated follow-up visits.

### `dev_fact_key.jsonl` (and the hidden test key): one JSON object per summary

| Key | Type | Description |
|---|---|---|
| summary_id | string | ID |
| condition_group | string | `diabetes`, `postop`, `cardiac`, `respiratory` or `maternity` |
| admission_date, discharge_date | string `YYYY-MM-DD` | DOA / DOD |
| medicines | list | One object per **discharge** medicine, in source order (see below). |
| stopped_medicines | list | `{generic_name, name_as_written, brand_as_written, reason_as_written}` for each "STOP …" instruction. |
| continue_previous_medications | bool | True if the summary says to continue earlier medicines (e.g. "Continue previous medications for hypothyroidism as before"). These medicines are **not named**, so you must not invent them. |
| continue_previous_text | string or null | That sentence, as written. |
| follow_ups | list | `{date (YYYY-MM-DD), date_as_written, with_whom, purpose, tests_to_bring}` |
| warning_signs, diet, activity, wound_care, other_advice | list of strings | Each advice line exactly as written in the summary. |
| drugs_mentioned_anywhere | list | Generic names of all drugs mentioned anywhere in the summary, including hospital-only drugs in the hospital course. The text check uses this list (see scoring). |

Each item of `medicines` has these keys:

- **Identity:**
    - `generic_name`: canonical lower-case, as in `drug_reference.csv`.
    - `name_as_written`.
    - `brand_as_written`: null if no brand was written.
    - `dose_form`.
    - `route`: `oral`, `SC`, `SL`, `inhalation`, `eye` or `topical`. (`SC` = injection just under the skin; `SL` = kept under the tongue to dissolve.)
- **Dose:**
    - `dose_value`, a string, given per dose: `"500"`.
        - For split insulin doses it shows the slots: `"16-0-10"`.
        - It is null for tapers (a dose that is lowered step by step) and ointments.
    - `dose_unit`: `mg`, `mcg`, `units`, `IU`, `puffs`, `ml`, `tablets` or `drops`.
    - `strength`: the tablet strength when half a tablet is prescribed, or an inhaler/eye-drop strength label.
    - `dose_quantity`: e.g. `"½ tablet"`.
    - `slot_doses`: e.g. `["16","0","10"]`.
- **Frequency:**
    - `frequency_code`: `OD`, `BD`, `TDS`, `QID`, `HS`, `SOS` or `WEEKLY`. (`OD` = once a day, `BD` = twice, `TDS` = three times, `QID` = four times, `HS` = at bedtime, `SOS` = only when needed.)
    - `slot_pattern`: e.g. `1-0-1`, or null when the summary does not show the time slots (e.g. it only says "BD").
- **Timing:**
    - `timing`: one of `after_food`, `before_food`, `before_breakfast`, `empty_stomach`, `after_breakfast`, `after_lunch`, `after_dinner`, `with_evening_meal`, `before_meals_15`, `before_meals_30`, `before_bf_dinner_30`, `at_10pm`, or null.
    - `timing_as_written`.
- **Duration:**
    - `duration_type`: `days`, `till_review`, `lifelong` or `none` (for SOS medicines).
    - `duration_days`: months count as 30 days and years as 365.
    - `duration_as_written`.
    - `taper`: a list of `{dose_value, dose_unit, frequency_code, duration_days}`, or null.
- **SOS:** `is_sos`, `sos_indication`, `sos_indication_as_written`.
- **Extra instructions:** `extra_instructions` (codes) and `extra_instructions_as_written` (e.g. "rinse mouth after use", "both eyes").
- **`dose_alternatives`:** the dose expressions the scorer accepts, e.g. `625 mg` or `500 mg + 125 mg` for Augmentin. `requires_half: true` means "this strength plus the word half/½".

### `abbreviation_glossary.csv` and `drug_reference.csv`

The columns are listed in the "What's in the box" table. Some rows in the drug reference are marked `used_at_discharge = no (hospital use only)`. These drugs appear only in hospital courses (e.g. Inj. Ceftriaxone). They must never appear in a care plan as something to take at home.

## Know your data

- **Formats vary on purpose.** Five doctor styles are mixed:
    - formal tables;
    - terse brand-heavy notes ("T. Dolo 650 SOS");
    - slot notation ("1-0-1");
    - narrative English;
    - mixed generic-and-brand styles.
- **Dates** appear in several Indian formats (`12/03/2026`, `12-Mar-26`, `12.03.2026`, `12th March 2026`). All dates are **day-first**.
- **Brands with numbers in them hide the unit.** "T. Pan 40" means pantoprazole 40 mg. Use `drug_reference.csv` to map brands to generics.
- **Hard cases appear throughout:**
    - half tablets (`½-0-½`);
    - insulin with different units at different meals;
    - steroid tapers;
    - SOS medicines with a daily maximum;
    - weekly doses;
    - inhaler puffs, eye drops and sublingual tablets;
    - "STOP Tab. …" lines;
    - durations like `x 5/7`, `x 2/52`, `x 1/12`, "till review" and "lifelong".
- **Hospital-course drugs are not discharge medicines.** "Inj. Ceftriaxone 1 g IV BD" in the hospital course was given in hospital. It must not appear in the care plan as a medicine to take.
- **"Continue previous medications for X as before"** means exactly that. Say so in the plan. Do not invent which medicines those are.
- **Abbreviations can be ambiguous** (e.g. `LA`, `MS`, `DM`, `IP`). The glossary lists every meaning used.
- **Regimens reflect common Indian practice, for realism; they are not guideline endorsements.** For example, some summaries continue oral antibiotics for a few days after clean surgery or LSCS (caesarean operation). This is common in India. However, ICMR and WHO advise against routine post-operative antibiotic courses after clean surgery.
- **The summaries are clean and clinically consistent.** The task assumes the source is correct and complete. Unlike other problem statements, there is no deliberate data corruption here. The difficulty lies in the formats.

## How the data is split

- **test (300):** you must submit for all of them. The fact keys are hidden.
- **dev (200):** fact keys are public in `data/dev_fact_key.jsonl`. The 50 reference examples come from dev.
- **unlabelled (1,500):** text only.
- Discharge dates run from 2026-01-04 to 2026-09-28 in all three splits.
- The split is stratified-random by condition: summaries were split at random, with about the same mix of conditions in each split. There is no time trend to exploit, and every summary is a different patient.

## What to submit

**Where the file goes:**

```
teamID_H2.zip                  a zip of your project (a fresh git clone)
├── README.md                  how to set up and run your code
├── submission/
│   └── H2_predictions.jsonl
└── FULL_CODE/                 This folder should contain all of your code:
                               your demo app + the code that created the
                               submission/ files
```

**Your code is required too.** Every team submits one zip of its whole project, as the *Participant Coding Guidelines* explain (zip a fresh `git clone` of your project). The zip holds **all** your code, not only the code that makes your submission files: also the code of the solution you demo (for example a web or mobile app, dashboard, API, command-line tool or notebook). It also holds your own `README.md` that explains how to set up and run everything. It is fine if everything sits inside one project folder in the zip.

- Write your **own** `README.md`. The *Participant Coding Guidelines* (rule 12) list what it must cover. If you built inside this pack's folder, replace this pack's README with yours (rename this one to `PACK_README.md` if you want to keep it).
- Put **all** your code inside a folder named exactly `FULL_CODE/`, at the top of your project: your demo app + the code that created the `submission/` files. At the top of your project, keep only `README.md`, `submission/`, `FULL_CODE/` and your `.gitignore` (plus a `LICENSE` file if you have one).
- Put `submission/` at the top of your project, next to your `README.md`.
- Commit the files in `submission/` to git. Make sure your `.gitignore` does not exclude them (for example with `*.csv` or `*.jsonl`).
- Before you commit, list `data/`, `models/`, `.env`, `.venv/` and `node_modules/` in your `.gitignore`. This keeps the pack's data files and downloaded models out of your zip. Your README should say where to put the data and how to get any models.

- **Required in the zip:** `submission/H2_predictions.jsonl`. This is the file `evaluate.py` scores.
- **Not scored by `evaluate.py`:** your fridge sheet. You show it in your demo, where it is judged.

The file has one JSON object per line, one line per `summary_id` in `data/test_ids.csv`. Extra ids are ignored. Missing test summaries score 0. This is one line, spread over several lines here so you can read it. In your file, each object must stay on one single line; a line break inside an object stops `evaluate.py` with a format error.

```json
{"summary_id": "H2S-XXXXXXXX",
 "care_plan": {
   "medicines": [{"name": "Metformin (Glycomet)", "dose": "500 mg", "frequency": "BD (1-0-1)", "timing": "after food",
                  "duration": "1 month", "how_to_take_plain": "Take 1 tablet (500 mg) twice a day, after food, for 1 month."}],
   "stop_medicines": ["Glimepiride 2 mg"],
   "warning_signs": ["..."], "diet": ["..."], "activity": ["..."],
   "follow_ups": [{"date": "2026-03-19", "with_whom": "Medicine OPD", "purpose": "Check-up with reports"}]},
 "plain_text_en": "MY CARE PLAN ... (the full English plain-language plan)",
 "translation": {"language_code": "hi", "text": "मेरी देखभाल योजना ... (the same plan in an Indian language)"},
 "readability": {"metric": "flesch_kincaid_grade", "score": 6.1}}
```

**Format checklist.** `evaluate.py` checks every rule below. If one fails, it stops with exit code 2 and prints a message that says what to fix.

1. All keys shown above are required.
2. Each item in `medicines` (`medicines[*]`) needs `name`, `dose`, `frequency`, `timing`, `duration` and `how_to_take_plain`. Each must be a string or `null`.
3. `follow_ups[*].date` must be a real date in `YYYY-MM-DD` format.
4. `stop_medicines`, `warning_signs`, `diet` and `activity` must be lists of strings.
5. `translation.language_code` must be one of `hi, mr, ne, kok, bn, as, pa, gu, or, ta, te, kn, ml, ur`.
6. `readability.score` must be a number or null.
7. Each `summary_id` must appear only once.
8. Short fields (`name`, `dose`, `frequency`, `timing`, `duration`, `date`, `with_whom`, each `stop_medicines` entry) must be at most 300 characters. Texts must be at most 100,000 characters.
9. The file must be UTF-8.

Extra keys are allowed and ignored (e.g. `wound_care`, `other_advice`).

**How to fill the fields:**

- Write **one entry per source medicine**.
- In `dose`, include the number **and unit** as in the source. Examples: `"500 mg"`, `"½ tablet of 6.25 mg (3.125 mg)"`, `"16 units before breakfast, 10 units before dinner"`, `"2 puffs"`, `"40 mg, then 30 mg, then 20 mg, then 10 mg"`.
- If the summary itself states a daily maximum, such as "(max 8 puffs/day)", you may repeat it in `dose`. It is not treated as the dose. Any other "max / up to N" amount counts as a dose, and so changes it.
- Do not add dose options the summary does not give, such as "or double if needed" or "2 or 4 puffs".
- A count such as "2 tablets" for a "500 mg" prescription is read as a doubled dose. Write "1 tablet (500 mg)" or just "500 mg".
- Write `frequency` and `duration` in English. Canonical codes are safest, e.g. `OD`, `BD`, `TDS`, `QID`, `HS`, `SOS`, `once weekly`, `5 days`, `2 weeks`, `1 month`, `till next review`, `lifelong`.
- **Translation convention:** in `translation.text`, keep medicine names, doses and numbers in **Latin script with Western digits**. Write dates as `YYYY-MM-DD` (or `DD/MM/YYYY`), e.g. "Metformin 500 mg … 2026-03-12". Write everything else in the Indian language's own script. This is what lets the faithfulness check run on the translation too.

## How you're scored

The score is computed per summary and then averaged over the 300 test summaries. `primary_score` runs from **0 (worst) to 1 (best)**, and faithfulness dominates.

Each summary's score has two parts: a **structured part** (80%) and a **text part** (20%). Part 3 below gives the formula.

**1. Structured part.** This checks your `care_plan` fields.

Your `care_plan.medicines` are matched to the source medicines by name. A name matches if it is the generic name, a brand (via `drug_reference.csv`), an alias, or a close misspelling. "Close" means a difflib ratio ≥ 0.88 (difflib is a Python library; its ratio measures how similar two spellings are, from 0 to 1).

Some entries count as **invented** even though they contain a source name:

- a name that also mentions another drug that is not a source medicine, e.g. "Aspirin / Amlodipine" when amlodipine is not one (stop entries are treated the same way). Drugs are recognised from `drug_reference.csv` plus a lookup-only list of common drugs that never appear in this dataset (`NON_REFERENCE_DRUGS` in `evaluate.py`, e.g. domperidone, hydrochlorothiazide, teneligliptin);
- a second entry for the same medicine. The only exceptions are a taper or split insulin dose, which you may spread over several entries.

If you do split a medicine over several entries, all of them must agree on frequency and duration.

The structured part is the weighted average of these components. Each component runs from 0 to 1, so a perfect structured part is 1.

| Component | Weight | Definition |
|---|---|---|
| Medicine recall | 0.30 | Share of source medicines present. |
| Dose exactness | 0.20 | The dose string (plus name) contains exactly the source dose(s): same number(s) in the same unit family. `1 g` = `1000 mg`. No other amount of that unit may appear. Half tablets need the per-dose amount, or the strength plus "half"/"½". Tapers need every step. Split insulin needs every slot dose. |
| Frequency exactness | 0.15 | Times per day match: OD/HS = once, BD = twice, TDS = 3, QID = 4, or SOS / weekly. Understood forms: codes (also `b.d.`), slot patterns (`1-0-1`), English ("twice a day", "every 8 hours", "morning and night"). |
| Duration exactness | 0.10 | Every duration value you give must match the source in days: `x 5/7` = 5 days, `x 2/52` = 14, `x 1/12` = 30, a month = 30 days, a year = 365. From 28 days, a tolerance of ±2 days or 7% applies (12 weeks ≈ 3 months). "For the rest of your life" counts as lifelong, and "until the doctor says to stop" as till review. Simple number words such as "five days" or "a week" are understood. "till review" and "lifelong" must match in kind. A list of many durations gets no credit. |
| Follow-up date recall | 0.15 | Share of source follow-up dates present (exact `YYYY-MM-DD`). |
| Stop recall | 0.10 | Share of "STOP" medicines listed in `stop_medicines`. If the source has none, this weight is shared among the rest. |

**Hard penalties.** Each is −1. They are subtracted from the structured part, which is then floored at 0 (it cannot go below 0). A penalty is given for:

- each **dropped** source medicine;
- each matched medicine with an **altered dose** (including a missing dose);
- each **invented** medicine (one not in the source's discharge list; this includes a stopped medicine listed as "take");
- each `stop_medicines` entry that is not a source STOP instruction (e.g. telling the patient to stop an active medicine);
- each **invented follow-up date**.

**2. Text part.** This checks faithfulness of the free text, in English (`plain_text_en`) and in the translation (`translation.text`). Each language gets its own text score from 0 to 1. The text part is their average (0.50 each).

For each language text:

- **Coverage** = the share of items found. An item is either:
    - a source medicine: its name (generic, brand or alias) must appear, with its dose number(s) **within 200 characters** of the name. `1 g` counts for 1000 mg. A half tablet needs either the per-dose amount, or the tablet strength plus "half"/"½"/"आधी".
    - a follow-up date.
- **Dose right after the name:** a medicine does not count if a wrong amount follows its name. The scorer looks from the name to the end of that line, the next drug name, or 200 characters, whichever comes first. If an amount in the same unit appears there that is not the source dose, the item is lost. A count of "1" tablet/capsule/puff/drop is always allowed.
- **Accepted date forms:**
    - `YYYY-MM-DD`, `DD/MM/YYYY`, `DD-MM-YYYY`, `DD.MM.YYYY`;
    - "12 March 2026", "12 March, 2026", "12 Mar 2026", "12th March 2026", "March 12, 2026", "12-Mar-26", `12/03/26`, `2026/03/12`;
    - "12 मार्च 2026".
- **Indian-script digits** are converted to Western digits everywhere in your text.
- **Invented drug names:** minus **0.25 for each drug name** (from `drug_reference.csv`) **that appears nowhere in the source summary**.
    - A mention is not counted when it follows "avoid …", "do not / don't / never take / use / buy / give / start …" or "without asking / advice".
    - It is still counted after "never skip / forget / miss / stop".
- The text score of a language = coverage minus these deductions, floored at 0.
- **Keep drug names in Latin script in the translation.** Names written in Indian scripts are not recognised.

The text score of a language is set to 0 if:

- the text copies the source summary. This means more than **50%** of its words lie in runs of 4 or more words copied in order from the source. Tiny filler words like "ok", "the", "and" are ignored. Plain language means rewriting. The reference plans copy at most 28%.
- for the translation, any of these checks fails (each one must be true):
    - at least **50%** of its letters are in the script of `language_code`;
    - at most **35%** of its words are foreign words. These are Latin-script words other than drug names, units and short medical codes, plus any word with letters from another script (look-alike letters count);
    - no single native-script word is more than **15%** of its native-script words (no filler);
    - its length is **0.4–3×** the length of `plain_text_en`;
    - it contains at least **3 different very common words** of the language (e.g. Hindi है, और, के, में, लें, दिन…). The lists are in `FUNCTION_WORDS` in `evaluate.py`; this check is skipped for `as` and `kok`.

An English copy, a letter cipher or random filler therefore scores 0 for the translation. Real translations pass with wide margins. The reference Hindi plans give:

- native-script share ≥ 0.86;
- foreign words ≤ 0.03;
- top word ≤ 0.06;
- length ratio 0.92–1.07;
- 18–24 common words.

**3. Per-summary score**

score = 0.80 × max(0, structured − penalties) + 0.20 × text

In plain words: 80% of each summary's score comes from the structured part after penalties (never below 0), and 20% from the text part. A perfect summary scores 1. Even one hard penalty sets the 80% share to 0.

Readability is **reported, not scored**. You must still include the `readability` key (see "What to submit"). Missing test summaries score 0 and are listed in the report.

All constants are defined at the top of `evaluate.py`:

- weights: W_MED_RECALL=0.30, W_DOSE=0.20, W_FREQ=0.15, W_DURATION=0.10, W_FOLLOWUP=0.15, W_STOP=0.10;
- penalties: PENALTY_* = 1.0;
- blend: W_STRUCTURED=0.80, W_TEXT=0.20, W_TEXT_EN = W_TEXT_TRANSLATION = 0.50;
- text checks: TEXT_INVENTED_DRUG_PENALTY=0.25, NAME_DOSE_WINDOW=200, MAX_SOURCE_COPY_SHARE=0.50, COPY_MIN_RUN=4;
- translation checks: MIN_NATIVE_SCRIPT_SHARE=0.50, MAX_LATIN_WORD_SHARE=0.35, MAX_TOP_WORD_SHARE=0.15, TRANSLATION_LENGTH_RATIO=(0.4, 3.0), MIN_FUNCTION_WORDS=3;
- other: FUZZY_NAME_RATIO=0.88, MAX_FIELD_LEN=300, MAX_TEXT_LEN=100000.

The per-summary breakdown is written to `score.json` (`per_summary`). For the test set, organisers return only the overall numbers, without the per-summary breakdown.

This score is **20% of the judging rubric**:

| Criterion | Weight |
|---|---|
| Problem framing & assumptions | 20% |
| Technical soundness | 25% |
| **Score on hidden split** | **20%** |
| Real-world usability (your fridge sheet!) | 15% |
| Responsibility | 15% |
| Demo & communication | 5% |

("Hidden split" in this table means the test set.)

Warning signs, diet and activity are not machine-scored: judges read them. Dropping or inventing one will cost you on usability and responsibility.

## Hour Zero quick start

Run these from this folder (the one that holds this README). You need Python ≥ 3.9 with pandas + numpy installed. On Mac/Linux, type `python3` instead of `python`.

**Step 1.** Run the baseline on the dev summaries. It writes `dev_predictions.jsonl`.

```bash
python baseline/baseline.py --split dev --out dev_predictions.jsonl
```

**Step 2.** Score those predictions against the public dev answer key.

```bash
python evaluate.py --submission dev_predictions.jsonl --truth data/dev_fact_key.jsonl
```

**Step 3.** Look at the score and its breakdown. The command shows `score.json` in a readable way.

```bash
python -m json.tool score.json
```

In Step 2, look for the line that starts with `PRIMARY SCORE`. The baseline scores about **0.52** on dev. It loses points in three places:

- altered doses (brand-number doses, half tablets, insulin splits, tapers);
- its English text, which mostly copies source lines and so fails the copy check;
- the translation, where an English copy scores 0.

The 50 reference examples score 1.0 on their own dev keys.

**Step 4: make the test submission.** This command writes the **test submission** file `submission/H2_predictions.jsonl`. Your final zip holds a file with this same name and format, made by your own solution.

```bash
python baseline/baseline.py --split test
```

## Assumptions you may make

- The source summary is clinically correct and complete. Nothing may be inferred beyond it.
- Aim for roughly a Class 8 reading level.
- You have no access to the patient's full EHR (electronic health record) beyond the summary.
- The patient may have low literacy, so structure and simplicity matter more than prose quality.

## Licence

Synthetic data generated for the TalentAP Mega Hackathon 2026. CC BY 4.0. Contains no real personal data. Drug and brand names are used for realism only; simplified for this hackathon; not a clinical reference.
