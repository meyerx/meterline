"""R-4 of the integrate part 1 spec: the files of example 04 (a container) are what the spec says, without a Docker daemon.

The Dockerfile has no key (no ENV, ARG or COPY of one) and its entry point is the job's; the CronJob and the ECS task definition
run exactly that entry point, keep their keys in secret references, give the data folders and the whole gold root a volume each
and, for the CronJob, forbid concurrent runs. The demo's data route (simulator output split into connector folders, then
``--step silver``, ``--step gold``, ``--step publish``) is run in-process. The image itself is built and run by the CI container
job (tools/test_ci_container_job.py checks that job's file).

Run: python3 -m pytest -q tools/test_example_04_container.py
"""
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "04-container"
KEYLIKE = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL)", re.I)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_image_key  # noqa: E402,F401


def instructions(text):
    """[(INSTRUCTION, rest)] of a Dockerfile, continuation lines joined and comments dropped."""
    joined = re.sub(r"\\\n", " ", text)
    return [(l.split(None, 1)[0].upper(), l.split(None, 1)[1] if len(l.split(None, 1)) > 1 else "")
            for l in (x.strip() for x in joined.split("\n")) if l and not l.startswith("#")]


def key_problems_in_dockerfile(text):
    """Lines that put a key into the image: an ENV or ARG named like a key, a COPY or ADD of a .env file or a key file."""
    bad = []
    for name, rest in instructions(text):
        if name in ("ENV", "ARG") and KEYLIKE.search(rest.split("=")[0].split()[0] if rest.split() else ""):
            bad.append("%s %s" % (name, rest.split("=")[0]))
        if name in ("COPY", "ADD") and re.search(r"(^|[\s/])\.env\b|\.pem\b|\.key\b|\bsecrets?\b", rest, re.I):
            bad.append("%s %s" % (name, rest))
    return bad


def inline_key_problems(env):
    """Entries of an env list (Kubernetes ``name``/``value``, ECS ``name``/``value``) that set a key-like name to a value."""
    return ["%s" % e["name"] for e in env or [] if KEYLIKE.search(e.get("name", "")) and "value" in e]


def dockerfile():
    return (EXAMPLE / "Dockerfile").read_text(encoding="utf-8")


def entrypoint():
    (rest,) = [r for n, r in instructions(dockerfile()) if n == "ENTRYPOINT"]
    return json.loads(rest)


def uid():
    return int(re.search(r"--uid (\d+)", dockerfile()).group(1))


def cronjob_docs():
    return list(yaml.safe_load_all((EXAMPLE / "k8s-cronjob.yaml").read_text(encoding="utf-8")))


def cronjob_spec():
    (job,) = [d for d in cronjob_docs() if d["kind"] == "CronJob"]
    return job["spec"]


def ecs():
    return json.loads((EXAMPLE / "ecs-task-definition.json").read_text(encoding="utf-8"))


# ---- the Dockerfile ----

def test_the_dockerfile_builds_from_wheels_made_here_and_runs_as_the_jobs_entry_point():
    text = dockerfile()
    assert entrypoint() == ["meterline-job"]
    assert "--find-links /wheels" in text and "pip wheel --no-deps --wheel-dir /wheels" in text
    for package in sorted(p.name for p in (ROOT / "packages").iterdir() if (p / "pyproject.toml").exists()):
        assert "/src/packages/%s" % package in text, package
    assert "pip list" in text and uid() >= 10000
    assert [r for n, r in instructions(text) if n == "USER"] == ["meterline"]


def test_the_dockerfile_puts_no_key_in_the_image():
    assert key_problems_in_dockerfile(dockerfile()) == []


def test_negative_control_a_planted_key_line_is_caught():
    for planted in ("ENV METERLINE_GOLD_KEY=0123456789abcdef", "ARG ANTHROPIC_ADMIN_KEY", "ENV API_TOKEN abc",
                    "COPY .env /app/.env", "COPY secrets/gold.key /app/"):
        assert key_problems_in_dockerfile(dockerfile() + planted + "\n") != [], planted
    assert key_problems_in_dockerfile("ENV PYTHONUNBUFFERED=1\nCOPY packages/ /src/packages/\n") == []


