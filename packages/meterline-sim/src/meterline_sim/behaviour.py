"""Profiles, drift, calendar and the usage ledger (spec 5, 6)."""

import math
from datetime import timedelta
from decimal import ROUND_FLOOR, Decimal

from meterline_sim.draws import HALF, bern, count, lognorm, pick, round_half_up, shuffle, stoch_round, uniform_int
from meterline_sim.params import ANTHROPIC_MODELS, COPILOT_MODELS, PROFILES

ANTHROPIC_PRODUCTS = ("chat", "cowork", "office_agent", "claude_code")
OFFICE_APPS = ("excel", "word", "powerpoint", "outlook")
OFFICE_WEIGHTS = ("0.35", "0.30", "0.15", "0.20")

D = Decimal
_ONE = D(1)


class UsageRow:
    """One ledger row of usage (spec 5.9)."""

    __slots__ = ("provider", "day", "actor", "product", "model", "requests", "uncached", "read", "w5", "w1",
                 "output", "web", "failed", "tag", "amount")

    def __init__(self, provider, day, actor, product, model, requests, uncached, read, w5, w1, output,
                 web=0, failed=0, tag="base"):
        self.provider, self.day, self.actor, self.product, self.model = provider, day, actor, product, model
        self.requests, self.uncached, self.read, self.w5, self.w1, self.output = requests, uncached, read, w5, w1, output
        self.web, self.failed, self.tag = web, failed, tag
        self.amount = None


class ActivityRow:
    __slots__ = ("day", "person", "product", "core", "counters")

    def __init__(self, day, person, product, core, counters):
        self.day, self.person, self.product, self.core, self.counters = day, person, product, core, counters


class CopilotDay:
    """One ledger row of Copilot use: a seat holder's Copilot-active day (5.7, 5.9).

    ``cli_prompts`` and ``app_prompts`` are 0 when the surface was not used. ``credits`` is a Decimal;
    ``tag`` is ``base``, or ``correction`` once the late correction (7.4) changed the credits."""

    __slots__ = ("day", "person", "model", "shown", "accepted", "completion_lines", "ask", "agent",
                 "agent_lines", "agent_deleted", "cli_prompts", "cli_in", "cli_out", "app_prompts", "app_in",
                 "app_out", "custom_agents", "mcp", "plugins", "skills", "slash", "credits", "tag")

    def __init__(self, **fields):
        for name in self.__slots__:
            setattr(self, name, fields[name])

    def corrected(self, credits):
        """The same row with other credits and the tag ``correction``."""
        return CopilotDay(**dict({n: getattr(self, n) for n in self.__slots__}, credits=credits, tag="correction"))


class CopilotLedger:
    """The Copilot side of the ledger: the seats that gate the product and the rows drawn for them."""

    def __init__(self, seats, credits_per_interaction):
        self.seats = seats
        self.credits_per_interaction = credits_per_interaction
        self.rows = []


CORE8 = ("sessions", "messages", "commits", "pull_requests", "lines_added", "lines_removed",
         "edits_accepted", "edits_rejected")
COUNTERS5 = ("conversations", "artifacts", "skills_used", "connectors_used", "web_searches")


def split_cache(input_total, f_read, f_write, f_1h):
    """The cache buckets of 5.4, in Decimal with half-up rounding."""
    cache_read = round_half_up(D(input_total) * f_read)
    cache_write = min(round_half_up(D(input_total) * f_write), input_total - cache_read)
    write_1h = round_half_up(D(cache_write) * f_1h)
    write_5m = cache_write - write_1h
    uncached = input_total - cache_read - cache_write
    return uncached, cache_read, write_5m, write_1h


def tokens_from_draws(requests, in_per_req, out_per_req, f_read, f_write, f_1h):
    input_total = max(1, math.floor(requests * in_per_req + HALF))
    output = math.floor(requests * out_per_req + HALF)
    return split_cache(input_total, f_read, f_write, f_1h) + (output,)


def tokens_from_medians(requests, in_median, out_median, f_read, f_write, f_1h):
    """Runaway rows (7.3): the same split with the medians, in Decimal and without draws."""
    input_total = max(1, round_half_up(D(requests) * in_median))
    output = round_half_up(D(requests) * out_median)
    return split_cache(input_total, f_read, f_write, f_1h) + (output,)


# ---- calendar -------------------------------------------------------------------------


