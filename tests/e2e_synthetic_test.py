"""End-to-end run of the shipped ETL over a tiny, fully synthetic ALOTT-shaped input.

This distribution is `event_configs.yaml`, the entry-point registration that names it `ALOTT`, and
one post-run step, `alott-meds-describe-codes`, that fills in diagnosis metadata. So this tests that
`meds-extract-run spec=ALOTT` resolves that registration, runs the pipeline over raw tables laid out
the way the spec expects, and leaves a well-formed MEDS cohort behind, which the step then
completes. Driving it through the registered name rather than a path is deliberate: it makes this
test cover the registration too.

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
import pyarrow.parquet as pq
from meds import CodeMetadataSchema

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
            # Some cells list several codes; each becomes its own diagnosis event.
            "Primary_ICD10": ["I50.23", "I50.23, I10"] * (n // 2),
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
            # Scored scale items are charted as `<score>--><label>`.
            "MEAS_VALUE": ["72", "irregular", "-1-->Drowsy", "98.6", "2-->Some help"] * (n // 5),
            "MEAS_COMMENT": [None] * n,
            "recordedOffsetDate": at("09:00:00"),
            "aFSD": list(range(n)),
            "Flow_Type": ["Vitals"] * n,
        },
        # One stay without a start time: it must yield no ICU_ADMISSION, and every other row one.
        "icu": {
            **ids,
            "startOffsetDate": [None, *at("10:00:00")[1:]],
            "endOffsetDate": at("18:00:00"),
            "LOS": [480] * n,
        },
        "location": {
            **ids,
            "startOffsetDate": [None, *at("08:30:00")[1:]],
            "endOffsetDate": at("10:00:00"),
            "LOS": [90] * n,
        },
        "medications": {
            **ids,
            "MEDICATION": ["FUROSEMIDE 40 MG TAB"] * n,
            # Order start and end are datetimes in form but always midnight in the release.
            "orderingOffsetDate": at("00:00:00"),
            "startOffsetDate": at("00:00:00"),
            "endOffsetDate": [None, *at("00:00:00")[1:]],
            "ORDERING_MODE": ["Inpatient"] * n,
            "HV_DISCRETE_DOSE": ["40", "1-2"] * (n // 2),
            "HV_DISCRET_DOSE_UNIT": ["mg"] * n,
            "FREQUENCY": ["DAILY"] * n,
            "MAR_DOSE": ["40"] * n,
            "MAR_DOSE_UNIT": ["mg"] * n,
            "OffsetMAR_TAKEN_TIME": at("11:05:00"),
            "MAR_ACTION": ["Given", None] * (n // 2),
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
            "resulting_aCSN": ["nan", *map(str, csns[1:])],
            "Order_type": ["Point of Care"] * n,
            "DESCRIPTION": ["POC GLUCOSE"] * n,
            "PROC_ID": ["202"] * n,
            "ORDER_STATUS_C": [5] * n,
            "order_status": ["Completed"] * n,
            "orderingOffsetDate": days,
            "resultsOffsetDate": [None, *days[1:]],
            "ORD_NUM_VALUE": [None, *["9999999", "105"] * (n // 2)][:n],
            "ORD_VALUE": [None, *["see comment", "105"] * (n // 2)][:n],
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

        # Date-only lab, order and medication start/stop times sit at the end of the day, never
        # before the event happened. Administrations keep their time of day.
        dated = data.filter(pl.col("code").str.contains(r"^(LAB|ORDER|MEDICATION//(START|STOP))//"))
        assert dated.height and (dated["time"].dt.strftime("%H:%M:%S") == "23:59:59").all()
        mar = data.filter(pl.col("code").str.starts_with("MEDICATION//MAR//"))
        assert mar.height == N_SUBJECTS and (mar["time"].dt.strftime("%H:%M") == "11:05").all()
        assert mar["code"].str.ends_with("//UNK").sum() == N_SUBJECTS // 2

        # Each code in a listed diagnosis cell is its own event.
        primary = data.filter(pl.col("diagnosis_rank") == "PRIMARY")
        assert sorted(primary["code"].unique()) == ["DIAGNOSIS//ICD//10//I10", "DIAGNOSIS//ICD//10//I50.23"]

        # Order results are extracted with the same sentinels as labs.
        results = data.filter(pl.col("code").str.starts_with("ORDER//RESULT//"))
        assert results.height == N_SUBJECTS - 1
        assert results.filter(pl.col("text_value") == "see comment")["numeric_value"].is_null().all()
        assert (results["numeric_value"].drop_nulls() == 105).all()

        # Free-text values keep their numeric reading only where they have one. Epic uses
        # ORD_NUM_VALUE=9999999 when a lab result is non-numeric; that sentinel must stay null.
        flows = data.filter(pl.col("code") == "FLOWSHEET//8")
        assert flows["numeric_value"].null_count() == N_SUBJECTS // 5
        scored = flows.filter(pl.col("text_value").str.contains("-->"))
        assert sorted(scored["numeric_value"]) == [-1, -1, 2, 2]
        starts = data.filter(pl.col("code").str.starts_with("MEDICATION//START//"))
        assert starts.filter(pl.col("numeric_value").is_null())["text_value"].unique().to_list() == ["1-2"]
        labs = data.filter(pl.col("code") == "LAB//RESULT//101//SODIUM")
        assert labs.filter(pl.col("text_value") == "see comment")["numeric_value"].is_null().all()

        # The `nan` resulting-encounter sentinel becomes a null, so `hadm_id` stays an integer.
        assert data.schema["hadm_id"] == pl.Int64
        assert labs["hadm_id"].null_count() == 0

        # The time-column guard that lets the constant codes carry a description drops exactly the
        # rows without a time (which produce no event anyway) and no other.
        counts = {c: (data["code"] == c).sum() for c in ("ICU_ADMISSION", "ICU_DISCHARGE", "TRANSFER")}
        assert counts == {
            "ICU_ADMISSION": N_SUBJECTS - 1,
            "ICU_DISCHARGE": N_SUBJECTS,
            "TRANSFER": N_SUBJECTS - 1,
        }

        # codes.parquet is valid MEDS code metadata as written, lists every code in the data, and
        # describes every code except diagnoses (which carry no `_metadata`; see the spec).
        codes_tbl = pq.read_table(metadata / "codes.parquet")
        CodeMetadataSchema.validate(codes_tbl)
        codes = pl.from_arrow(codes_tbl)
        assert set(data["code"].unique()) == set(codes["code"])
        undescribed = codes.filter(pl.col("description").is_null())["code"]
        assert undescribed.str.starts_with("DIAGNOSIS//").all(), undescribed.to_list()
        described = dict(zip(codes["code"], codes["description"], strict=True))
        assert described["GENDER//M"] == "Patient sex/gender: M (ALOTT demographics.Gender)"
        assert described["HOSPITAL_ADMISSION//EMERGENCY//UNK"] == (
            "Hospital admission; admission type EMERGENCY (ALOTT admission.Admission_Type), "
            "admitted from UNK (admission.Admit_Source)"
        )
        assert described["MEDICATION//MAR//FUROSEMIDE 40 MG TAB//Given"].startswith(
            "Medication administration record (MAR) entry: FUROSEMIDE 40 MG TAB"
        )
        assert described["TRANSFER"].startswith("Start of a bed stay")
        # Descriptions the release's own display names already supplied are unchanged.
        assert described["FLOWSHEET//8"] == "Pulse"
        assert described["LAB//RESULT//101//SODIUM"] == "BASIC METABOLIC PANEL - SODIUM"

        # The post-run step describes every diagnosis code, gives each an `ICD10CM/` parent, keeps
        # every other code's metadata, and leaves valid MEDS code metadata behind.
        describe = scripts / "alott-meds-describe-codes"
        assert describe.exists(), f"alott-meds-describe-codes is not installed ({sys.executable})."
        result = subprocess.run([str(describe), str(out)], capture_output=True, check=False, env=env)
        assert result.returncode == 0, result.stderr.decode()
        after_tbl = pq.read_table(metadata / "codes.parquet")
        CodeMetadataSchema.validate(after_tbl)
        after = pl.from_arrow(after_tbl)
        assert after["description"].null_count() == 0
        dx = after.filter(pl.col("code").str.starts_with("DIAGNOSIS//"))
        assert dx["parent_codes"].to_list() == [
            [c.replace("DIAGNOSIS//ICD//10//", "ICD10CM/")] for c in dx["code"]
        ]
        others = after.filter(~pl.col("code").str.starts_with("DIAGNOSIS//")).sort("code")
        assert others.select("code", "description").equals(
            codes.filter(~pl.col("code").str.starts_with("DIAGNOSIS//"))
            .sort("code")
            .select("code", "description")
        )
