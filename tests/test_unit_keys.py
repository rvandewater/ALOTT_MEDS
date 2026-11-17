"""The `alott_unit_keys` pre-stage on small synthetic medication tables."""

import doctest
from pathlib import Path

import polars as pl

import ALOTT_MEDS.unit_keys
from ALOTT_MEDS.unit_keys import MAR_TABLE, START_TABLE, keys_with_several_units, unit_keys, write_unit_keys


def test_doctests():
    assert doctest.testmod(ALOTT_MEDS.unit_keys, optionflags=doctest.ELLIPSIS).failed == 0


def test_only_keys_with_several_units_are_listed():
    rows = pl.LazyFrame(
        {
            "drug": ["A", "A", "B", "B", "B", "C", "C", "D", None, None],
            "unit": ["mg", "g", "mg", " mg", "mg ", "mg", None, "", "mg", "g"],
        }
    )
    out = keys_with_several_units(rows, {"drug": pl.col("drug")}, "unit", "in_code")
    # B differs only in blanks around the unit, C and D have one unit or none besides a null or empty one,
    # and a null drug produces no code.
    assert out.rows() == [("A", True)]
    assert out.schema == pl.Schema({"drug": pl.String, "in_code": pl.Boolean})


def _medications() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "MEDICATION": ["A", "A", "A", "B", "B", "B", "C", "C"],
            "startOffsetDate": ["d", "d", "d", "d", "d", None, "d", "d"],
            "HV_DISCRET_DOSE_UNIT": ["mg", "g", None, "mg", "mg", "g", "mg", "mg"],
            "OffsetMAR_TAKEN_TIME": ["t", "t", "t", "t", "t", "t", "t", None],
            "MAR_ACTION": ["Given", "Given", "Held", None, None, "Given", "Given", "Given"],
            "MAR_DOSE_UNIT": ["mg", "mg", "g", "mg", "g", "mg", "mg", "g"],
        }
    )


def test_each_family_is_keyed_like_its_code_on_the_rows_that_become_events():
    tables = unit_keys(_medications().lazy())
    # B's third unit is on a row without a start time, which is no start event.
    assert tables[START_TABLE].rows() == [("A", True)]
    # MAR codes are keyed by drug and action (`UNK` for none, as in the code): A is given in one unit per
    # action; C's second unit is on a row without an administration time.
    assert tables[MAR_TABLE].rows() == [("B", "UNK", True)]
    assert tables[MAR_TABLE].columns == ["MEDICATION", "mar_action", "mar_unit_in_code"]


def test_the_side_tables_are_written_from_the_raw_csv(tmp_path: Path):
    raw = tmp_path / "raw" / "emr-datasets"
    raw.mkdir(parents=True)
    _medications().with_columns(other=pl.lit("x")).write_csv(raw / "medications.csv")
    written = write_unit_keys(tmp_path / "raw", tmp_path / "staged")
    assert set(written) == {START_TABLE, MAR_TABLE}
    assert pl.read_parquet(written[START_TABLE]).rows() == [("A", True)]
    assert pl.read_parquet(written[MAR_TABLE]).rows() == [("B", "UNK", True)]
    assert sorted(p.name for p in (tmp_path / "staged").iterdir()) == [
        f"{MAR_TABLE}.parquet",
        f"{START_TABLE}.parquet",
    ]


def test_no_code_with_several_units_gives_empty_tables(tmp_path: Path):
    raw = tmp_path / "raw" / "emr-datasets"
    raw.mkdir(parents=True)
    _medications().with_columns(HV_DISCRET_DOSE_UNIT=pl.lit("mg"), MAR_DOSE_UNIT=pl.lit("mg")).write_csv(
        raw / "medications.csv"
    )
    written = write_unit_keys(tmp_path / "raw", tmp_path / "staged")
    start, mar = pl.read_parquet(written[START_TABLE]), pl.read_parquet(written[MAR_TABLE])
    assert start.is_empty() and mar.is_empty()
    assert start.schema == pl.Schema({"MEDICATION": pl.String, "start_unit_in_code": pl.Boolean})
