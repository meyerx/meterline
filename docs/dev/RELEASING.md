# Releasing meterline

How a public version is built from the maintainer's working repository, what can go wrong, and what the
scanner does not catch. The control is one squashed commit: the public repository starts from a single
commit built by `tools/export_public.py`, so the working history is never published.

## Release checklist

Do the steps in order. Stop at the first one that fails.

0. Free the name. Every public file links `github.com/meyerx/meterline`, which is the name of the private working
   repository today. Rename the private repository (Settings > General > Repository name), or delete it once the
   backup chain holds a copy. The public repository is a new one and the private one is never made public: its
   history is the working history. Never change the visibility of the private repository. Before step 4, check that
   `https://github.com/meyerx/meterline` answers "not found".
1. Run the export in scan-only mode and read the output. Nothing is committed. The term file holds one term per
   line (plain text, case-insensitive; `#` starts a comment). A line that starts with `re:` is a regular expression
   instead, also case-insensitive: put the private ticket prefixes and the runner and host names there, never in the
   tool. A hit is reported by line number only, so the file's content is never printed.

   ```sh
   python3 tools/export_public.py --scan-only --ref origin/main \
     --denylist /path/outside/the/repo/terms.txt --out "$(mktemp -d)/public"
   ```

2. Review the exported tree by eye: the file list, `docs/`, every fixture and example. A scanner finds
   patterns, not meaning (see Scan limits). `docs/dev/` holds only the files that a public document links; a file
   there that no document links is left out, and a document that links a file that is left out stops the export.
3. Check the tools, then run the export again with the author flags. Use the maintainer's name and a noreply
   address, never the git config identity. The tool needs `gitleaks` 8.30 or newer on `PATH` (or `--no-gitleaks`,
   which you should not use for a release): older versions ignore the `[[allowlists]]` in `.gitleaks.toml` and report
   false hits, so the tool refuses them. CI pins 8.30.1; install that one. The scan covers the files, the author
   identity and the commit message.

   ```sh
   gitleaks version        # 8.30.1, the version CI pins
   python3 tools/export_public.py --ref origin/main --tag export/v0.1 \
     --denylist /path/outside/the/repo/terms.txt --out "$(mktemp -d)/public" \
     --author-name "Maintainer Name" --author-email "$NOREPLY_ADDRESS"
   ```

   `--tag` adds the `Export-Tag:` line to the commit message: the later versions start from it. The tag `export/v0.1`
   must name the same commit as `--ref`.

   Then search the whole working history, in the working repository. The export publishes none of it, but a term
   that only a commit message, a deleted file or an old version of a file ever held is in no tree scan, and any
   route that would publish the history needs these two to pass first. The first prints the kind and the commit id
   of each hit, never a term, and exits 1 on a hit; the second is the secret scan of every commit of every ref.

   ```sh
   python3 tools/export_public.py --scan-history --denylist /path/outside/the/repo/terms.txt
   gitleaks git --no-banner --redact --config .gitleaks.toml .
   ```

4. Create the empty public repository on the host. Do not initialise it with a README or licence.
5. Push the one commit from the export directory to the new repository. Nothing else is pushed.

The first hour of the new repository, in this order:

6. Turn on private vulnerability reporting (Settings > Code security): `SECURITY.md` tells reporters to use it, and
   with it off they have nowhere to go.
7. Turn on secret scanning and push protection, on the same page.
8. Turn on branch protection for `main` (a ruleset): pull request required, no force pushes, no deletion, and these
   status checks required. The name to type is the second column; the `tests` job runs on Python 3.12 and 3.10 on the
   weekly and manual runs, but only on 3.12 on a pull request, so require `tests (python 3.12)` and never the 3.10 one,
   which a pull request does not create and so would wait for for ever. `minimum-versions` and `quickstart` are skipped
   on most pull requests, and `audit` and `dashboard-a11y` on a pull request that changes only prose the package tests
   do not read (`tools/ci_suite.py`); GitHub counts a skipped check as passed. `tests` always runs, so its check
   always arrives; on such a pull request it runs fewer tests. Keep `changes` required: when it fails, the jobs that
   need it are skipped, and only its own red check stops the merge. A push to `main` runs the light jobs only, so
   also turn on "Require branches to be up to date before merging": the pull request's tests then ran on what `main`
   becomes.

   | CI job | Check name |
   |---|---|
   | `changes` | changes (which heavy jobs this event runs) |
   | `tests` | tests (python 3.12) |
   | `minimum-versions` | minimum versions (python 3.10, lowest direct dependencies) |
   | `quickstart` | quick start (the README commands, fresh venv) |
   | `audit` | dependency audit (pip-audit) |
   | `lint` | lint (ruff) |
   | `dashboard-a11y` | dashboard accessibility (axe-core 4.10) |
   | `secrets` | secret scan (gitleaks) |

