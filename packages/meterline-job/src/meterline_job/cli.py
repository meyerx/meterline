"""The ``meterline-job`` command: ``run CONFIG [--step NAME] [--no-retry]`` and ``check CONFIG``.

Exit codes of ``run``: 0 done; 1 a connector failed transiently or its folder was locked; 2 an input error; 3 a provider
refused; 4 gold's build or the publish step failed. ``check`` exits 0 or 2 and makes no network call.
"""
import argparse
import sys
from typing import List, Optional

from meterline_job.config import ConfigError, check_problems, load_config
from meterline_job.job import run_job


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="meterline-job", description="Run the meterline connectors, stage silver and build gold.")
    commands = parser.add_subparsers(dest="command", required=True, metavar="{run,check}")
    run = commands.add_parser("run", help="run every connector, then silver, then gold, then publish (or one step)",
                              description="Run every connector, then silver, gold and publish; one JSON line per step on stdout.")
    run.add_argument("config", help="the TOML configuration (see examples/03-daily-job/job.example.toml)")
    run.add_argument("--step", metavar="NAME", help="run one step: a connector's name, silver, gold or publish")
    run.add_argument("--no-retry", action="store_true", help="do not retry a connector that failed transiently")
    check = commands.add_parser("check", help="validate the configuration and the paths it names (no network call)",
                                description="Validate the configuration, the connector packages and the paths; exit 0 or 2.")
    check.add_argument("config", help="the TOML configuration")
    return parser


def _load(path: str):
    try:
        return load_config(path), None
    except ConfigError as exc:
        for problem in exc.problems:
            print("meterline-job: %s" % problem, file=sys.stderr)
        return None, 2


def main(argv: Optional[List[str]] = None) -> int:
    args = _parser().parse_args(argv)
    config, failed = _load(args.config)
    if config is None:
        return failed
    if args.command == "check":
        problems = check_problems(config)
        for problem in problems:
            print("meterline-job: %s" % problem, file=sys.stderr)
        if problems:
            return 2
        print("ok: %d connector(s), gold at %s" % (len(config["connector"]), config["gold_root"]))
        return 0
    return run_job(config, step=args.step, no_retry=args.no_retry)


if __name__ == "__main__":
    sys.exit(main())
