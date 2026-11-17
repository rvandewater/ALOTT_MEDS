"""The ``collapse_unit_metadata`` post-stage: one ``unit`` String per code in ``metadata/codes.parquet``.

MEDS-Extract writes every ``_metadata`` column as ``List(String)``, the distinct values seen for a code. A
code has one unit, so after the ETL this stage turns ``unit`` into a nullable String: each entry is
stripped, empty and null entries are dropped, and the distinct rest must be at most one value (none gives a
null). A code with more than one unit is an error, not a silent choice: the code family should carry the
unit as its last code component, as ``MEDICATION//START`` and ``MEDICATION//MAR`` do in this spec. Units
are kept as recorded otherwise. A ``unit`` that is already a String is left as it is, and a table without
``unit`` is not touched.

It then links unit siblings: a code is unit-suffixed when its ``unit`` is set and the code ends in
``//<unit>``, and its stem is the code without that suffix. Where two or more unit-suffixed codes share a
stem (one drug ordered in ``mg`` and in ``g``, say), each gets the stem as an in-dataset parent in
``parent_codes``, after any parents it already has, and a stem without a row of its own gets one: the
siblings' shared description, the parents every sibling has, and no unit.

The spec declares it before suMEDS enrichment, so that suMEDS records the coverage of the final table and
a later ``suMEDS-enrich`` gives the same result as the run's own enrichment::

    etl:
      post_stages:
        - collapse_unit_metadata
        - sumeds_enrich: { rules: ALOTT }

It is registered in MEDS-Transforms' ``MEDS_transforms.stages`` entry-point group (see ``pyproject.toml``),
and ``python -m ALOTT_MEDS.collapse_unit_metadata OUTPUT_DIR`` runs it on an existing build.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq

logger = logging.getLogger(__name__)

STAGE_NAME = "collapse_unit_metadata"
UNIT = "unit"
PARENTS = "parent_codes"
#: How many codes with several units the error names.
N_EXAMPLES = 5


class MultipleUnitsError(ValueError):
    """Some codes in ``codes.parquet`` have more than one unit.

    Attributes:
        codes: Every code with more than one unit.
    """

    def __init__(self, codes: list[str]):
        self.codes = codes
        super().__init__(
            f"{len(codes)} codes in codes.parquet have more than one unit, e.g. {codes[:N_EXAMPLES]}. "
            "Put the unit into the code (as its last component) for that code family, so that each code "
            "has one unit."
        )


def collapse_unit_metadata(codes: pl.DataFrame) -> pl.DataFrame:
    """Return ``codes`` with its ``unit`` column as one nullable String per code.

    Args:
        codes: A ``codes.parquet`` table. ``unit``, if present, is a List of Strings or a String.

    Returns:
        The same table, with ``unit`` in the same place. A List ``unit`` becomes its single distinct
        non-blank (stripped) value, or null if it has none.

    Raises:
        MultipleUnitsError: If a code has more than one distinct unit.
        TypeError: If ``unit`` is neither a String nor a List.

    Examples:
        >>> codes = pl.DataFrame({"code": ["A", "B", "C"], "unit": [["mg", " mg"], [None, ""], None]})
        >>> collapse_unit_metadata(codes)["unit"].to_list()
        ['mg', None, None]
        >>> collapse_unit_metadata(pl.DataFrame({"code": ["A"], "unit": [["mg", "g"]]}))
        Traceback (most recent call last):
            ...
        ALOTT_MEDS.collapse_unit_metadata.MultipleUnitsError: 1 codes in codes.parquet have more than one...
    """
    if UNIT not in codes.columns:
        return codes
    dtype = codes.schema[UNIT]
    if dtype == pl.String:
        return codes
    if dtype == pl.Null:
        return codes.with_columns(pl.col(UNIT).cast(pl.String))
    if not isinstance(dtype, pl.List):
        raise TypeError(f"codes.parquet `{UNIT}` is {dtype}, expected a List of Strings or a String.")

    distinct = (
        pl.col(UNIT)
        .cast(pl.List(pl.String))
        .list.eval(pl.element().str.strip_chars().filter(pl.element().str.strip_chars() != "").unique())
    )
    codes = codes.with_columns(distinct)
    several = codes.filter(pl.col(UNIT).list.len() > 1)["code"].sort().to_list()
    if several:
        raise MultipleUnitsError(several)
    return codes.with_columns(pl.col(UNIT).list.first())


def _sibling_stems(codes: pl.DataFrame) -> pl.Series | None:
    """Each code's stem if it is in a unit-sibling group, else null; ``None`` if there is no group."""
    if UNIT not in codes.columns or codes.schema[UNIT] != pl.String:
        return None
    unit = pl.col(UNIT)
    stems = codes.select(
        pl.when(unit.is_not_null() & pl.col("code").str.ends_with(pl.lit("//") + unit))
        .then(pl.col("code").str.head(pl.col("code").str.len_chars() - unit.str.len_chars() - 2))
        .alias("_stem")
    )["_stem"]
    stems = pl.select(pl.when(stems.is_duplicated()).then(stems)).to_series().alias("_stem")
    return None if stems.null_count() == len(stems) else stems


