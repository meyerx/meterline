"""Defaults, preset loading, merging, validation and scaling (spec 3)."""

import hashlib
import json
import re
from decimal import ROUND_FLOOR, Decimal, InvalidOperation
from pathlib import Path

DATA = Path(__file__).parent / "data"

PROFILES = ("dormant", "curious", "office_worker", "developer", "power_user")
PRODUCTS = ("chat", "cowork", "office_agent", "claude_code", "copilot")
ANTHROPIC_MODELS = ("model-a-light", "model-a-standard", "model-a-premium")
COPILOT_MODELS = ("model-b-light", "model-a-standard", "model-b-premium")
UNIT_KINDS = ("company", "division", "department", "team")

_KEY_RE = re.compile(r"^[a-z][a-z0-9-]*$")
_ACCOUNT_KEYS = ("model", "req_median", "req_sigma", "in_median", "out_median", "f_read", "f_write", "failure_rate")
_STRUCTURE_KEYS = ("preset_format", "name", "org", "dimensions", "units", "matrix_units", "profiles",
                   "developer_functions", "parameters")
_OK_DOMAINS = ("example.com", "example.org", "example.net")
_OK_SUFFIXES = (".test", ".invalid", ".example")


class ParamError(ValueError):
    """Bad arguments, preset, parameters or price table (exit 2)."""


class RunError(RuntimeError):
    """A failure inside the run (exit 1)."""


def _reject_fraction(text):
    raise ParamError("a JSON number with a fraction is not allowed: " + text)


def parse_json(text, where):
    try:
        return json.loads(text, parse_float=_reject_fraction, parse_constant=_reject_fraction)
    except json.JSONDecodeError as exc:
        raise ParamError("%s: not valid JSON (%s)" % (where, exc)) from exc


def load_json_file(path):
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ParamError("cannot read %s: %s" % (path, exc)) from exc
    return parse_json(text, str(path))


def canonical_sha(obj):
    text = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def dec(value, where="value"):
    if isinstance(value, bool) or not isinstance(value, str):
        raise ParamError("%s must be a decimal string" % where)
    try:
        out = Decimal(value)
    except InvalidOperation as exc:
        raise ParamError("%s is not a decimal string: %r" % (where, value)) from exc
    if not out.is_finite():
        raise ParamError("%s is not finite" % where)
    return out


def load_defaults():
    return load_json_file(DATA / "defaults.json")


# ---- merging and type checks -----------------------------------------------


def _is_decimal_text(value):
    if not isinstance(value, str):
        return False
    try:
        return Decimal(value).is_finite()
    except InvalidOperation:
        return False


def _check_leaf(path, default, value):
    where = ".".join(path)
    if isinstance(default, bool):
        ok = isinstance(value, bool)
    elif isinstance(default, int):
        ok = isinstance(value, int) and not isinstance(value, bool)
    elif isinstance(default, str):
        ok = isinstance(value, str) and (not _is_decimal_text(default) or _is_decimal_text(value))
    elif isinstance(default, list):
        ok = isinstance(value, list)
    elif default is None:
        ok = value is None or isinstance(value, (str, int)) and not isinstance(value, bool)
    else:
        ok = isinstance(value, dict)
    if not ok:
        raise ParamError("parameter %s has the wrong type: %r" % (where, value))


def deep_merge(base, override, path=()):
    """Objects merge key by key; lists and scalars are replaced. A key that is
    not in the defaults is an error, except a new service account."""
    if not isinstance(override, dict):
        raise ParamError("parameters %s must be an object" % ".".join(path))
    out = dict(base)
    for key in override:
        value = override[key]
        here = path + (key,)
        if key not in base:
            if path == ("behaviour", "service_accounts") and isinstance(value, dict):
                missing = [k for k in _ACCOUNT_KEYS if k not in value]
                extra = [k for k in value if k not in _ACCOUNT_KEYS]
                if missing or extra:
                    raise ParamError("service account %s: missing %s, unknown %s" % (key, missing, extra))
                out[key] = value
                continue
            raise ParamError("unknown parameter: " + ".".join(here))
        default = base[key]
        if isinstance(default, dict):
            if not isinstance(value, dict):
                raise ParamError("parameter %s must be an object" % ".".join(here))
            out[key] = deep_merge(default, value, here)
        else:
            _check_leaf(here, default, value)
            out[key] = value
    return out


