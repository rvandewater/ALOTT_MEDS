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


def test_constant_codes_are_plain_literals():
    """The three constant codes are plain literals (no `if $t == $t` guard): MEDS-Extract >= 0.8.1 attaches
    `_self` metadata to literal codes (mmcdermott/MEDS_extract#166), so each still carries its description."""
    literals = 0
    for table, name, event in _events():
        code = event["code"]
        if "{" in code:
            continue
        assert re.fullmatch(r"'([A-Z_]+)'", code), (
            f"{table}/{name}: constant code is not a plain literal: {code}"
        )
        assert event["_metadata"]["_self"]["description"], (
            f"{table}/{name}: constant code without a description"
        )
        literals += 1
    assert literals == 3


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


def test_only_codes_flagged_by_the_pre_stage_end_in_their_unit():
    """Each code with `unit` metadata ends in that same column (or `UNK`) only where the `mixed_unit_keys`
    side table flags it, so that a code with one unit keeps its layout and a code with several gets one per
    unit."""
    cfg = yaml.safe_load(CONFIG_FP.read_text())
    table_cols = cfg["emr-datasets/medications"]["_table"]["cols"]
    for table, name, event in _events():
        unit = event.get("_metadata", {}).get("_self", {}).get("unit")
        if unit is None:
            continue
        suffix = re.search(r"\{\$(_\w+_unit)\}\"$", event["code"])
        assert suffix, f"{table}/{name}: {event['code']}"
        assert re.fullmatch(
            rf"""\(f"//\{{\{unit} \?\? 'UNK'\}}" if \$\w+_unit_in_code else ""\)""",
            table_cols[suffix.group(1)],
        ), table_cols[suffix.group(1)]


def test_the_pre_stage_stages_the_side_tables_the_spec_joins():
    cfg = yaml.safe_load(CONFIG_FP.read_text())
    [stage] = cfg["etl"]["pre_stages"]
    side_tables = stage["mixed_unit_keys"]["side_tables"]
    assert {s["table"] for s in side_tables.values()} == {"emr-datasets/medications"}
    assert set(cfg["emr-datasets/medications"]["_table"]["joins"]) == set(side_tables)


def test_unit_siblings_are_linked_before_suMEDS_enriches():
    post_stages = yaml.safe_load(CONFIG_FP.read_text())["etl"]["post_stages"]
    assert post_stages[0] == "sumeds_link_unit_siblings"
