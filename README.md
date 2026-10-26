# ALOTT MEDS Extraction ETL

[![tests](https://github.com/rvandewater/ALOTT_MEDS/actions/workflows/tests.yaml/badge.svg)](https://github.com/rvandewater/ALOTT_MEDS/actions/workflows/tests.yaml)
[![code-quality](https://github.com/rvandewater/ALOTT_MEDS/actions/workflows/code-quality-main.yaml/badge.svg)](https://github.com/rvandewater/ALOTT_MEDS/actions/workflows/code-quality-main.yaml)
![python](https://img.shields.io/badge/-Python_3.11+-blue?logo=python&logoColor=white)
[![license](https://img.shields.io/badge/License-MIT-green.svg?labelColor=gray)](LICENSE)

This pipeline extracts the EMR tables of the
[ALOTT](https://physionet.org/content/alott/1.0.0/) dataset (PhysioNet; the "Carescape"
data from the dataset authors' [release code](https://github.com/johnclawrence/Carescape))
into the [MEDS](https://github.com/Medical-Event-Data-Standard/meds) format. It is built
the same way as [MIMIC_IV_MEDS](https://github.com/Medical-Event-Data-Standard/MIMIC_IV_MEDS):
one declarative [MEDS-Extract](https://github.com/mmcdermott/MEDS_extract) spec, no Python.

## Usage

```bash
pip install git+https://github.com/rvandewater/ALOTT_MEDS
export DATASET_DOWNLOAD_USERNAME=$PHYSIONET_USERNAME
export DATASET_DOWNLOAD_PASSWORD=$PHYSIONET_PASSWORD
meds-extract-run spec=ALOTT output_dir=$MEDS_OUTPUT_DIR
```

When you run this, the program will:

1. Download only the `emr-datasets/` folder of the ALOTT 1.0.0 release (the telemetry is
    skipped) into `$MEDS_OUTPUT_DIR/.meds_extract_run/raw_input`. Files that already exist
    and verify against PhysioNet's checksums are skipped, so an interrupted download
    resumes. Pass `download_dest_dir=$RAW_INPUT_DIR` to keep the raw data somewhere durable.
2. Construct the MEDS cohort directly from those raw files — every transformation is
    declared in [`src/ALOTT_MEDS/event_configs.yaml`](src/ALOTT_MEDS/event_configs.yaml) —
    and write it to `$MEDS_OUTPUT_DIR` (`data/` and `metadata/`).

ALOTT is a credentialed release: the PhysioNet account must have signed the ALOTT data use
agreement.

If you already downloaded the EMR tables yourself, skip the download. `input_dir` must be
the directory that *contains* `emr-datasets/`:

```bash
wget -r -N -c -np -nH --cut-dirs=3 -R "index.html*" -P "$RAW_INPUT_DIR" \
	--user "$PHYSIONET_USERNAME" --ask-password \
	https://physionet.org/files/alott/1.0.0/emr-datasets/
meds-extract-run spec=ALOTT output_dir=$MEDS_OUTPUT_DIR do_download=false input_dir=$RAW_INPUT_DIR
```

Run `meds-extract-run --help` for the full set of arguments.

## How this ETL is defined

There is no Python in this package. The entire ETL is one file,
[`src/ALOTT_MEDS/event_configs.yaml`](src/ALOTT_MEDS/event_configs.yaml), written in MESSY
(MEDS-Extract Specification Syntax YAML):

- a `sources:` block pointing at the PhysioNet release, restricted to `emr-datasets/*`;
- an `etl:` block with the run's few knobs;
- one block per raw EMR table describing the events extracted from it.

`pyproject.toml` registers that file with MEDS-Extract under the name `ALOTT`, which is
what makes `spec=ALOTT` work from anywhere and what stamps the output's
`metadata/dataset.json` with `<ALOTT release>:<this package's version>`.

## Modelling decisions

- **Timestamps.** ALOTT shifts each patient's dates by a random -180..180 days but keeps
    the time of day. Most tables store each timestamp as one shifted `%Y-%m-%d %H:%M:%S`
    column. Labs and orders carry only a shifted date, so their events are placed at the
    end of that day (23:59:59): midnight would make a result visible before it existed.
    Same-day lab orders and results therefore share a time. A row with a null timestamp
    produces no event.
- **IDs.** `aMRN` is the MEDS `subject_id`; the encounter key `aCSN` is carried as
    `hadm_id`. Lab results use the resulting encounter and lab/other orders the ordering
    one; the release's `nan` placeholder for an unresulted encounter becomes a null.
- **Diagnoses.** The primary and secondary ICD-10 codes on each visit become
    `DIAGNOSIS//ICD//10//<code>` events at discharge time, with `diagnosis_rank` set to
    `PRIMARY` or `SECONDARY`. No `parent_codes` metadata is attached: the same code occurs
    as both primary and secondary, and MEDS-Extract rejects one code produced by two code
    templates. `DIAGNOSIS//ICD//10//<code>` maps to `ICD10CM/<code>` directly.
- **ICU stays and bed moves** carry only their start and end times in the release (no
    unit, room or bed), so they become bare `ICU_ADMISSION`, `ICU_DISCHARGE` and
    `TRANSFER` events.
- **Demographics and allergies** carry no time in the release and are static events.
    Missing demographics produce no event rather than an `…//UNK` code.
- **Flowsheet and lab values** are free text. The numeric reading is kept where it parses,
    and the raw string is always kept in `text_value`.
- **Not extracted:** the telemetry (alarms, 2-second measurements, waveforms; per-admission
    files keyed by file name, far larger than the EMR), `social_hx` (no event times and
    unlabeled Epic category codes), and medication administrations
    (`OffsetMAR_TAKEN_TIME`; not emitted yet).

> [!NOTE]
> The table and column names in the spec were checked against the ten EMR CSVs of the
> 1.0.0 release (`admission.csv`, `labs.csv`, ...; all valid UTF-8). The folder they sit
> in on PhysioNet, assumed to be `emr-datasets/`, has not been checked against a download.
> If your copy is a flat folder of these CSVs, point `input_dir` at a directory holding an
> `emr-datasets` link to it.

## Validation against the release

The spec was first written from the authors' release code and did not run on the published
files. Profiling the 1.0.0 EMR CSVs (value *shapes* and null counts only, never values)
showed what had to change:

- Tables are lowercase (`admission`, `labs`, ...) rather than `Patient`, `Labs`, ...; the
    keys are `aMRN` and `aCSN`, not `csID` and `CS_PAT_ENC_CSN_ID`.
- Each timestamp is one already-shifted column rather than a date plus an `HH:MM:SS`
    column. Labs and orders keep only the date, which the spec now places at 23:59:59 to
    avoid future leakage (see *Timestamps* above).
- `icu` and `location` have no department, event type, room or bed columns.
- `resulting_aCSN` holds the string `nan` for unresulted rows; cast leniently to an
    integer so `hadm_id` stays `Int64`.
- 1,574 ICD-10 codes occur as both primary and secondary diagnoses, which MEDS-Extract
    rejects when both events attach code metadata, so diagnosis `parent_codes` were dropped.
    The synthetic test now covers this collision.

On a local copy of the 1.0.0 EMR tables (4.8 GB of CSVs), `meds-extract-run` with MEDS-Extract 0.7.1
finishes in about a minute with about 3.2 GB peak memory, producing 36.5M events for 18,472
subjects. Fewer lab, order and medication events come out than raw rows because identical
events (same subject, time and code) are merged: lab and order times have day
resolution, and the medication table repeats an order's start and end on every
administration row.

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
