# 05 - a minimal dashboard for one audience

**Goal.** Serve the files of one audience folder of the published gold folder over HTTP to one reader, with the standard library and `meterline_gold` only, so the pattern can be copied into a dashboard of your own. It sits beside the gold package's [static dashboard](https://github.com/meyerx/meterline/blob/main/packages/meterline-gold/README.md) and does not replace it.

**Reads.** `<gold root>/published/<folder of the audience>` and nothing else. The audience is given by `--audience`: `central`, `exec`, `manager:<key>` or `person:<key>`. Each request path goes through `meterline_gold.servable(path, audience)`, and then the real file, after links are followed, must still sit inside the audience's own folder, so a link into another reader's folder is refused. It never opens `builds/`, `frozen/` or `delivery/`. An empty cell on a row whose `hidden` column is `true` is written as "withheld", never as 0. The server binds to `127.0.0.1` unless `--host` says otherwise, and `--check` renders the page once and exits 0. If `published/` is absent it exits 2.

One process serves one audience. To serve many readers, start one process per audience behind your own sign-in, or write the sign-in and the choice of audience yourself: [the integration guide](https://github.com/meyerx/meterline/blob/main/docs/integrate/README.md) says which scenarios need that and what to keep out of the reach of a browser.

## Run it

Run this from the repository root, after the install in the README quick start. The job's `publish` step leaves a `published` link in the gold root; the commands below make the same layout by hand from a build of the simulator's output, so the example needs no key and no network.

```sh
.venv/bin/meterline-sim generate --preset tiny --seed 7 --out sim-out
export METERLINE_GOLD_KEY="a-key-of-at-least-32-bytes-that-you-keep-out-of-git"
mkdir -p dashboard-root
.venv/bin/meterline-gold build --silver sim-out/tiny-s7-m6 --org-files sim-out/tiny-s7-m6/org --policy packages/meterline-gold/src/meterline_gold/examples/policy.example.yaml --prices packages/meterline-gold/src/meterline_gold/examples/prices.example.yaml --out dashboard-root/published-1
ln -sfn published-1 dashboard-root/published
MANAGER="$(ls dashboard-root/published/manager | head -1)"
.venv/bin/python examples/05-dashboard/dashboard.py dashboard-root --audience "manager:$MANAGER" --check
```

**Expected output.** One line, `ok: rendered manager/<key>`, and exit code 0. Without `--check` the command prints `serving manager/<key> on http://127.0.0.1:8080/`; open that address, or `curl` it, to see the tables of that manager's folder. A request for another manager's folder, for `central/`, `exec/`, `person/`, `delivery/directory.csv` or `manifest.json` answers 403. The tests in `tools/test_example_05_dashboard.py` check every pair of the gold README table "Who gets which folder", a `..` path, an absolute path and a link into another manager's folder.

## Run it in the container

Run this after [example 04's "Run it" block](https://github.com/meyerx/meterline/blob/main/examples/04-container/README.md#run-it) and before its "Clean up": it needs the image `meterline-job:local` and the volume `meterline-gold`, which example 04 leaves in place with the published gold in it. The image carries this example's `dashboard.py` at `/app/dashboard.py`, and the dashboard needs no gold key, so none is passed. The block lists the keys of `published/manager/`, takes the first, and renders that manager's folder once.

```sh
KEYS="$(docker run --rm -v meterline-gold:/gold --entrypoint ls meterline-job:local /gold/published/manager)"
echo "$KEYS"
MANAGER="$(printf '%s\n' "$KEYS" | sed -n 1p)"
docker run --rm -v meterline-gold:/gold --entrypoint python meterline-job:local /app/dashboard.py /gold --audience "manager:$MANAGER" --check
```

**Expected output.** The manager keys, one per line, then `ok: rendered manager/<key>` and exit code 0.

## Serve it from the container

The same image can serve that manager's folder. There is no sign-in: whoever reaches the port reads the folder. Inside the container the server must listen on every interface (`--host 0.0.0.0`) for the published port to reach it, so the safety is in the `docker run` line: `-p 127.0.0.1:8080:8080` publishes the port on the host's loopback only. Never write `-p 8080:8080`, which publishes it on every interface of the host. Run this in the same shell as the block above, so that `$MANAGER` is set.

```sh
docker run --rm -p 127.0.0.1:8080:8080 -v meterline-gold:/gold --entrypoint python meterline-job:local /app/dashboard.py /gold --audience "manager:$MANAGER" --host 0.0.0.0
```

It prints `serving manager/<key> on http://0.0.0.0:8080/`. Open `http://127.0.0.1:8080/` on the host; stop it with Ctrl-C. Remove the volumes with [example 04's "Clean up"](https://github.com/meyerx/meterline/blob/main/examples/04-container/README.md#clean-up) when you have finished.

## Before you use it for real

- It has no sign-in. Whoever reaches the port reads the audience's folder, so keep it on `127.0.0.1` or behind a proxy that signs readers in and starts or selects the process of their audience.
- The key in `manager:<key>` is the reader's own `manager_key`; the mapping from a signed-in person to that key needs `delivery/directory.csv`, which only your server may read, never a browser.
- The server mode is not run in CI: CI runs `--check` (on the local install and in the container) and the tests.
