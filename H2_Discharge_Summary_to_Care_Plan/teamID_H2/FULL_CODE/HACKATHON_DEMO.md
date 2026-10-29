# Hackathon Demo: English + Telugu Care Plan

## Reproduce the local results

Run these commands in PowerShell from the project directory:

```powershell
Set-Location 'E:\brocoders\H2_Dataset_AI_Work\H2_Discharge_Summary_to_Care_Plan'
$py = 'C:\Users\pmlra\AppData\Local\Programs\Python\Python313\python.exe'

# Original baseline: generate predictions for the 200 public dev IDs and score them.
& $py .\baseline\baseline.py --split dev --out .\dev_predictions.jsonl
& $py .\evaluate.py --submission .\dev_predictions.jsonl --truth .\data\dev_fact_key.jsonl --out .\baseline_score.json

# Improved English + Telugu generator: generate and score the same dev cases.
& $py .\improved_solution.py --split dev --out .\dev_predictions_telugu.jsonl
& $py .\evaluate.py --submission .\dev_predictions_telugu.jsonl --truth .\data\dev_fact_key.jsonl --out .\telugu_dev_score.json
& $py .\validate_telugu.py --submission .\dev_predictions_telugu.jsonl --split dev

# Final test submission: writes .\submission\H2_predictions.jsonl.
& $py .\improved_solution.py --split test
& $py .\validate_telugu.py --submission .\submission\H2_predictions.jsonl --split test
```

The scripts default to `.\data` for inputs. The baseline uses the selected split IDs and `summaries.jsonl`; the improved generator additionally loads `drug_reference.csv` and `abbreviation_glossary.csv`. The evaluator's dev command reads `dev_fact_key.jsonl` only for scoring. The generator and validator do not read that key. The validated final submission is at:

`E:\brocoders\H2_Dataset_AI_Work\H2_Discharge_Summary_to_Care_Plan\submission\H2_predictions.jsonl`

The matching dev outputs are `.\dev_predictions.jsonl`, `.\dev_predictions_telugu.jsonl`, `.\baseline_score.json`, and `.\telugu_dev_score.json`.

## Demo example from the generated test output

This is synthetic case `H2S-0HHVFAGP`, taken from `data/summaries.jsonl` and its matching row in `submission/H2_predictions.jsonl`.

**Source excerpt**

```text
TREATMENT ADVISED ON DISCHARGE:
1. Injection Human Premix Insulin 30/70 10 U before breakfast, 4 U before dinner SC ½ hr before breakfast & dinner until the next visit
2. Tablet Metformin 500 mg twice daily after food lifelong
3. Tablet Atorvastatin (Storvas) 10 mg once daily at bedtime to continue lifelong
PLEASE STOP:
  - Tab. Glimepiride 2 mg (now on insulin)
COME BACK TO THE HOSPITAL IMMEDIATELY IF:
  - Hypoglycaemia - sweating / tremors / confusion: 3 tsp glucose or sugar in water immediately and report; if drowsy, nothing orally - bring to hospital
  - Sugar > 300 mg/dl on 2 readings
REVIEW:
  - Review with SMBG chart in DM clinic, Medicine OPD on 29th April 2026.
```

**Generated English care plan**

```text
MY CARE PLAN

MEDICINES
- Insulin premix 30/70: 10 units before breakfast, 4 units before dinner, twice a day, 30 minutes before breakfast and dinner, until your next check-up, inject under the skin.
- Metformin: 500 mg, twice a day, after food, for life.
- Atorvastatin (Storvas): 10 mg, once a day, at bedtime, for life.

MEDICINES TO STOP
- Stop taking Glimepiride 2 mg.

GET MEDICAL HELP NOW IF
- If you are sweaty, shaky, or confused from low blood sugar, take 3 tsp of sugar or glucose in water and seek care; if drowsy, take nothing by mouth and go to hospital.
- If two readings show blood sugar above 300 mg/dl.

OTHER INSTRUCTIONS
- For diabetes, avoid sugar, sweets, jaggery, honey, and fruit juice.
- Eat small meals often, and do not miss a meal.
- Check your blood sugar four times each day, before meals and at bedtime, and keep a record.
- Use the injection method you were shown; alternate sites on the abdomen and thigh, and store insulin in a refrigerator, not the freezer.
- Keep sugar or glucose with you at all times.
- Check your feet every day, wear footwear, and do not walk barefoot.
- In an emergency, contact the 24-hour emergency department at Chandrapuri District Hospital.

FOLLOW-UP VISITS
- 2026-04-29: Medicine clinic Review with home sugar readings chart in DM clinic.
```

**Generated Telugu care plan**

