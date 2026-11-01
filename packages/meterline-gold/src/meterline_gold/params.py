"""Pydantic models of the policy file (spec 4) and the price list (spec 3.3), YAML loading, schema export.

Every amount, price, threshold and fraction is a quoted decimal string; an unquoted number is a validation
error (analysis F2). Unknown keys are errors. Nothing here reads a clock or the network.
"""

import json
import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from pydantic.functional_validators import BeforeValidator
from typing_extensions import Annotated

from meterline_gold.errors import InputError
from meterline_gold.iso4217 import is_iso_4217

DECIMAL_PATTERN = r"^[0-9]+(\.[0-9]{1,12})?$"
INTEGER_PATTERN = r"^[0-9]+$"
Dec = Annotated[str, Field(pattern=DECIMAL_PATTERN)]
Dec6 = Annotated[str, Field(pattern=r"^[0-9]+(\.[0-9]{1,6})?$")]


def _iso_currency(value):
    if not is_iso_4217(value):
        raise ValueError("currency %r is not an ISO 4217 code (three upper-case letters, such as USD or EUR)" % (value,))
    return value


def _to_date(value):
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    raise ValueError("expected a date written YYYY-MM-DD")


YDate = Annotated[date, BeforeValidator(_to_date)]

BASE_ATTRIBUTES = ("function", "level", "cost_center", "population")
NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")
SCENARIO_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


# ---- the policy --------------------------------------------------------------------------------


ATTRS_KEEP_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class People(_Strict):
    attrs_keep: List[str] = []

    @field_validator("attrs_keep")
    @classmethod
    def _names(cls, value):
        # spec 4.3 (v1.1): a kept attribute name never holds %, | or = (it is written into cell ids)
        for name in value:
            if not ATTRS_KEEP_RE.match(name):
                raise ValueError("attrs_keep entry %r must match [A-Za-z0-9_-]+" % name)
        return value


class Tree(_Strict):
    kind: Literal["unit", "attributes"]
    attributes: Optional[List[str]] = None

    @model_validator(mode="after")
    def _check(self):
        if self.kind == "attributes":
            if not self.attributes:
                raise ValueError("tree kind attributes needs at least one attribute")
            if len(set(self.attributes)) != len(self.attributes):
                raise ValueError("tree attributes must not repeat")
        elif self.attributes is not None:
            raise ValueError("tree kind unit takes no attributes")
        return self


class Privacy(_Strict):
    k: int = Field(ge=2)
    tree: Tree
    collapse: Dict[str, Dict[str, str]] = {}
    compare_by: List[str]


class Threshold(_Strict):
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)
    band: str
    from_: str = Field(alias="from", pattern=DECIMAL_PATTERN)


class Bands(_Strict):
    metric: Literal["cost_ref", "tokens"] = "cost_ref"
    thresholds: List[Threshold] = Field(min_length=1)

    @model_validator(mode="after")
    def _check(self):
        values = [Decimal(t.from_) for t in self.thresholds]
        if values[0] != 0:
            raise ValueError("the first band threshold must be \"0\"")
        if any(b <= a for a, b in zip(values, values[1:], strict=False)):  # pairs of neighbours: one shorter on purpose
            raise ValueError("band thresholds must be strictly ascending")
        if self.metric == "tokens" and any(not re.match(INTEGER_PATTERN, t.from_) for t in self.thresholds):
            raise ValueError("token band thresholds are integer strings")
        return self


class Seats(_Strict):
    idle_after_days: int = Field(ge=1)


class Budget(_Strict):
    provider: str
    provider_account_id: str
    monthly_amount: Dec


class Pools(_Strict):
    budgets: List[Budget] = []


class Spike(_Strict):
    multiplier: Dec
    floor: Dec
    trailing_days: int = Field(ge=1)
    min_history_days: int = Field(ge=1)


class Alerts(_Strict):
    daily_breaker: Optional[Dec] = None
    spike: Optional[Spike] = None


class ExecAudience(_Strict):
    max_depth: int = Field(default=1, ge=0)


class Audiences(_Strict):
    exec: ExecAudience = ExecAudience()


class NamedException(_Strict):
    id: str
    audience: Literal["central", "manager"]
    condition: Literal["month_cost_ref_above", "breaker_events_at_least", "spike_events_at_least"]
    threshold: str

    @model_validator(mode="after")
    def _check(self):
        if self.condition == "month_cost_ref_above":
            if self.audience != "central":
                raise ValueError("month_cost_ref_above is allowed for audience central only")
            if not re.match(DECIMAL_PATTERN, self.threshold):
                raise ValueError("a money threshold is a decimal string")
        elif not re.match(INTEGER_PATTERN, self.threshold):
            raise ValueError("a count threshold is an integer string")
        return self


class PersonCaps(_Strict):
    default: Optional[Dec] = None
    by: Optional[str] = None
    values: Dict[str, Dec] = {}


class UnitEnvelopes(_Strict):
    values: Dict[str, Dec]


class Scenario(_Strict):
    id: str
    label: str
    person_caps: Optional[PersonCaps] = None
    unit_envelopes: Optional[UnitEnvelopes] = None
    alert_fractions: List[Dec] = []
    top_up_amount: Optional[Dec] = None

    @field_validator("id")
    @classmethod
    def _id(cls, value):
        if not SCENARIO_ID_RE.match(value):
            raise ValueError("scenario id must match ^[a-z][a-z0-9_]*$")
        return value


