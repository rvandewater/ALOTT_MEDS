"""The ``alott_unit_metadata`` stage: links unit siblings in ``metadata/codes.parquet``.

MEDS-Extract (0.9 and later) writes ``unit`` as one nullable String per code: each value stripped, blanks
dropped, and a code with more than one distinct unit an error. Such a code should carry the unit as its
last code component, as the ``MEDICATION//START`` and ``MEDICATION//MAR`` codes with several units do in
this spec (see ``ALOTT_MEDS.unit_keys``). This stage no longer collapses ``unit``; a table without
``unit`` is not touched.

It links unit siblings: a code is unit-suffixed when its ``unit`` is set and the code ends in
``//<unit>``, and its stem is the code without that suffix. Where two or more unit-suffixed codes share a
stem (one drug ordered in ``mg`` and in ``g``, say), each gets the stem as an in-dataset parent in
``parent_codes``, after any parents it already has, and a stem without a row of its own gets one: the
siblings' shared description, the parents every sibling has, and no unit.

The spec declares it before suMEDS enrichment, so that suMEDS records the coverage of the final table and
a later ``suMEDS-enrich`` gives the same result as the run's own enrichment::

    etl:
      post_stages:
        - alott_unit_metadata
        - sumeds_enrich: { rules: ALOTT }

It is registered in MEDS-Transforms' ``MEDS_transforms.stages`` entry-point group (see ``pyproject.toml``),
and ``python -m ALOTT_MEDS.unit_metadata OUTPUT_DIR`` runs it on an existing build.
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

STAGE_NAME = "alott_unit_metadata"
UNIT = "unit"
PARENTS = "parent_codes"


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


def unit_metadata_file(codes_in: Path, codes_out: Path | None = None) -> None:
    """Link the unit siblings in the ``codes.parquet`` at ``codes_in`` (parents and stem rows), writing to
    ``codes_out`` (default: in place). ``unit`` is written as an Arrow ``string``.

    A table that needs no change is not rewritten in place.
    """
    codes_out = codes_in if codes_out is None else codes_out
    table = pq.read_table(codes_in)
    codes = pl.from_arrow(table)
    linked = add_unit_sibling_parents(codes)
    stems = unit_sibling_stem_rows(linked)
    if codes_out == codes_in and stems.is_empty() and linked.equals(codes) and linked.schema == codes.schema:
        logger.info(f"{codes_in}: unit siblings need no change.")
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
        logger.info(f"{codes_out}: {linked[UNIT].count()} codes have a `{UNIT}`.")


def _main(cfg: Any) -> None:
    """Link the unit siblings in the finished cohort's ``metadata/codes.parquet`` (a post-stage reads and
    writes ``metadata/`` in place)."""
    if getattr(cfg, "worker", 0) != 0:
        return
    import meds

    name = Path(meds.code_metadata_filepath).name
    stage_cfg = cfg.stage_cfg
    unit_metadata_file(Path(stage_cfg.metadata_input_dir) / name, Path(stage_cfg.reducer_output_dir) / name)


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
    unit_metadata_file(Path(sys.argv[1]) / meds.code_metadata_filepath)
