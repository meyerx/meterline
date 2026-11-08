"""Write one message per person, with that person's own figures, as .eml files. Sending is locked.

    python examples/07-person-messages/person_messages.py GOLD_ROOT --out MAIL_FOLDER [--month YYYY-MM] [--sender ADDRESS] [--send]

GOLD_ROOT is the job's gold root. The figures come from ``published/person/<key>/person_month.csv`` and
``published/person/<key>/comparison.csv`` (a comparison shown only where its ``hidden`` flag is false). The name and the address
come from ``builds/<latest>/delivery/directory.csv``, read in place: ``<latest>`` is the build that
``published/central/published.json`` names. The directory is central-side material that only a sender reads; this program is that
sender. It never copies the directory, never reads one from ``published/``, and has no option to name another. People are joined on
``person_key``; a key missing from the directory, a name or address that is not plain, or a person with no row for the month gets no
message, and the summary counts them without naming anyone. The month is a month that ``published.json`` lists as frozen (the latest
one by default), so a month that a late correction can still change is not sent. A message carries the person's name and figures
only: no person key, no manager key, in a header, in the body or in a file name (the files are numbered).

Messages are written into ``--out``, which must be new or empty and outside the gold root. Sending through Amazon SES needs both
``--send`` and ``METERLINE_MAIL_CONFIRM=yes`` in the environment, is refused whenever ``CI`` is set, refuses the example sender
address, and imports ``boto3`` only on that path (``pip install boto3``; it is not a dependency of any meterline package). The
sending code is written and tested for its refusals only: no test and no CI job sends.

Exit codes: 0 done, 2 the input is wrong or sending is refused. Standard library only (``boto3`` on the send path).
"""
import argparse
import csv
import email.utils
import importlib.util
import json
import os
import re
import sys
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from email.message import EmailMessage
from email import policy
from pathlib import Path

BUILD_NAME = re.compile(r"^\d{4}-\d{2}-\d{2}(-\d+)?$")
MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
KEY = re.compile(r"^[A-Za-z0-9_-]+$")
ADDRESS = re.compile(r"^[^@\s<>,;\"'()\[\]\\]+@[^@\s<>,;\"'()\[\]\\]+\.[^@\s<>,;\"'()\[\]\\]+$")
CONTROL = re.compile(r"[\x00-\x1f\x7f]")
DEFAULT_SENDER = "usage-reports@example.com"
CONFIRM_VARIABLE, CONFIRM_VALUE = "METERLINE_MAIL_CONFIRM", "yes"
PRIVATE_UMASK = 0o077  # files 0600, folders 0700


class InputError(Exception):
    """The input is wrong or sending is refused: exit 2, the message says what to look at."""


def inside(path, base):
    return path == base or base in path.parents


def regular_file_inside(path, folder):
    path, folder = Path(path), Path(folder)
    return path.is_file() and not path.is_symlink() and inside(path.resolve(), folder.resolve())


def read_published(gold_root):
    """(real published folder, name of the latest build, frozen months) from ``published/central/published.json``."""
    link = Path(gold_root) / "published"
    if not link.is_dir():
        raise InputError("%s: published/ is absent (give the gold root of the job, not the published folder)" % gold_root)
    published = link.resolve()
    info = published / "central" / "published.json"
    if not regular_file_inside(info, published):
        raise InputError("%s: central/published.json is absent" % published.name)
    try:
        data = json.loads(info.read_text(encoding="utf-8"))
        latest, frozen = data["latest_build"], data["frozen_months"]
    except (ValueError, KeyError, TypeError) as exc:
        raise InputError("central/published.json does not name the latest build and the frozen months (%s)" % type(exc).__name__)
    if not (isinstance(latest, str) and BUILD_NAME.match(latest)) or not (isinstance(frozen, list) and all(isinstance(m, str) and MONTH.match(m) for m in frozen)):
        raise InputError("central/published.json holds a build name or a month that is not valid")
    return published, latest, sorted(frozen)