def resolve_params(preset_params, run_params):
    resolved = load_defaults()
    for layer in (preset_params or {}, run_params or {}):
        resolved = deep_merge(resolved, layer)
    validate_params(resolved)
    return resolved


# ---- validation ---------------------------------------------------------------


def _unit(p, where, lo=Decimal(0), hi=Decimal(1)):
    value = dec(p, where)
    if not lo <= value <= hi:
        raise ParamError("%s must be between %s and %s" % (where, lo, hi))
    return value


def _date_text(value, where):
    from datetime import date

    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ParamError("%s must be a YYYY-MM-DD date" % where) from exc


def _validate_profiles(behaviour):
    for profile in PROFILES:
        prof = behaviour["profiles"][profile]
        _unit(prof["p_active"], "p_active")
        dec(prof["weekend_factor"], "weekend_factor")
        for product, prob in prof["products"].items():
            _unit(prob, "products." + product)
        for product, vol in behaviour["volume"][profile].items():
            dec(vol["req_median"], "req_median")
            dec(vol["req_sigma"], "req_sigma")
            weights = [dec(vol["model_weights"][m], "model_weights") for m in ANTHROPIC_MODELS]
            if any(w < 0 for w in weights) or sum(weights) <= 0:
                raise ParamError("model_weights of %s.%s must be non-negative with a positive sum" % (profile, product))


def _validate_tokens_copilot(behaviour):
    for product, tok in behaviour["tokens"].items():
        for key, value in tok.items():
            dec(value, "tokens.%s.%s" % (product, key))
    for profile, row in behaviour["copilot"].items():
        for key, value in row.items():
            if key != "model_weights":
                dec(value, "copilot.%s.%s" % (profile, key))
        weights = [dec(row["model_weights"][m], "copilot model_weights") for m in COPILOT_MODELS]
        if any(w < 0 for w in weights) or sum(weights) <= 0:
            raise ParamError("copilot model_weights of %s must be non-negative with a positive sum" % profile)


def _validate_drift_accounts(behaviour):
    for source, outcomes in behaviour["drift"].items():
        total = Decimal(0)
        for target, prob in outcomes.items():
            if target not in PROFILES:
                raise ParamError("drift target %s is not a profile" % target)
            total += _unit(prob, "drift")
        if total > 1:
            raise ParamError("drift probabilities of %s exceed 1" % source)
    for sa_id, sa in behaviour["service_accounts"].items():
        if not _KEY_RE.match(sa_id):
            raise ParamError("service account id %r" % sa_id)
        for key in _ACCOUNT_KEYS[1:]:
            dec(sa[key], "service_accounts.%s.%s" % (sa_id, key))


def _validate_providers(p):
    behaviour = p["behaviour"]
    prov = p["providers"]
    for name in ("anthropic", "github_copilot"):
        windows = prov[name]["active_windows"]
        if len(windows) != 2 or not all(isinstance(x, int) and x >= 1 for x in windows):
            raise ParamError("providers.%s.active_windows must be two integers >= 1" % name)
    ages = prov["github_copilot"]["seat_age_days"]
    if len(ages) != 2 or not all(isinstance(x, int) and x >= 0 for x in ages) or ages[0] > ages[1]:
        raise ParamError("providers.github_copilot.seat_age_days must be [low, high]")
    if prov["github_copilot"]["included_credits_per_seat"] is not None \
            and dec(prov["github_copilot"]["included_credits_per_seat"], "included_credits_per_seat") < 0:
        raise ParamError("providers.github_copilot.included_credits_per_seat must not be negative")
    if prov["litellm"]["unpriced_cost"] not in ("null", "zero"):
        raise ParamError('providers.litellm.unpriced_cost must be "null" or "zero"')
    for sa_id in prov["litellm"]["accounts"]:
        if sa_id not in behaviour["service_accounts"]:
            raise ParamError("providers.litellm.accounts names an unknown account: %s" % sa_id)
    if len(set(prov["litellm"]["accounts"])) != len(prov["litellm"]["accounts"]):
        raise ParamError("providers.litellm.accounts repeats an account")


