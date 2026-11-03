"""meterline-gold: per-person, per-group and per-audience datasets from meterline silver."""

import os
import shutil
import sys
from importlib.metadata import PackageNotFoundError as _NotInstalled, version as _version
from pathlib import Path

from meterline_gold.audiences import servable
from meterline_gold.errors import BuildFailure, InputError

GOLD_SCHEMA_VERSION = "2.0"
try:
    __version__ = _version("meterline-gold")  # the one source is pyproject.toml
except _NotInstalled:  # a source tree that is not installed
    __version__ = "0+unknown"
PRIVATE_UMASK = 0o077  # outputs hold per-person data: files 0600, folders 0700

__all__ = ["build", "servable", "policy_privacy", "dataset_forms", "write_parquet_text", "GOLD_SCHEMA_VERSION", "__version__",
           "InputError", "BuildFailure"]


def dataset_forms():
    """``{(audience, dataset): (schema name, key columns)}`` for every file an audience folder holds.

    ``audience`` is ``central``, ``exec``, ``manager`` or ``person`` (``delivery`` is left out); ``dataset`` is the file
    stem; the schema name is the key of the column list the file is written with (the exec and person forms have their
    own); the key columns are the dataset's primary key. Internal-stable: ``meterline-job`` composes published files
    from it."""
    from meterline_gold import dataset_docs, write

    forms = {}
    for _stem, schema_name, dataset, audiences in dataset_docs.VARIANTS:
        for audience in audiences:
            if audience != "delivery":
                forms[(audience, dataset)] = (schema_name, tuple(write.DATASET_KEYS[dataset]))
    return forms


def write_parquet_text(path, schema_name, header, rows):
    """Write the Parquet twin of a CSV file of a gold folder from its text (see ``meterline_gold.write``); internal-stable."""
    from meterline_gold import write

    write.write_parquet_text(path, schema_name, header, rows)


def policy_privacy(policy):
    """``(sha256, parameters)`` of the policy file at ``policy``: the hash a build records as ``inputs.policy_sha256``
    and the privacy parameters ``{"k": ..., "exec_max_depth": ...}`` that decide what a reader sees.

    Internal-stable: ``meterline-job`` reads it to tell whether a frozen month was published under other privacy
    parameters than the current ones. Raises InputError when the file cannot be read or is not a valid policy."""
    import hashlib

    from meterline_gold import params

    model = params.load_policy(policy)
    digest = hashlib.sha256(params.canonical_hash_input(model).encode("utf-8")).hexdigest()
    return digest, {"k": model.privacy.k, "exec_max_depth": model.audiences.exec.max_depth}


def build(silver, org, policy, prices, out, invoice=None, parquet=False, key=None, threads=None):
    """Build a gold folder at ``out`` and return its path.

    ``key`` is ``bytes`` and defaults to the environment variable METERLINE_GOLD_KEY. ``threads`` sets DuckDB's
    thread count and exists for the determinism check only. Raises InputError (exit 2) or BuildFailure (exit 1);
    a failed build leaves neither ``out`` nor ``out.tmp``.

    Everything the build writes is readable by its owner only (files 0600, folders 0700), whatever the caller's
    umask: the process umask is 077 for the duration of the call and is put back afterwards. To share a folder,
    change its permissions after the build.
    """
    old_umask = os.umask(PRIVATE_UMASK)
    try:
        return _build(silver, org, policy, prices, out, invoice, parquet, key, threads)
    finally:
        os.umask(old_umask)


def _build(silver, org, policy, prices, out, invoice, parquet, key, threads):
    from meterline_gold import run, write

    out = Path(out)
    tmp = Path(str(out) + ".tmp")
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise InputError("%s: the output folder must not exist or be empty" % out)
    if tmp.exists():
        raise InputError("%s: a leftover temporary folder is in the way" % tmp)
    ctx = run.prepare_context(silver, org, policy, prices, invoice=invoice, key=key, threads=threads)
    for scenario_id, keys in write.unknown_envelope_keys(ctx).items():
        print("meterline-gold: warning: scenario %s: unit_envelopes keys match no line unit: %s"
              % (scenario_id, ", ".join(keys)), file=sys.stderr)
    failed = run.failures(ctx.con)
    if failed:
        name, row = failed[0]
        raise BuildFailure("assertion %s failed; first offending row: %r" % (name, row))
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        write.write_gold(ctx, tmp, run, parquet=parquet)
        os.replace(tmp, out)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return out