class Calendar:
    def __init__(self, holidays):
        self.ranges = [(h["from"], h["to"], D(h["factor"])) for h in holidays]

    def holiday_factor(self, day):
        mmdd = "%02d-%02d" % (day.month, day.day)
        factor = _ONE
        for lo, hi, value in self.ranges:
            inside = (lo <= mmdd <= hi) if lo <= hi else (mmdd >= lo or mmdd <= hi)
            if inside:
                factor = factor * value
        return factor


# ---- profiles ----------------------------------------------------------------------------


class Profiles:
    def __init__(self, preset, params, world, streams):
        self.preset = preset
        self.params = params
        self.world = world
        self.streams = streams
        self.hist = {}
        self.targets = {}

    def on(self, pid, day):
        current = None
        for from_day, profile in self.hist[pid]:
            if from_day <= day:
                current = profile
            else:
                break
        return current

    def add(self, pid, day, profile):
        self.hist[pid].append((day, profile))

    def compute_targets(self, n):
        body = self.preset["profiles"]
        if "counts" in body:
            counts = body["counts"]
            if sum(counts[p] for p in PROFILES) == n:
                return {p: counts[p] for p in PROFILES}
            total = sum(counts[p] for p in PROFILES)
            q = {p: D(counts[p]) / D(total) * n for p in PROFILES}
        else:
            q = {p: D(body["shares"][p]) * n for p in PROFILES}
        out = {p: int(q[p].to_integral_value(ROUND_FLOOR)) for p in PROFILES}
        left = n - sum(out.values())
        order = sorted(PROFILES, key=lambda p: (-(q[p] - out[p]), PROFILES.index(p)))
        for p in order[:left]:
            out[p] += 1
        return out

    def assign_initial(self, start):
        people = [p for p in self.world.employed_on(start)]
        targets = self.compute_targets(len(people))
        self.targets = dict(targets)
        r = self.streams.get("profiles")
        dev_functions = self.preset["developer_functions"]
        assigned = {}
        candidates = shuffle(r, [p.pid for p in people if p.function in dev_functions])
        n_dev = min(targets["developer"], len(candidates))
        for pid in candidates[:n_dev]:
            assigned[pid] = "developer"
        n_office = targets["office_worker"] + (targets["developer"] - n_dev)
        remaining = [p.pid for p in people if p.pid not in assigned]
        power = shuffle(r, remaining)
        for pid in power[:targets["power_user"]]:
            assigned[pid] = "power_user"
        rest = shuffle(r, [p.pid for p in people if p.pid not in assigned])
        for pid in rest[:n_office]:
            assigned[pid] = "office_worker"
        for pid in rest[n_office:n_office + targets["curious"]]:
            assigned[pid] = "curious"
        for pid in rest[n_office + targets["curious"]:]:
            assigned[pid] = "dormant"
        for p in people:
            self.hist[p.pid] = [(start, assigned[p.pid])]

    def drift(self, f):
        dev_functions = self.preset["developer_functions"]
        drift_params = self.params["behaviour"]["drift"]
        for p in self.world.employed_on(f):
            current = self.on(p.pid, f)
            outcomes = drift_params[current]
            items, weights = [], []
            total = D(0)
            for target, prob in outcomes.items():
                weight = D(prob)
                if target == "developer" and p.function not in dev_functions:
                    weight = D(0)
                items.append(target)
                weights.append(weight)
                total += weight
            items.append("stay")
            weights.append(_ONE - total)
            chosen = pick(self.streams.get("drift|" + p.pid), items, weights)
            if chosen != "stay":
                self.add(p.pid, f, chosen)


# ---- the ledger -------------------------------------------------------------------------------


class _Cfg:
    """Per-profile parameters converted once."""

    def __init__(self, params):
        beh = params["behaviour"]
        self.profile = {}
        for name in PROFILES:
            pr = beh["profiles"][name]
            self.profile[name] = {
                "p_active": D(pr["p_active"]),
                "weekend": D(pr["weekend_factor"]),
                "products": [(prod, pr["products"][prod]) for prod in ANTHROPIC_PRODUCTS if prod in pr["products"]],
                "copilot": pr["products"].get("copilot"),
            }
        self.copilot = {}
        for name, row in beh["copilot"].items():
            self.copilot[name] = {
                "shown": row["shown_median"], "ask": row["ask_median"], "agent": row["agent_median"],
                "cli": row["cli_median"], "app": row["app_median"],
                "weights": [row["model_weights"][m] for m in COPILOT_MODELS],
            }
        self.volume = {}
        for name in PROFILES:
            self.volume[name] = {}
            for prod, v in beh["volume"][name].items():
                self.volume[name][prod] = (
                    v["req_median"], v["req_sigma"],
                    [v["model_weights"][m] for m in ANTHROPIC_MODELS],
                )
        self.tokens = {}
        for prod, t in beh["tokens"].items():
            self.tokens[prod] = {
                "in_median": t["in_median"], "in_sigma": t["in_sigma"],
                "out_median": t["out_median"], "out_sigma": t["out_sigma"],
                "f_read": D(t["f_read"]), "f_write": D(t["f_write"]), "f_1h": D(t["f_1h"]),
                "web_rate": D(t["web_rate"]),
            }