def test_the_two_dockerignore_files_are_equal_and_keep_keys_and_state_out():
    plain, named = (EXAMPLE / ".dockerignore").read_text(encoding="utf-8"), (EXAMPLE / "Dockerfile.dockerignore").read_text(encoding="utf-8")
    assert plain == named
    lines = [l for l in plain.split("\n") if l and not l.startswith("#")]
    assert lines[0] == "*" and "!packages/" in lines and "!examples/04-container/" in lines
    for pattern in ("**/.env", "**/.env.*", "**/*.pem", "**/*.key", "**/.venv"):
        assert pattern in lines, pattern


# ---- the CronJob and the ECS task definition ----

def test_the_cronjob_runs_the_images_entry_point_one_run_at_a_time():
    spec = cronjob_spec()
    assert spec["concurrencyPolicy"] == "Forbid"
    pod = spec["jobTemplate"]["spec"]["template"]["spec"]
    (container,) = pod["containers"]
    assert container["command"] == entrypoint() and container["args"][0] == "run"
    assert pod["securityContext"]["runAsUser"] == uid() and pod["securityContext"]["runAsNonRoot"] is True
    assert pod["restartPolicy"] == "Never" and spec["jobTemplate"]["spec"]["backoffLimit"] == 0


def test_the_cronjob_gives_the_data_folders_and_the_whole_gold_root_a_volume_each_and_keeps_keys_in_a_secret():
    pod = cronjob_spec()["jobTemplate"]["spec"]["template"]["spec"]
    (container,) = pod["containers"]
    mounts = {m["mountPath"]: m["name"] for m in container["volumeMounts"]}
    volumes = {v["name"]: v for v in pod["volumes"]}
    assert {"/data", "/gold"} <= set(mounts) and mounts["/data"] != mounts["/gold"]
    assert "persistentVolumeClaim" in volumes[mounts["/data"]] and "persistentVolumeClaim" in volumes[mounts["/gold"]]
    assert inline_key_problems(container["env"]) == []
    keys = {e["name"]: e for e in container["env"] if KEYLIKE.search(e["name"])}
    assert "METERLINE_GOLD_KEY" in keys and all("secretKeyRef" in e["valueFrom"] for e in keys.values())
    claims = [d for d in cronjob_docs() if d["kind"] == "PersistentVolumeClaim"]
    assert len(claims) == 2 and {c["metadata"]["name"] for c in claims} == {v["persistentVolumeClaim"]["claimName"] for n, v in volumes.items() if n in (mounts["/data"], mounts["/gold"])}


def test_the_ecs_task_runs_the_images_entry_point_and_keeps_keys_in_secrets():
    task = ecs()
    (container,) = task["containerDefinitions"]
    assert container["entryPoint"] == entrypoint() and container["command"][0] == "run"
    assert container["user"] == str(uid())
    assert inline_key_problems(container.get("environment")) == []
    secrets = {s["name"]: s["valueFrom"] for s in container["secrets"]}
    assert "METERLINE_GOLD_KEY" in secrets and all(v.startswith("arn:aws:secretsmanager:") for v in secrets.values())
    mounts = {m["containerPath"]: m["sourceVolume"] for m in container["mountPoints"]}
    assert mounts["/data"] != mounts["/gold"]
    assert {v["name"] for v in task["volumes"]} >= {mounts["/data"], mounts["/gold"]}


def test_negative_control_an_inline_key_in_a_manifest_is_caught():
    assert inline_key_problems([{"name": "METERLINE_GOLD_KEY", "value": "0123456789abcdef"}, {"name": "TZ", "value": "UTC"}]) == ["METERLINE_GOLD_KEY"]
    assert inline_key_problems([{"name": "METERLINE_GOLD_KEY", "valueFrom": {"secretKeyRef": {"name": "s", "key": "k"}}}]) == []


# ---- the key check the CI job runs ----

def test_the_key_check_finds_a_key_in_a_history_and_never_prints_it(tmp_path):
    key = "throwaway-0123456789abcdef-key"
    history = tmp_path / "history.txt"
    history.write_text("IMAGE CREATED BY\nENV METERLINE_GOLD_KEY=%s\n" % key, encoding="utf-8")
    clean = tmp_path / "log.txt"
    clean.write_text("{\"step\": \"gold\", \"ok\": true}\n", encoding="utf-8")
    env = dict(os.environ, GOLD_KEY_UNDER_TEST=key)
    tool = ROOT / "tools" / "check_image_key.py"
    found = subprocess.run([sys.executable, str(tool), "GOLD_KEY_UNDER_TEST", str(history), str(clean)], env=env, capture_output=True, text=True)
    assert found.returncode == 1 and str(history) in found.stderr and key not in found.stdout + found.stderr
    none = subprocess.run([sys.executable, str(tool), "GOLD_KEY_UNDER_TEST", str(clean)], env=env, capture_output=True, text=True)
    assert none.returncode == 0 and none.stdout.startswith("ok:")
    unset = subprocess.run([sys.executable, str(tool), "NO_SUCH_VARIABLE", str(clean)], env=env, capture_output=True, text=True)
    assert unset.returncode == 2


