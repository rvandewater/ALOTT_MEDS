"""The ALOTT MEDS ETL — a configuration package, not a library.

This distribution ships no code. Its content is ``ALOTT_MEDS/event_configs.yaml``, a MESSY
(MEDS-Extract Specification Syntax YAML) document declaring where the raw ALOTT EMR tables come from
and how every event is extracted from them, and ``ALOTT_MEDS/sumeds_rules.yaml``, the suMEDS rules that
enrich the output's code metadata. ``pyproject.toml`` registers both under the name ``ALOTT``: the spec
in MEDS-Extract's ``MEDS_extract.pipelines`` entry-point group and the rules in suMEDS's ``sumeds.rules``
group. The spec declares suMEDS's ``sumeds_enrich`` post-stage with these rules, so the ETL and the
enrichment run as one command::

    meds-extract-run spec=ALOTT output_dir=$MEDS_OUTPUT_DIR

and the plain ETL with ``skip_stages=[sumeds_enrich]`` added.

Two small modules are the spec's unit stages, registered in MEDS-Transforms' ``MEDS_transforms.stages``
entry-point group. ``ALOTT_MEDS.unit_keys`` is its pre-stage: it finds the medication codes whose rows have
more than one dose unit, which therefore end in the unit. ``ALOTT_MEDS.collapse_unit_metadata`` is its first
post-stage: it makes ``unit`` in ``metadata/codes.parquet`` one String per code (MEDS-Extract writes it as a
list) and gives codes that differ only in their unit the code without it as a parent.

Otherwise this package exists so the YAML files are addressable as package data —
``importlib.resources.files("ALOTT_MEDS")`` — from an installed wheel.
"""
