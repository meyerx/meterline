"""Negative control of rule R17: a connector whose run() does not take the data-folder lock fails the kit, naming the rule."""
import contextlib
from dataclasses import replace

import pytest

from meterline.conformance import ConformanceError, check
from test_conformance_litellm import CONNECTOR

import meterline_litellm.cli as cli


@contextlib.contextmanager
def _no_lock(_data_dir):
    yield True  # the defect: the lock call is gone, so a held lock changes nothing


def test_a_run_that_ignores_the_lock_fails_r17(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "data_folder_lock", _no_lock)
    with pytest.raises(ConformanceError, match=r"contract rule R17 .*not 1 \(transient\)") as caught:
        check("R17", replace(CONNECTOR), tmp_path)
    assert "docs/reference/connector-contract.md#r17" in str(caught.value)


def test_the_unbroken_connector_passes_r17(tmp_path):
    check("R17", CONNECTOR, tmp_path)
