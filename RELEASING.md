# Releasing ALOTT-MEDS

The version lives in `pyproject.toml` and always holds the **last released** version. A release
bumps it with `uv version --bump`, commits the bump, and tags that commit with the version it
was just bumped to. The tag is read back from `pyproject.toml`, never typed, so it cannot name
the wrong version; the publish workflow also refuses any tag that differs from
`pyproject.toml`. Pushing the tag publishes to PyPI and creates the GitHub release.

The version is stamped into every MEDS output as `metadata/dataset.json` `dataset_version` =
`<ALOTT release>:<package version>` (e.g. `1.0.0:0.0.1`).

## Choosing the bump

Tags are plain [SemVer](https://semver.org) numbers without a `v` prefix (`0.0.1`, not
`v0.0.1`), identical to the PyPI version.

| Change since the last release                                                                    | Bump    | Example         |
| ------------------------------------------------------------------------------------------------ | ------- | --------------- |
| Docs, CI, packaging, or a fix that leaves every code and its meaning unchanged                   | `patch` | `0.0.1 → 0.0.2` |
| Output changes: new or renamed codes, events moved in time, values parsed differently            | `minor` | `0.0.2 → 0.1.0` |
| `1.0.0` once the code vocabulary is stable; afterwards, any change that breaks downstream models | `major` | `0.4.0 → 1.0.0` |

Before `1.0.0`, a minor bump is how to flag an output change that would break a model trained
on the previous output. Preview a bump without changing anything with
`uv version --bump patch --dry-run`.

The ALOTT data release is versioned separately, as `sources.dataset_version` in
[`src/ALOTT_MEDS/event_configs.yaml`](src/ALOTT_MEDS/event_configs.yaml). Change it only when
the spec targets a new PhysioNet release, and release the package with at least a minor bump.

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

1. Start from an up-to-date, clean `main` whose checks are green (Tests, Synthetic ETL, Code
    Quality Main):

    ```bash
    git switch main && git pull --ff-only && git status --short # must print nothing
    ```

2. If the spec changed, run it over the full ALOTT EMR tables (aggregate checks only, see the
    README) and update the figures in the README's *Validation against the release* first.

3. Bump, test, build, commit, tag and push. Use `patch`, `minor` or `major` as chosen above:

    ```bash
    uv version --bump patch
    uv run pytest
    uv build --clear
    git add pyproject.toml uv.lock
    git commit -m "Release $(uv version --short)"
    VERSION=$(uv version --short)
    git tag -a "$VERSION" -m "Release $VERSION"
    git push origin main "$VERSION"
    ```

    If `pytest` or `uv build` fails, stop: nothing is committed yet. Undo the bump with
    `git checkout pyproject.toml uv.lock`, fix the problem on `main`, and start again.

    Do not create the GitHub release by hand; the workflow creates it and fails if it already
    exists.

4. The tag push runs [`python-build.yaml`](.github/workflows/python-build.yaml). Follow it
    with `gh run watch`. It:

    - fails unless the tag equals the `pyproject.toml` version;
    - builds the wheel and sdist and publishes them to PyPI;
    - signs the dists with Sigstore and attaches them to a new GitHub release named after the
        tag.

5. Check the result:

    ```bash
    uv run --no-project --with ALOTT-MEDS==$VERSION python -c \
    	"import importlib.resources as r; print(r.files('ALOTT_MEDS').joinpath('event_configs.yaml').is_file())"
    ```

    It must print `True`: a wheel without the spec cannot run `meds-extract-run spec=ALOTT`.

6. First release only: if the repository is connected to Zenodo, the GitHub release mints a
    DOI. Replace the `zenodo.XXXXXXX` placeholders in the README badge with it.

## When something goes wrong

- **The workflow fails on the tag push** (e.g. the tag check): nothing was published, because
    the PyPI upload and the GitHub release only run after a successful build. Delete the tag,
    fix `main`, and release again; the next bump skips the failed number, which is fine:

    ```bash
    git push --delete origin "$VERSION" && git tag -d "$VERSION"
    ```

- **PyPI rejects the upload** with `invalid-publisher`: the trusted publisher above is missing
    or a field differs (repository, workflow file name, environment). Fix it on PyPI and
    re-run the failed job; the tag can stay.

- **A wrong release was published**: PyPI never accepts the same version twice, even after
    deleting it, and a published tag may already be archived (Zenodo) or installed. Leave the
    tag in place, release the fix as the next patch, and if needed
    [yank](https://pypi.org/help/#yanked) the bad version on PyPI.

Never move or re-create a tag that reached PyPI or a GitHub release.
