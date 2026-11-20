"""The ``alott_unit_keys`` pre-stage: which medication codes need their dose unit in the code.

``MEDICATION//START//{MEDICATION}`` holds an ordered dose, whose unit is the row's ``HV_DISCRET_DOSE_UNIT``,
and ``MEDICATION//MAR//{MEDICATION}//{MAR_ACTION}`` a given dose, whose unit is the row's ``MAR_DOSE_UNIT``.
Most of these codes have one unit, but some drugs are ordered or given in several. Only the codes whose rows
have more than one unit get the unit as their last component (``...//{unit}``, ``//UNK`` on their rows
without one), so that each code has one unit; every other code stays as it was.

Which codes those are depends on the data, so this stage writes them as two side tables that the
``emr-datasets/medications`` block of ``event_configs.yaml`` joins:

- ``alott_start_unit_in_code``: the drugs (``MEDICATION``) whose start rows have more than one unit, each
  with ``start_unit_in_code`` true;
- ``alott_mar_unit_in_code``: the drug and action pairs (``MEDICATION``, ``mar_action``: ``MAR_ACTION``,
  ``UNK`` where null, as in the code) whose administration rows have more than one unit, each with
  ``mar_unit_in_code`` true.

The rows compared are the ones that become events (a drug and the event's time are present), because
``unit`` in ``codes.parquet`` is read off the events. Units are compared as MEDS-Extract compares them
when it writes a code's one unit: stripped, blanks and nulls left out. No drug is
named in the spec: the tables are written from the data on every run.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import polars as pl

logger = logging.getLogger(__name__)

STAGE_NAME = "alott_unit_keys"
MEDICATIONS = "emr-datasets/medications"
START_TABLE = "alott_start_unit_in_code"
MAR_TABLE = "alott_mar_unit_in_code"


def keys_with_several_units(
    rows: pl.LazyFrame, keys: dict[str, pl.Expr], unit: str, flag: str
) -> pl.DataFrame:
    """The distinct ``keys`` whose rows have more than one unit, each with ``flag`` true.

    Args:
        rows: The rows that become events of one code family.
        keys: The side table's key columns, by name, as computed from ``rows`` (the code's components).
        unit: The column holding each row's unit.
        flag: The name of the side table's flag column.

    Returns:
        One row per such key, sorted, with the key columns and ``flag``.

    Examples:
        >>> rows = pl.LazyFrame({
        ...     "drug": ["A", "A", "A", "B", "B", "C", "C", None, None],
        ...     "unit": ["mg", " mg", None, "mg", "g", "mg", "", "mg", "g"],
        ... })
        >>> keys_with_several_units(rows, {"drug": pl.col("drug")}, "unit", "in_code").rows()
        [('B', True)]
    """
    stripped = pl.col(unit).str.strip_chars()
    return (
        rows.select(*(expr.alias(name) for name, expr in keys.items()), stripped.alias("_unit"))
        .filter(pl.all_horizontal(pl.col(name).is_not_null() for name in keys))
        .filter(pl.col("_unit").is_not_null() & (pl.col("_unit") != ""))
        .group_by(*keys)
        .agg(pl.col("_unit").n_unique().alias("_n"))
        .filter(pl.col("_n") > 1)
        .select(*keys, pl.lit(True).alias(flag))
        .sort(*keys)
        .collect()
    )


def unit_keys(medications: pl.LazyFrame) -> dict[str, pl.DataFrame]:
    """The two side tables, by name, for the ``emr-datasets/medications`` rows ``medications``.

    Examples:
        >>> medications = pl.LazyFrame({
        ...     "MEDICATION": ["A", "A", "B", "B", "B"],
        ...     "startOffsetDate": ["d", "d", "d", "d", None],
        ...     "HV_DISCRET_DOSE_UNIT": ["mg", "g", "mg", "mg", "g"],
        ...     "OffsetMAR_TAKEN_TIME": ["t", "t", "t", "t", "t"],
        ...     "MAR_ACTION": ["Given", "Given", None, None, "Given"],
        ...     "MAR_DOSE_UNIT": ["mg", "mg", "mg", "g", "g"],
        ... })
        >>> tables = unit_keys(medications)
        >>> tables["alott_start_unit_in_code"].rows()
        [('A', True)]
        >>> tables["alott_mar_unit_in_code"].rows()
        [('B', 'UNK', True)]
    """
    drug = pl.col("MEDICATION")
    start = medications.filter(pl.col("startOffsetDate").is_not_null())
    mar = medications.filter(pl.col("OffsetMAR_TAKEN_TIME").is_not_null())
    return {
        START_TABLE: keys_with_several_units(
            start, {"MEDICATION": drug}, "HV_DISCRET_DOSE_UNIT", "start_unit_in_code"
        ),
        MAR_TABLE: keys_with_several_units(
            mar,
            {"MEDICATION": drug, "mar_action": pl.col("MAR_ACTION").fill_null("UNK")},
            "MAR_DOSE_UNIT",
            "mar_unit_in_code",
        ),
    }


def write_unit_keys(input_dir: Path, output_dir: Path) -> dict[str, Path]:
    """Write the side tables of the release in ``input_dir`` to ``output_dir``.

    Returns:
        The written files, by side table name, each replaced atomically.
    """
    from MEDS_extract.io import resolve_source_files, scan_source

    columns = [
        "MEDICATION",
        "startOffsetDate",
        "HV_DISCRET_DOSE_UNIT",
        "OffsetMAR_TAKEN_TIME",
        "MAR_ACTION",
        "MAR_DOSE_UNIT",
    ]
    fps = resolve_source_files(Path(input_dir), MEDICATIONS)
    csv = all(fp.name.lower().endswith((".csv", ".csv.gz")) for fp in fps)
    medications = scan_source(fps, **({"infer_schema": False} if csv else {})).select(
        pl.col(c).cast(pl.String) for c in columns
    )
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    written = {}
    for name, table in unit_keys(medications).items():
        out = output_dir / f"{name}.parquet"
        tmp = out.with_name(f".{out.name}.tmp")
        table.write_parquet(tmp)
        os.replace(tmp, out)
        written[name] = out
        logger.info(f"{name}: {table.height} codes have more than one unit; they end in it.")
    return written


def _main(cfg: Any) -> None:
    """Write the side tables of the release in ``input_dir`` (worker 0)."""
    if getattr(cfg, "worker", 0) != 0:
        return
    write_unit_keys(Path(cfg.input_dir), Path(cfg.stage_cfg.output_dir))


try:
    from MEDS_transforms.stages import Stage
except ModuleNotFoundError as error:  # pragma: no cover - MEDS-Transforms comes with MEDS-Extract
    if error.name != "MEDS_transforms":
        raise
    main = _main
else:
    main = Stage.register(main_fn=_main, stage_name=STAGE_NAME, is_metadata=False, default_config={})