def directory_path(gold_root, latest):
    """``builds/<latest>/delivery/directory.csv`` of the gold root; InputError when it is absent."""
    folder = Path(gold_root) / "builds" / latest / "delivery"
    path = folder / "directory.csv"
    if not regular_file_inside(path, Path(gold_root) / "builds"):
        raise InputError("builds/%s/delivery/directory.csv is absent" % latest)
    return path


def directory_by_key(path):
    """{person_key: {"given_name", "email"}} of the directory file. A key that appears twice is left out: it names no one."""
    seen, rows = set(), {}
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if not {"person_key", "given_name", "email"} <= set(reader.fieldnames or []):
            raise InputError("directory.csv lacks the columns person_key, given_name and email")
        for row in reader:
            key = row["person_key"]
            if key in seen:
                rows.pop(key, None)
                continue
            seen.add(key)
            rows[key] = {"given_name": row["given_name"], "email": row["email"]}
    return rows


def plain(value, pattern=None):
    """True when the text has no control character (so no way to add a header) and, if given, matches the pattern."""
    return bool(value) and not CONTROL.search(value) and (pattern is None or bool(pattern.match(value)))


def rounded(text):
    try:
        return str(Decimal(text).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, TypeError):
        return None


def whole(text):
    try:
        return str(int(Decimal(text)))
    except (InvalidOperation, TypeError, ValueError):
        return None


