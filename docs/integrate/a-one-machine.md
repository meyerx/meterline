# A. One machine

A single machine or virtual machine runs the job from cron or systemd and keeps every folder on its disk. This is the scenario that the [tutorial](../tutorial.md) and the [daily job how-to](../how-to/operate-daily.md) describe, and it is how to evaluate meterline before choosing another. The [base page](README.md) defines the marks and what every scenario shares.

The table below lists its steps.

| Step | How | Maturity |
|---|---|---|
| Install | `pip install "meterline-job[all]"` in a virtual environment, which installs the `meterline-job` command and the connectors | built |
| Schedule | cron or a systemd timer runs `meterline-job run job.toml` once a day ([example 03](../../examples/03-daily-job/README.md) has both lines) | built |
| One run at a time | the lock per data folder in `meterline-job` skips a folder that another run holds | built |
| Connectors, silver step, gold, publish | `meterline-job run job.toml`, configured as in [example 03](../../examples/03-daily-job/README.md) | built; `meterline-anthropic-admin` verified |
| Read | the reference dashboard of [example 05](../../examples/05-dashboard/README.md) over `published/<audience>`, a notebook, or the CSV files | built |

**Least mature step:** built

Keys stay out of the configuration: each connector reads its key from the environment variable its README names, and gold reads `METERLINE_GOLD_KEY`. Keep the data root and the gold root on a disk that survives a reboot, with the mode of a private folder.
