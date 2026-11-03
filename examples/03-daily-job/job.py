"""The daily job, as a script: a thin call to the ``meterline-job`` package.

    python examples/03-daily-job/job.py job.toml

This is ``meterline-job run job.toml`` (see ``meterline-job --help``); the package holds the job: the steps, the retry
rule, the exit codes 0 to 4 and the status lines. Install it with its connectors: ``pip install 'meterline-job[all]'``.
"""
import sys

from meterline_job.cli import main

if __name__ == "__main__":
    sys.exit(main(["run", *sys.argv[1:]]))