def add_unit_sibling_parents(codes: pl.DataFrame) -> pl.DataFrame:
    """Give each unit-sibling code its code without the unit as a parent in ``parent_codes``.

    Args:
        codes: A ``codes.parquet`` table whose ``unit``, if present, is one String per code.

    Returns:
        The same rows, with the stem appended to the ``parent_codes`` of every code in a sibling group
        (unless it is already listed). ``parent_codes`` is created as ``List(String)`` if it is missing and
        a sibling group exists. Codes outside sibling groups are unchanged.

    Examples:
        >>> codes = pl.DataFrame({
        ...     "code": ["D//A//mg", "D//A//g", "D//B//mg", "D//C//UNK"],
        ...     "unit": ["mg", "g", "mg", None],
        ... })
        >>> add_unit_sibling_parents(codes)["parent_codes"].to_list()
        [['D//A'], ['D//A'], None, None]
    """
    stems = _sibling_stems(codes)
    if stems is None:
        return codes
    if PARENTS not in codes.columns or codes.schema[PARENTS] == pl.Null:
        codes = codes.with_columns(pl.lit(None, dtype=pl.List(pl.String)).alias(PARENTS))
    columns = codes.columns
    parents = pl.col(PARENTS).fill_null(pl.lit([], dtype=pl.List(pl.String)))
    add = pl.col("_stem").is_not_null() & ~parents.list.contains(pl.col("_stem"))
    codes = codes.with_columns(stems).with_columns(add.alias("_add"))
    logger.info(
        f"{stems.n_unique() - 1} groups of unit-sibling codes; {codes['_add'].sum()} codes get their stem "
        "as a parent."
    )
    return codes.with_columns(
        pl.when("_add").then(parents.list.concat(pl.col("_stem"))).otherwise(pl.col(PARENTS)).alias(PARENTS)
    ).select(columns)


def unit_sibling_stem_rows(codes: pl.DataFrame) -> pl.DataFrame:
    """The ``codes.parquet`` rows to add for the stems of unit-sibling groups that have no row yet.

    A stem row has the siblings' ``description`` if they all share one (else null), the ``parent_codes``
    every sibling has apart from the stem itself (in the first sibling's order; null if none) and a null
    ``unit``.

    Args:
        codes: A ``codes.parquet`` table whose ``unit``, if present, is one String per code.

    Returns:
        A frame with ``code``, ``description``, ``parent_codes`` and ``unit``, one row per new stem, sorted
        by code; empty if there is none.

    Examples:
        >>> codes = pl.DataFrame({
        ...     "code": ["D//A//mg", "D//A//g", "D//B//mg", "D//B//g", "D//B"],
        ...     "description": ["drug A", "drug A", "drug B", "drug B", "drug B itself"],
        ...     "unit": ["mg", "g", "mg", "g", None],
        ...     "parent_codes": [["ATC//X", "ATC//Y"], ["ATC//Y", "ATC//X"], None, None, None],
        ... })
        >>> unit_sibling_stem_rows(codes).rows()
        [('D//A', 'drug A', ['ATC//X', 'ATC//Y'], None)]
    """
    schema = {"code": pl.String, "description": pl.String, PARENTS: pl.List(pl.String), UNIT: pl.String}
    stems = _sibling_stems(codes)
    if stems is None:
        return pl.DataFrame(schema=schema)
    description = pl.col("description") if "description" in codes.columns else pl.lit(None, pl.String)
    parents = pl.col(PARENTS) if PARENTS in codes.columns else pl.lit(None, pl.List(pl.String))
    groups = (
        codes.with_columns(stems)
        .filter(pl.col("_stem").is_not_null() & ~pl.col("_stem").is_in(codes["code"].implode()))
        .group_by("_stem", maintain_order=True)
        .agg(
            description.cast(pl.String).alias("descriptions"),
            parents.cast(pl.List(pl.String)).alias("parents"),
        )
    )
    rows = []
    for stem, descriptions, parent_lists in groups.iter_rows():
        shared = descriptions[0] if descriptions[0] is not None and len(set(descriptions)) == 1 else None
        common = [p for p in dict.fromkeys(parent_lists[0] or []) if p != stem]
        for other in parent_lists[1:]:
            common = [p for p in common if p in set(other or [])]
        rows.append((stem, shared, common or None, None))
    return pl.DataFrame(rows, schema=schema, orient="row").sort("code")


