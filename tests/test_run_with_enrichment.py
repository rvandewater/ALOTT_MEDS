"""`meds-extract-run spec=ALOTT` runs the ETL and then suMEDS enrichment, offline.

The spec declares suMEDS's `sumeds_enrich` post-stage with the rules this package registers in
suMEDS's `sumeds.rules` entry-point group under the same name, so the one command also enriches the
output. The tests are suMEDS's shared enrichment contract (`sumeds.testing.contracts`), run over the
synthetic input of `e2e_synthetic_test.py`: once with `skip_stages=[sumeds_enrich]`, the plain ETL,
and once with the stage, so the enriched output can be compared with the plain one. The ontology
source is a tiny pack repository in suMEDS's `sumeds-ontologies/0.1` layout, written by
`sumeds.testing.write_ontology_source` with made-up titles for the synthetic diagnosis codes and
their categories, so the tests read nothing over the network and do not depend on the published
packs. Further runs point at an empty pack source, which is how a site without internet fails: the
ETL still succeeds, unenriched, and `suMEDS-enrich` completes it later; with `strict` it fails.
"""

from pathlib import Path
from typing import ClassVar

import pytest
from e2e_synthetic_test import SPEC, write_synthetic_input
from sumeds.testing import synthetic_title
from sumeds.testing.contracts import EnrichmentContract

DIAGNOSIS = "DIAGNOSIS//ICD//10CM//I50.23"


@pytest.fixture(scope="session")
def raw_dir(tmp_path_factory) -> Path:
    raw = tmp_path_factory.mktemp("alott_raw")
    write_synthetic_input(raw)
    return raw


class TestEnrichment(EnrichmentContract):
    rules_name = SPEC
    # vocabulary -> (release, code -> parent; None for a category). The synthetic input's other
    # diagnosis, I10, is deliberately missing, as a code suMEDS cannot validate would be, so it must
    # stay unenriched.
    packs: ClassVar = {"ICD10CM": ("fy2016-fy2027", {"I50": None, "I50.2": "I50", "I50.23": "I50.2"})}
    # The ETL writes no diagnosis description: the pack title fills it, and the vocabulary code and
    # its ancestors become the parents, each named in `parent_names`.
    expected_parents: ClassVar = {DIAGNOSIS: ["ICD10CM//I50.23", "ICD10CM//I50.2", "ICD10CM//I50"]}
    expected_descriptions: ClassVar = {
        DIAGNOSIS: synthetic_title("ICD10CM", "I50.23"),
        "DIAGNOSIS//ICD//10CM//I10": None,
        "FLOWSHEET//8": "Pulse",
    }
    # A code that is not in the vocabulary, and the non-diagnosis codes, are left alone.
    unenriched_codes: ClassVar = ["DIAGNOSIS//ICD//10CM//I10", "FLOWSHEET//8"]
