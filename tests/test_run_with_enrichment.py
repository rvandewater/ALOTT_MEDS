"""`suMEDS-extract spec=ALOTT` runs the ETL and then suMEDS enrichment, offline.

This package registers its rules in suMEDS's `sumeds.rules` entry-point group under the same name as
its MEDS-Extract spec, so `suMEDS-extract` finds them from `spec=ALOTT` alone. Both runs here read the
synthetic input of `e2e_synthetic_test.py`: one with plain `meds-extract-run`, one with
`suMEDS-extract`, so the enriched output can be compared with the plain one. The ontology source is a
tiny pack repository in suMEDS's `sumeds-ontologies/0.1` layout, built here with made-up titles for the
synthetic diagnosis codes and their categories, so the test reads nothing over the network and does
not depend on the published packs.
"""

import csv
import hashlib
import io
import json
import os
import subprocess
import sysconfig
from pathlib import Path

import polars as pl
import pyarrow.parquet as pq
import pytest
from e2e_synthetic_test import SPEC, write_synthetic_input
from meds import CodeMetadataSchema

# vocabulary -> release -> code -> parent (None for a category). The synthetic input's other
# diagnosis, I10, is deliberately missing, as a code suMEDS cannot validate would be, so it must
# stay unenriched.
PACKS = {
    "ICD10CM": (
        "fy2016-fy2027",
        {"I50": None, "I50.2": "I50", "I50.23": "I50.2"},
    ),
}


def title(vocabulary: str, code: str) -> str:
    return f"Synthetic {vocabulary} title {code}"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def run(command: str, *args: str) -> None:
    """Run an installed console script, resolved like `e2e_synthetic_test.py` does.

    The script dir is prepended to the child's PATH, because `suMEDS-extract` runs the `meds-extract-run` on
    PATH and the runner spawns each stage by bare name.
    """
    scripts = Path(sysconfig.get_path("scripts"))
    env = os.environ | {"PATH": f"{scripts}{os.pathsep}{os.environ.get('PATH', '')}"}
    full = [str(scripts / command), *args]
    result = subprocess.run(full, capture_output=True, check=False, env=env)
    assert result.returncode == 0, (
        f"{' '.join(full)} failed with return code {result.returncode}.\n"
        f"stdout:\n{result.stdout.decode()}\nstderr:\n{result.stderr.decode()}"
    )


@pytest.fixture(scope="session")
def raw_dir(tmp_path_factory) -> Path:
    raw = tmp_path_factory.mktemp("alott_raw")
    write_synthetic_input(raw)
    return raw


@pytest.fixture(scope="session")
def ontology_source(tmp_path_factory) -> Path:
    """A local `sumeds-ontologies/0.1` repository holding the synthetic ICD-10-CM pack."""
    root = tmp_path_factory.mktemp("ontologies")
    columns = ["code", "description", "parent", "first_year", "last_year"]
    vocabularies = {}
    for vocabulary, (release, rows) in PACKS.items():
        directory = root / vocabulary / release
        directory.mkdir(parents=True)
        by_shard: dict[str, list[list[str]]] = {}
        for code, parent in rows.items():
            by_shard.setdefault(code[:1], []).append([code, title(vocabulary, code), parent or "", "", ""])
        shards = {}
        for key, shard_rows in by_shard.items():
            buffer = io.StringIO()
            writer = csv.writer(buffer, lineterminator="\n")
            writer.writerows([columns, *shard_rows])
            data = buffer.getvalue().encode()
            (directory / f"{key}.csv").write_bytes(data)
            shards[key] = {
                "file": f"{key}.csv",
                "rows": len(shard_rows),
                "bytes": len(data),
                "sha256": _sha256(data),
            }
        manifest = {
            "schema": "sumeds-ontology-pack/0.1",
            "vocabulary": vocabulary,
            "release": release,
            "columns": columns,
            "fields": {"code": "code", "description": "description", "parent": "parent"},
            "shard_key_length": 1,
            "rows": len(rows),
            "bytes": sum(s["bytes"] for s in shards.values()),
            "shards": shards,
        }
        manifest_bytes = json.dumps(manifest).encode()
        (directory / "manifest.json").write_bytes(manifest_bytes)
        vocabularies[vocabulary] = {
            "manifest": f"{vocabulary}/{release}/manifest.json",
            "sha256": _sha256(manifest_bytes),
            "release": release,
            "rows": manifest["rows"],
            "bytes": manifest["bytes"],
        }
    index = {"schema": "sumeds-ontologies/0.1", "vocabularies": vocabularies}
    (root / "index.json").write_text(json.dumps(index))
    return root


