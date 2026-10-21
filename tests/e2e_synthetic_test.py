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
    return {"csID": subject, "CS_PAT_ENC_CSN_ID": 1000 + subject, "day": f"2020-01-{subject + 1:02d}"}


def write_synthetic_input(root: Path) -> None:
    emr = root / "emr-datasets"
    emr.mkdir(parents=True)
    visits = [_visit_rows(s) for s in range(1, N_SUBJECTS + 1)]
    ids = {"csID": [v["csID"] for v in visits], "CS_PAT_ENC_CSN_ID": [v["CS_PAT_ENC_CSN_ID"] for v in visits]}
    days = [v["day"] for v in visits]
    csns = ids["CS_PAT_ENC_CSN_ID"]
    n = len(visits)

    tables = {
        "Patient": {
            **ids,
            "Admission_Type": ["EMERGENCY"] * n,
            "Admit_Source": ["HOME", None] * (n // 2),
            "LOS_Hours": ["48"] * n,
            "REASON_VISIT_NAME": ["CHEST PAIN"] * n,
            "offsetHospAdmsnDate": days,
            "offsetHospAdmsnTime": ["08:00:00"] * n,
            "offsetHospDischDate": days,
            "offsetHospDischTime": ["20:00:00"] * n,
            "Discharge_Destination": ["HOME"] * n,
            "Primary_ICD10": ["I50.23"] * n,
            "Primary_Diagnosis": ["Acute on chronic systolic heart failure"] * n,
            "Secondary_ICD10": ["Z95.2", None] * (n // 2),
            "Secondary_Diagnosis": ["Presence of prosthetic heart valve", None] * (n // 2),
        },
        "Demographics": {
            **ids,
            "Gender": ["M", "F"] * (n // 2),
            "Age": [None] * n,
            "Marital_Status": ["Single"] * n,
            "Patient_Race": ["Other", None] * (n // 2),
            "Ethnicity": ["Unknown"] * n,
            "Preferred_Language": ["English"] * n,
            "ABO_RH_Blood_Type": ["O Pos"] * n,
        },
        "Allergies": {**ids, "Allergy": ["PENICILLIN"] * n, "Allergy_Reaction": ["Rash"] * n},
        "Flowsheet": {
            **ids,
            "FLO_MEAS_ID": ["8"] * n,
            "flo_meas_name": ["PULSE"] * n,
            "disp_name": ["Pulse"] * n,
            "MEAS_VALUE": ["72", "irregular"] * (n // 2),
            "MEAS_COMMENT": [None] * n,
            "offsetDate": days,
            "offsetTime": ["09:00:00"] * n,
            "fsd_id": list(range(n)),
            "Flow_Type": ["Vitals"] * n,
        },
        "ICU": {
            **ids,
            "event_id": list(range(n)),
            "IN_Event_Type": ["Transfer In"] * n,
            "DEPARTMENT_ID": [1] * n,
            "DEPARTMENT_NAME": ["CARDIAC ICU"] * n,
            "offsetStartDate": days,
            "offsetStartTime": ["10:00:00"] * n,
            "offsetEndDate": days,
            "offsetEndTime": ["18:00:00"] * n,
            "OUT_Event_Type": ["Transfer Out"] * n,
            "LOS": [8] * n,
        },
        "Location": {
            **ids,
            "event_id": list(range(n)),
            "IN_Event_Type": ["Admission"] * n,
            "DEPARTMENT_ID": [2] * n,
            "DEPARTMENT_NAME": ["WARD A"] * n,
            "BED_ID": ["B1"] * n,
            "ROOM_ID": ["R1"] * n,
            "offsetStartDate": days,
            "offsetStartTime": ["08:30:00"] * n,
            "offsetEndDate": days,
            "offsetEndTime": ["10:00:00"] * n,
            "OUT_Event_Type": ["Transfer Out"] * n,
            "LOS": [2] * n,
        },
        "Medications": {
            **ids,
            "MEDICATION": ["FUROSEMIDE 40 MG TAB"] * n,
            "offsetOrderingDate": days,
            "offsetStartDate": days,
            "offsetStartTime": ["11:00:00"] * n,
            "offsetEndDate": days,
            "offsetEndTime": [None, "19:00:00"] * (n // 2),
            "ORDERING_MODE": ["Inpatient"] * n,
            "HV_DISCRETE_DOSE": ["40"] * n,
            "HV_DISCRET_DOSE_UNIT": ["mg"] * n,
            "FREQUENCY": ["DAILY"] * n,
            "MAR_DOSE": ["40"] * n,
            "MAR_DOSE_UNIT": ["mg"] * n,
            "MAR_TAKEN_TIME": [None] * n,
            "MAR_ACTION": ["Given"] * n,
            "MAR_ROUTE": ["Oral"] * n,
        },
        "Labs": {
            "csID": ids["csID"],
            "Order_type": ["Lab"] * n,
            "DESCRIPTION": ["BASIC METABOLIC PANEL"] * n,
            "PROC_ID": ["101"] * n,
            "COMPONENT_NAME": ["SODIUM"] * n,
            "ORDER_STATUS_C": [5] * n,
            "order_status": ["Completed"] * n,
            "offsetOrderDate": days,
            "offsetOrderTime": ["09:30:00"] * n,
            "offsetResultDate": days,
            "offsetResultTime": ["10:30:00"] * n,
            "ORD_NUM_VALUE": ["140", "9999999"] * (n // 2),
            "ORD_VALUE": ["140", "see comment"] * (n // 2),
            "COMPONENT_COMMENT": [None] * n,
            "Ordering_CSN": csns,
            "Resulting_CSN": csns,
        },
        "Orders": {
            "csID": ids["csID"],
            "Order_type": ["Imaging"] * n,
            "DESCRIPTION": ["XR CHEST 1 VIEW"] * n,
            "PROC_ID": ["202"] * n,
            "ORDER_STATUS_C": [5] * n,
            "order_status": ["Completed"] * n,
            "offsetOrderDate": days,
            "offsetOrderTime": ["12:00:00"] * n,
            "offsetResultDate": days,
            "offsetResultTime": ["13:00:00"] * n,
            "ORD_NUM_VALUE": [None] * n,
            "ORD_VALUE": [None] * n,
            "COMPONENT_COMMENT": [None] * n,
            "Ordering_CSN": csns,
            "Resulting_CSN": csns,
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
            "ICU_ADMISSION", "ICU_DISCHARGE", "TRANSFER_TO", "MEDICATION", "LAB", "ORDER",
        }  # fmt: skip
        assert expected <= set(prefixes), f"missing event families: {expected - set(prefixes)}"

        # Date and time columns are recombined, not just the date parsed.
        admits = data.filter(pl.col("code").str.starts_with("HOSPITAL_ADMISSION"))
        assert (admits["time"].dt.hour() == 8).all()

        # Free-text flowsheet values keep their numeric reading only where they have one.
        flows = data.filter(pl.col("code") == "FLOWSHEET//8")
        assert flows["numeric_value"].null_count() == N_SUBJECTS // 2

        codes = pl.read_parquet(metadata / "codes.parquet")
        dx = codes.filter(pl.col("code") == "DIAGNOSIS//ICD//10//I50.23")
        assert dx["parent_codes"].to_list() == [["ICD10CM/I50.23"]]
