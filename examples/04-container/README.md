# 04 - the job in a container

**Goal.** Build one image that runs `meterline-job`, run it on simulated data until a published gold folder exists, and show how a Kubernetes CronJob or an Amazon ECS task starts the same image once a day. meterline publishes no image: you build this one from the repository and push it to your own registry.

**Reads.** The `Dockerfile` in this folder, built from the repository root. It builds one wheel of every meterline package from `packages/`, installs them into the image from that one folder with `--find-links`, and takes their third-party dependencies from the package index, so `pip list` in the image shows this repository's version of every meterline package. The entry point is `meterline-job`; the user is `meterline` (uid 10001). The image holds no key: `METERLINE_GOLD_KEY` and the provider keys enter when the container runs, as environment variables or mounted secrets, and a test refuses an `ENV`, an `ARG` or a `COPY` of a key or of a `.env` file. `prepare_demo.py` and `check_published.py` are for the demo and the smoke check only; `dashboard.py`, `load_warehouse.py` and `person_messages.py` are the scripts of [example 05](https://github.com/meyerx/meterline/blob/main/examples/05-dashboard/README.md), [example 06](https://github.com/meyerx/meterline/blob/main/examples/06-warehouse-duckdb/README.md) and [example 07](https://github.com/meyerx/meterline/blob/main/examples/07-person-messages/README.md), copied in so that the same image can serve the published folder, load it into DuckDB files and write one message per person. The image sets the mode of every file it copies into `/app` (0644), so that a clone made under a restrictive umask still builds an image whose user can read them.

Beside it: [`k8s-cronjob.yaml`](k8s-cronjob.yaml), a CronJob with `concurrencyPolicy: Forbid` and two persistent volume claims, one for the data folders and one for the whole gold root; and [`ecs-task-definition.json`](ecs-task-definition.json), the same for ECS with EFS volumes and Secrets Manager. Both run exactly the image's entry point and read the keys from a secret reference. Mount the whole gold root, not `published/` alone: `published` is a relative link to a folder beside it. Both are patterns: CI checks their shape and not their effect on a cluster.

## Run it

You need Docker. Run this from the repository root. It builds the image, fills the data folders from `meterline-sim` output, runs the job's three steps one at a time (`--step silver`, `--step gold`, `--step publish`) with a configuration that `prepare_demo.py` writes in the format of [example 03's `job.example.toml`](https://github.com/meyerx/meterline/blob/main/examples/03-daily-job/job.example.toml), and checks that `published/` holds gold files. The volumes stay, so that the container blocks of examples [05](https://github.com/meyerx/meterline/blob/main/examples/05-dashboard/README.md#run-it-in-the-container), [06](https://github.com/meyerx/meterline/blob/main/examples/06-warehouse-duckdb/README.md#run-it-in-the-container) and [07](https://github.com/meyerx/meterline/blob/main/examples/07-person-messages/README.md#run-it-in-the-container) can use the gold you just published; remove them with [Clean up](#clean-up) when you are done. The key is made here for the run and is not a secret; no network call goes to a provider.

```sh
docker build -f examples/04-container/Dockerfile -t meterline-job:local .
export METERLINE_GOLD_KEY="${METERLINE_GOLD_KEY:-$(python3 -c 'import secrets; print(secrets.token_hex(32))')}"
docker volume create meterline-data >/dev/null && docker volume create meterline-gold >/dev/null && docker volume create meterline-config >/dev/null
docker run --rm -v meterline-data:/data -v meterline-gold:/gold -v meterline-config:/config --entrypoint python meterline-job:local /app/prepare_demo.py /config /data /gold
for step in silver gold publish; do docker run --rm -e METERLINE_GOLD_KEY -v meterline-data:/data -v meterline-gold:/gold -v meterline-config:/config meterline-job:local run /config/job.toml --step "$step"; done
docker run --rm -v meterline-gold:/gold --entrypoint python meterline-job:local /app/check_published.py /gold
```

**Expected output.** The first container prints `wrote /config/job.toml and the data folders of claude, proxy, copilot`. The three steps print one JSON line each, with `"exit_code": 0` and `"ok": true`; the `publish` line names the `published-<time>` folder it linked. The last container prints `ok: published holds N audience folders with gold files` and exits 0. The first build takes a few minutes, mostly for the dependencies from the package index.

The next steps are the container blocks of [example 05](https://github.com/meyerx/meterline/blob/main/examples/05-dashboard/README.md#run-it-in-the-container) (the dashboard), [example 06](https://github.com/meyerx/meterline/blob/main/examples/06-warehouse-duckdb/README.md#run-it-in-the-container) (the DuckDB files) and [example 07](https://github.com/meyerx/meterline/blob/main/examples/07-person-messages/README.md#run-it-in-the-container) (one message per person), each on the same gold volume.

## Clean up

Run this when you have finished with the demo, and after the container blocks of examples 05, 06 and 07 if you ran them. It removes the three volumes, and with them the simulated data and the published gold, and the two volumes of examples 06 and 07 (the DuckDB files and the messages) when they exist.

```sh
docker volume rm meterline-data meterline-gold meterline-config >/dev/null
for volume in meterline-warehouse meterline-mail; do
  if docker volume inspect "$volume" >/dev/null 2>&1; then docker volume rm "$volume" >/dev/null; fi
done
```

## Use it for real

1. Build the image from the repository root and push it to your registry. Pin the base image of the `Dockerfile` by digest in your copy.
2. Put the job configuration, the organisation files, the policy and the price list in a ConfigMap (or on a volume) mounted at `/config`, as [example 03](https://github.com/meyerx/meterline/blob/main/examples/03-daily-job/README.md) describes. Keys never go in them.
3. Create the secret that holds `METERLINE_GOLD_KEY` and each connector's key, and edit the image, the schedule and the names in the manifest.
4. Serve readers from `published/<audience>` only, for example with [example 05](https://github.com/meyerx/meterline/blob/main/examples/05-dashboard/README.md); never serve `builds/` or `frozen/`.

The job's lock is tested on local disk only. If the volumes are network disks, run two jobs against one data folder and check that the second exits 1. `concurrencyPolicy: Forbid` already keeps the scheduler from starting two. [The integration guide](https://github.com/meyerx/meterline/blob/main/docs/integrate/README.md) says where this fits among the scenarios.

## What this does not prove

The demo runs on simulated data. It does not show that the image runs on your cluster, that your storage class keeps the ownership of the volumes (the container user is 10001 and needs to write to `/data` and `/gold`), that a connector's key works against its provider, or that a network disk honours the lock.