9. Settings > Actions > General: require approval for all outside collaborators before a workflow of their pull
   request runs.
10. Set the repository description (the first sentence of the README) and the topics.
11. Create the `pypi` environment and register the pending publishers (see "Publishing to PyPI", once, before the
    first release).
12. Make sure the new repository has no `CI_RUNNER` or `CI_RUNNER_HEAVY` variable, at repository or account
    level, and no self-hosted runner (Settings > Actions > Runners): a self-hosted runner on a public repository
    runs anyone's pull request code. If a runner is registered for the account or an organisation, remove its
    access to the new repository.
13. Check that CI is green on the first commit, then run the full-history secret scan once more on a fresh clone of
    the public repository (it is one commit, so this is quick):

    ```sh
    git clone https://github.com/meyerx/meterline "$(mktemp -d)/check" && cd "$_"
    gitleaks git --no-banner --redact --config .gitleaks.toml .
    ```

Time buffer: do steps 1 to 3 at least a day before step 5, and do not publish late in the day or before a
weekend. A second look after a night catches what the first pass missed.

## Pre-mortem

It is a month after publication and it went badly. The history of that disaster, in one paragraph: a term
or key that should not have been public was found by a stranger, copied before it could be removed, and the
repository had to be deleted and rebuilt under a new name. The scenarios below are the ways that story starts.

Stop conditions, any one of which halts the release: a scan hit that is not understood, a fixture whose
origin is not certain, the author identity not yet checked, the review in step 2 not finished, or a feeling
that the buffer is too short. Stopping costs a day; publishing a leak costs the repository.

### A secret or private term is in the published history

**Control:** the public repository starts from one squashed commit, so the working history is not
published; the export scans contents, paths, gzip and Parquet text for secret patterns and every denylist
term, and runs gitleaks before it commits.
**Detection:** the scan output; GitHub secret scanning on the new repository.
**Response:** delete the public repository, rotate any key at once, rebuild from a clean export.

### A real-looking fixture or example is published

**Control:** fixtures and examples are synthetic only, by rule; a reviewer confirms each one in step 2. No
tool can prove a value is synthetic, so this is a human control.
**Detection:** the step 2 review; a later report from a reader.
**Response:** remove the file, publish a new commit, and judge whether the repository must be rebuilt.

### An unintended identity is in the commit

**Control:** the export requires `--author-name` and `--author-email` and refuses to read the git config
identity; the identity and the commit message are scanned against the denylist before the commit.
**Detection:** the refusal at export time; `git log --format=fuller` on the export before the push.
**Response:** if it is already pushed, treat it as a leak: delete and rebuild.

### A wrong cost figure that users trust

**Control:** no control yet. The cost tables and rates are only as correct as the provider pages they were
read from, and they change.
**Detection:** a user report; a periodic comparison against the provider's own usage page.
**Response:** fix the table, add a changelog entry that names the wrong period, and say so in the README.

### Missing files make the public tree broken

**Control:** the export refuses, with a non-zero status, when an allowlisted path is missing from the ref;
the CI quick-start job installs the packages in a fresh environment and runs the README commands.
**Detection:** the refusal at export time; the first CI run on the public repository.
**Response:** fix the allowlist or the file, and export again.

### The scan was skipped or run on the wrong ref

**Control:** the commit path runs the scan and gitleaks in the same run that makes the commit; there is no
separate commit command. The ref is printed in the summary line.
**Detection:** the summary line names the ref and the number of terms scanned; compare it with the intent.
**Response:** discard the export directory and run again on the right ref.

## Scan limits

The scanner reads each exported file as text (a gzip file is decompressed once; Parquet strings are read as
bytes). It does not catch:

- content inside a DEFLATE zip archive
- base64-encoded text
- URL-encoded text
- text encoded as UTF-16
- a file compressed with gzip twice (double gzip)
- a term split across lines, or written with a zero-width character inside it
- a term that is correct but written with a different spelling or a typo
- the meaning of a value: a real identifier that matches no pattern passes

