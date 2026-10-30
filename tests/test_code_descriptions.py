"""The post-pipeline diagnosis step: fills only what the pipeline left null, and keeps valid MEDS metadata."""

from pathlib import Path

import polars as pl
import pyarrow.parquet as pq
from meds import CodeMetadataSchema

from ALOTT_MEDS.code_descriptions import apply_diagnosis_metadata, main


def _write(fp: Path) -> None:
    pl.DataFrame(
        {
            "code": [
                "DIAGNOSIS//ICD//10//I10",
                "DIAGNOSIS//ICD//10//E11.9",
                "DIAGNOSIS//ICD//10//not-icd",
                "DIAGNOSIS//ICD//10//R65.20",
                "GENDER//F",
            ],
            "description": [None, None, None, "kept as extracted", "Patient sex/gender: F"],
            "parent_codes": [None, [], None, ["ICD10CM/R65.20"], None],
            "code_template": ["DIAGNOSIS//ICD//10//{...}"] * 4 + ["GENDER//{Gender}"],
        },
        schema_overrides={"parent_codes": pl.List(pl.String)},
    ).write_parquet(fp)


def test_fills_only_missing_and_validates(tmp_path: Path):
    fp = tmp_path / "codes.parquet"
    _write(fp)
    assert apply_diagnosis_metadata(fp) == (3, 2)
    table = pq.read_table(fp)
    CodeMetadataSchema.validate(table)
    out = {r["code"]: r for r in pl.from_arrow(table).iter_rows(named=True)}
    assert out["DIAGNOSIS//ICD//10//I10"]["parent_codes"] == ["ICD10CM/I10"]
    assert out["DIAGNOSIS//ICD//10//E11.9"]["parent_codes"] == ["ICD10CM/E11.9"]
    assert out["DIAGNOSIS//ICD//10//not-icd"]["parent_codes"] is None
    assert out["DIAGNOSIS//ICD//10//not-icd"]["description"].startswith("Diagnosis coded not-icd ")
    assert out["DIAGNOSIS//ICD//10//R65.20"]["description"] == "kept as extracted"
    assert out["GENDER//F"]["parent_codes"] is None
    assert out["GENDER//F"]["description"] == "Patient sex/gender: F"
    assert pl.from_arrow(table).height == 5


def test_idempotent_and_cli(tmp_path: Path):
    out_dir = tmp_path / "MEDS"
    (out_dir / "metadata").mkdir(parents=True)
    fp = out_dir / "metadata" / "codes.parquet"
    _write(fp)
    main([str(out_dir)])
    once = pl.read_parquet(fp)
    assert apply_diagnosis_metadata(fp) == (0, 0)
    assert pl.read_parquet(fp).equals(once)
