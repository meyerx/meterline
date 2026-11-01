"""Units, people, memberships and identities (spec 3, 7.1, 7.2, 11.1)."""

import hashlib
import json
from datetime import timedelta

from meterline_sim.draws import bern, pick, uniform_int
from meterline_sim.names import NameDrawer, display
from meterline_sim.params import ParamError, scaled


def _cc(n):
    return "CC-%03d" % n


class Person:
    __slots__ = ("pid", "n", "start", "last_day", "unit_id", "manager_id", "function", "level", "cost_center",
                 "population", "location", "given", "family", "email", "login", "hist", "invite_date")

    def employed(self, day):
        return self.start <= day and (self.last_day is None or day <= self.last_day)

    def state(self):
        attrs = json.dumps({"location": self.location}, sort_keys=True, separators=(",", ":"))
        return (self.unit_id, self.manager_id, self.function, self.level, self.cost_center, self.population, attrs)


class World:
    """The organisation: units and people with their histories."""

    def __init__(self, preset, params, scale, start, streams):
        self.preset = preset
        self.params = params
        self.scale = scale
        self.start = start
        self.streams = streams
        self.domain = preset["org"].get("email_domain", "example.com")
        self.drawer = NameDrawer(streams.get("names"))
        self.units = {}          # unit_id -> current state
        self.unit_hist = {}      # unit_id -> [(date, state tuple or None)]
        self.unit_order = []
        self.key_to_unit = {}    # current unit key -> unit id
        self.people = {}
        self.person_order = []
        self.memberships = []    # (person_id, unit_id, dimension, role, valid_from)
        self.joiners = []        # person ids of the joiners, in creation order
        self._next_unit = 1
        self._next_person = 1
        self._next_cc = 1
        self._build()

    # ---- units ------------------------------------------------------------

    def _unit_state(self, uid):
        s = self.units[uid]
        return (s["dimension"], s["key"], s["name"], s["kind"], s["parent_id"], s["function"],
                s["cost_center"], s["head_person_id"])

    def _new_unit(self, day, **fields):
        uid = "u%04d" % self._next_unit
        self._next_unit += 1
        state = {"dimension": "line", "key": "", "name": "", "kind": "", "parent_id": None, "function": "",
                 "cost_center": None, "head_person_id": None}
        state.update(fields)
        self.units[uid] = state
        self.unit_order.append(uid)
        self.unit_hist[uid] = [(day, self._unit_state(uid))]
        self.key_to_unit[state["key"]] = uid
        return uid

    def set_unit(self, uid, day, **changes):
        self.units[uid].update(changes)
        state = self._unit_state(uid)
        hist = self.unit_hist[uid]
        if hist[-1][0] == day:
            hist[-1] = (day, state)
        elif hist[-1][1] != state:
            hist.append((day, state))

    def end_unit(self, uid, day):
        self.unit_hist[uid].append((day, None))

    def is_valid(self, uid, day):
        state = None
        for from_day, st in self.unit_hist[uid]:
            if from_day <= day:
                state = st
        return state is not None

    def parent_chain(self, uid):
        out = []
        cur = self.units[uid]["parent_id"]
        while cur is not None:
            out.append(cur)
            cur = self.units[cur]["parent_id"]
        return out

    def below_or_equal(self, uid, ancestor):
        return uid == ancestor or ancestor in self.parent_chain(uid)

    def head_above(self, uid):
        """The head of the closest unit above ``uid`` that has a head."""
        for anc in self.parent_chain(uid):
            head = self.units[anc]["head_person_id"]
            if head is not None:
                return head
        return None

    def line_units(self, day):
        """Line unit ids valid on a day, in unit-id order."""
        return [u for u in self.unit_order if self.units[u]["dimension"] == "line" and self.is_valid(u, day)]

    def children(self, uid):
        return [c for c in self.unit_order
                if self.units[c]["dimension"] == "line" and self.units[c]["parent_id"] == uid
                and self.unit_hist[c][-1][1] is not None]

    # ---- people -----------------------------------------------------------

    def new_person(self, day, unit_id, manager_id, function, level, cost_center, population=None):
        n = self._next_person
        self._next_person += 1
        p = Person()
        p.pid = "p%05d" % n
        p.n = n
        p.start = day
        p.last_day = None
        p.unit_id, p.manager_id, p.function, p.level, p.cost_center = unit_id, manager_id, function, level, cost_center
        p.invite_date = None
        p.given, p.family = self.drawer.draw()
        p.email = "%s.%s@%s" % (p.given, p.family, self.domain)
        p.login = "%s-%s" % (p.given, p.family)
        attrs = self.streams.get("attrs")
        if population is None:
            population = "contractor" if bern(attrs, self.params["contractor_share"]) else "employee"
        p.population = population
        loc = self.params["locations"]
        p.location = pick(attrs, loc["values"], loc["weights"])
        p.hist = [(day, p.state())]
        self.people[p.pid] = p
        self.person_order.append(p.pid)
        return p

    def set_person(self, pid, day, **changes):
        p = self.people[pid]
        for key, value in changes.items():
            setattr(p, key, value)
        state = p.state()
        if p.hist[-1][0] == day:
            p.hist[-1] = (day, state)
        elif p.hist[-1][1] != state:
            p.hist.append((day, state))

    def employed_on(self, day):
        return [self.people[pid] for pid in self.person_order if self.people[pid].employed(day)]

    # ---- the initial walk ---------------------------------------------------

    def _build(self):
        self._walk(self.preset["units"], None, None)
        for m in self.preset.get("matrix_units", []):
            for key in m["members"]["from_units"]:
                if key not in self.key_to_unit:
                    raise ParamError("matrix unit %s names an unknown unit: %s" % (m["key"], key))
            uid = self._new_unit(self.start, dimension=m["dimension"], key=m["key"], name=m["name"], kind=m["kind"])
            line_ids = [self.key_to_unit[k] for k in m["members"]["from_units"]]
            chosen = []
            for pid in self.person_order:
                p = self.people[pid]
                if p.employed(self.start) and any(self.below_or_equal(p.unit_id, u) for u in line_ids):
                    chosen.append(pid)
                if len(chosen) >= m["members"]["count"]:
                    break
            for pid in chosen:
                self.memberships.append((pid, uid, m["dimension"], "member", self.start))

    def _walk(self, node, parent_id, dept_cc):
        kind = node["kind"]
        if kind == "team" and dept_cc is not None:
            cc = dept_cc
        else:
            cc = _cc(self._next_cc)
            self._next_cc += 1
        uid = self._new_unit(self.start, key=node["key"], name=node["name"], kind=kind,
                             parent_id=parent_id, function=node["function"], cost_center=cc)
        if kind == "team":
            self._make_team(uid, scaled(node["size"], self.scale))
            return
        level = {"company": "L1", "division": "L2", "department": "L3"}[kind]
        head = self.new_person(self.start, uid, self.head_above(uid), node["function"], level, cc)
        self.set_unit(uid, self.start, head_person_id=head.pid)
        if "teams" in node:
            org_stream = self.streams.get("org")
            lo, hi = node["team_size"]
            for n in range(1, scaled(node["teams"], self.scale) + 1):
                size = uniform_int(org_stream, lo, hi)
                tid = self._new_unit(self.start, key="%s-%d" % (node["key"], n), name="%s %d" % (node["name"], n),
                                     kind="team", parent_id=uid, function=node["function"], cost_center=cc)
                self._make_team(tid, size)
        else:
            for child in node.get("children", []):
                self._walk(child, uid, cc if kind == "department" else None)

    def _make_team(self, uid, size):
        unit = self.units[uid]
        lead = self.new_person(self.start, uid, self.head_above(uid), unit["function"], "L4", unit["cost_center"])
        self.set_unit(uid, self.start, head_person_id=lead.pid)
        for _ in range(size - 1):
            self.new_person(self.start, uid, lead.pid, unit["function"], "L5", unit["cost_center"])

    # ---- reorganisation (7.2) --------------------------------------------------

    def reorganise(self, unit_key, f):
        """Split a unit on day ``f``. Returns the details, or None when it does not apply."""
        uid = self.key_to_unit.get(unit_key)
        if uid is None or self.unit_hist[uid][-1][1] is None:
            return None
        unit = self.units[uid]
        head_pid = unit["head_person_id"]

        def members(unit_id):
            return [self.people[pid] for pid in self.person_order
                    if self.people[pid].unit_id == unit_id and self.people[pid].employed(f)]

        kids = self.children(uid)
        half = (len(kids) + 1) // 2
        first_b = None
        if kids:
            for child in kids[half:]:
                if len(members(child)) >= 2:
                    first_b = child
                    break
            if len(kids) < 2 or first_b is None:
                return None
        elif len([m for m in members(uid) if m.pid != head_pid]) < 2:
            return None
        before = {pid: self.people[pid].state() for pid in self.person_order}

        self.end_unit(uid, f)
        old_cc = unit["cost_center"]
        a = self._new_unit(f, key=unit["key"] + "-a", name=unit["name"] + " A", kind=unit["kind"],
                           parent_id=unit["parent_id"], function=unit["function"],
                           cost_center=old_cc, head_person_id=head_pid)
        cc_b = _cc(self._next_cc)
        self._next_cc += 1
        b = self._new_unit(f, key=unit["key"] + "-b", name=unit["name"] + " B", kind=unit["kind"],
                           parent_id=unit["parent_id"], function=unit["function"], cost_center=cc_b)
        details = {"old_unit_id": uid, "new_unit_ids": [a, b], "moved_units": [], "new_heads": {}}
        parent_head = self.head_above(a)
        old_level = self.people[head_pid].level

        if kids:
            for child in kids[:half]:
                self._move_unit(child, f, a, old_cc)
            for child in kids[half:]:
                self._move_unit(child, f, b, cc_b)
            details["moved_units"] = list(kids)
            self.set_person(head_pid, f, unit_id=a)
            lead = self.people[self.units[first_b]["head_person_id"]]
            team_members = members(first_b)
            rest = [m for m in team_members if m.pid != lead.pid]
            new_lead = min(rest, key=lambda m: m.n)
            self.set_unit(b, f, head_person_id=lead.pid)
            self.set_person(lead.pid, f, unit_id=b, level="L3", manager_id=parent_head, cost_center=cc_b)
            self.set_unit(first_b, f, head_person_id=new_lead.pid)
            self.set_person(new_lead.pid, f, level="L4", manager_id=lead.pid)
            for m in rest:
                if m.pid != new_lead.pid:
                    self.set_person(m.pid, f, manager_id=new_lead.pid)
            for child in kids[half:]:
                head = self.units[child]["head_person_id"]
                if child != first_b and head is not None:
                    self.set_person(head, f, manager_id=lead.pid)
            details["new_heads"] = {a: head_pid, b: lead.pid, first_b: new_lead.pid}
        else:
            others = [m for m in members(uid) if m.pid != head_pid]
            cut = (len(others) + 1) // 2
            to_a, to_b = others[:cut], others[cut:]
            new_head = to_b[0]
            self.set_unit(b, f, head_person_id=new_head.pid)
            self.set_person(head_pid, f, unit_id=a)
            for m in to_a:
                self.set_person(m.pid, f, unit_id=a)
            self.set_person(new_head.pid, f, unit_id=b, level=old_level, manager_id=parent_head, cost_center=cc_b)
            for m in to_b[1:]:
                self.set_person(m.pid, f, unit_id=b, manager_id=new_head.pid, cost_center=cc_b)
            details["new_heads"] = {a: head_pid, b: new_head.pid}
        details["changed_people"] = sorted(
            pid for pid in self.person_order if self.people[pid].state() != before[pid])
        return details

    def _move_unit(self, child, f, new_parent, new_cc):
        self.set_unit(child, f, parent_id=new_parent, cost_center=new_cc)
        below, stack = [], [child]
        while stack:
            cur = stack.pop(0)
            below.append(cur)
            stack.extend(self.children(cur))
        for u in below:
            if u != child and self.units[u]["kind"] == "team":
                self.set_unit(u, f, cost_center=new_cc)
        for pid in self.person_order:
            p = self.people[pid]
            if p.unit_id in below and p.employed(f):
                self.set_person(pid, f, cost_center=new_cc)

    # ---- joiners ---------------------------------------------------------------

    def add_joiner(self, day, team_uid, population, invite_date):
        team = self.units[team_uid]
        p = self.new_person(day, team_uid, team["head_person_id"], team["function"], "L5",
                            team["cost_center"], population=population)
        p.invite_date = invite_date
        self.joiners.append(p.pid)
        return p

    # ---- output rows -------------------------------------------------------------

    @staticmethod
    def _spans(hist, end):
        """[(from, to or None, state)] with ``to`` inclusive; None while the row runs past ``end``."""
        rows = []
        for i, (from_day, state) in enumerate(hist):
            if state is None:
                continue
            to_day = hist[i + 1][0] - timedelta(days=1) if i + 1 < len(hist) else None
            if to_day is not None and to_day > end:
                to_day = None
            rows.append((from_day, to_day, state))
        return rows

    def unit_rows(self, end):
        out = []
        for uid in sorted(self.unit_order):
            for from_day, to_day, st in self._spans(self.unit_hist[uid], end):
                dim, key, name, kind, parent, fn, cc, head = st
                out.append([uid, from_day.isoformat(), to_day.isoformat() if to_day else "", dim, key, name, kind,
                            parent or "", fn or "", cc or "", head or ""])
        return out

    def people_rows(self, end):
        out = []
        for pid in sorted(self.person_order):
            p = self.people[pid]
            hist = list(p.hist)
            if p.last_day is not None:
                hist.append((p.last_day + timedelta(days=1), None))
            for from_day, to_day, st in self._spans(hist, end):
                unit_id, manager, fn, level, cc, population, attrs = st
                out.append([pid, from_day.isoformat(), to_day.isoformat() if to_day else "", unit_id,
                            manager or "", fn, level, cc, population, attrs])
        return out

    def membership_rows(self, end):
        out = []
        for pid, uid, dim, role, from_day in sorted(self.memberships, key=lambda m: (m[0], m[1], m[4])):
            last = self.people[pid].last_day
            out.append([pid, uid, dim, role, from_day.isoformat(),
                        last.isoformat() if last is not None and last <= end else ""])
        return out

    def directory_rows(self):
        return [[pid, display(self.people[pid].given), display(self.people[pid].family), self.people[pid].email]
                for pid in sorted(self.person_order)]


