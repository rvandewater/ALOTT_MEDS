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
    MEDS-Extract 0.7 rejects a code produced by two metadata-carrying templates."""
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
