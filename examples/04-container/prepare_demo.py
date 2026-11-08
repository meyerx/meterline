"""Demo only: fill the data folders from meterline-sim output and write the job configuration, so that the container can run on
simulated data with no key and no network. A real deployment does none of this: its connectors fill the data folders, and its
configuration, organisation files, policy and price list are mounted at /config.

    python /app/prepare_demo.py CONFIG_DIR DATA_ROOT [GOLD_ROOT]

Each record kind of the simulator's silver output is split by provider into one folder per connector, <DATA_ROOT>/<name>/records/
<kind>/, the layout the job's silver step copies from. The configuration is written in the format of examples/03-daily-job/
job.example.toml. No package code is involved.
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

import meterline_gold
import meterline_sim

KINDS = ("usage", "activity", "seats", "seat_assignments", "org_totals")
CONNECTORS = {  # provider in the records -> (connector name, kind, extra options of the [[connector]] table)
    "anthropic": ("claude", "anthropic", {}),
    "litellm": ("proxy", "litellm", {"proxy_name": "example-proxy"}),
    "github_copilot": ("copilot", "copilot", {"level": "organization", "slug": "example-tiny"}),
}


def split_records(sim, data_root):
    """Write the simulator's records under <data_root>/<connector>/records/<kind>/; returns the first day of the data."""
    first = None
    for kind in KINDS:
        for path in sorted((Path(sim) / "records" / kind).glob("*.jsonl")):
            buckets = {}
            for line in path.read_text(encoding="utf-8").splitlines():
                record = json.loads(line)
                buckets.setdefault(record["provider"], []).append(line)
                first = min(first or record["period_start"], record["period_start"])
            for provider, lines in buckets.items():
                target = Path(data_root) / CONNECTORS[provider][0] / "records" / kind / path.name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return first


def toml_value(value):
    return json.dumps(value)  # a TOML basic string is a JSON string for these values


def write_config(config_dir, data_root, gold_root, since):
    lines = ["# written by prepare_demo.py: the format of examples/03-daily-job/job.example.toml",
             "data_root = %s" % toml_value(str(data_root)), "gold_root = %s" % toml_value(str(gold_root)), "",
             "[gold]", 'org = "%s/org"' % config_dir, 'policy = "%s/policy.yaml"' % config_dir,
             'prices = "%s/prices.yaml"' % config_dir, "parquet = true"]
    for name, kind, options in CONNECTORS.values():
        lines += ["", "[[connector]]", "name = %s" % toml_value(name), "kind = %s" % toml_value(kind), "since = %s" % toml_value(since)]
        lines += ["%s = %s" % (key, toml_value(value)) for key, value in options.items()]
    (Path(config_dir) / "job.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv):
    if len(argv) not in (2, 3):
        print(__doc__.strip().split("\n\n")[1], file=sys.stderr)
        return 2
    config_dir, data_root = Path(argv[0]), Path(argv[1])
    gold_root = Path(argv[2]) if len(argv) == 3 else Path("/gold")
    with tempfile.TemporaryDirectory() as scratch:
        sim = meterline_sim.generate(preset="tiny", seed=7, months=3, out=scratch)
        since = split_records(sim, data_root)
        shutil.copytree(Path(sim) / "org", config_dir / "org", dirs_exist_ok=True)
    examples = Path(meterline_gold.__file__).resolve().parent / "examples"
    shutil.copyfile(examples / "policy.example.yaml", config_dir / "policy.yaml")
    shutil.copyfile(examples / "prices.example.yaml", config_dir / "prices.yaml")
    write_config(config_dir, data_root, gold_root, since)
    print("wrote %s/job.toml and the data folders of %s" % (config_dir, ", ".join(v[0] for v in CONNECTORS.values())))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
