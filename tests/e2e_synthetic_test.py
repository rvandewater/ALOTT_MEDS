"""End-to-end run of the shipped ETL over a tiny, fully synthetic ALOTT-shaped input.

This distribution ships no Python — it is `event_configs.yaml` plus the entry-point registration that
names it `ALOTT`. So there is exactly one thing worth testing: that `meds-extract-run spec=ALOTT`
resolves that registration, runs the pipeline over raw tables laid out the way the spec expects, and
leaves a well-formed MEDS cohort behind. Driving it through the registered name rather than a path is
deliberate: it makes this test cover the registration too.

ALOTT has no public demo release, so the input is generated here: every table the spec reads, with
the columns it reads, filled with made-up values. Nothing in it is derived from the real dataset. It
checks the spec's *mechanics* (column references, date+time recombination, parsing), not that the
published release still matches the spec — only a run over the real files can do that.
"""

import json
import os
import subprocess
import sys
import sysconfig
from pathlib import Path
from tempfile import TemporaryDirectory

import polars as pl

SPEC = "ALOTT"
RELEASE = "1.0.0"
N_SUBJECTS = 10


def _visit_rows(subject: int) -> dict:
    """The two IDs and an admission-day date every per-visit table hangs off."""
    return {"aMRN": subject, "aCSN": 1000 + subject, "day": f"2020-01-{subject + 1:02d}"}


