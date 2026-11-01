"""The invented name lists (spec 9.3), generated from a short syllable set."""

import json
from itertools import product
from pathlib import Path

from meterline_sim.draws import uniform_int
from meterline_sim.params import RunError

SYLLABLES = ["ba", "be", "da", "de", "la", "le", "li", "lo", "ma", "me", "mi", "mo", "re", "ri", "ro",
             "sa", "ta", "te", "to", "va", "ve", "vo", "za", "zo"]

NOTE = "Generated nonsense syllables; no word of any language and no real name is meant."


def generate_lists():
    given = ["".join(parts) for parts in product(SYLLABLES, repeat=2)]
    family = ["".join(parts) for parts in product(SYLLABLES, repeat=3)]
    return {"given": given, "family": family}


def load_lists():
    path = Path(__file__).parent / "data" / "names.json"
    with open(path, encoding="utf-8") as handle:
        obj = json.load(handle)
    return obj["given"], obj["family"]


class NameExhausted(RunError):
    """1,000 draws in a row hit a name pair already used (exit 1)."""


class NameDrawer:
    def __init__(self, stream):
        self.stream = stream
        self.given, self.family = load_lists()
        self.used = set()

    def draw(self):
        for _ in range(1000):
            g = self.given[uniform_int(self.stream, 0, len(self.given) - 1)]
            f = self.family[uniform_int(self.stream, 0, len(self.family) - 1)]
            if (g, f) not in self.used:
                self.used.add((g, f))
                return g, f
        raise NameExhausted("no free name pair after 1,000 tries")


def display(word):
    return word[:1].upper() + word[1:]