class Policy(_Strict):
    policy_format: Literal[1]
    name: str
    label: str
    currency: str
    dimensions: List[str] = Field(default=["line"], min_length=1)
    people: People = People()
    privacy: Privacy
    bands: Bands
    premium_tiers: List[str] = []
    seats: Seats
    pools: Pools = Pools()
    alerts: Alerts = Alerts()
    audiences: Audiences = Audiences()
    named_exceptions: List[NamedException] = []
    scenarios: List[Scenario] = []

    @field_validator("name")
    @classmethod
    def _name(cls, value):
        if not NAME_RE.match(value):
            raise ValueError("name must match ^[a-z][a-z0-9-]*$")
        return value

    @field_validator("currency")
    @classmethod
    def _currency(cls, value):
        return _iso_currency(value)

    def allowed_attributes(self):
        return set(BASE_ATTRIBUTES) | {"attrs." + k for k in self.people.attrs_keep}

    @model_validator(mode="after")
    def _attributes(self):
        allowed = self.allowed_attributes()

        def check(names, where):
            for name in names:
                if name not in allowed:
                    raise ValueError("%s: unknown attribute %r" % (where, name))

        if self.privacy.tree.attributes:
            check(self.privacy.tree.attributes, "privacy.tree.attributes")
        check(self.privacy.compare_by, "privacy.compare_by")
        check(self.privacy.collapse, "privacy.collapse")
        ids = [r.id for r in self.named_exceptions]
        if len(set(ids)) != len(ids):
            raise ValueError("named_exceptions ids must be unique")
        sids = [s.id for s in self.scenarios]
        if len(set(sids)) != len(sids):
            raise ValueError("scenario ids must be unique")
        for s in self.scenarios:
            if s.person_caps and s.person_caps.by is not None:
                check([s.person_caps.by], "scenarios[%s].person_caps.by" % s.id)
        return self


# ---- the price list ------------------------------------------------------------------------------


class ModelEntry(_Strict):
    model: str
    vendor: str
    family: str
    tier: str


class PerMillion(_Strict):
    input_uncached: Dec6
    cache_read: Dec6
    cache_write_5m: Dec6
    cache_write_1h: Optional[Dec6] = None
    output: Dec6


class TokenPrice(_Strict):
    provider: str
    model: str
    valid_from: YDate
    per_million: Optional[PerMillion] = None


class UnitPrice(_Strict):
    provider: str
    unit: str
    valid_from: YDate
    price: Dec


class Included(_Strict):
    provider: str
    product: str


class SeatPrice(_Strict):
    provider: str
    provider_account_id: Optional[str] = None
    valid_from: YDate
    per_month: Dec
    included_credits: Optional[Dec] = None


class PriceList(_Strict):
    price_list_format: Literal[1]
    as_of: YDate
    source: str
    currency: str
    models: List[ModelEntry] = []
    token_prices: List[TokenPrice] = []
    unit_prices: List[UnitPrice] = []
    included: List[Included] = []
    seats: List[SeatPrice] = []

    @field_validator("currency")
    @classmethod
    def _currency(cls, value):
        return _iso_currency(value)

    @model_validator(mode="after")
    def _unique(self):
        names = [m.model for m in self.models]
        if len(set(names)) != len(names):
            raise ValueError("models: model must be unique")
        keys = [(t.provider, t.model, t.valid_from) for t in self.token_prices]
        if len(set(keys)) != len(keys):
            raise ValueError("token_prices: (provider, model, valid_from) must be unique")
        unit_keys = [(u.provider, u.unit, u.valid_from) for u in self.unit_prices]
        if len(set(unit_keys)) != len(unit_keys):
            raise ValueError("unit_prices: (provider, unit, valid_from) must be unique")
        seat_keys = [(s.provider, s.provider_account_id, s.valid_from) for s in self.seats]
        if len(set(seat_keys)) != len(seat_keys):
            raise ValueError("seats: (provider, provider_account_id, valid_from) must be unique")
        return self


# ---- loading and schema export --------------------------------------------------------------------


def _where(exc):
    lines = []
    for err in exc.errors():
        path = ".".join(str(p) for p in err["loc"]) or "(file)"
        lines.append("%s: %s" % (path, err["msg"]))
    return "; ".join(lines)


def load_yaml(path, model):
    """Read ``path`` with yaml.safe_load and validate it with ``model``. Raises InputError."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise InputError("%s: cannot read (%s)" % (path, exc)) from exc
    try:
        obj = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise InputError("%s: not valid YAML (%s)" % (path, exc)) from exc
    if not isinstance(obj, dict):
        raise InputError("%s: expected a mapping at the top level" % path)
    try:
        return model.model_validate(obj)
    except ValidationError as exc:
        raise InputError("%s: %s" % (path, _where(exc))) from exc


def load_policy(path):
    return load_yaml(path, Policy)


def load_prices(path):
    return load_yaml(path, PriceList)


def schema_text(model):
    return json.dumps(model.model_json_schema(mode="validation"), sort_keys=True, indent=2) + "\n"


def write_schemas(out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "policy.schema.json").write_text(schema_text(Policy), encoding="utf-8")
    (out / "prices.schema.json").write_text(schema_text(PriceList), encoding="utf-8")


def canonical_hash_input(model):
    return json.dumps(model.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