def _validate_holidays(p):
    for item in p["calendar"]["holidays"]:
        if not isinstance(item, dict) or set(item) != {"name", "from", "to", "factor"}:
            raise ParamError("a holiday needs name, from, to and factor")
        for end in ("from", "to"):
            if not re.match(r"^(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])$", str(item[end])):
                raise ParamError("holiday %s must be MM-DD" % end)
        if dec(item["factor"], "holiday factor") < 0:
            raise ParamError("a holiday factor must be non-negative")


def _validate_events(ev):
    jl = ev["joiners_leavers"]
    _unit(jl["leaver_rate"], "leaver_rate")
    _unit(jl["joiner_rate"], "joiner_rate")
    if jl["invite_lead_days"] < 0 or jl["min_per_period"] not in (0, 1):
        raise ParamError("joiners_leavers: invite_lead_days >= 0 and min_per_period 0 or 1")
    idx = ev["reorganisation"]["month_index"]
    if idx is not None and (isinstance(idx, bool) or not isinstance(idx, int) or idx < 0):
        raise ParamError("events.reorganisation.month_index must be an integer >= 1 or null")
    unit = ev["reorganisation"]["unit"]
    if unit is not None and not isinstance(unit, str):
        raise ParamError("events.reorganisation.unit must be a unit key or null")
    ra = ev["runaway_agent"]
    if ra["multiplier"] < 1 or ra["days"] < 1:
        raise ParamError("runaway_agent: multiplier and days must be >= 1")
    if ra["start"] is not None:
        _date_text(ra["start"], "runaway_agent.start")
    if ra["model"] not in ANTHROPIC_MODELS:
        raise ParamError("runaway_agent.model must be one of %s" % (ANTHROPIC_MODELS,))
    lc = ev["late_correction"]
    if lc["after_days"] < 1 or lc["window_days"] < 1 or lc["window_end_offset_days"] < 0:
        raise ParamError("late_correction: after_days and window_days >= 1, window_end_offset_days >= 0")
    _unit(lc["share"], "late_correction.share")
    if dec(lc["factor"], "late_correction.factor") < 0:
        raise ParamError("late_correction.factor must be >= 0")
    pc = ev["price_change"]
    if dec(pc["factor"], "price_change.factor") < 0:
        raise ParamError("price_change.factor must be >= 0")
    if pc["date"] is not None:
        _date_text(pc["date"], "price_change.date")


def _validate_locations(loc):
    if not loc["values"] or len(loc["values"]) != len(loc["weights"]):
        raise ParamError("locations.values and locations.weights must have the same length")
    if any(dec(w, "locations.weights") < 0 for w in loc["weights"]):
        raise ParamError("locations.weights must be non-negative")
    if sum(dec(w, "locations.weights") for w in loc["weights"]) <= 0:
        raise ParamError("locations.weights must have a positive sum")


def validate_params(p):
    behaviour = p["behaviour"]
    _validate_profiles(behaviour)
    _validate_tokens_copilot(behaviour)
    _validate_drift_accounts(behaviour)
    _validate_providers(p)
    _validate_holidays(p)
    _validate_events(p["events"])
    if p["pull"]["lag_days"] < 1:
        raise ParamError("pull.lag_days must be >= 1")
    _unit(p["contractor_share"], "contractor_share")
    _validate_locations(p["locations"])
    if p["price_table"] is not None and not isinstance(p["price_table"], str):
        raise ParamError("price_table must be a path or null")


