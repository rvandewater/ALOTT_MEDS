"""Every event block in the spec describes its codes, except the diagnosis blocks (see below).

`codes.parquet` descriptions come from `_metadata: _self:` templates in `event_configs.yaml`. This
checks the spec statically, without running the pipeline; `e2e_synthetic_test.py` checks the
resulting `codes.parquet`.
"""

import re
from pathlib import Path

import yaml

CONFIG_FP = Path(__file__).parents[1] / "src" / "ALOTT_MEDS" / "event_configs.yaml"
RESERVED = {"etl", "sources", "_defaults"}


def _events():
    cfg = yaml.safe_load(CONFIG_FP.read_text())
    for table, body in cfg.items():
        if table in RESERVED:
            continue
        for name, event in body.items():
            if not name.startswith("_"):
                yield table, name, event


def _family(code_expr: str) -> str:
    """The constant first segment of a code expression (`f"LAB//..."` -> `LAB`)."""
    return re.match(r"""^f?["']+([A-Z_]+)""", code_expr.strip()).group(1)


def test_every_family_is_described_except_diagnoses():
    """Diagnoses carry no `_metadata`: one ICD code is produced by several of the per-position blocks, and
    MEDS-Extract 0.8 rejects a code produced by two metadata-carrying templates."""
    described, undescribed = set(), set()
    for _table, _name, event in _events():
        family = _family(event["code"])
        block = event.get("_metadata", {}).get("_self", {})
        (described if "description" in block else undescribed).add(family)
    assert undescribed == {"DIAGNOSIS"}, undescribed
    assert described == {
        "HOSPITAL_ADMISSION", "HOSPITAL_DISCHARGE", "GENDER", "MARITAL_STATUS", "RACE", "ETHNICITY",
        "LANGUAGE", "BLOOD_TYPE", "ALLERGY", "FLOWSHEET", "ICU_ADMISSION", "ICU_DISCHARGE", "TRANSFER",
        "MEDICATION", "LAB", "ORDER",
    }  # fmt: skip


def test_diagnosis_blocks_carry_no_metadata():
    for table, name, event in _events():
        if _family(event["code"]) == "DIAGNOSIS":
            assert "_metadata" not in event, f"{table}/{name}"


def test_constant_codes_are_guarded_by_their_own_time_column():
    """A constant code only gets a component (and so a description) through its `if $t == $t` guard, and the
    guard must test the event's own time column so that it drops no row that had a time."""
    guarded = 0
    for table, name, event in _events():
        code = event["code"]
        if "{" in code:
            continue
        m = re.fullmatch(r"'([A-Z_]+)' if \$(\w+) == \$(\w+)", code)
        assert m, f"{table}/{name}: constant code without a guard: {code}"
        assert m.group(2) == m.group(3), code
        assert event["time"].startswith(f"${m.group(2)}::"), f"{table}/{name}: guard is not the time column"
        guarded += 1
    assert guarded == 3


def test_unit_metadata_comes_from_the_dose_unit_columns():
    """`unit` is the recorded dose unit, on exactly the two medication blocks with a numeric dose; the release
    records no unit for flowsheets, labs or orders."""
    with_unit = {
        f"{table}/{name}": event["_metadata"]["_self"]["unit"]
        for table, name, event in _events()
        if "unit" in event.get("_metadata", {}).get("_self", {})
    }
    assert with_unit == {
        "emr-datasets/medications/medication_start": "$HV_DISCRET_DOSE_UNIT",
        "emr-datasets/medications/medication_administration": "$MAR_DOSE_UNIT",
    }


def test_the_unit_is_the_last_component_of_the_codes_that_carry_one():
    """One drug is dosed in several units, so each code with `unit` metadata ends in that same column (or
    `UNK`), which gives every code one unit."""
    for table, name, event in _events():
        unit = event.get("_metadata", {}).get("_self", {}).get("unit")
        if unit is not None:
            assert event["code"].endswith(f"//{{{unit} ?? 'UNK'}}\""), f"{table}/{name}"


def test_the_unit_is_collapsed_to_one_string_before_suMEDS_enriches():
    post_stages = yaml.safe_load(CONFIG_FP.read_text())["etl"]["post_stages"]
    assert post_stages[0] == "collapse_unit_metadata"
