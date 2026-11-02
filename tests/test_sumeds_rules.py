"""The packaged suMEDS rules load in suMEDS and match the code layouts of the spec.

suMEDS validates the rules file itself (``sumeds.load_rules``) and resolves codes with the rule objects its
enrichment uses, so these tests only state what ALOTT's codes must resolve to. The example codes are built
from the code templates in ``event_configs.yaml`` with synthetic (public example) values, so a change to a
code layout that the rules no longer match fails here. The diagnosis rule is validated against suMEDS's
ontology data, which is fetched over the network, so its enrichment is not run here.
"""

import re
import tomllib
from importlib.resources import files
from pathlib import Path

import yaml
from sumeds import load_rules

ROOT = Path(__file__).parents[1]
RULES_FP = ROOT / "src" / "ALOTT_MEDS" / "sumeds_rules.yaml"
CONFIG_FP = ROOT / "src" / "ALOTT_MEDS" / "event_configs.yaml"

# Synthetic cell values: public example ICD-10-CM codes, including a four-code list.
ICD10_CELLS = ["I50.23, E11.9, N18.3, Z79.4", "S72.001A", "I10", "U07.1, J12.82"]
# The vocabulary code each diagnosis example resolves to through `alott-icd10cm-diagnoses`.
EXPECTED_DIAGNOSES = {
    f"DIAGNOSIS//ICD//10//{code}": code
    for code in ["I50.23", "E11.9", "N18.3", "Z79.4", "S72.001A", "I10", "U07.1", "J12.82"]
}
# Field of a code template: `{$col}`, `{$col ?? 'DEFAULT'}` or `{extract group N of /re/ from $col}`.
FIELD = re.compile(r"\{(?:extract group (\d+) of /(.+?)/ from )?\$(\w+)(?: \?\? '([^']*)')?\}")


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


def test_rules_load_in_sumeds():
    """Loading fails on any rule suMEDS rejects; each pattern must also span a whole code."""
    rules = load_rules(RULES_FP)
    for rule in (*rules.vocabulary_rules, *rules.hierarchy):
        if rule.pattern is not None:
            assert rule.pattern.pattern.startswith("^"), rule.id
            assert rule.pattern.pattern.endswith("$"), rule.id


def test_diagnosis_rule_matches_every_diagnosis_template():
    rules = load_rules(RULES_FP)
    assert not rules.crosswalk
    (rule,) = rules.ontology
    examples = example_codes()
    assert set(EXPECTED_DIAGNOSES) <= examples, set(EXPECTED_DIAGNOSES) - examples

    resolved = {code: value for code in examples if (value := rule.resolve(code)) is not None}
    assert resolved == EXPECTED_DIAGNOSES
    # Every diagnosis template yields a code the rule resolves; no other family is matched.
    diagnosis_templates = [t for t in code_templates() if t.startswith('f"DIAGNOSIS//')]
    assert len(diagnosis_templates) == 8
    for template in diagnosis_templates:
        assert rule.resolve(render(template, ICD10_CELLS[0])), template
    assert rule.resolve("DIAGNOSIS//ICD//10//X") is None


def test_diagnosis_parents_come_from_the_diagnosis_rule():
    """Same-dataset parents are the ICD-10-CM ancestors of the validated diagnosis codes."""
    rules = load_rules(RULES_FP)
    (hierarchy,) = rules.hierarchy
    assert hierarchy.id == "alott-diagnosis-parents-in-dataset"
    assert hierarchy.mode == "ontology"
    assert hierarchy.rules == tuple(rule.id for rule in rules.ontology)