def _anthropic_day(r, pid, day, product, cfg, vol, tok, rows, activity):
    """The draws of one product on one active day (5.4 and 5.6); the gate was already drawn."""
    req_median, req_sigma, weights = vol
    requests = count(r, req_median, req_sigma, 1)
    model = pick(r, ANTHROPIC_MODELS, weights)
    in_per_req = lognorm(r, tok["in_median"], tok["in_sigma"])
    out_per_req = lognorm(r, tok["out_median"], tok["out_sigma"])
    web = 0
    if tok["web_rate"] > 0:
        web = stoch_round(r, D(requests) * tok["web_rate"])
    uncached, read, w5, w1, output = tokens_from_draws(
        requests, in_per_req, out_per_req, tok["f_read"], tok["f_write"], tok["f_1h"])
    rows.append(UsageRow("anthropic", day, pid, product, model, requests, uncached, read, w5, w1, output, web=web))

    core = dict.fromkeys(CORE8, 0)
    counters = dict.fromkeys(COUNTERS5, 0)
    act_product = product
    if product == "chat":
        conversations = max(1, stoch_round(r, D(requests) / D(5)))
        artifacts = stoch_round(r, D(conversations) * D("0.2"))
        skills = stoch_round(r, D(conversations) * D("0.1"))
        connectors = stoch_round(r, D(conversations) * D("0.1"))
        core["messages"] = requests
        counters.update(conversations=conversations, artifacts=artifacts, skills_used=skills,
                        connectors_used=connectors)
    elif product == "cowork":
        sessions = max(1, stoch_round(r, D(requests) / D(6)))
        artifacts = stoch_round(r, D(sessions) * D("0.5"))
        skills = stoch_round(r, D(sessions) * D("0.3"))
        connectors = stoch_round(r, D(sessions) * D("0.2"))
        core["sessions"], core["messages"] = sessions, requests
        counters.update(artifacts=artifacts, skills_used=skills, connectors_used=connectors)
    elif product == "office_agent":
        app = pick(r, OFFICE_APPS, OFFICE_WEIGHTS)
        sessions = max(1, stoch_round(r, D(requests) / D(5)))
        core["sessions"], core["messages"] = sessions, requests
        act_product = "office_agent." + app
    else:  # claude_code
        sessions = max(1, stoch_round(r, D(requests) / D(40)))
        x = lognorm(r, "4", "0.5")
        lines_added = stoch_round(r, requests * x)
        lines_removed = stoch_round(r, D(lines_added) * D("0.3"))
        edits_accepted = stoch_round(r, D(requests) * D("0.25"))
        edits_rejected = stoch_round(r, D(edits_accepted) * D("0.1"))
        commits = stoch_round(r, D(sessions) * D("0.8"))
        pull_requests = stoch_round(r, D(commits) * D("0.3"))
        core.update(sessions=sessions, lines_added=lines_added, lines_removed=lines_removed,
                    edits_accepted=edits_accepted, edits_rejected=edits_rejected, commits=commits,
                    pull_requests=pull_requests)
    activity.append(ActivityRow(day, pid, act_product, core, counters))
    return web


