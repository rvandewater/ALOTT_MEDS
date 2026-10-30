"""Descriptions and ICD-10-CM parents for diagnosis codes, filled into a finished ``codes.parquet``.

The spec (``event_configs.yaml``) describes every other code. Diagnoses cannot carry ``_metadata``
in the spec: each list position of the primary and secondary ICD-10 columns is its own code
template, one code occurs under several of them, and MEDS-Extract 0.7 rejects a code produced by
two metadata-carrying templates. So this step fills them in after the run:

    meds-extract-run spec=ALOTT output_dir=$MEDS_OUTPUT_DIR ...
    alott-meds-describe-codes $MEDS_OUTPUT_DIR

It fills only values the pipeline left null and never adds codes, so it is idempotent. Once
MEDS-Extract can split a delimited cell into one event per code (tracked in mmcdermott/dftly#87),
the spec can attach this metadata itself and the step becomes a no-op.
"""

import argparse
import logging
import os
from pathlib import Path

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq
from meds import CodeMetadataSchema

logger = logging.getLogger(__name__)

DIAGNOSIS_PREFIX = "DIAGNOSIS//ICD//10//"
# A dotted ICD-10-CM code: category (letter, digit, digit or letter), optionally a dot and up to four
# more characters. OMOP's ICD10CM vocabulary keeps the dot in its concept codes.
ICD10CM_SHAPE = r"^[A-Z][0-9][0-9A-Z](\.[0-9A-Z]{1,4})?$"
DESCRIPTION = (
    "Diagnosis coded {} in the visit's ICD-10 diagnosis columns "
    "(ALOTT admission.Primary_ICD10, admission.Secondary_ICD10)"
)
MEDS_TYPES = {"code": pa.string(), "description": pa.string(), "parent_codes": pa.list_(pa.string())}


def fill_diagnosis_metadata(codes: pl.DataFrame) -> pl.DataFrame:
    """Give every diagnosis code a description, and an ``ICD10CM/`` parent where the code is ICD-10-CM.

    Only a null ``description`` and a null or empty ``parent_codes`` are filled; anything the
    pipeline wrote wins. Other codes are returned unchanged, and no row is added or removed.

    Examples:
        >>> codes = pl.DataFrame(
        ...     {"code": ["DIAGNOSIS//ICD//10//I50.23", "DIAGNOSIS//ICD//10//NOT AN ICD", "GENDER//M"],
        ...      "description": [None, None, "Patient sex/gender: M"]},
        ... )
        >>> out = fill_diagnosis_metadata(codes)
        >>> out["parent_codes"].to_list()
        [['ICD10CM/I50.23'], None, None]
        >>> out["description"][0].startswith("Diagnosis coded I50.23 in the visit's ICD-10 diagnosis columns")
        True
        >>> out["description"][2]
        'Patient sex/gender: M'
        >>> fill_diagnosis_metadata(out).equals(out)
        True
    """
    if "description" not in codes.columns:
        codes = codes.with_columns(pl.lit(None, dtype=pl.String).alias("description"))
    if "parent_codes" not in codes.columns:
        codes = codes.with_columns(pl.lit(None, dtype=pl.List(pl.String)).alias("parent_codes"))
    is_dx = pl.col("code").str.starts_with(DIAGNOSIS_PREFIX)
    icd = pl.col("code").str.strip_prefix(DIAGNOSIS_PREFIX)
    no_parents = pl.col("parent_codes").list.len().fill_null(0) == 0
    return codes.with_columns(
        description=pl.when(is_dx & pl.col("description").is_null())
        .then(pl.format(DESCRIPTION, icd))
        .otherwise(pl.col("description")),
        parent_codes=pl.when(is_dx & no_parents & icd.str.contains(ICD10CM_SHAPE))
        .then(pl.concat_list(pl.concat_str([pl.lit("ICD10CM/"), icd])))
        .otherwise(pl.col("parent_codes")),
    )


def apply_diagnosis_metadata(codes_fp: Path) -> tuple[int, int]:
    """Fill diagnosis metadata in a finished ``codes.parquet``, in place.

    The result is validated against the MEDS code-metadata schema before it atomically replaces
    the file. polars writes strings as Arrow ``large_string``, which that schema rejects, so the
    MEDS columns are cast back to ``string`` / ``list<string>``, the types the pipeline writes.

    Returns:
        The number of codes given a description, and the number given parent codes.
    """
    codes_fp = Path(codes_fp)
    codes = pl.from_arrow(pq.read_table(codes_fp))
    filled = fill_diagnosis_metadata(codes)

    def n_described(df: pl.DataFrame) -> int:
        return int(df["description"].is_not_null().sum()) if "description" in df.columns else 0

    def n_parented(df: pl.DataFrame) -> int:
        if "parent_codes" not in df.columns:
            return 0
        return int((df["parent_codes"].list.len().fill_null(0) > 0).sum())

    counts = (n_described(filled) - n_described(codes), n_parented(filled) - n_parented(codes))
    out = filled.to_arrow()
    out = out.cast(pa.schema([pa.field(f.name, MEDS_TYPES.get(f.name, f.type)) for f in out.schema]))
    CodeMetadataSchema.validate(out)
    tmp_fp = codes_fp.with_suffix(".parquet.tmp")
    pq.write_table(out, tmp_fp)
    os.replace(tmp_fp, codes_fp)
    return counts


def main(argv: list[str] | None = None) -> None:
    """``alott-meds-describe-codes``: fill diagnosis metadata after ``meds-extract-run``."""
    parser = argparse.ArgumentParser(prog="alott-meds-describe-codes", description=__doc__.splitlines()[0])
    parser.add_argument(
        "output_dir", type=Path, help="The meds-extract-run output_dir (or its metadata/codes.parquet)."
    )
    args = parser.parse_args(argv)
    codes_fp = args.output_dir
    if codes_fp.is_dir():
        codes_fp = codes_fp / "metadata" / "codes.parquet"
    if not codes_fp.is_file():
        parser.error(f"{codes_fp} not found; run meds-extract-run first.")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    n_described, n_parented = apply_diagnosis_metadata(codes_fp)
    logger.info(f"Described {n_described} and parented {n_parented} diagnosis codes in {codes_fp}.")


if __name__ == "__main__":  # pragma: no cover
    main()