def link_unit_siblings(codes: pl.DataFrame) -> pl.DataFrame:
    """:func:`add_unit_sibling_parents`, plus a row for each new stem (:func:`unit_sibling_stem_rows`).

    The table stays sorted by ``code`` if it was. Running it again changes nothing.
    """
    linked = add_unit_sibling_parents(codes)
    stems = unit_sibling_stem_rows(linked)
    if stems.is_empty():
        return linked
    out = pl.concat(
        [linked, stems.select(c for c in stems.columns if c in linked.columns)], how="diagonal_relaxed"
    )
    return out.sort("code") if codes["code"].is_sorted() else out


def collapse_unit_metadata_file(codes_in: Path, codes_out: Path | None = None) -> None:
    """Collapse ``unit`` in the ``codes.parquet`` at ``codes_in`` and link its unit siblings (parents and stem
    rows), writing to ``codes_out`` (default: in place).

    A table that needs no change is not rewritten in place.
    """
    codes_out = codes_in if codes_out is None else codes_out
    table = pq.read_table(codes_in)
    codes = pl.from_arrow(table)
    linked = add_unit_sibling_parents(collapse_unit_metadata(codes))
    stems = unit_sibling_stem_rows(linked)
    if codes_out == codes_in and stems.is_empty() and linked.equals(codes) and linked.schema == codes.schema:
        logger.info(f"{codes_in}: `{UNIT}` and unit siblings need no change.")
        return
    # Only `unit` and `parent_codes` are replaced, and the stem rows appended; every other column keeps
    # its Arrow type as written (a polars round trip would widen `string` to `large_string`, which the MEDS
    # schema rejects for `code`, and cannot write suMEDS's map columns back).
    for name, dtype in ((UNIT, pa.string()), (PARENTS, pa.list_(pa.string()))):
        if name not in linked.columns:
            continue
        if name in table.schema.names:
            if name == PARENTS and not pa.types.is_null(table.schema.field(name).type):
                dtype = table.schema.field(name).type
            column = linked[name].to_arrow().cast(dtype)
            table = table.set_column(table.schema.get_field_index(name), name, column)
        else:
            table = table.append_column(name, linked[name].to_arrow().cast(dtype))
    if not stems.is_empty():
        new_rows = pa.table(
            {
                f.name: (
                    stems[f.name].to_arrow().cast(f.type)
                    if f.name in stems.columns
                    else pa.nulls(stems.height, f.type)
                )
                for f in table.schema
            },
            schema=table.schema,
        )
        sorted_before = codes["code"].is_sorted()
        table = pa.concat_tables([table, new_rows])
        if sorted_before:
            table = table.sort_by("code")
        logger.info(f"{stems.height} stem rows added for unit-sibling groups.")
    codes_out.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, codes_out)
    if UNIT in linked.columns:
        logger.info(f"{codes_out}: `{UNIT}` is one String per code ({linked[UNIT].count()} codes have one).")


def _main(cfg: Any) -> None:
    """Collapse ``unit`` in the finished cohort's ``metadata/codes.parquet`` (a post-stage reads and writes
    ``metadata/`` in place)."""
    if getattr(cfg, "worker", 0) != 0:
        return
    import meds

    name = Path(meds.code_metadata_filepath).name
    stage_cfg = cfg.stage_cfg
    collapse_unit_metadata_file(
        Path(stage_cfg.metadata_input_dir) / name, Path(stage_cfg.reducer_output_dir) / name
    )


try:
    from MEDS_transforms.stages import Stage
except ModuleNotFoundError as error:  # pragma: no cover - MEDS-Transforms comes with MEDS-Extract
    if error.name != "MEDS_transforms":
        raise
    main = _main
else:
    main = Stage.register(main_fn=_main, stage_name=STAGE_NAME, is_metadata=True)


if __name__ == "__main__":  # pragma: no cover - exercised by hand on an existing build
    import meds

    if len(sys.argv) != 2:
        sys.exit(f"usage: python -m {__spec__.name} MEDS_ROOT")
    logging.basicConfig(level=logging.INFO)
    collapse_unit_metadata_file(Path(sys.argv[1]) / meds.code_metadata_filepath)
