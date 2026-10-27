# Releasing ALOTT-MEDS

A release is a git tag. The package version comes from the tag (setuptools-scm), so there is
no version number to edit anywhere: pushing tag `0.0.2` builds `ALOTT-MEDS 0.0.2`, publishes
it to PyPI and creates the GitHub release. The same version is stamped into every MEDS
output as `metadata/dataset.json` `dataset_version` = `<ALOTT release>:<package version>`
(e.g. `1.0.0:0.0.2`).

## Choosing the version ("bumping")

Bumping means choosing the next tag. Tags are plain [SemVer](https://semver.org) numbers
without a `v` prefix (`0.0.2`, not `v0.0.2`), which keeps them identical to the PyPI version.

| Change since the last release                                                                    | Bump  | Example         |
| ------------------------------------------------------------------------------------------------ | ----- | --------------- |
| Docs, CI, packaging, or a fix that leaves every code and its meaning unchanged                   | patch | `0.0.1 → 0.0.2` |
| Output changes: new or renamed codes, events moved in time, values parsed differently            | minor | `0.0.2 → 0.1.0` |
| `1.0.0` once the code vocabulary is stable; afterwards, any change that breaks downstream models | major | `0.4.0 → 1.0.0` |

Before `1.0.0`, a minor bump is how to flag an output change that would break a model trained
on the previous output.

The ALOTT data release is versioned separately, as `sources.dataset_version` in
[`src/ALOTT_MEDS/event_configs.yaml`](src/ALOTT_MEDS/event_configs.yaml). Change it only when
the spec targets a new PhysioNet release, and release the package with at least a minor bump.

To see the last release and what the current commit would build as:

```bash
git describe --tags --abbrev=0                      # last release tag
uvx --from setuptools-scm python -m setuptools_scm  # e.g. 0.0.3.dev2+g113c173
```

## One-time setup (before the first release)

The publish job uses PyPI
[trusted publishing](https://docs.pypi.org/trusted-publishers/), so no API token is stored in
GitHub. PyPI must know the workflow before the first upload. Until a project exists there,
register it as a **pending publisher** at https://pypi.org/manage/account/publishing/:

| Field             | Value               |
| ----------------- | ------------------- |
| PyPI project name | `ALOTT-MEDS`        |
| Owner             | `rvandewater`       |
| Repository name   | `ALOTT_MEDS`        |
| Workflow name     | `python-build.yaml` |
| Environment name  | `pypi`              |

GitHub creates the `pypi` environment the first time the job runs. To require a manual
approval before anything reaches PyPI, create it beforehand under *Settings → Environments*
and add yourself as a required reviewer.

## Releasing

1. Make sure `main` holds everything to release and its checks are green (Tests, Synthetic
    ETL, Code Quality Main).

2. If the spec changed, run it over the full ALOTT EMR tables (aggregate checks only, see the
    README) and update the figures in the README's *Validation against the release*.

3. Tag the release commit on `main` and push the tag:

    ```bash
    git switch main && git pull --ff-only
    git tag -a 0.0.2 -m "ALOTT-MEDS 0.0.2"
    git push origin 0.0.2
    ```

    Do not create the GitHub release by hand; the workflow creates it and fails if it already
    exists.

4. The tag push runs [`python-build.yaml`](.github/workflows/python-build.yaml). Follow it
    with `gh run watch`. It:

    - builds the wheel and sdist from the full git history;
    - fails unless the wheel's version is exactly the tag (so a dev version can never be
        published);
    - publishes to PyPI;
    - signs the dists with Sigstore and attaches them to a new GitHub release named after the
        tag.

5. Check the result:

    ```bash
    uv run --no-project --with ALOTT-MEDS==0.0.2 python -c \
        "import importlib.metadata as m; print(m.version('ALOTT-MEDS'))"
    ```

6. First release only: if the repository is connected to Zenodo, the GitHub release mints a
    DOI. Replace the `zenodo.XXXXXXX` placeholders in the README badge with it.

## When something goes wrong

- **The build fails on a tag push** (e.g. the version check): nothing was published, because
    PyPI upload and the GitHub release only run after a successful build. Delete the tag, fix
    `main`, and tag again:

    ```bash
    git push --delete origin 0.0.2 && git tag -d 0.0.2
    ```

- **PyPI rejects the upload** with `invalid-publisher`: the trusted publisher above is missing
    or a field differs (repository, workflow file name, environment). Fix it on PyPI and
    re-run the failed job; the tag can stay.

- **A wrong release was published**: PyPI never accepts the same version twice, even after
    deleting it, and a published tag may already be archived (Zenodo) or installed. Leave the
    tag in place, release the fix as the next patch, and if needed
    [yank](https://pypi.org/help/#yanked) the bad version on PyPI.

Never move or re-create a tag that reached PyPI or a GitHub release.