def _copilot_day(r, pid, day, cfg, credits_per_interaction):
    """The draws of one Copilot-active day, in the order of 5.7; the product gate was already drawn."""
    shown = count(r, cfg["shown"], "0.6", 1)
    accepted = stoch_round(r, D(shown) * D("0.28"))
    completion_lines = stoch_round(r, D(accepted) * D("2.5"))
    ask = count(r, cfg["ask"], "0.7", 0)
    agent = count(r, cfg["agent"], "0.8", 0)
    agent_lines = stoch_round(r, D(agent) * D(15))
    agent_deleted = stoch_round(r, D(agent_lines) * D("0.3"))
    cli_prompts = cli_in = cli_out = 0
    if bern(r, "0.25"):
        cli_prompts = count(r, cfg["cli"], "0.6", 1)
        cli_in = count(r, "4000", "0.4", 1)
        cli_out = count(r, "600", "0.5", 1)
    app_prompts = app_in = app_out = 0
    if bern(r, "0.10"):
        app_prompts = count(r, cfg["app"], "0.6", 1)
        app_in = count(r, "3000", "0.4", 1)
        app_out = count(r, "500", "0.5", 1)
    model = pick(r, COPILOT_MODELS, cfg["weights"])
    custom_agents = uniform_int(r, 0, 2)
    mcp = uniform_int(r, 0, 3)
    plugins = uniform_int(r, 0, 1)
    skills = uniform_int(r, 0, 2)
    slash = uniform_int(r, 0, 3)
    credits = D(ask + agent + cli_prompts + app_prompts) * credits_per_interaction[model]
    return CopilotDay(
        day=day, person=pid, model=model, shown=shown, accepted=accepted, completion_lines=completion_lines,
        ask=ask, agent=agent, agent_lines=agent_lines, agent_deleted=agent_deleted, cli_prompts=cli_prompts,
        cli_in=cli_in, cli_out=cli_out, app_prompts=app_prompts, app_in=app_in, app_out=app_out,
        custom_agents=custom_agents, mcp=mcp, plugins=plugins, skills=skills, slash=slash, credits=credits,
        tag="base")


def generate_ledger(world, profiles, params, streams, days, copilot=None):
    """The base ledger of the period: people day by day, then service accounts (7, step 4).

    ``copilot`` is a ``CopilotLedger`` when Copilot is enabled: its rows are filled in beside the returned
    ones, from the seat holders' own streams. Returns (usage rows, activity rows)."""
    cfg = _Cfg(params)
    cal = Calendar(params["calendar"]["holidays"])
    usage, activity = [], []
    one = _ONE
    for day in days:
        weekend = day.weekday() >= 5
        holiday = cal.holiday_factor(day)
        for pid in world.person_order:
            person = world.people[pid]
            if not person.employed(day):
                continue
            profile = profiles.on(pid, day)
            pc = cfg.profile[profile]
            p = pc["p_active"]
            if weekend:
                p = p * pc["weekend"]
            p = min(one, p * holiday)
            if not bern(streams.get("active|" + pid), p):
                continue
            day_web = 0
            for product, gate in pc["products"]:
                r = streams.get("use|%s|%s" % (pid, product))
                if not bern(r, gate):
                    continue
                day_web += _anthropic_day(r, pid, day, product, cfg, cfg.volume[profile][product],
                                          cfg.tokens[product], usage, activity)
            # Copilot is the last product of the day and needs a seat that day (5.2, 5.5)
            if copilot is not None and pc["copilot"] is not None and copilot.seats.holds(pid, day):
                r = streams.get("use|%s|copilot" % pid)
                if bern(r, pc["copilot"]):
                    copilot.rows.append(_copilot_day(r, pid, day, cfg.copilot[profile],
                                                     copilot.credits_per_interaction))
            if day_web > 0:
                core = dict.fromkeys(CORE8, 0)
                counters = dict.fromkeys(COUNTERS5, 0)
                counters["web_searches"] = day_web
                activity.append(ActivityRow(day, pid, "all", core, counters))
    return usage, activity


def generate_service_accounts(params, streams, days):
    """LiteLLM service-account rows (9.1): every day of the period, in id order."""
    rows = []
    prov = params["providers"]["litellm"]
    accounts = params["behaviour"]["service_accounts"]
    for sa_id in sorted(prov["accounts"]):
        sa = accounts[sa_id]
        r = streams.get("use|" + sa_id)
        f_read, f_write = D(sa["f_read"]), D(sa["f_write"])
        for day in days:
            requests = count(r, sa["req_median"], sa["req_sigma"], 1)
            in_per_req = lognorm(r, sa["in_median"], "0.4")
            out_per_req = lognorm(r, sa["out_median"], "0.5")
            failed = stoch_round(r, D(requests) * D(sa["failure_rate"]))
            uncached, read, w5, w1, output = tokens_from_draws(
                requests, in_per_req, out_per_req, f_read, f_write, D(0))
            rows.append(UsageRow("litellm", day, sa_id, "", sa["model"], requests, uncached, read, w5, w1,
                                 output, failed=failed))
    return rows


def iter_days(first, last):
    day = first
    while day <= last:
        yield day
        day += timedelta(days=1)
