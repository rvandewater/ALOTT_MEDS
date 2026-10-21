"""The ALOTT MEDS ETL — a configuration package, not a library.

This distribution ships no code. Its entire content is ``ALOTT_MEDS/event_configs.yaml``, a MESSY
(MEDS-Extract Specification Syntax YAML) document declaring where the raw ALOTT EMR tables come from
and how every event is extracted from them. ``pyproject.toml`` registers that file with MEDS-Extract's
``MEDS_extract.pipelines`` entry-point group under the name ``ALOTT``, so the ETL runs as::

    meds-extract-run spec=ALOTT output_dir=$MEDS_OUTPUT_DIR

This package exists so the YAML is addressable as package data — ``importlib.resources.files("ALOTT_MEDS")``
— from an installed wheel. Nothing here is importable API; deliberately, there is nothing to import.
"""
