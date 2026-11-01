"""Command line: ``meterline-gold build | schemas | import-litellm-prices`` (spec 10)."""

import argparse
import json
import sys
from pathlib import Path

import duckdb

import meterline_gold
from meterline_gold import params
from meterline_gold.errors import BuildFailure, InputError


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise InputError(message)


def _parser():
    parser = _Parser(prog="meterline-gold", description="Per-person, per-group and per-audience datasets from meterline silver.")
    sub = parser.add_subparsers(dest="command", parser_class=_Parser)
    sub.required = True
    build = sub.add_parser("build", help="build a gold folder")
    build.add_argument("--silver", required=True)
    build.add_argument("--org-files", required=True)
    build.add_argument("--policy", required=True)
    build.add_argument("--prices", required=True)
    build.add_argument("--out", required=True)
    build.add_argument("--invoice")
    build.add_argument("--parquet", action="store_true")
    schemas = sub.add_parser("schemas", help="write the policy, prices, organisation, manifest and dataset schemas, and audiences.json")
    schemas.add_argument("--out", required=True)
    imp = sub.add_parser("import-litellm-prices", help="convert a local copy of LiteLLM's price file")
    imp.add_argument("--file", required=True)
    imp.add_argument("--keys", required=True)
    imp.add_argument("--as-of", required=True, dest="as_of")
    imp.add_argument("--out", required=True)
    return parser


def _build(args):
    out = meterline_gold.build(
        silver=args.silver, org=args.org_files, policy=args.policy, prices=args.prices, out=args.out,
        invoice=args.invoice, parquet=args.parquet,
    )
    manifest = json.loads((Path(out) / "manifest.json").read_text(encoding="utf-8"))
    print("gold folder: %s" % out)
    print("months: %s" % ", ".join(manifest["months"]))
    for entry in manifest["outputs"]:
        if entry["path"].startswith("central/") and entry["path"].endswith(".csv"):
            print("%s: %d rows" % (entry["path"], entry["rows"]))
    flags = manifest["input_flags"]
    if flags["persons_missing"]:
        print("warning: %d person(s) named by identities.csv or by usage are missing from people.csv or not employed "
              "in a month of the build; their cost is in no person_month (data_quality: rows_person_missing, "
              "cost_ref_person_missing)" % flags["persons_missing"])
    if flags["units_parent_unknown"]:
        print("warning: %d unit(s) name a parent that is no unit valid on that day (data_quality: "
              "units_parent_unknown)" % flags["units_parent_unknown"])
    return 0


def _import(args):
    from meterline_gold.importers import litellm

    obj = litellm.convert(args.file, args.keys, args.as_of)
    litellm.write(obj, args.out)
    return 0


def main(argv=None):
    """Returns the exit code: 0 done, 2 bad input or arguments, 1 an assertion or invariant failed."""
    try:
        try:
            args = _parser().parse_args(argv)
        except SystemExit as exc:
            return int(exc.code or 0)
        if args.command == "build":
            return _build(args)
        if args.command == "schemas":
            from meterline_gold import contract

            params.write_schemas(args.out)
            contract.write_schemas(args.out)
            return 0
        return _import(args)
    except InputError as exc:
        print("meterline-gold: input error: %s" % exc, file=sys.stderr)
        return 2
    except BuildFailure as exc:
        print("meterline-gold: build failed: %s" % exc, file=sys.stderr)
        return 1
    except duckdb.Error as exc:
        print("meterline-gold: build failed in the engine: %s" % exc, file=sys.stderr)
        return 1
    except OSError as exc:
        print("meterline-gold: input error: %s" % exc, file=sys.stderr)
        return 2