# ---- presets --------------------------------------------------------------------


def load_preset(name_or_path):
    candidate = DATA / "presets" / (str(name_or_path) + ".json")
    if candidate.is_file() and re.match(r"^[a-z][a-z0-9-]*$", str(name_or_path)):
        path = candidate
    else:
        path = Path(str(name_or_path))
        if not path.is_file():
            raise ParamError("unknown preset: %s" % name_or_path)
    obj = load_json_file(path)
    validate_preset(obj)
    return obj


def _check_team_unit(node, key):
    has = [k for k in ("children", "teams", "size") if k in node]
    if has != ["size"] or isinstance(node["size"], bool) or not isinstance(node["size"], int) or node["size"] < 1:
        raise ParamError("team %s needs a size >= 1 and nothing else" % key)
    if "team_size" in node:
        raise ParamError("team %s: team_size belongs to a department" % key)


def _check_department_unit(node, key):
    if "children" in node and "teams" in node:
        raise ParamError("department %s has both children and teams" % key)
    if "size" in node:
        raise ParamError("department %s: size belongs to a team" % key)
    if "teams" in node:
        teams, size = node["teams"], node.get("team_size")
        if isinstance(teams, bool) or not isinstance(teams, int) or teams < 1:
            raise ParamError("department %s: teams must be an integer >= 1" % key)
        if (not isinstance(size, list) or len(size) != 2 or not all(
                isinstance(x, int) and not isinstance(x, bool) and x >= 1 for x in size) or size[0] > size[1]):
            raise ParamError("department %s: team_size must be [min, max]" % key)
    elif "children" not in node:
        raise ParamError("department %s needs children or teams" % key)


def _check_unit_tree(node, seen, in_department, where):
    if not isinstance(node, dict):
        raise ParamError(where + ": a unit must be an object")
    allowed = {"key", "name", "kind", "function", "children", "teams", "team_size", "size"}
    extra = set(node) - allowed
    if extra:
        raise ParamError("%s: unknown unit key(s) %s" % (where, sorted(extra)))
    for need in ("key", "name", "kind", "function"):
        if not isinstance(node.get(need), str) or not node[need]:
            raise ParamError("%s: unit needs %s" % (where, need))
    key, kind = node["key"], node["kind"]
    if not _KEY_RE.match(key):
        raise ParamError("unit key %r is not valid" % key)
    if key in seen:
        raise ParamError("unit key %r is used twice" % key)
    seen.add(key)
    if kind not in UNIT_KINDS:
        raise ParamError("unit %s has unknown kind %r" % (key, kind))
    if kind == "team":
        _check_team_unit(node, key)
    elif kind == "department":
        _check_department_unit(node, key)
    else:
        if "children" not in node or "teams" in node or "size" in node or "team_size" in node:
            raise ParamError("%s unit %s needs children only" % (kind, key))
    for child in node.get("children", []):
        _check_unit_tree(child, seen, in_department or kind == "department", where)
    if "children" in node and not isinstance(node["children"], list):
        raise ParamError("unit %s: children must be a list" % key)


