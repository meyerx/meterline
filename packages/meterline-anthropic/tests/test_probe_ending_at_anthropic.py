"""B1-a: the docs make ``ending_at`` required whenever ``bucket_width`` is set on the per-user reports, so the stable-read
probe (one ``limit=1`` request before each window) must send it. The public fake answers 400 without it.

The accepted spec (v1.1, rule 8) says the probe is sent without ``ending_at``; the deviation is recorded in
docs/dev/change-list-meterline-anthropic.md.
"""

from datetime import datetime, timezone

import pytest

from meterline_anthropic.cli import main

NOW = datetime(2026, 10, 5, 6, 0, 0, tzinfo=timezone.utc)
PER_USER = ("user_usage_report", "user_cost_report")


def _backfill(fake_api, clock, data_dir):
    return main(["backfill", "--since", "2026-10-01", "--data-dir", str(data_dir)], transport=fake_api.transport,
                clock=clock, sleep=clock.sleep, now=lambda: NOW, jitter=lambda: 0)


@pytest.mark.parametrize("endpoint", PER_USER)
def test_probe_sends_ending_at_per_user(endpoint, fake_api, fake_clock, tmp_path, capsys):
    assert _backfill(fake_api, fake_clock, tmp_path) == 0, capsys.readouterr().err
    probes = [e for e in fake_api.requests_for(endpoint) if e.params.get("limit") == "1"]
    assert probes, "no probe was sent"
    for probe in probes:
        assert probe.status == 200
        assert "ending_at" in probe.params and "bucket_width" in probe.params


def test_fake_refuses_bucket_width_without_ending_at_per_user(fake_api, fake_clock, fake_key_env):
    from meterline_anthropic.client import AnalyticsClient, ApiError

    client = AnalyticsClient(fake_key_env, transport=fake_api.transport, clock=fake_clock, sleep=fake_clock.sleep, jitter=lambda: 0)
    with client, pytest.raises(ApiError) as excinfo:
        client.get("user_usage_report", {"starting_at": "2026-10-01T00:00:00Z", "bucket_width": "1d", "limit": "1"})
    assert excinfo.value.status == 400