def _etl_args(output_dir: Path, raw_dir: Path) -> list[str]:
    return [f"spec={SPEC}", f"output_dir={output_dir}", "do_download=false", f"input_dir={raw_dir}"]


@pytest.fixture(scope="session")
def plain_output(tmp_path_factory, raw_dir: Path) -> Path:
    output_dir = tmp_path_factory.mktemp("alott_plain") / "MEDS_cohort"
    run("meds-extract-run", *_etl_args(output_dir, raw_dir))
    return output_dir


@pytest.fixture(scope="session")
def enriched_output(tmp_path_factory, raw_dir: Path, ontology_source: Path) -> Path:
    output_dir = tmp_path_factory.mktemp("alott_enriched") / "MEDS_cohort"
    run("suMEDS-extract", *_etl_args(output_dir, raw_dir), "--", "--ontology-source", str(ontology_source))
    return output_dir


def _code_rows(output_dir: Path) -> dict[str, dict]:
    """`codes.parquet` by code.

    Read with pyarrow, so a names map is a list of (code, name) pairs on every Polars version.
    """
    table = pq.read_table(output_dir / "metadata" / "codes.parquet")
    return {row["code"]: row for row in table.to_pylist()}


def _events(output_dir: Path) -> pl.DataFrame:
    shards = [pl.read_parquet(fp) for fp in (output_dir / "data").rglob("*.parquet")]
    return pl.concat(shards, how="diagonal_relaxed")


def test_codes_parquet_is_replaced_by_valid_meds_metadata(enriched_output: Path):
    metadata = enriched_output / "metadata"
    CodeMetadataSchema.validate(pq.read_table(metadata / "codes.parquet"))
    assert not list(metadata.glob(".*enriched*")), "the temporary enrichment output was left behind"


def test_data_matches_the_plain_etl(enriched_output: Path, plain_output: Path):
    key = ["subject_id", "time", "code"]
    enriched, plain = _events(enriched_output), _events(plain_output)
    assert enriched.sort(key).select(key).equals(plain.sort(key).select(key))


def test_enrichment_keeps_the_plain_metadata(enriched_output: Path, plain_output: Path):
    """Enrichment only adds: every code keeps its description, and any parents it had lead the new
    list. (The plain ETL writes no `parent_codes` column at all; see the README.)"""
    enriched, plain = _code_rows(enriched_output), _code_rows(plain_output)
    assert set(enriched) == set(plain)
    for code, row in plain.items():
        if row["description"] is not None:
            assert enriched[code]["description"] == row["description"], code
        parents = row.get("parent_codes") or []
        assert (enriched[code]["parent_codes"] or [])[: len(parents)] == parents, code


def test_diagnoses_get_titles_and_parents(enriched_output: Path):
    rows = _code_rows(enriched_output)

    # The ETL writes no diagnosis description: the pack title fills it, and the vocabulary code
    # and its ancestors become the parents, each named in `parent_names`.
    diagnosis = rows["DIAGNOSIS//ICD//10//I50.23"]
    assert diagnosis["description"] == title("ICD10CM", "I50.23")
    parents = ["ICD10CM//I50.23", "ICD10CM//I50.2", "ICD10CM//I50"]
    assert diagnosis["parent_codes"] == parents
    assert dict(diagnosis["parent_names"]) == {p: title("ICD10CM", p.split("//")[1]) for p in parents}

    # A code that is not in the vocabulary, and the non-diagnosis codes, are left alone.
    assert rows["DIAGNOSIS//ICD//10//I10"]["description"] is None
    assert not rows["DIAGNOSIS//ICD//10//I10"]["parent_codes"]
    assert rows["FLOWSHEET//8"]["description"] == "Pulse"
    assert not rows["FLOWSHEET//8"]["parent_codes"]