It scans the tree it exports and the commit identity and message. It does not scan the working history,
because the history is not published. That is why the squashed export is the control, and why a route that
would publish the working history needs the history scanned first: `--scan-history` (a term search over every
commit message, identity, path and file version of every ref) and the gitleaks command of step 3 (a secret scan of every
commit). Run them before any such route, and treat a hit as a leak.

## Later public versions

Development stays in the private repository. Each later public version is one new commit on top of the public history,
built from a tag of the private repository.

1. Tag the private commit `export/vX.Y`. Never use a name that starts with `meterline` and carries `-v` and a digit: those
   tags start the release workflow.
2. Run the gate on the tag: the export in scan-only mode (`--ref export/vX.Y`), `python3 tools/export_public.py
   --scan-history --since export/vPREVIOUS` (for v0.2: `--since export/v0.1`) and the full test suite. Run the blind trials
   only when `AGENTS.md`, `llms.txt` or `docs/how-to` changed.
3. Run the export tool only from a clean checkout of the export tag (its allowlists are constants of the tool). Clone the
   public repository and export onto the clone. The tool makes one commit on a new branch named after the tag:

   ```sh
   python3 tools/export_public.py --tag export/vX.Y --onto /path/to/public-clone \
     --denylist /path/outside/the/repo/terms.txt \
     --author-name "Maintainer Name" --author-email "$NOREPLY_ADDRESS"
   ```

   After `--onto`, build the wheels, install them non-editable in a fresh venv and run `CI=true pytest -q packages tools`
   on the public clone's new branch before pushing (an editable install hides a version that does not match the package).
4. Push the new branch to the public repository and open a pull request there with the fixed body ("Public text" below).
   Merge it with a merge commit, not a squash, when CI is green, so the export commit and its `Export-Tag:` line stay in the history.
5. Release on PyPI as "Each release" describes.

Porting an outside contribution back: on the public repository, run `git format-patch` for the merged commits since the
last export commit. In the private repository, apply them with `git am` on a branch and open a pull request.
Before merging a public pull request, review its new paths against the `EXCLUDE` list of the export: a contribution can
add files. `git am` refuses a patch that clashes with a private-only file.

The export refuses when the public repository holds a change that is not ported back. It compares the clone's HEAD with
the clone's last export commit. For each path that changed on the public side since then, the new export must equal what the
public repository now holds, which is the case once the change has been ported back. Otherwise it lists the paths and
refuses, because the new commit would silently undo the change. Port it back first; `--allow-divergence` overrides the
refusal with a warning that lists exactly those paths.

## Public text

All public text is pointer-only and hand-checked by the maintainer: a pull request body, a tag message, a release note, an
issue comment. It points to `CHANGELOG.md` and says nothing about how, why or by whom a change was made. The export scan
reads only the tree, the commit identity and the commit message, so text typed into the host's web page is covered by
nothing else. Before posting a body, save it to a file and run:

```sh
python3 tools/check_public_text.py FILE --denylist /path/outside/the/repo/terms.txt
```

It prints `PASS` or `FAIL` and the kind of each hit (never a term): it runs the export's scan (private working traces, secret,
e-mail and path patterns, every denylist term) on that text. The two texts of a release are fixed: the export pull request
body `Export of export/vX.Y. What changed: CHANGELOG.md.` and the tag message `<package> <version>`.

## Publishing to PyPI