```text
నా సంరక్షణ ప్రణాళిక
క్రింది మందులు మరియు సూచనలను మీ ఆసుపత్రి బృందం చెప్పిన విధంగా పాటించండి.

మందులు
- Insulin premix 30/70: 10 units అల్పాహారానికి ముందు, 4 units రాత్రి భోజనానికి ముందు, రోజుకు 2 సార్లు, అల్పాహారం మరియు రాత్రి భోజనానికి 30 నిమిషాల ముందు, తదుపరి వైద్య పరీక్ష వరకు; చర్మం కింద ఇంజెక్షన్‌గా ఇవ్వండి.
- Metformin: 500 mg, రోజుకు 2 సార్లు, జీవితాంతం వాడండి.
- Atorvastatin (Storvas): 10 mg, రోజుకు 1 సారి, రాత్రి నిద్రపోయే ముందు, జీవితాంతం వాడండి.

ఆపాల్సిన మందులు
- Glimepiride 2 mg — ఈ మందును ఆపండి.

వెంటనే వైద్య సహాయం పొందండి, ఒకవేళ
- రక్తంలో చక్కెర తగ్గినప్పుడు చెమటలు, వణుకు లేదా గందరగోళం ఉంటే, 3 చిన్న చెంచాల చక్కెర లేదా గ్లూకోజ్‌ను నీటిలో తీసుకుని వెంటనే ఆసుపత్రికి వెళ్లండి. మత్తుగా ఉంటే నోటి ద్వారా ఏమీ ఇవ్వకండి; ఆసుపత్రికి తీసుకెళ్లండి.
- రెండు రక్త చక్కెర పరీక్షల్లోనూ 300 mg/dl కంటే ఎక్కువగా ఉంటే వైద్య బృందాన్ని సంప్రదించండి.

ఇతర సూచనలు
- చక్కెర, స్వీట్లు, బెల్లం, తేనె మరియు పండ్ల రసాలు తీసుకోకండి.
- చిన్న పరిమాణంలో తరచుగా తినండి; భోజనం మానేయకండి.
- భోజనానికి ముందు మరియు నిద్రకు ముందు సహా రోజుకు 4 సార్లు రక్త చక్కెర పరీక్షించి, ఫలితాలను రాయండి.
- ఇన్సులిన్ ఇంజెక్షన్ ఇచ్చే చోటును పొట్ట లేదా తొడపై మార్చుతూ ఇవ్వండి; ఇన్సులిన్‌ను ఫ్రిజ్‌లో ఉంచండి, ఫ్రీజర్‌లో కాదు.
- చక్కెర లేదా గ్లూకోజ్‌ను ఎల్లప్పుడూ మీ వెంట ఉంచుకోండి.
- పాదాలను ప్రతిరోజూ పరిశీలించండి, ఎల్లప్పుడూ పాదరక్షలు ధరించండి; చెప్పులు లేకుండా నడవకండి.
- అత్యవసర పరిస్థితిలో Chandrapuri District Hospital అత్యవసర విభాగాన్ని (24 గంటలు) సంప్రదించండి.

తదుపరి వైద్య తనిఖీలు
- 2026-04-29: మెడిసిన్ క్లినిక్ రక్తంలో చక్కెర కొలతల పట్టికతో తదుపరి వైద్య తనిఖీకి రండి.
```

## Architecture and result

The generator selects summary IDs from `dev_ids.csv` or `test_ids.csv`, reads those source records from `summaries.jsonl`, and uses the drug reference to identify discharge medicines. It builds a structured care plan, then renders English and Telugu separately; selected unambiguous glossary entries are used for patient-facing phrase expansion. It writes one JSON object per line in the official schema. The separate Telugu validator checks split-ID coverage, JSONL/schema, script, and selected rendering consistency. It does not evaluate clinical correctness.

The unchanged official evaluator reports **0.5198 for the baseline** and **1.0000 for the improved implementation** on the same **200 public development cases**. The saved reports record `n_scored = 200` for each. These are dev results only, not hidden-test results.

**Interpret the perfect dev score cautiously:** all 50 supplied gold-example IDs overlap the public dev set, and previous development work inspected those examples and iterated against dev scoring. The generator does not read gold outputs at prediction time, but the overlap and tuning make this an in-sample, potentially overfit result—not independent evidence of generalization.

## Limitations and safety

- The Telugu is generated with deterministic phrase rules, not a translation model. A native Telugu speaker should review grammar, idiom, technical terms, and whether each instruction is understood as intended.
- The validator's script and structural checks cannot prove translation completeness, semantic equivalence, or medical faithfulness.
- The benchmark and drug/glossary references are synthetic or simplified hackathon materials. No clinician/pharmacist clinical-safety validation has been performed; this is not a treatment tool.
- Do not claim hidden-test accuracy from the dev score. Any real-world use would require qualified clinical review and a human-in-the-loop safety process.
