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
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.23698074.svg)](https://doi.org/10.5281/zenodo.23698074)

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
[`src/ALOTT_MEDS/event_configs.yaml`](https://github.com/rvandewater/ALOTT_MEDS/blob/main/src/ALOTT_MEDS/event_configs.yaml), written in MESSY
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
only a shifted **date**, and medication start and end times are always `00:00:00`, i.e. dates
too. Those events are placed at the **end of that day (23:59:59)**: midnight would make a
result or a new medication visible before it existed, which leaks the future into any
prediction made later that day. Same-day lab orders and results therefore share a time.
Medication administrations keep their recorded time of day. A row with a null timestamp
produces no event.

Absolute dates are shifted per patient, so only differences *within* a subject are
meaningful.

### IDs

`aMRN` is the MEDS `subject_id`; the encounter key `aCSN` is carried as `hadm_id`. Lab and
order results use the resulting encounter, and orders the ordering one. The release
writes the string `nan` for an unresulted encounter; it is cast leniently, so it becomes a
null and `hadm_id` stays `Int64`.

### Codes

Every code is `FAMILY//...`, from general to specific, so a prefix selects a group (all
labs, one lab panel, one drug). Where a table records both an order and its outcome, the
second component says which: `ORDERED` or `RESULT`, `START` or `STOP`, or `MAR`
(Medication Administration Record: the bedside log of each dose given, held or refused).
`UNK` stands for a null component that must not drop the row (see
[Null handling](#null-handling)).

| Code                                             | Table        | Time                  | Value and extra columns                                                                |
| ------------------------------------------------ | ------------ | --------------------- | -------------------------------------------------------------------------------------- |
| `HOSPITAL_ADMISSION//<admission type>//<source>` | admission    | admission             | reason for visit in `text_value`                                                       |
| `HOSPITAL_DISCHARGE//<destination>`              | admission    | discharge             |                                                                                        |
| `DIAGNOSIS//ICD//10//<ICD-10-CM code>`           | admission    | discharge             | diagnosis text; `diagnosis_rank` `PRIMARY` or `SECONDARY`                              |
| `<FIELD>//<value>`                               | demographics | static                | `FIELD` is `GENDER`, `MARITAL_STATUS`, `RACE`, `ETHNICITY`, `LANGUAGE` or `BLOOD_TYPE` |
| `ALLERGY//<allergen>`                            | allergy      | static                | reaction in `text_value`                                                               |
| `FLOWSHEET//<FLO_MEAS_ID>`                       | flowsheet    | recorded              | reading or score; `flow_type`                                                          |
| `ICU_ADMISSION`, `ICU_DISCHARGE`                 | icu          | start, end            |                                                                                        |
| `TRANSFER`                                       | location     | start                 |                                                                                        |
| `MEDICATION//START//<drug>`                      | medications  | start date, 23:59:59  | ordered dose (raw text if a range); `dose_unit`, `frequency`                           |
| `MEDICATION//STOP//<drug>`                       | medications  | end date, 23:59:59    |                                                                                        |
| `MEDICATION//MAR//<drug>//<action>`              | medications  | administration        | given dose; `dose_unit`, `route`                                                       |
| `LAB//ORDERED//<PROC_ID>`                        | labs         | order date, 23:59:59  |                                                                                        |
| `LAB//RESULT//<PROC_ID>//<component>`            | labs         | result date, 23:59:59 | result; `order_status`                                                                 |
| `ORDER//ORDERED//<order type>//<PROC_ID>`        | orders       | order date, 23:59:59  | `order_status`                                                                         |
| `ORDER//RESULT//<order type>//<PROC_ID>`         | orders       | result date, 23:59:59 | result; `order_status`                                                                 |

### Code metadata

`metadata/codes.parquet` gives every code a `description`, except diagnoses (see
[Diagnoses](#diagnoses)), which suMEDS describes (see
[Enriching code metadata with suMEDS](#enriching-code-metadata-with-sumeds)). All of them come
from `_metadata: _self:` blocks in the spec, evaluated on the extracted rows, so the package
still ships no Python, only the spec and a suMEDS rules file:

| Codes                                                                                             | Description                                                                                                                              |
| ------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| `FLOWSHEET//...`, `LAB//...`, `ORDER//...`                                                        | the release's own display names: `disp_name` (else `flo_meas_name`) for flowsheets; `DESCRIPTION`, plus `COMPONENT_NAME` for lab results |
| `HOSPITAL_ADMISSION`, `HOSPITAL_DISCHARGE`, the six demographic families, `ALLERGY`, `MEDICATION` | the code's own components, each named with the column it comes from, e.g. `Patient sex/gender: M (ALOTT demographics.Gender)`            |
| `ICU_ADMISSION`, `ICU_DISCHARGE`, `TRANSFER`                                                      | a fixed text saying what the event marks and that the release records no unit, room or bed                                               |

What each column means follows the table descriptions on the
[ALOTT 1.0.0 PhysioNet page](https://physionet.org/content/alott/1.0.0/) and the column names
in the authors' release code ([Carescape](https://github.com/johnclawrence/Carescape), MIT);
the descriptions paraphrase them and copy no text.

MEDS-Extract 0.7 attaches `_metadata` only to a code whose expression references a source
column ([MEDS_extract#166](https://github.com/mmcdermott/MEDS_extract/issues/166)), so the
three constant codes are written `'ICU_ADMISSION' if $startOffsetDate == $startOffsetDate`.
The guard tests the event's own time column, which is false only where the time is null, and
such a row produces no event anyway: the extracted events are unchanged.

The ETL writes no `parent_codes`. The only standard vocabulary in the EMR tables is
ICD-10-CM, and diagnoses cannot carry metadata (below), so their ICD-10-CM parents come from
suMEDS. Labs and orders are keyed by Epic procedure IDs,
flowsheets by Epic flowsheet IDs and medications by name; the release has no LOINC, RxNorm or
NDC column.

### Null handling

A null component of a composite code drops the row under MEDS-Extract 0.7. Components are
therefore coalesced with `?? 'UNK'` wherever a null must not drop the row (admission type and
source, discharge destination, lab component, order type, medication administration action).

The rest are deliberately left uncoalesced. In the release, the code columns of
flowsheets, labs, orders and allergies are never null, and `MEDICATION` is null in 10 of
12,698,867 rows, so a coalesce would add nothing. A missing ICD-10 code or demographic
produces no event rather than an `UNK` category, because a missing value is not an observed
one.

### Diagnoses

The primary and secondary ICD-10 codes on each visit become `DIAGNOSIS//ICD//10//<code>`
events at discharge time, with `diagnosis_rank` set to `PRIMARY` or `SECONDARY`. About 4% of
cells list up to four codes separated by a comma and a space; each code becomes its own event, carrying the
cell's full diagnosis text. No `_metadata` is attached, so diagnosis codes have neither a
`description` nor `parent_codes`: each list position of each column is its own code template,
one code occurs under several of them (as primary and as secondary, at different positions),
and MEDS-Extract 0.7 rejects a code produced by two metadata-carrying code templates.
The codes are dotted ICD-10-CM codes, so `DIAGNOSIS//ICD//10//<code>` maps to
`ICD10CM//<code>` directly, and suMEDS adds their titles and parents (below).

### Enriching code metadata with suMEDS

Vocabulary work is maintained once, in [suMEDS](https://github.com/rvandewater/SumMEDS), rather
than in every ETL. This package ships the rules for it,
[`src/ALOTT_MEDS/sumeds_rules.yaml`](src/ALOTT_MEDS/sumeds_rules.yaml):

```bash
pip install sumeds # a release with rules support (newer than 0.3.2)
suMEDS-enrich $MEDS_OUTPUT_DIR/metadata/codes.parquet -o codes_enriched.parquet \
	--rules src/ALOTT_MEDS/sumeds_rules.yaml
```

Diagnosis codes are validated against suMEDS's versioned ontology data (sumeds-ontologies,
pinned release), built from the CDC/NCHS ICD-10-CM files. The rules add:

- `alott-icd10cm-diagnoses`: each `DIAGNOSIS//ICD//10//<code>` with the dotted ICD-10-CM shape
    that is a valid ICD-10-CM code gets the official title as its `description`, and
    `ICD10CM//<code>` followed by its ICD-10-CM ancestors as `parent_codes`.
- `alott-diagnosis-parents-in-dataset`: same-dataset parents. A diagnosis code also gets the
    nearest ICD-10-CM ancestor that is itself an ALOTT diagnosis code, e.g.
    `DIAGNOSIS//ICD//10//I50.2` for `DIAGNOSIS//ICD//10//I50.23`.

suMEDS fills only missing descriptions, never removes or rewrites existing metadata, and writes
parents as `VOCABULARY//CODE`. The ETL alone emits no diagnosis descriptions or parent codes.

### Other tables

- **ICU stays and bed moves** carry only their start and end times in the release (no unit,
    room or bed), so they become bare `ICU_ADMISSION`, `ICU_DISCHARGE` and `TRANSFER`
    events.
- **Demographics and allergies** carry no time in the release and are static events.
- **Flowsheet, lab and order-result values** are free text. The numeric reading is kept where
    it parses; scored flowsheet items charted as `<score>--><label>` (e.g. `-1-->Drowsy`) keep
    their score. The raw string goes to `text_value` unless it is just the number. Epic's
    nonnumeric sentinel `9999999` never becomes a numeric value.
- **Medications:** each row of the release is one administration and repeats its order's
    start and end, so an order yields one `START`, one `STOP` and one `MAR` event per
    administration.
- **Orders** hold every order type except labs; roughly half carry a single result.
- **Birth and death:** the release has no birth date, age or death time, so there are no
    `MEDS_BIRTH` or `MEDS_DEATH` events.
- **Not extracted:** the telemetry (alarms, 2-second measurements, waveforms; per-admission
    files keyed by file name, far larger than the EMR), `social_hx` (no event times and
    unlabeled Epic category codes), and the medication order date.

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
- 1,536 ICD-10 codes occur as both primary and secondary diagnoses, and some cells list
    several codes (see [Diagnoses](#diagnoses)). The synthetic test covers both.
- Medication start and end times are always midnight, and `orders` carries results of its
    own (it holds no lab orders).

All ten CSVs are valid UTF-8. On the full 1.0.0 EMR tables (6.3 GB of CSVs read),
`meds-extract-run` with MEDS-Extract 0.7.1 finishes in about 90 seconds on a laptop with about
6.5 GB peak memory, producing 52.6M events for 18,472 subjects. Fewer lab, order and
medication start/stop events come out than raw rows because identical events are merged: lab
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

A release bumps the version in `pyproject.toml` with `uv version --bump`, commits it and pushes
a tag of that exact version, which publishes to PyPI and creates the GitHub release. See [`RELEASING.md`](https://github.com/rvandewater/ALOTT_MEDS/blob/main/RELEASING.md) for choosing the
version, the one-time PyPI setup and recovering from a failed release.

ALOTT has no open demo, so CI runs the spec end to end over a small **synthetic** input
generated in [`tests/e2e_synthetic_test.py`](https://github.com/rvandewater/ALOTT_MEDS/blob/main/tests/e2e_synthetic_test.py). That checks the
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
@software{robin_van_de_water_2026_23698074,
  author       = {Robin van de Water},
  title        = {rvandewater/ALOTT\_MEDS: Zenodo release},
  month        = oct,
  year         = 2026,
  publisher    = {Zenodo},
  doi          = {10.5281/zenodo.23698074},
  url          = {https://doi.org/10.5281/zenodo.23698074},
}
```

(also check the "Cite this repository" link on this repo's main page for the latest version)

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
