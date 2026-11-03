"""The job's configuration file: what it may hold, and the checks of ``meterline-job check``.

A key the job does not know is an input error (a typo must not pass silently). The options of a connector are the
connector's own (``provider_account_id``, ``level``, ``rate_per_hour`` ...) and are passed to its ``run`` as they are.
"""
import importlib
import os
from datetime import date
from pathlib import Path
from typing import Any, Dict, List

try:
    import tomllib
except ImportError:  # Python 3.10
    import tomli as tomllib

# kind -> (import name, the extra of meterline-job that installs it)
KINDS = {"anthropic": ("meterline_anthropic", "anthropic"), "anthropic_admin": ("meterline_anthropic_admin", "anthropic-admin"),
         "litellm": ("meterline_litellm", "litellm"), "copilot": ("meterline_copilot", "copilot")}
RESERVED_STEPS = ("silver", "gold", "publish")
TOP_KEYS = ("data_root", "gold_root", "gold", "connector", "publish")
PUBLISH_DEFAULTS = {"freeze_after_days": 4, "freeze_cap_extra_days": 10}
GOLD_KEYS = ("org", "policy", "prices", "parquet", "invoice")
JOB_CONNECTOR_KEYS = ("name", "kind", "since", "retries", "retry_wait_seconds")  # the rest are the connector's options


class ConfigError(ValueError):
    """The configuration is wrong; the message lists every problem found, one per line. Exit code 2."""

    def __init__(self, problems: List[str]) -> None:
        self.problems = list(problems)
        super().__init__("\n".join(self.problems))


def parse_config(raw: bytes) -> Dict[str, Any]:
    """The configuration in ``raw`` (TOML), checked and with its defaults filled in; ``ConfigError`` when it is wrong."""
    try:
        config = tomllib.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:  # TOMLDecodeError is a ValueError
        raise ConfigError(["the file is not valid TOML: %s" % exc]) from exc
    problems = _problems(config)
    if problems:
        raise ConfigError(problems)
    gold = config["gold"]
    gold.setdefault("parquet", True)
    gold.setdefault("invoice", None)
    config["publish"] = dict(PUBLISH_DEFAULTS, **config.get("publish", {}))
    return config


def load_config(path: Any) -> Dict[str, Any]:
    """``parse_config`` of the file at ``path``; an unreadable file is a ``ConfigError`` too."""
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise ConfigError(["cannot read %s: %s" % (path, exc.strerror or type(exc).__name__)]) from exc
    return parse_config(raw)


def _problems(config: Dict[str, Any]) -> List[str]:
    out = ["unknown key %r" % key for key in config if key not in TOP_KEYS]
    for key in ("data_root", "gold_root"):
        if not isinstance(config.get(key), str) or not config[key]:
            out.append("%s must be a path (a non-empty string)" % key)
    out += _gold_problems(config.get("gold"))
    out += _publish_problems(config.get("publish"))
    connectors = config.get("connector")
    if not isinstance(connectors, list) or not connectors or not all(isinstance(c, dict) for c in connectors):
        return out + ["at least one [[connector]] table is needed"]
    seen = set()
    for index, spec in enumerate(connectors, 1):
        out += _connector_problems(spec, index, seen)
    return out


def _gold_problems(gold: Any) -> List[str]:
    if not isinstance(gold, dict):
        return ["the [gold] table is needed"]
    out = ["unknown key %r under [gold]" % key for key in gold if key not in GOLD_KEYS]
    for key in ("org", "policy", "prices"):
        if not isinstance(gold.get(key), str) or not gold[key]:
            out.append("[gold] %s must be a path (a non-empty string)" % key)
    if "parquet" in gold and not isinstance(gold["parquet"], bool):
        out.append("[gold] parquet must be true or false")
    if "invoice" in gold and (not isinstance(gold["invoice"], str) or not gold["invoice"]):
        out.append("[gold] invoice must be a path (a non-empty string)")
    return out


def _publish_problems(table: Any) -> List[str]:
    if table is None:
        return []
    if not isinstance(table, dict):
        return ["[publish] must be a table"]
    out = ["unknown key %r under [publish]" % key for key in table if key not in PUBLISH_DEFAULTS]
    for key in PUBLISH_DEFAULTS:
        if key in table and (isinstance(table[key], bool) or not isinstance(table[key], int) or table[key] < 0):
            out.append("[publish] %s must be a whole number, 0 or more" % key)
    return out


def _connector_problems(spec: Dict[str, Any], index: int, seen: set) -> List[str]:
    name = spec.get("name")
    label = "connector %r" % name if isinstance(name, str) and name else "connector number %d" % index
    out = []
    if not isinstance(name, str) or not name:
        out.append("%s has no name" % label)
    elif name in RESERVED_STEPS:
        out.append("%s: %r is a reserved step name (silver, gold and publish are the job's own steps)" % (label, name))
    elif name in seen:
        out.append("%s: the name is used twice" % label)
    else:
        seen.add(name)
    if spec.get("kind") not in KINDS:
        out.append("%s: kind %r is not one of %s" % (label, spec.get("kind"), ", ".join(sorted(KINDS))))
    try:
        date.fromisoformat(str(spec["since"]))
    except (KeyError, ValueError):
        out.append("%s: since must be a date such as 2026-09-01" % label)
    for key in ("retries", "retry_wait_seconds"):
        if key in spec and (isinstance(spec[key], bool) or not isinstance(spec[key], int) or spec[key] < 0):
            out.append("%s: %s must be a whole number, 0 or more" % (label, key))
    return out


def missing_extra_message(kind: str, name: str) -> str:
    package, extra = KINDS[kind][0].replace("_", "-"), KINDS[kind][1]
    return "connector %r (kind %s) needs the package %s: pip install 'meterline-job[%s]'" % (name, kind, package, extra)


def check_problems(config: Dict[str, Any]) -> List[str]:
    """What is wrong with a valid configuration on this machine: a package or a path that is not there. No network call."""
    out = []
    for spec in config["connector"]:
        try:
            importlib.import_module(KINDS[spec["kind"]][0])
        except ImportError:
            out.append(missing_extra_message(spec["kind"], spec["name"]))
    gold = config["gold"]
    if not os.path.isdir(gold["org"]):
        out.append("[gold] org: %s is not a folder" % gold["org"])
    for key in ("policy", "prices", "invoice"):
        if gold.get(key) and not os.path.isfile(gold[key]):
            out.append("[gold] %s: %s is not a file" % (key, gold[key]))
    return out