def read_rows(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def person_figures(published, key, month):
    """The person's own figures of ``month`` from their folder, or None when there is no row to show."""
    folder = published / "person" / key
    monthly = folder / "person_month.csv"
    if not regular_file_inside(monthly, folder):
        return None
    rows = [r for r in read_rows(monthly) if r.get("month") == month]
    if len(rows) != 1:
        return None
    row = rows[0]
    figures = {"active_days": whole(row.get("active_days")), "requests": whole(row.get("requests")), "cost": rounded(row.get("cost_ref"))}
    tokens = [whole(row.get("tokens_input_total")), whole(row.get("tokens_output"))]
    figures["tokens"] = str(sum(int(t) for t in tokens)) if all(t is not None for t in tokens) else None
    figures["band"] = None
    comparison = folder / "comparison.csv"
    if regular_file_inside(comparison, folder):
        shown = [r for r in read_rows(comparison) if r.get("month") == month and r.get("hidden") == "false" and r.get("comparison_band")]
        figures["band"] = shown[0]["comparison_band"] if len(shown) == 1 else None
    return figures


BODY = """Hello {name},

Here are your own AI usage figures for {month}:

- Active days: {active_days}
- Requests: {requests}
- Tokens (input and output): {tokens}
- Estimated cost: {cost}
{band}
The cost is a usage value in your organisation's reporting currency, not an invoice.
This message holds your own figures only.
"""


def render(name, address, sender, month, figures):
    """The message for one person; a header or a name that carries a line break raises ValueError."""
    shown = {k: ("-" if v is None else v) for k, v in figures.items() if k != "band"}
    band = "- Compared with your comparison group: %s\n" % figures["band"] if figures["band"] else ""
    msg = EmailMessage(policy=policy.SMTP)
    msg["From"] = sender
    msg["To"] = email.utils.formataddr((name, address))
    msg["Subject"] = "Your AI usage for %s" % month
    msg["Message-ID"] = email.utils.make_msgid(domain=sender.rsplit("@", 1)[1])
    msg.set_content(BODY.format(name=name, month=month, band=band, **shown))
    return msg


def person_keys(published):
    folder = published / "person"
    if not folder.is_dir():
        raise InputError("%s: published/person/ is absent" % published.name)
    keys = []
    for entry in sorted(folder.iterdir()):
        if entry.is_dir() and not entry.is_symlink() and KEY.match(entry.name):
            keys.append(entry.name)
    return keys


def build_messages(published, directory, keys, month, sender):
    """([(file number, message)], skipped count): one message per person the directory names, in the order of the sorted keys."""
    messages, skipped = [], 0
    for key in keys:
        entry = directory.get(key)
        if entry is None or not plain(entry["given_name"]) or not plain(entry["email"], ADDRESS):
            skipped += 1
            continue
        figures = person_figures(published, key, month)
        if figures is None:
            skipped += 1
            continue
        try:
            messages.append(render(entry["given_name"].strip(), entry["email"].strip(), sender, month, figures))
        except ValueError:
            skipped += 1
    return messages, skipped


def write_messages(out_dir, messages):
    out_dir.mkdir(parents=True)
    written = []
    for number, msg in enumerate(messages, start=1):
        path = out_dir / ("%04d.eml" % number)
        path.write_bytes(msg.as_bytes())
        written.append(path)
    return written


def send_all(files, sender):
    """Send each written file through Amazon SES. Imports boto3 here and nowhere else; written, and tested for its refusals only."""
    import boto3  # pip install boto3: not a dependency of any meterline package
    client = boto3.client("sesv2")
    for path in files:
        raw = path.read_bytes()
        to = email.message_from_bytes(raw, policy=policy.default)["To"].addresses[0].addr_spec
        client.send_email(FromEmailAddress=sender, Destination={"ToAddresses": [to]}, Content={"Raw": {"Data": raw}})


def refuse_send_unless_allowed(sender):
    """InputError unless both switches are set, CI is not, boto3 is installed and the sender is not the example address."""
    if os.environ.get("CI"):
        raise InputError("--send is refused: CI is set. Nothing sends from CI.")
    if os.environ.get(CONFIRM_VARIABLE) != CONFIRM_VALUE:
        raise InputError("--send needs %s=%s in the environment as well" % (CONFIRM_VARIABLE, CONFIRM_VALUE))
    if importlib.util.find_spec("boto3") is None:
        raise InputError("--send needs boto3: pip install boto3")
    if sender.lower().endswith("@example.com"):
        raise InputError("--send needs --sender with an address your SES identity is verified for, not the example address")


def run(args):
    sender = args.sender
    if not (plain(sender, ADDRESS)):
        raise InputError("--sender is not a plain address")
    if args.send:
        refuse_send_unless_allowed(sender)  # before anything is read or written
    published, latest, frozen = read_published(args.gold_root)
    month = args.month or (frozen[-1] if frozen else None)
    if not month or not MONTH.match(month) or month not in frozen:
        raise InputError("--month must be a month that published.json lists as frozen: %s" % (", ".join(frozen) or "none yet"))
    out_dir = Path(args.out).resolve()
    if inside(out_dir, Path(args.gold_root).resolve()):
        raise InputError("--out is inside the gold root; write the messages elsewhere")
    if out_dir.exists() and (not out_dir.is_dir() or any(out_dir.iterdir())):
        raise InputError("--out exists and is not empty; use a new folder so that no earlier message is overwritten or mixed in")
    directory = directory_by_key(directory_path(args.gold_root, latest))
    messages, skipped = build_messages(published, directory, person_keys(published), month, sender)
    files = write_messages(out_dir, messages)
    print("wrote %d messages into %s, skipped %d (no directory entry, no plain name or address, or no figures for %s)" % (len(files), out_dir.name, skipped, month))
    if args.send:
        send_all(files, sender)
        print("sent %d messages" % len(files))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("gold_root")
    ap.add_argument("--out", required=True, help="a new or empty folder outside the gold root that receives the .eml files")
    ap.add_argument("--month", help="a frozen month, YYYY-MM (default: the latest frozen month)")
    ap.add_argument("--sender", default=DEFAULT_SENDER, help="the From address (default: an example address that --send refuses)")
    ap.add_argument("--send", action="store_true", help="also send the messages through Amazon SES (needs %s=%s, refused when CI is set)" % (CONFIRM_VARIABLE, CONFIRM_VALUE))
    args = ap.parse_args(argv)
    old_umask = os.umask(PRIVATE_UMASK)
    try:
        return run(args)
    except InputError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    finally:
        os.umask(old_umask)


if __name__ == "__main__":
    sys.exit(main())
