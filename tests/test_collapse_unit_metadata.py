"""The `alott_collapse_unit_metadata` post-stage on small synthetic `codes.parquet` tables."""

from pathlib import Path

import polars as pl
import pytest

from ALOTT_MEDS.collapse_unit_metadata import (
    MultipleUnitsError,
    add_unit_sibling_parents,
    collapse_unit_metadata,
    collapse_unit_metadata_file,
    link_unit_siblings,
    unit_sibling_stem_rows,
)

LIST_OF_STRINGS = pl.List(pl.String)


def _codes(units: list, dtype: pl.DataType = LIST_OF_STRINGS) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "code": [f"C{i}" for i in range(len(units))],
            "unit": pl.Series(units, dtype=dtype),
            "description": [f"code {i}" for i in range(len(units))],
        }
    )


def test_a_list_becomes_its_one_distinct_non_blank_value():
    codes = _codes([["mg"], ["mg", "mg"], [" mg ", "mg"], ["", "  ", None, "mL"], [None], [], None])
    out = collapse_unit_metadata(codes)
    assert out.schema["unit"] == pl.String
    assert out["unit"].to_list() == ["mg", "mg", "mg", "mL", None, None, None]
    # Column order and the other columns are kept.
    assert out.columns == codes.columns
    assert out["description"].equals(codes["description"])


def test_units_are_kept_as_recorded():
    """Only the surrounding blanks go; case, spelling and placeholder units stay."""
    out = collapse_unit_metadata(_codes([["MG"], ["Geen"], ["None"], ["mg/kg/hr"]]))
    assert out["unit"].to_list() == ["MG", "Geen", "None", "mg/kg/hr"]


def test_several_units_fail_naming_the_count_and_up_to_five_codes():
    units = [["mg"], *([["mg", "g"]] * 7), [" mL", "mg"]]
    with pytest.raises(MultipleUnitsError) as error:
        collapse_unit_metadata(_codes(units))
    assert len(error.value.codes) == 8
    message = str(error.value)
    assert message.startswith("8 codes in codes.parquet have more than one unit")
    assert sum(f"'C{i}'" in message for i in range(9)) == 5
    assert "Put the unit into the code" in message


def test_a_string_unit_passes_through_unchanged():
    codes = _codes(["mg", None, " mL "], dtype=pl.String)
    assert collapse_unit_metadata(codes) is codes
    once = collapse_unit_metadata(_codes([["mg"], ["mL"]]))
    assert collapse_unit_metadata(once).equals(once)


def test_no_unit_column_is_a_no_op():
    codes = pl.DataFrame({"code": ["A"], "description": ["a"]})
    assert collapse_unit_metadata(codes) is codes


def test_an_all_null_unit_becomes_a_null_string():
    out = collapse_unit_metadata(_codes([None, None], dtype=pl.Null))
    assert out.schema["unit"] == pl.String


def test_a_unit_of_another_type_is_refused():
    with pytest.raises(TypeError):
        collapse_unit_metadata(_codes([1, 2], dtype=pl.Int64))


def test_the_file_is_rewritten_in_place_only_when_it_changes(tmp_path: Path):
    fp = tmp_path / "codes.parquet"
    _codes([["mg"], [None]]).write_parquet(fp)
    collapse_unit_metadata_file(fp)
    assert pl.read_parquet(fp)["unit"].to_list() == ["mg", None]
    before = fp.read_bytes()
    collapse_unit_metadata_file(fp)
    assert fp.read_bytes() == before

    out = tmp_path / "out" / "codes.parquet"
    collapse_unit_metadata_file(fp, out)
    assert pl.read_parquet(out).equals(pl.read_parquet(fp))


def _parents(codes: pl.DataFrame) -> dict:
    return dict(zip(codes["code"], codes["parent_codes"].to_list(), strict=True))


