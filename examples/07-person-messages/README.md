# 07 - one message per person

**Goal.** Write, for each person, one message with their own AI usage figures of a closed month, as an `.eml` file you can open in any mail client, and show how the sending path of an adopter is locked: it needs two switches, it is refused in CI, and nothing in this repository sends. meterline has no sender; this is an example of one, kept small enough to copy.

**Reads.** The figures come from `published/person/<key>/person_month.csv` and `comparison.csv` of the gold root (a comparison band only where its `hidden` flag is false). The name and the address come from `builds/<latest>/delivery/directory.csv`, where `<latest>` is the build that `published/central/published.json` names. That file is the only one in the gold root with names and addresses: the sender runs on the central side, reads it in place, and never copies it or serves it. The program has no option to read a directory from anywhere else, and a `directory.csv` placed under `published/` is never read.

- People are joined on `person_key`. A key the directory does not hold, a name or an address with a control character or that is not a plain address, and a person with no row for the month get no message. The summary counts them and names no one.
- The month is one that `published.json` lists as frozen, the latest by default: a frozen month is delivered once and does not change, so a late correction cannot send one person's changed figure after another's. Keep your own record of the months you have sent; the program does not.
- A message holds the person's first name and figures only. No person key and no manager key is in a header, in the body or in a file name: the files are `0001.eml`, `0002.eml` and so on.
- `--out` must be a new or empty folder outside the gold root. The files are readable by their owner only. The repository ignores `*.eml` and the folder `/mail-out/`, so a message cannot be committed by mistake.

## Run it

Run this from the repository root, after the install in the README quick start. It runs the job's three steps on simulated data (the route of [example 04](https://github.com/meyerx/meterline/blob/main/examples/04-container/README.md), on the host), writes the messages into `mail-out/`, which must be new or empty, and then shows that sending is refused when `CI` is set. The key is made here for the run and is not a secret; the addresses of the simulated people end in `example.com`; no message leaves the machine.

```sh
export METERLINE_GOLD_KEY="${METERLINE_GOLD_KEY:-$(python3 -c 'import secrets; print(secrets.token_hex(32))')}"
mkdir -p messages-demo/config messages-demo/data messages-demo/gold
.venv/bin/python examples/04-container/prepare_demo.py messages-demo/config messages-demo/data messages-demo/gold
for step in silver gold publish; do .venv/bin/meterline-job run messages-demo/config/job.toml --step "$step"; done
.venv/bin/python examples/07-person-messages/person_messages.py messages-demo/gold --out mail-out
ls mail-out | sed -n 1,3p
if CI=true METERLINE_MAIL_CONFIRM=yes .venv/bin/python examples/07-person-messages/person_messages.py messages-demo/gold --out mail-out-refused --send; then echo "sending was not refused" >&2; exit 1; fi
```

**Expected output.** The three steps print one JSON line each with `"exit_code": 0`. The program prints `wrote N messages into mail-out, skipped M (...)`, `ls` lists `0001.eml`, `0002.eml` and `0003.eml`, and the last command prints `error: --send is refused: CI is set. Nothing sends from CI.` on the error output and creates no folder. Open `mail-out/0001.eml` with a mail client or `less`: it is addressed to one simulated person, greets them by first name and shows their own active days, requests, tokens and estimated cost for the month. The tests in `tools/test_example_07_messages.py` give two people each a sentinel name, address and figures and look for the second person's in every header and part of the first one's message.

## Run it in the container

Run this after [example 04's "Run it" block](https://github.com/meyerx/meterline/blob/main/examples/04-container/README.md#run-it) and before its "Clean up": it needs the image `meterline-job:local` and the volume `meterline-gold`, which example 04 leaves in place with the published gold in it. The image carries this example's `person_messages.py` at `/app/person_messages.py`; the program needs no gold key, so none is passed, and the gold volume is mounted read-only. The block creates a volume of its own, `meterline-mail`, writes the `.eml` files into a new folder `messages` of it (the program wants an `--out` that does not exist yet, and the mount point of a volume does) and lists the first three. It never passes `--send`: nothing is sent. Example 04's "Clean up" removes the volume too.

```sh
docker volume create meterline-mail >/dev/null
docker run --rm -v meterline-gold:/gold:ro -v meterline-mail:/mail --entrypoint python meterline-job:local /app/person_messages.py /gold --out /mail/messages
docker run --rm -v meterline-mail:/mail --entrypoint ls meterline-job:local /mail/messages | sed -n 1,3p
```

**Expected output.** `wrote N messages into messages, skipped M (...)`, then `0001.eml`, `0002.eml` and `0003.eml`, and exit code 0. Read one with `docker run --rm -v meterline-mail:/mail --entrypoint cat meterline-job:local /mail/messages/0001.eml`. Run the block a second time only after removing the volume: `--out` must not exist yet.

## Run it each month (pattern)

The program writes the messages of one frozen month and does nothing on its own, so something has to start it. This is a pattern: meterline neither ships nor tests the trigger. Run it once a month, after the job's publish step has frozen the month: a monthly CronJob or a scheduler of your choice, in the image of [example 04](https://github.com/meyerx/meterline/blob/main/examples/04-container/README.md) and on the same gold root, with `--month` set to that month (`YYYY-MM`, one that `published.json` lists as frozen) and a new `--out` for each run. Keep the record of months sent yourself: the program does not keep it, and a second run for a month already sent writes the same messages again.

## Sending, locked

The program sends only when all of these hold: you pass `--send`; the environment has `METERLINE_MAIL_CONFIRM=yes` (exactly); `CI` is not set; `boto3` is installed (`pip install boto3`; it is not a dependency of any meterline package, and the program imports it on this path only); and `--sender` is an address your Amazon SES identity is verified for, not the example address. Each failed condition is a refusal with exit code 2 before anything is read or written. The sending code is written and tested for those refusals only: no test and no CI job sends, and a call to SES has never run from this repository.

To use it for real, you add what this example leaves to you: a verified sender identity, SES production access, permission for the sending role (`ses:SendEmail`), handling of bounces and complaints, the record of the months you have sent, and the choice of who may run it. The scenario C page lists these beside its commented SES excerpt.

## What this does not prove

It does not show that a message reaches an inbox, that your mail system accepts the sender, or that the people named in `directory.csv` are the right recipients for their figures. The simulated names and addresses are made up.