def write_synthetic_input(root: Path) -> None:
    emr = root / "emr-datasets"
    emr.mkdir(parents=True)
    visits = [_visit_rows(s) for s in range(1, N_SUBJECTS + 1)]
    ids = {"aMRN": [v["aMRN"] for v in visits], "aCSN": [v["aCSN"] for v in visits]}
    days = [v["day"] for v in visits]
    csns = ids["aCSN"]
    n = len(visits)

    def at(hms: str) -> list[str]:
        return [f"{d} {hms}" for d in days]

    tables = {
        "admission": {
            **ids,
            "admsnOffset": at("08:00:00"),
            "dischOffset": at("20:00:00"),
            "Admission_Type": ["EMERGENCY"] * n,
            "Admit_Source": ["HOME", None] * (n // 2),
            "LOS_Hours": [12] * n,
            "REASON_VISIT_NAME": ["CHEST PAIN"] * n,
            "Discharge_Destination": ["HOME"] * n,
            "Primary_ICD10": ["I50.23"] * n,
            "Primary_Diagnosis": ["Acute on chronic systolic heart failure"] * n,
            "Secondary_ICD10": ["I50.23", None] * (n // 2),
            "Secondary_Diagnosis": ["Presence of prosthetic heart valve", None] * (n // 2),
        },
        "demographics": {
            **ids,
            "Gender": ["M", "F"] * (n // 2),
            "Marital_Status": ["Single"] * n,
            "Patient_Race": ["Other", None] * (n // 2),
            "Ethnicity": ["Unknown"] * n,
            "Preferred_Language": ["English"] * n,
            "ABO_RH_Blood_Type": ["O Pos"] * n,
        },
        "allergy": {**ids, "Allergy": ["PENICILLIN"] * n, "Allergy_Reaction": ["Rash"] * n},
        "flowsheet": {
            **ids,
            "FLO_MEAS_ID": ["8"] * n,
            "flo_meas_name": ["PULSE"] * n,
            "disp_name": ["Pulse"] * n,
            "MEAS_VALUE": ["72", "irregular"] * (n // 2),
            "MEAS_COMMENT": [None] * n,
            "recordedOffsetDate": at("09:00:00"),
            "aFSD": list(range(n)),
            "Flow_Type": ["Vitals"] * n,
        },
        "icu": {**ids, "startOffsetDate": at("10:00:00"), "endOffsetDate": at("18:00:00"), "LOS": [480] * n},
        "location": {
            **ids,
            "startOffsetDate": at("08:30:00"),
            "endOffsetDate": at("10:00:00"),
            "LOS": [90] * n,
        },
        "medications": {
            **ids,
            "MEDICATION": ["FUROSEMIDE 40 MG TAB"] * n,
            "orderingOffsetDate": at("10:45:00"),
            "startOffsetDate": at("11:00:00"),
            "endOffsetDate": [None, *at("19:00:00")[1:]],
            "ORDERING_MODE": ["Inpatient"] * n,
            "HV_DISCRETE_DOSE": ["40"] * n,
            "HV_DISCRET_DOSE_UNIT": ["mg"] * n,
            "FREQUENCY": ["DAILY"] * n,
            "MAR_DOSE": ["40"] * n,
            "MAR_DOSE_UNIT": ["mg"] * n,
            "OffsetMAR_TAKEN_TIME": at("11:05:00"),
            "MAR_ACTION": ["Given"] * n,
            "MAR_ROUTE": ["Oral"] * n,
        },
        # Unresulted lab/order rows carry the literal string `nan` as their resulting encounter.
        "labs": {
            "aMRN": ids["aMRN"],
            "Order_type": ["Lab"] * n,
            "DESCRIPTION": ["BASIC METABOLIC PANEL"] * n,
            "PROC_ID": ["101"] * n,
            "COMPONENT_NAME": ["SODIUM"] * n,
            "ORDER_STATUS_C": [5] * n,
            "order_status": ["Completed"] * n,
            "orderingOffsetDate": days,
            "resultsOffsetDate": [None, *days[1:]],
            "ORD_NUM_VALUE": ["140", "9999999"] * (n // 2),
            "ORD_VALUE": ["140", "see comment"] * (n // 2),
            "ordering_aCSN": csns,
            "resulting_aCSN": ["nan", *map(str, csns[1:])],
        },
        "orders": {
            "aMRN": ids["aMRN"],
            "ordering_aCSN": csns,
            "resulting_aCSN": ["nan"] * n,
            "Order_type": ["Imaging"] * n,
            "DESCRIPTION": ["XR CHEST 1 VIEW"] * n,
            "PROC_ID": ["202"] * n,
            "ORDER_STATUS_C": [5] * n,
            "order_status": ["Completed"] * n,
            "orderingOffsetDate": days,
            "resultsOffsetDate": [None] * n,
            "ORD_NUM_VALUE": [None] * n,
            "ORD_VALUE": [None] * n,
        },
    }
    for name, cols in tables.items():
        pl.DataFrame(cols).write_csv(emr / f"{name}.csv")


def test_e2e_synthetic():
    # Everything resolves against `sysconfig`'s script dir — the one `activate` puts on PATH — rather
    # than the ambient PATH, so the test behaves the same under `uv run pytest` and a bare `pytest`. The
    # dir is also *prepended* to the child's PATH, because the runner spawns each stage by bare name.
    scripts = Path(sysconfig.get_path("scripts"))
    runner = scripts / "meds-extract-run"
    assert runner.exists(), f"meds-extract-run is not installed in this environment ({sys.executable})."
    env = os.environ | {"PATH": f"{scripts}{os.pathsep}{os.environ.get('PATH', '')}"}

    with TemporaryDirectory() as temp_dir:
        raw = Path(temp_dir) / "raw"
        write_synthetic_input(raw)
        out = Path(temp_dir) / "MEDS_cohort"

        command = [
            str(runner),
            f"spec={SPEC}",
            f"output_dir={out.resolve()!s}",
            "do_download=false",
            f"input_dir={raw.resolve()!s}",
        ]
        result = subprocess.run(command, capture_output=True, check=False, env=env)
        assert result.returncode == 0, (
            f"{' '.join(command)} failed with return code {result.returncode}.\n"
            f"stdout:\n{result.stdout.decode()}\nstderr:\n{result.stderr.decode()}"
        )

        data_files = list((out / "data").glob("**/*.parquet"))
        assert data_files, f"No data shards written; {out / 'data'} holds {list((out / 'data').rglob('*'))}"

        metadata = out / "metadata"
        for fname in ("dataset.json", "codes.parquet", "subject_splits.parquet"):
            assert (metadata / fname).exists(), f"{fname} missing from {metadata}"

        dataset_json = json.loads((metadata / "dataset.json").read_text())
        assert dataset_json["dataset_name"] == SPEC
        assert dataset_json["dataset_version"].startswith(f"{RELEASE}:")

        data = pl.concat([pl.read_parquet(fp) for fp in data_files], how="diagonal_relaxed")
        assert data["subject_id"].n_unique() == N_SUBJECTS

        # Every table in the spec must have produced at least one event.
        prefixes = data["code"].str.split("//").list.first().unique().to_list()
        expected = {
            "HOSPITAL_ADMISSION", "HOSPITAL_DISCHARGE", "DIAGNOSIS", "GENDER", "ALLERGY", "FLOWSHEET",
            "ICU_ADMISSION", "ICU_DISCHARGE", "TRANSFER", "MEDICATION", "LAB", "ORDER",
        }  # fmt: skip
        assert expected <= set(prefixes), f"missing event families: {expected - set(prefixes)}"

        # Full timestamps keep their time of day.
        admits = data.filter(pl.col("code").str.starts_with("HOSPITAL_ADMISSION"))
        assert (admits["time"].dt.hour() == 8).all()

        # Date-only lab and order times sit at the end of the day, never before the event happened.
        dated = data.filter(
            pl.col("code").str.starts_with("LAB//") | pl.col("code").str.starts_with("ORDER//")
        )
        assert (dated["time"].dt.strftime("%H:%M:%S") == "23:59:59").all()

        # Free-text values keep their numeric reading only where they have one. Epic uses
        # ORD_NUM_VALUE=9999999 when a lab result is non-numeric; that sentinel must stay null.
        flows = data.filter(pl.col("code") == "FLOWSHEET//8")
        assert flows["numeric_value"].null_count() == N_SUBJECTS // 2
        labs = data.filter(pl.col("code") == "LAB//RESULT//101//SODIUM")
        assert labs.filter(pl.col("text_value") == "see comment")["numeric_value"].is_null().all()

        # The `nan` resulting-encounter sentinel becomes a null, so `hadm_id` stays an integer.
        assert data.schema["hadm_id"] == pl.Int64
        assert labs["hadm_id"].null_count() == 0
