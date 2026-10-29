# H2 Discharge Summary to Care Plan

This submission contains a deterministic English + Telugu care-plan generator, its test predictions, the source code, and a printable demo fridge sheet. The included healthcare examples and dataset are synthetic. This software is a hackathon prototype, not a clinically validated product or a substitute for a clinician.

## Package contents

- `submission/H2_predictions.jsonl` - the existing predictions for the 300 test IDs.
- `FULL_CODE/` - generator, validator, unchanged baseline and official evaluator, plus project and demo documentation.
- `FULL_CODE/SAMPLE_FRIDGE_SHEET.html` - a printable A4 portrait bilingual sheet using the existing synthetic demo case.
- `.gitignore` - excludes data, environments, caches, and development outputs without excluding the required submission.

## Setup and data

Python 3.9 or newer is required. The generator and validator use only the Python standard library; no model download or third-party service is needed.

The original task-pack `data/` directory is intentionally not included in this package. Obtain it from the supplied H2 task pack and keep it outside this submission directory. The generator needs `test_ids.csv`, `summaries.jsonl`, `drug_reference.csv`, and `abbreviation_glossary.csv` in that data directory. The validator needs `test_ids.csv` and `summaries.jsonl`. No answer keys are needed to generate or validate predictions.

## Run and validate

The checked submission is already included; do not rerun generation unless necessary. To regenerate it from the task-pack data, run these commands from this package directory in PowerShell, setting `$data` to the task pack's `data` directory:

```powershell
$py = 'python'
$data = 'C:\path\to\H2_Discharge_Summary_to_Care_Plan\data'
& $py .\FULL_CODE\improved_solution.py --split test --data_dir $data --out .\submission\H2_predictions.jsonl
& $py .\FULL_CODE\validate_telugu.py --submission .\submission\H2_predictions.jsonl --split test --data_dir $data
```

The generator is deterministic and writes one UTF-8 JSON object per line. The validation command checks test ID coverage, schema, Telugu script, and selected rendering consistency; it does not establish semantic completeness or clinical correctness. `FULL_CODE\evaluate.py` is the unchanged official scorer and can be run on development predictions with the public development fact key.

To print the demo sheet, open `FULL_CODE/SAMPLE_FRIDGE_SHEET.html` in a browser and print on A4 portrait. It is a synthetic example and is not for real patient care.

## Evaluation caveat

The reported official development score of 1.0000 is from 200 public development cases. The public development cases overlap with the supplied gold examples and were used during development, so the result may be overfit. It is not evidence of hidden-test accuracy, and the system has not been clinically validated or native-speaker reviewed.