Every package of the repository is published one at a time, each with its own version (see
"Versions of the packages" in [`records.md`](../records.md#versions-of-the-packages)); step 1 below names them. A package
that is not ready to be released has the line "`meterline-NAME` is not released yet." in this section instead: it is
then left out of the package list of `release.yml` and of the release order, and `tools/check_new_connector.py` and
`tools/test_release_path.py` expect exactly that. A release is never built or uploaded from a laptop: pushing
a version tag starts `.github/workflows/release.yml`, which builds on a GitHub-hosted runner, checks, attests,
publishes and creates the GitHub release. No PyPI token exists anywhere: PyPI accepts the upload because it trusts
that one workflow (trusted publishing). The provenance target is SLSA Build Level 2: a hosted build with signed
provenance (a GitHub artifact attestation for every file, plus the PEP 740 attestations PyPI stores).

`meterline-job` is released like the others, last: it needs `meterline-gold` and the connectors it runs (its extras) on PyPI first.

### Once, before the first release (the maintainer, by hand)

1. **Reserve the package names:** `meterline`, `meterline-anthropic`, `meterline-anthropic-admin`, `meterline-litellm`,
   `meterline-copilot`, `meterline-sim`, `meterline-gold` and `meterline-job`. On PyPI, sign in with an account that has two-factor authentication on. For each
   of these names, open "Your account", then "Publishing", and add a pending GitHub publisher: PyPI project name =
   the package name, owner = `meyerx`, repository = `meterline`, workflow = `release.yml`, environment = `pypi`.
   A pending publisher does **not** reserve a name: PyPI creates the project, and the name is yours, only at the
   first upload. So check the names are still free (`https://pypi.org/project/<name>/` returns "not found")
   just before, and do the first release of the packages (steps below) soon after, in the order given there. If a name
   is taken in between, stop and choose a new name; never publish under a name that someone else holds.
2. **Create the `pypi` environment on GitHub**, in the public repository's settings under "Environments": add the
   maintainer as required reviewer, and allow deployments from tags matching `meterline*-v*` only. The publish job
   then waits for an approval before anything is uploaded.
3. After the first upload of each package, check on its PyPI page that the trusted publisher is listed under
   "Publishing", and remove any pending publisher left over.
4. **The admin package is released with the others.** `meterline-anthropic-admin` is released in the first release
   with the others: its live run, the calibration run in its README, passed on 2026-10-30 on a small Console
   organisation (one model, two days of usage).

### Each release

Versions and `CHANGELOG.md` change only in the private repository, in a pull request merged into `main`; a tag whose
commit is not on `main` is refused by the build job (`git merge-base --is-ancestor`).

1. In a pull request: set the new version in `packages/<package>/pyproject.toml` (the only place it is written;
   `__version__` reads it from the installed metadata), add the release to `CHANGELOG.md`, and, if the package now
   needs a newer `meterline`, raise its `meterline>=` bound. Merge only with every CI job green, including
   `minimum-versions` and `audit`.
2. Release `meterline` first, then the packages that depend on it: `meterline-anthropic`, `meterline-anthropic-admin`,
   `meterline-litellm`, `meterline-copilot` and `meterline-sim`, then `meterline-gold` (its `dev` extra installs `meterline-sim`),
   then `meterline-job` (it needs `meterline-gold` and the connectors it runs). The
   workflow installs the built wheel to record its SBOM, so the `meterline` version a dependent requires must
   already be on PyPI.
3. Tag the merged commit on `main` and push the tag. The tag is `<package>-v<version>` and its message is always
   `<package> <version>` (for example `meterline 0.3.0`), nothing else:

   ```sh
   git switch main && git pull --ff-only
   git tag -a meterline-anthropic-v0.2.0 -m "meterline-anthropic 0.2.0"
   git push origin meterline-anthropic-v0.2.0
   ```

4. The workflow then, in order: refuses a tag whose version differs from `pyproject.toml`; builds the sdist and
   the wheel with `SOURCE_DATE_EPOCH` set to the commit time; runs `twine check --strict`; records a CycloneDX SBOM
   of the package and everything it installs; writes a build provenance attestation for each file; waits for the
   approval on the `pypi` environment; uploads to PyPI through trusted publishing; and creates a GitHub release that
   carries the two files and the SBOM.
5. Check the result from a clean environment:

   ```sh
   python3 -m venv /tmp/check && /tmp/check/bin/pip install "meterline-anthropic==0.2.0"
   gh attestation verify meterline_anthropic-0.2.0-py3-none-any.whl --repo meyerx/meterline
   ```

6. A published version is never replaced. If it is wrong, yank it on PyPI (with the reason) and release a new patch
   version.

### CHANGELOG entry for a version

Add one entry per released package to `CHANGELOG.md`, under `[Unreleased]` until the tag is pushed and then above the
older ones, in this shape. Leave out a heading with nothing under it, and say plainly what a user of that package
notices.

    ## [meterline-anthropic 0.2.0] - 2026-11-15

    ### Added

    - What a user can now do that they could not before.

    ### Changed

    - What behaves differently. Name anything a user must change in their own setup.

    ### Fixed

    - What was wrong, in the user's words, and what is right now.

    ### Known limits

    - What the release still does not do or has not been checked against.

Between releases, Dependabot opens weekly pull requests for the pinned actions and for the dependencies, and the
`audit` CI job runs `pip-audit` on every pull request that changes more than prose, and on the weekly run.