def _check_preset_head(obj):
    if not isinstance(obj, dict):
        raise ParamError("a preset must be a JSON object")
    extra = [k for k in obj if k not in _STRUCTURE_KEYS]
    if extra:
        raise ParamError("unknown preset key(s): %s" % sorted(extra))
    for key in ("preset_format", "name", "org", "dimensions", "units", "profiles", "developer_functions"):
        if key not in obj:
            raise ParamError("preset key missing: " + key)
    if obj["preset_format"] != 1 or isinstance(obj["preset_format"], bool):
        raise ParamError("preset_format must be 1")
    if not isinstance(obj["name"], str) or not _KEY_RE.match(obj["name"]):
        raise ParamError("preset name must match ^[a-z][a-z0-9-]*$")
    org = obj["org"]
    if not isinstance(org, dict) or set(org) - {"name", "slug", "email_domain"} or "name" not in org or "slug" not in org:
        raise ParamError("org needs name and slug (and optionally email_domain)")
    if not isinstance(org["slug"], str) or not _KEY_RE.match(org["slug"]):
        raise ParamError("org.slug must match ^[a-z][a-z0-9-]*$")
    domain = org.get("email_domain", "example.com")
    if not isinstance(domain, str) or not (domain in _OK_DOMAINS or domain.endswith(_OK_SUFFIXES)):
        raise ParamError("email_domain %r is not a reserved domain" % (domain,))
    dims = obj["dimensions"]
    if not isinstance(dims, list) or not dims or not all(isinstance(x, str) and _KEY_RE.match(x) for x in dims) \
            or len(set(dims)) != len(dims):
        raise ParamError("dimensions must be a list of distinct names")


def _check_matrix_units(obj, seen):
    dims = obj["dimensions"]
    for m in obj.get("matrix_units", []):
        if not isinstance(m, dict) or set(m) != {"key", "name", "dimension", "kind", "members"}:
            raise ParamError("a matrix unit needs key, name, dimension, kind and members")
        if m["key"] in seen:
            raise ParamError("matrix unit key %r is used twice" % m["key"])
        seen.add(m["key"])
        if m["dimension"] not in dims[1:]:
            raise ParamError("matrix unit %s: dimension must be one of the further dimensions" % m["key"])
        mem = m["members"]
        if not isinstance(mem, dict) or set(mem) != {"from_units", "count"} or not isinstance(mem["count"], int):
            raise ParamError("matrix unit %s: members needs from_units and count" % m["key"])


def _check_preset_profiles(profiles):
    if not isinstance(profiles, dict) or len(profiles) != 1 or next(iter(profiles)) not in ("shares", "counts"):
        raise ParamError("profiles needs exactly one of shares or counts")
    kind, body = next(iter(profiles.items()))
    if not isinstance(body, dict) or sorted(body) != sorted(PROFILES):
        raise ParamError("profiles.%s needs exactly the five profiles" % kind)
    if kind == "counts" and sum(body[k] for k in PROFILES if isinstance(body[k], int)) <= 0:
        raise ParamError("profile counts must not all be zero")
    if kind == "shares":
        total = sum((dec(body[k], "profiles.shares") for k in PROFILES), Decimal(0))
        if total != 1 or any(dec(body[k]) < 0 for k in PROFILES):
            raise ParamError("profile shares must be non-negative and sum to exactly 1")
    else:
        if not all(isinstance(body[k], int) and not isinstance(body[k], bool) and body[k] >= 0 for k in PROFILES):
            raise ParamError("profile counts must be integers >= 0")


def validate_preset(obj):
    _check_preset_head(obj)
    seen = set()
    _check_unit_tree(obj["units"], seen, False, "units")
    if obj["units"]["kind"] != "company":
        raise ParamError("the root unit must be a company")
    _check_matrix_units(obj, seen)
    _check_preset_profiles(obj["profiles"])
    if not isinstance(obj["developer_functions"], list):
        raise ParamError("developer_functions must be a list")
    if "parameters" in obj and not isinstance(obj["parameters"], dict):
        raise ParamError("parameters must be an object")


def scaled(value, scale):
    """max(1, floor(value * scale + 0.5)) in Decimal."""
    out = int((Decimal(value) * scale + Decimal("0.5")).to_integral_value(ROUND_FLOOR))
    return max(1, out)


def parse_scale(text):
    try:
        scale = Decimal(str(text))
    except InvalidOperation as exc:
        raise ParamError("--scale must be a decimal > 0") from exc
    if not scale.is_finite() or scale <= 0:
        raise ParamError("--scale must be a decimal > 0")
    return scale
