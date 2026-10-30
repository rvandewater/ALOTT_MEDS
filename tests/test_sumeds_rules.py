"""The packaged suMEDS rules parse, and their patterns match the code layouts of the spec.

The example codes are built from the code templates in ``event_configs.yaml`` with synthetic
(public example) values, so a change to a code layout that the rules no longer match fails here.
"""

import re
import tomllib
from importlib.resources import files
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
RULES_FP = ROOT / "src" / "ALOTT_MEDS" / "sumeds_rules.yaml"
CONFIG_FP = ROOT / "src" / "ALOTT_MEDS" / "event_configs.yaml"

RULE_KINDS = {"schema", "ontology", "crosswalk", "hierarchy"}
ONTOLOGY_KEYS = {"id", "pattern", "vocabulary", "normalize", "validate"}
HIERARCHY_KEYS = {"id", "pattern", "group", "separator", "mode", "ignore", "nearest_only"}
FROM_ONTOLOGY_KEYS = {"id", "from_ontology", "rules", "nearest_only"}
VALIDATE_MODES = {"pack", "athena", "none"}

# Synthetic cell values: public example ICD-10-CM codes, including a four-code list.
ICD10_CELLS = ["I50.23, E11.9, N18.3, Z79.4", "S72.001A", "I10", "U07.1, J12.82"]
# The vocabulary code each diagnosis example resolves to through `alott-icd10cm-diagnoses`.
EXPECTED_DIAGNOSES = {
    f"DIAGNOSIS//ICD//10//{code}": code
    for code in ["I50.23", "E11.9", "N18.3", "Z79.4", "S72.001A", "I10", "U07.1", "J12.82"]
}
# Field of a code template: `{$col}`, `{$col ?? 'DEFAULT'}` or `{extract group N of /re/ from $col}`.
FIELD = re.compile(r"\{(?:extract group (\d+) of /(.+?)/ from )?\$(\w+)(?: \?\? '([^']*)')?\}")


def load_rules() -> dict:
    return yaml.safe_load(RULES_FP.read_text())


def code_templates() -> list[str]:
    cfg = yaml.safe_load(CONFIG_FP.read_text())
    templates = []
    for table, events in cfg.items():
        if table.startswith("_") or not isinstance(events, dict):
            continue
        templates += [
            str(e["code"]) for name, e in events.items() if not name.startswith("_") and isinstance(e, dict)
        ]
    return templates


def render(template: str, value: str) -> str | None:
    """Render a spec code expression with every column set to ``value``; None if it yields no code."""
    head = template.split(" if ")[0].strip()
    if not head.startswith('f"'):
        return head.strip("'\"")
    missing = False

    def field(match: re.Match) -> str:
        nonlocal missing
        group, regex, _, default = match.groups()
        if regex is None:
            return value
        found = re.search(regex, value)
        if found is None:
            missing = default is None
            return default or ""
        return found.group(int(group))

    code = FIELD.sub(field, head[2:-1])
    return None if missing else code


def example_codes() -> set[str]:
    codes = {render(t, v) for t in code_templates() for v in [*ICD10_CELLS, "X"]}
    return codes - {None}


def test_rules_file_is_packaged():
    package_data = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["setuptools"]["package-data"]
    assert "sumeds_rules.yaml" in package_data["ALOTT_MEDS"]
    assert files("ALOTT_MEDS").joinpath("sumeds_rules.yaml").is_file()


def test_rules_file_parses_and_is_well_formed():
    rules = load_rules()
    assert set(rules) <= RULE_KINDS
    assert rules["schema"] == "sumeds-rules/0.1"
    ids = [r["id"] for kind in ("ontology", "crosswalk", "hierarchy") for r in rules.get(kind) or []]
    assert len(ids) == len(set(ids)), "duplicate rule ids"

    ontology_ids = set()
    for rule in rules["ontology"]:
        assert set(rule) <= ONTOLOGY_KEYS and {"id", "pattern", "vocabulary"} <= set(rule), rule["id"]
        assert "code" in re.compile(rule["pattern"]).groupindex, rule["id"]
        assert rule["pattern"].startswith("^") and rule["pattern"].endswith("$"), rule["id"]
        assert rule.get("validate", "pack") in VALIDATE_MODES, rule["id"]
        assert rule["vocabulary"].strip() and "/" not in rule["vocabulary"], rule["id"]
        for step in rule.get("normalize") or []:
            assert set(step) == {"pattern", "replace"}
            re.compile(step["pattern"])
        ontology_ids.add(rule["id"])

    for rule in rules["hierarchy"]:
        if rule.get("from_ontology"):
            assert set(rule) <= FROM_ONTOLOGY_KEYS, rule["id"]
            assert set(rule.get("rules") or []) <= ontology_ids, rule["id"]
            continue
        assert set(rule) <= HIERARCHY_KEYS, rule["id"]
        pattern = re.compile(rule["pattern"])
        assert rule["group"] in pattern.groupindex, rule["id"]
        assert (rule.get("mode") == "prefix") != ("separator" in rule), rule["id"]


def test_diagnosis_rule_matches_every_diagnosis_template():
    (rule,) = load_rules()["ontology"]
    pattern = re.compile(rule["pattern"])
    examples = example_codes()
    assert set(EXPECTED_DIAGNOSES) <= examples, set(EXPECTED_DIAGNOSES) - examples

    matched = {code: m.group("code") for code in examples if (m := pattern.search(code))}
    assert matched == EXPECTED_DIAGNOSES
    # Every diagnosis template yields a code the rule matches; no other family is matched.
    diagnosis_templates = [t for t in code_templates() if t.startswith('f"DIAGNOSIS//')]
    assert len(diagnosis_templates) == 8
    for template in diagnosis_templates:
        assert pattern.search(render(template, ICD10_CELLS[0])), template
    assert not pattern.search("DIAGNOSIS//ICD//10//X")
