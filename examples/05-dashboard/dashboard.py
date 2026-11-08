"""A minimal dashboard that serves one audience folder of the published gold folder, and nothing else.

    python examples/05-dashboard/dashboard.py GOLD_ROOT --audience manager:<key> [--port 8080] [--check]

GOLD_ROOT is the job's gold root; the dashboard reads ``GOLD_ROOT/published`` only, never ``builds/`` or ``frozen/``. One process
serves one audience, so a real deployment starts one per reader (or puts its own sign-in in front and picks the audience). Every
request path goes through ``meterline_gold.servable(path, audience)`` and then through a check that the real file, after links
are followed, still sits inside the audience's own folder. It binds to 127.0.0.1 unless ``--host`` says otherwise. An empty cell
on a row whose ``hidden`` flag is ``true`` is shown as "withheld", never as 0. Standard library and ``meterline_gold`` only.

Exit codes: 0 done, 2 the input is wrong (``published/`` is absent, or the audience is unknown or has no folder).
"""
import argparse
import csv
import html
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from meterline_gold import servable

DEFAULT_HOST, DEFAULT_PORT, ROWS_SHOWN = "127.0.0.1", 8080, 20
FOLDERS = {"central": "central", "exec": "exec", "manager": "manager/%s", "person": "person/%s"}
HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
           "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"}


def folder_of(audience):
    """The folder of ``published/`` that ``audience`` reads, such as ``exec`` or ``manager/<key>``; ValueError if it is not one."""
    name, _, key = audience.partition(":")
    if name not in FOLDERS or bool(key) != ("%s" in FOLDERS[name]):
        raise ValueError("audience %r: use central, exec, manager:<key> or person:<key>" % audience)
    return FOLDERS[name] % key if key else FOLDERS[name]


def inside(path, base):
    """True when the real ``path`` (links followed) is ``base`` or below it."""
    return path == base or base in path.parents


def resolve(published, audience, rel):
    """The file of ``published`` that the request path ``rel`` (relative to the gold folder) names for ``audience``.

    PermissionError when ``servable`` refuses it or a link leads out of the audience's folder; FileNotFoundError when it is
    not a file."""
    if not servable(rel, audience):
        raise PermissionError("%s is not servable to %s" % (rel, audience))
    base = Path(published) if rel == "manifest.json" else Path(published) / folder_of(audience)  # only central is served the manifest
    real = (Path(published) / rel).resolve()
    if not inside(real, base.resolve()):
        raise PermissionError("%s leads out of the folder of %s" % (rel, audience))
    if not real.is_file():
        raise FileNotFoundError(rel)
    return real


def cell(value, hidden):
    return html.escape(value) if value != "" else ("none" if hidden == "false" else "withheld")


def table(published, audience, rel):
    with resolve(published, audience, rel).open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    head = "".join("<th>%s</th>" % html.escape(c) for c in (rows[0] if rows else {}))
    body = "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % cell(v, r.get("hidden")) for v in r.values()) for r in rows[:ROWS_SHOWN])
    return "<h2><a href=\"/%s\">%s</a></h2><table><tr>%s</tr>%s</table>" % (html.escape(rel), html.escape(rel.rsplit("/", 1)[-1]), head, body)


def render(published, audience):
    """The page of the audience: the first rows of every CSV file of its folder that it may be served."""
    folder = folder_of(audience)
    names = sorted(p.name for p in (Path(published) / folder).glob("*.csv") if servable(folder + "/" + p.name, audience))
    sections = "".join(table(published, audience, folder + "/" + name) for name in names)
    return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><title>%s</title></head><body><h1>%s</h1>"
            "<p>An empty figure is never zero: \"withheld\" means a privacy rule hides it, \"none\" means the row has no such value.</p>%s"
            "</body></html>\n") % (html.escape(audience), html.escape(audience), sections or "<p>No file for this audience.</p>")


def handler(published, audience):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = unquote(urlsplit(self.path).path).lstrip("/")
            try:
                if path == "":
                    body, kind = render(published, audience).encode("utf-8"), "text/html; charset=utf-8"
                else:
                    body, kind = resolve(published, audience, path).read_bytes(), "text/plain; charset=utf-8"
                self.reply(200, body, kind)
            except PermissionError:
                self.reply(403, b"refused\n", "text/plain")
            except (FileNotFoundError, OSError):
                self.reply(404, b"not found\n", "text/plain")

        def reply(self, code, body, kind):
            self.send_response(code)
            for name, value in dict(HEADERS, **{"Content-Type": kind, "Content-Length": str(len(body))}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # the default log line holds the client address and the path
            pass

    return Handler


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("gold_root")
    ap.add_argument("--audience", required=True)
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--check", action="store_true", help="render the page once and exit")
    args = ap.parse_args(argv)
    published = Path(args.gold_root) / "published"
    try:
        folder = folder_of(args.audience)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    if not published.is_dir():
        print("%s: published/ is absent; run `meterline-job run CONFIG` first" % args.gold_root, file=sys.stderr)
        return 2
    if not (published / folder).is_dir():
        print("published/%s is absent: no such audience folder" % folder, file=sys.stderr)
        return 2
    if args.check:
        render(published, args.audience)
        print("ok: rendered %s" % folder)
        return 0
    print("serving %s on http://%s:%d/" % (folder, args.host, args.port))
    ThreadingHTTPServer((args.host, args.port), handler(published, args.audience)).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