def anthropic_provider_account_id(seed):
    digest = hashlib.sha256(("anthropic-org|%d" % seed).encode("utf-8")).hexdigest()[:32]
    return "-".join([digest[0:8], digest[8:12], digest[12:16], digest[16:20], digest[20:32]])


def anthropic_actor_id(seed, person_id):
    return "user_" + hashlib.sha256(("anthropic|%d|%s" % (seed, person_id)).encode("utf-8")).hexdigest()[:24]


def copilot_actor_id(person_number):
    """``str(100000 + n)`` for person ``p<n>`` (4.2): p00042 is "100042"."""
    return str(100000 + person_number)


def first_of_next_month(day):
    """The day after the month of ``day`` ends (7.1: a leaver's pending_cancellation_date)."""
    return (day.replace(day=1) + timedelta(days=32)).replace(day=1)


class CopilotSeat:
    __slots__ = ("start", "granted_by", "assigned")

    def __init__(self, start, granted_by, assigned):
        self.start = start            # first day of the seat
        self.granted_by = granted_by  # key of the outermost granted unit that held the person's unit
        self.assigned = assigned      # the date written as assigned_at


class CopilotSeats:
    """Who holds a GitHub Copilot seat, and from when to when (4.2, 7.1).

    A person holds a seat if their line unit is in ``granted_units`` or below one of them on their seat
    start day (the period start for the initial population, the start date for a joiner). A unit that
    the reorganisation creates counts as granted when the split unit was granted or below a granted
    unit. A seat lasts until the day before the person's pending_cancellation_date, which is the first
    day of the month after their last day, or to the end of the period."""

    def __init__(self, world, params):
        gh = params["providers"]["github_copilot"]
        self.world = world
        self.plan = gh["plan"]
        self.age_days = gh["seat_age_days"]
        self.granted = {world.key_to_unit[key] for key in gh["granted_units"]}
        self.seats = {}  # person id -> CopilotSeat

    def granted_by(self, unit_id):
        """The key of the outermost granted unit that is ``unit_id`` or above it, else None."""
        found = None
        cur = unit_id
        while cur is not None:
            if cur in self.granted:
                found = cur
            cur = self.world.units[cur]["parent_id"]
        return None if found is None else self.world.units[found]["key"]

    def grant_initial(self, start, stream):
        """Seats of the people present on the period start, with their ages drawn in person-id order."""
        for person in self.world.employed_on(start):
            key = self.granted_by(person.unit_id)
            if key is None:
                continue
            age = uniform_int(stream, self.age_days[0], self.age_days[1])
            self.seats[person.pid] = CopilotSeat(start, key, start - timedelta(days=age))

    def joined(self, person):
        key = self.granted_by(person.unit_id)
        if key is not None:
            self.seats[person.pid] = CopilotSeat(person.start, key, person.start)

    def reorganised(self, old_unit_id, new_unit_ids):
        if self.granted_by(old_unit_id) is not None:
            self.granted.update(new_unit_ids)

    def has(self, person_id):
        return person_id in self.seats

    def cancellation_date(self, person_id):
        """The pending_cancellation_date of the person's seat (7.1), None for someone who stays."""
        last = self.world.people[person_id].last_day
        return None if last is None else first_of_next_month(last)

    def end_of(self, person_id):
        """The last day of the seat, None when it runs to the end of the period."""
        cancel = self.cancellation_date(person_id)
        return None if cancel is None else cancel - timedelta(days=1)

    def holds(self, person_id, day):
        seat = self.seats.get(person_id)
        if seat is None or day < seat.start:
            return False
        end = self.end_of(person_id)
        return end is None or day <= end

    def intervals(self):
        """[(person id, seat start, seat end or None)] in person-id order."""
        return [(pid, self.seats[pid].start, self.end_of(pid)) for pid in sorted(self.seats)]


class SeatBook:
    """The seats of a run: Claude Enterprise (every person, 4.1) and GitHub Copilot (``copilot``)."""

    def __init__(self, anthropic_enabled, copilot=None):
        self.anthropic_enabled = anthropic_enabled
        self.copilot = copilot

    def providers(self, person_id):
        """The providers that give the person a seat, in sorted order (the ``seats`` of the events)."""
        out = []
        if self.anthropic_enabled:
            out.append("anthropic")
        if self.copilot is not None and self.copilot.has(person_id):
            out.append("github_copilot")
        return out

    def joined(self, person):
        if self.copilot is not None:
            self.copilot.joined(person)

    def reorganised(self, details):
        if self.copilot is not None:
            self.copilot.reorganised(details["old_unit_id"], details["new_unit_ids"])
