# ALOTT-MEDS

<!-- Placeholders until available: the PyPI badges resolve once ALOTT-MEDS is published; replace
CODECOV_TOKEN_PLACEHOLDER with the repo's Codecov badge token and zenodo.XXXXXXX with its Zenodo DOI. -->

[![PyPI - Version](https://img.shields.io/pypi/v/ALOTT-MEDS)](https://pypi.org/project/ALOTT-MEDS/)
[![codecov](https://codecov.io/gh/rvandewater/ALOTT_MEDS/graph/badge.svg?token=CODECOV_TOKEN_PLACEHOLDER)](https://codecov.io/gh/rvandewater/ALOTT_MEDS)
[![tests](https://github.com/rvandewater/ALOTT_MEDS/actions/workflows/synthetic-etl.yaml/badge.svg)](https://github.com/rvandewater/ALOTT_MEDS/actions/workflows/synthetic-etl.yaml)
[![code-quality](https://github.com/rvandewater/ALOTT_MEDS/actions/workflows/code-quality-main.yaml/badge.svg)](https://github.com/rvandewater/ALOTT_MEDS/actions/workflows/code-quality-main.yaml)
![python](https://img.shields.io/badge/python-%3E%3D3.11.4%2C%20%3C3.14-blue?logo=python&logoColor=white)
![Static Badge](https://img.shields.io/badge/MEDS-0.4.1-blue)
[![license](https://img.shields.io/badge/License-MIT-green.svg?labelColor=gray)](https://github.com/rvandewater/ALOTT_MEDS#license)
[![PRs](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/rvandewater/ALOTT_MEDS/pulls)
[![contributors](https://img.shields.io/github/contributors/rvandewater/ALOTT_MEDS.svg)](https://github.com/rvandewater/ALOTT_MEDS/graphs/contributors)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.XXXXXXX.svg)](https://doi.org/10.5281/zenodo.XXXXXXX)

ALOTT (ALarms, Outcomes Telemetry with Timing) is a pilot bedside-EMR database from two
hospitals, collected to support algorithms that predict emergency response team events. It
pairs bedside telemetry from 270 beds (alarms, monitor measurements and waveforms) with
electronic medical record data: admissions, diagnoses, ICU stays and bed moves, labs,
orders, medications and nursing flowsheets.
This pipeline extracts the EMR tables of ALOTT (from PhysioNet,
https://physionet.org/content/alott/) into the
[MEDS](https://github.com/Medical-Event-Data-Standard/meds) format. It is built the same way
as [MIMIC_IV_MEDS](https://github.com/Medical-Event-Data-Standard/MIMIC_IV_MEDS): one
declarative [MEDS-Extract](https://github.com/mmcdermott/MEDS_extract) spec, no Python.

## Usage:

```bash
pip install git+https://github.com/rvandewater/ALOTT_MEDS
export DATASET_DOWNLOAD_USERNAME=$PHYSIONET_USERNAME
export DATASET_DOWNLOAD_PASSWORD=$PHYSIONET_PASSWORD

meds-extract-run spec=ALOTT output_dir=$MEDS_OUTPUT_DIR
```

That downloads only the `emr-datasets/` folder of the ALOTT 1.0.0 release (the telemetry is
skipped) into `$MEDS_OUTPUT_DIR/.meds_extract_run/raw_input` and runs the full MEDS-Extract
pipeline, writing `data/` and `metadata/` to `$MEDS_OUTPUT_DIR`. Files that already exist and
verify against PhysioNet's checksums are skipped, so an interrupted download resumes. Pass
`download_dest_dir=$RAW_INPUT_DIR` to keep the raw data somewhere durable.

ALOTT is a credentialed release: the PhysioNet account must have signed the ALOTT data use
agreement.

To reuse a download you already have, point `input_dir` at the directory that *contains*
`emr-datasets/`:

```bash
meds-extract-run spec=ALOTT output_dir=$MEDS_OUTPUT_DIR do_download=false input_dir=$RAW_INPUT_DIR
```

> [!NOTE]
> The folder the EMR CSVs sit in on PhysioNet, assumed to be `emr-datasets/`, has not been
> checked against a download. If your copy is a flat folder of the ten CSVs
> (`admission.csv`, `labs.csv`, ...), point `input_dir` at a directory holding an
> `emr-datasets` link to it.

Run `meds-extract-run --help` for the full set of arguments.

## Configuration

The whole pipeline is one file,
[`src/ALOTT_MEDS/event_configs.yaml`](src/ALOTT_MEDS/event_configs.yaml), written in MESSY
(MEDS-Extract Specification Syntax YAML) and registered under the `MEDS_extract.pipelines`
entry-point group as `ALOTT`. That registration is what makes `spec=ALOTT` work from anywhere
and what stamps the output's `metadata/dataset.json` with
`<ALOTT release>:<this package's version>`. The file holds:

- a `sources:` block pointing at the PhysioNet release, restricted to `emr-datasets/*`;
- an `etl:` block with the run's few knobs;
- one block per raw EMR table describing the events extracted from it.

### Timestamps

ALOTT shifts each patient's dates by a random -180..180 days but keeps the time of day. Most
tables store each timestamp as one shifted `%Y-%m-%d %H:%M:%S` column. Labs and orders carry
only a shifted **date**, so their events are placed at the **end of that day (23:59:59)**:
midnight would make a result visible before it existed, which leaks the future into any
prediction made later that day. Same-day lab orders and results therefore share a time. A row
with a null timestamp produces no event.

Absolute dates are shifted per patient, so only differences *within* a subject are
meaningful.

### IDs

`aMRN` is the MEDS `subject_id`; the encounter key `aCSN` is carried as `hadm_id`. Lab
results use the resulting encounter and lab and other orders the ordering one. The release
writes the string `nan` for an unresulted encounter; it is cast leniently, so it becomes a
null and `hadm_id` stays `Int64`.

### Null handling

A null component of a composite code drops the row under MEDS-Extract 0.7. Components are
therefore coalesced with `?? 'UNK'` wherever a null must not drop the row (admission type and
source, discharge destination, lab component, order type).

The rest are deliberately left uncoalesced. In the local copy of the release, the code
columns of flowsheets, labs, orders and allergies are never null, and `MEDICATION` is null in
9 of 6,384,831 rows, so a coalesce would add nothing. A missing ICD-10 code or demographic
produces no event rather than an `UNK` category, because a missing value is not an observed
one.

### Diagnoses

The primary and secondary ICD-10 codes on each visit become `DIAGNOSIS//ICD//10//<code>`
events at discharge time, with `diagnosis_rank` set to `PRIMARY` or `SECONDARY`. No
`parent_codes` metadata is attached: the same code occurs as both primary and secondary, and
MEDS-Extract rejects one code produced by two code templates.
`DIAGNOSIS//ICD//10//<code>` maps to `ICD10CM/<code>` directly.

### Other tables

- **ICU stays and bed moves** carry only their start and end times in the release (no unit,
    room or bed), so they become bare `ICU_ADMISSION`, `ICU_DISCHARGE` and `TRANSFER`
    events.
- **Demographics and allergies** carry no time in the release and are static events.
- **Flowsheet and lab values** are free text. The numeric reading is kept where it parses,
    and the raw string is always kept in `text_value`.
- **Birth and death:** the release has no birth date, age or death time, so there are no
    `MEDS_BIRTH` or `MEDS_DEATH` events.
- **Not extracted:** the telemetry (alarms, 2-second measurements, waveforms; per-admission
    files keyed by file name, far larger than the EMR), `social_hx` (no event times and
    unlabeled Epic category codes), and medication administrations
    (`OffsetMAR_TAKEN_TIME`; not emitted yet).

### Validation against the release

The spec was first written from the authors' release code
([Carescape](https://github.com/johnclawrence/Carescape)) and did not run on the published
files. Profiling the 1.0.0 EMR CSVs (value *shapes* and null counts only, never values)
showed what had to change:

- Tables are lowercase (`admission`, `labs`, ...) rather than `Patient`, `Labs`, ...; the
    keys are `aMRN` and `aCSN`, not `csID` and `CS_PAT_ENC_CSN_ID`.
- Each timestamp is one already-shifted column rather than a date plus an `HH:MM:SS`
    column. Labs and orders keep only the date (see [Timestamps](#timestamps)).
- `icu` and `location` have no department, event type, room or bed columns.
- `resulting_aCSN` holds the string `nan` for unresulted rows.
- 1,574 ICD-10 codes occur as both primary and secondary diagnoses (see
    [Diagnoses](#diagnoses)). The synthetic test now covers this collision.

All ten CSVs are valid UTF-8. On a local copy of the 1.0.0 EMR tables (4.8 GB of CSVs),
`meds-extract-run` with MEDS-Extract 0.7.1 finishes in about a minute with about 3.2 GB peak
memory, producing 36.5M events for 18,472 subjects. Fewer lab, order and medication events
come out than raw rows because identical events (same subject, time and code) are merged: lab
and order times have day resolution, and the medication table repeats an order's start and
end on every administration row.

## MEDS-transforms settings

If you want to convert a large dataset, you can parallelize the MEDS-transforms stages
(the MEDS-transformation step that takes the longest).

Using local parallelization with the `hydra-joblib-launcher` package:

```bash
pip install hydra-joblib-launcher --upgrade
```

Then write a stage-runner file and pass it to the run:

```yaml
# runner.yaml
parallelize:
  n_workers: 8
  launcher: joblib
```

```bash
meds-extract-run spec=ALOTT output_dir=$MEDS_OUTPUT_DIR stage_runner_fp=runner.yaml
```

The number of subjects per shard is `n_subjects_per_shard` in the spec's `etl:` block
(1000 by default); raise it to cut the parallelization overhead.

## Development

```bash
uv sync --group dev
uv run pytest tests/
uv run pre-commit run --all-files
```

ALOTT has no open demo, so CI runs the spec end to end over a small **synthetic** input
generated in [`tests/e2e_synthetic_test.py`](tests/e2e_synthetic_test.py). That checks the
spec's mechanics, not its agreement with the real release. Never commit raw ALOTT data or
MEDS output derived from it.

## Citation

Please cite the following original dataset when using ALOTT:

```bibtex
@article{PhysioNet-alott-1.0.0,
  author = {Lawrence, John and Rayo, Mike and Huerta, Timothy},
  title = {{ALarms, Outcomes Telemetry with Timing (ALOTT): a Bedside-EMR Database}},
  journal = {{PhysioNet}},
  year = {2025},
  month = mar,
  note = {Version 1.0.0},
  doi = {10.13026/sbq5-dy17},
  url = {https://doi.org/10.13026/sbq5-dy17}
}
```

Also cite the ETL framework used to extract the dataset:

```bibtex
@software{van_de_Water_ALOTT_MEDS_ETL_2025,
author = {van de Water, Robin Philippus},
license = {MIT},
month = apr,
title = {{ALOTT\_MEDS ETL}},
url = {https://github.com/rvandewater/ALOTT_MEDS},
year = {2025}
}
```

For citing MEDS in general:

```bibtex
@article{mcdermott2026meds,
  title={MEDS—An Emerging Data Standard and Ecosystem for Health AI Research},
  author={McDermott, Matthew BA and Steinberg, Ethan and Fries, Jason A and van de Water, Robin P and Pang, Chao and Rockenschaub, Patrick and Renc, Pawel and Oh, Jungwoo and Stankevi{\v{c}}i{\=u}t{\.e}, Kamil{\.e} and Xu, Justin and others},
  journal={NEJM AI},
  volume={3},
  number={6},
  pages={AIra2501253},
  year={2026},
  publisher={Massachusetts Medical Society}
}
```
