"""Command line: ``meterline-sim generate --preset NAME --seed N --months N``."""

import argparse
import sys

from meterline_sim.params import ParamError, RunError


class _Parser(argparse.ArgumentParser):
    def error(self, message):  # exit 2 with a one-line reason, never a usage dump
        raise ParamError(message)


def _parser():
    parser = _Parser(prog="meterline-sim", description="Generate a simulated organisation and its AI usage.")
    sub = parser.add_subparsers(dest="command")
    gen = sub.add_parser("generate", help="write silver records, organisation files and a ground truth")
    gen.add_argument("--preset", required=True, help="vertical, tiny, or a path to a preset file")
    gen.add_argument("--seed", required=True, type=int)
    gen.add_argument("--months", type=int, default=6)
    gen.add_argument("--start", default="2026-04-01")
    gen.add_argument("--scale", default="1")
    gen.add_argument("--out", default="./sim-out")
    gen.add_argument("--params", default=None, help="a JSON file of parameter overrides")
    return parser


def main(argv=None):
    from meterline_sim.pipeline import run

    try:
        args = _parser().parse_args(argv)
        if args.command != "generate":
            raise ParamError("a command is required: generate")
        folder, counts = run(args.preset, args.seed, args.months, args.start, args.scale, args.out, args.params)
    except ParamError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    except RunError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1
    except SystemExit as exc:  # --help
        return int(exc.code or 0)
    print(folder)
    for kind, n in sorted(counts.items()):
        print("%s: %d" % (kind, n))
    return 0


if __name__ == "__main__":
    sys.exit(main())
