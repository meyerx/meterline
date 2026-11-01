"""Seat fees (spec 8.4) and the Copilot pool, discounts and overage (8.3). Money and credits are Decimal only."""

from decimal import Decimal


def seats_billed(intervals, first_day, last_day):
    """Distinct people holding a seat on any day of [first_day, last_day].

    ``intervals`` is a list of (person_id, seat_start, seat_end) with seat_end None
    for a seat that runs past the period."""
    people = set()
    for person_id, seat_start, seat_end in intervals:
        if seat_start <= last_day and (seat_end is None or seat_end >= first_day):
            people.add(person_id)
    return len(people)


def seat_fees(count, per_month):
    return Decimal(count) * per_month


# ---- the organization's pool (8.3) -------------------------------------------------------------------


def org_pool(gross, pool):
    """The organization's discount over a month.

    ``gross`` maps (day, model) to the gross credits of that day and model. The days are walked in order
    and the models of a day in sorted order. Each (day, model) takes min(gross, pool left) as its discount
    and leaves the rest as net. Returns {(day, model): (discount, net)}."""
    left = Decimal(pool)
    out = {}
    for key in sorted(gross):
        g = gross[key]
        discount = min(g, left)
        left -= discount
        out[key] = (discount, g - discount)
    return out


def person_allowance(gross_by_model, allowance):
    """A person's discount over a month (8.3): the models in sorted order use the person's own allowance up.

    Returns {model: (discount, net)}."""
    left = Decimal(allowance)
    out = {}
    for model in sorted(gross_by_model):
        g = gross_by_model[model]
        used = min(g, left)
        left -= used
        out[model] = (used, g - used)
    return out


def budget_crossed_on(net_amount_by_day, budget):
    """The first day on which the cumulative net amount exceeds the budget, or None (8.3).

    The budget is recorded only: usage is never stopped."""
    if budget is None:
        return None
    total = Decimal(0)
    for day in sorted(net_amount_by_day):
        total += net_amount_by_day[day]
        if total > budget:
            return day
    return None


class CopilotBilling:
    """The Copilot billing of one state of the ledger (8.3).

    ``rows`` are ledger rows with ``person``, ``day``, ``model`` and ``credits``; ``ranges`` the
    [(first, last)] of the months of the period; ``intervals`` the seat intervals of
    ``seats_billed``; ``included`` the included credits per seat; ``price`` the price of one credit;
    ``budget`` the budget in USD or None.

    After construction:
    - ``org[(day, model)]`` is (gross, discount, net) of the organization, for gross credits above 0;
    - ``person[(person, "YYYY-MM", model)]`` is (gross, discount, net) of a person's month;
    - ``months["YYYY-MM"]`` holds seats_billed, pool, gross, discount, overage (USD) and budget_crossed_on."""

    def __init__(self, rows, ranges, intervals, included, price, budget):
        self.price = price
        org_gross = {}
        person_gross = {}
        for row in rows:
            if row.credits <= 0:
                continue
            key = (row.day, row.model)
            org_gross[key] = org_gross.get(key, Decimal(0)) + row.credits
            pkey = (row.person, row.day.strftime("%Y-%m"), row.model)
            person_gross[pkey] = person_gross.get(pkey, Decimal(0)) + row.credits

        self.org = {}
        self.months = {}
        for first, last in ranges:
            month = first.strftime("%Y-%m")
            in_month = {k: g for k, g in org_gross.items() if first <= k[0] <= last}
            n_seats = seats_billed(intervals, first, last)
            pool = Decimal(n_seats) * included
            walked = org_pool(in_month, pool)
            daily_net = {}
            gross_total = discount_total = Decimal(0)
            for (day, model), (discount, net) in walked.items():
                g = in_month[(day, model)]
                self.org[(day, model)] = (g, discount, net)
                gross_total += g
                discount_total += discount
                daily_net[day] = daily_net.get(day, Decimal(0)) + net * price
            self.months[month] = {
                "seats_billed": n_seats, "pool": pool, "gross": gross_total, "discount": discount_total,
                "overage": (gross_total - discount_total) * price,
                "budget_crossed_on": budget_crossed_on(daily_net, budget),
            }

        self.person = {}
        by_person_month = {}
        for (person, month, model), g in person_gross.items():
            by_person_month.setdefault((person, month), {})[model] = g
        for (person, month), models in by_person_month.items():
            for model, (discount, net) in person_allowance(models, included).items():
                self.person[(person, month, model)] = (models[model], discount, net)

    def keep_revised_keys(self, earlier):
        """Keys that the ``earlier`` state billed and this one no longer does keep a zero entry.

        A correction can bring a key to no credits at all, and the format has no deletion marker: the
        silver record under the key has to be revised to zero."""
        zero = (Decimal(0), Decimal(0), Decimal(0))
        for key in earlier.org:
            self.org.setdefault(key, zero)
        for key in earlier.person:
            self.person.setdefault(key, zero)