def test_unit_siblings_get_their_stem_as_a_parent():
    codes = pl.DataFrame(
        {
            "code": [
                "MED//A//mg", "MED//A//g", "MED//A//UNK",  # siblings, and the unitless code
                "MED//B//mg",  # a lone unit-suffixed code
                "LAB//C//1", "LAB//C//2",  # last component is not the unit
                "MED//D//mL/h", "MED//D//mL",  # units with a slash
            ],
            "unit": ["mg", "g", None, "mg", "mg", "mg", "mL/h", "mL"],
            "parent_codes": [["ATC//X"], ["ATC//X", "MED//A"], None, None, None, None, [], None],
        }
    )  # fmt: skip
    out = add_unit_sibling_parents(codes)
    assert out.columns == codes.columns and out.schema == codes.schema
    assert _parents(out) == {
        "MED//A//mg": ["ATC//X", "MED//A"],  # vocabulary parents kept, stem appended
        "MED//A//g": ["ATC//X", "MED//A"],  # no duplicate
        "MED//A//UNK": None,
        "MED//B//mg": None,
        "LAB//C//1": None,
        "LAB//C//2": None,
        "MED//D//mL/h": ["MED//D"],
        "MED//D//mL": ["MED//D"],
    }
    # No row is added for a stem, and a second run changes nothing.
    assert out.height == codes.height
    assert add_unit_sibling_parents(out).equals(out)


def test_parent_codes_is_created_only_when_there_are_siblings():
    lone = pl.DataFrame({"code": ["X//mg", "Y//g"], "unit": ["mg", "g"]})
    assert add_unit_sibling_parents(lone) is lone
    out = add_unit_sibling_parents(pl.DataFrame({"code": ["X//mg", "X//g", "Y"], "unit": ["mg", "g", None]}))
    assert out.schema["parent_codes"] == pl.List(pl.String)
    assert _parents(out) == {"X//mg": ["X"], "X//g": ["X"], "Y": None}


def test_a_list_unit_is_left_to_the_collapse():
    codes = _codes([["mg"], ["g"]])
    assert add_unit_sibling_parents(codes) is codes


def test_a_stem_row_gets_the_shared_description_and_shared_parents():
    codes = pl.DataFrame(
        {
            "code": ["D//A//UNK", "D//A//g", "D//A//mg", "D//B//g", "D//B//mg", "D//C//mg"],
            "description": ["drug A", "drug A", "drug A", "drug B in g", "drug B in mg", "drug C"],
            "unit": [None, "g", "mg", "g", "mg", "mg"],
            "parent_codes": [
                ["ATC//Q"], ["ATC//X", "ATC//Y"], ["ATC//Y", "ATC//X", "ATC//Z"], ["ATC//X"], None, None,
            ],
            "other": [1, 2, 3, 4, 5, 6],
        }
    )  # fmt: skip
    out = link_unit_siblings(codes)
    rows = {r["code"]: r for r in out.iter_rows(named=True)}
    assert set(rows) == {*codes["code"], "D//A", "D//B"}
    # Shared description; the parents both siblings have, in the first one's order, never the stem.
    assert rows["D//A"] == {
        "code": "D//A", "description": "drug A", "unit": None,
        "parent_codes": ["ATC//X", "ATC//Y"], "other": None,
    }  # fmt: skip
    # Differing descriptions give none, and no parent shared by all siblings gives null.
    assert rows["D//B"]["description"] is None
    assert rows["D//B"]["parent_codes"] is None
    # The table stays sorted and typed; a second run adds nothing.
    assert out["code"].is_sorted()
    assert out.schema == codes.schema
    assert link_unit_siblings(out).equals(out)


def test_an_existing_stem_row_is_left_unchanged():
    codes = pl.DataFrame(
        {
            "code": ["D//A", "D//A//g", "D//A//mg"],
            "description": ["the drug", "drug A", "drug A"],
            "unit": [None, "g", "mg"],
        }
    )
    assert unit_sibling_stem_rows(codes).is_empty()
    out = link_unit_siblings(codes)
    assert out.height == 3
    assert out.row(0) == ("D//A", "the drug", None, None)


def test_the_file_step_collapses_then_links_siblings(tmp_path: Path):
    fp = tmp_path / "codes.parquet"
    pl.DataFrame(
        {"code": ["X//g", "X//mg", "Y"], "description": ["x", "x", "y"], "unit": [["g", "g"], ["mg"], None]}
    ).write_parquet(fp)
    collapse_unit_metadata_file(fp)
    out = pl.read_parquet(fp)
    assert out["code"].to_list() == ["X", "X//g", "X//mg", "Y"]
    assert out["unit"].to_list() == [None, "g", "mg", None]
    assert out["description"].to_list() == ["x", "x", "x", "y"]
    assert _parents(out) == {"X": None, "X//g": ["X"], "X//mg": ["X"], "Y": None}
    before = fp.read_bytes()
    collapse_unit_metadata_file(fp)
    assert fp.read_bytes() == before