# ---- the demo route, in-process ----

def load(name):
    spec = importlib.util.spec_from_file_location("example_04_" + name, EXAMPLE / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def route(tmp_path_factory):
    """prepare_demo.py, then the three steps of the job, each by --step, exactly as the container job runs them."""
    pytest.importorskip("meterline_job")
    pytest.importorskip("meterline_sim")
    base = tmp_path_factory.mktemp("route")
    config_dir, data_root, gold_root = base / "config", base / "data", base / "gold"
    for folder in (config_dir, data_root, gold_root):
        folder.mkdir()
    assert load("prepare_demo").main([str(config_dir), str(data_root), str(gold_root)]) == 0
    return config_dir, data_root, gold_root


def run_step(config_dir, step, monkeypatch, capsys):
    import meterline_job
    monkeypatch.setenv("METERLINE_GOLD_KEY", "a-key-of-at-least-32-bytes-that-you-keep-out-of-git")
    code = meterline_job.run_job(meterline_job.load_config(config_dir / "job.toml"), step=step)
    lines = [json.loads(l) for l in capsys.readouterr().out.splitlines() if l.startswith("{")]
    return code, lines


def test_the_step_route_runs_on_simulator_output_with_no_package_code(route, monkeypatch, capsys):
    config_dir, data_root, gold_root = route
    assert not (gold_root / "published").exists()
    for step in ("silver", "gold", "publish"):
        code, lines = run_step(config_dir, step, monkeypatch, capsys)
        assert code == 0 and [l["step"] for l in lines] == [step], (step, lines)
    checker = load("check_published")
    assert checker.problems(gold_root) == []
    assert checker.main([str(gold_root)]) == 0
    assert sorted(p.name for p in data_root.iterdir()) == ["claude", "copilot", "proxy", "silver"]


def test_the_configuration_the_example_writes_is_in_the_format_of_example_03(route):
    config_dir, _, _ = route
    import meterline_job
    config = meterline_job.load_config(config_dir / "job.toml")
    assert [c["kind"] for c in config["connector"]] == ["anthropic", "litellm", "copilot"]
    assert set(config) == {"data_root", "gold_root", "gold", "connector", "publish"}
    assert "METERLINE_GOLD_KEY" not in (config_dir / "job.toml").read_text(encoding="utf-8")


def test_the_published_check_refuses_an_empty_folder_and_a_delivery_folder(tmp_path):
    checker = load("check_published")
    assert checker.main([str(tmp_path)]) == 1
    (tmp_path / "published" / "exec").mkdir(parents=True)
    assert checker.problems(tmp_path) != []
    (tmp_path / "published" / "exec" / "costs_month.csv").write_text("month\n", encoding="utf-8")
    assert checker.problems(tmp_path) == []
    (tmp_path / "published" / "delivery").mkdir()
    assert "delivery" in checker.problems(tmp_path)[0]


# ---- the version check the CI job runs ----

def test_the_version_check_passes_this_repositorys_versions_and_catches_a_wrong_or_missing_one():
    import check_image_versions as civ
    wanted = civ.repository_versions()
    assert "meterline" in wanted and "meterline-job" in wanted and len(wanted) >= 8
    pip_list = [{"name": name.replace("-", "_"), "version": version} for name, version in wanted.items()]
    assert civ.mismatches(pip_list, wanted) == []
    # negative control: one package from the index with another version, and one missing
    pip_list[0] = dict(pip_list[0], version="9.9.9")
    del pip_list[1]
    assert len(civ.mismatches(pip_list, wanted)) == 2
    done = subprocess.run([sys.executable, str(ROOT / "tools" / "check_image_versions.py")], input=json.dumps(pip_list),
                          capture_output=True, text=True)
    assert done.returncode == 1 and "the repository has" in done.stderr
