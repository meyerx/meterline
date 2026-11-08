# Example 04: the meterline job in a container. Build it from the repository root (the context holds packages/):
#   docker build -f examples/04-container/Dockerfile -t meterline-job:local .
# The image holds no key: METERLINE_GOLD_KEY and the provider keys enter when the container runs, as environment variables
# or mounted secrets. Do not add an ENV, an ARG or a COPY of a key or of a .env file here; a test refuses one.

# Stage 1: one wheel per meterline package, built from this repository, into one folder that stage 2 installs from.
FROM python:3.12-slim AS wheels
WORKDIR /src
COPY packages/ /src/packages/
RUN pip wheel --no-deps --wheel-dir /wheels \
    /src/packages/meterline /src/packages/meterline-anthropic /src/packages/meterline-anthropic-admin \
    /src/packages/meterline-litellm /src/packages/meterline-copilot /src/packages/meterline-sim \
    /src/packages/meterline-gold /src/packages/meterline-job

# Stage 2: the image. The meterline packages come from the wheels above and their third-party dependencies from the
# package index, so `pip list` shows this repository's versions of every meterline package.
FROM python:3.12-slim
RUN useradd --create-home --uid 10001 meterline \
    && mkdir -p /data /gold /config /warehouse /mail \
    && chown meterline /data /gold /config /warehouse /mail
COPY --from=wheels /wheels /wheels
RUN pip install --no-cache-dir --find-links /wheels /wheels/*.whl \
    && pip list --format=freeze | grep -i '^meterline'
# prepare_demo.py and check_published.py are for the demo and the smoke check. The other three scripts are the examples' own, so
# that the same image can serve a published folder (dashboard.py, example 05), load it into DuckDB files (load_warehouse.py,
# example 06; they go into /warehouse) and write one message per person (person_messages.py, example 07; into /mail). No key,
# no new package code.
# COPY keeps the mode of the source file, and a clone made under umask 077 holds files that only their owner can read, which
# the image user (uid 10001) could not: the RUN below, before USER, sets the mode of every file copied into /app. It is a RUN
# and not COPY --chmod because the legacy builder (Docker without buildx) refuses that flag. A test refuses a COPY into /app
# that no such RUN covers.
COPY examples/04-container/prepare_demo.py examples/04-container/check_published.py examples/05-dashboard/dashboard.py \
     examples/06-warehouse-duckdb/load_warehouse.py examples/07-person-messages/person_messages.py /app/
RUN chmod 0644 /app/*.py
USER meterline
WORKDIR /data
ENTRYPOINT ["meterline-job"]
CMD ["check", "/config/job.toml"]
