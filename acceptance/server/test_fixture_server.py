import hashlib
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
from tempfile import NamedTemporaryFile
from time import monotonic, sleep
from uuid import uuid4

import httpx
import pytest


ENTRY = Path(__file__).with_name("yuki_fixture_server.py")
COMMIT = "6cf15321575391f217d1c9129e6f56e0010b09b9"
BASE_PATH = "/role/test"


@pytest.fixture
def source():
    return Path(os.environ["YUKI_BACKEND_TEST_ROOT"]).resolve()


def launch(tmp_path, source, *, root=None, state=None, environment=None):
    return subprocess.Popen(
        [
            sys.executable,
            "-B",
            str(ENTRY),
            "--root",
            str(root or tmp_path / "runtime"),
            "--state-file",
            str(state or tmp_path / "ready.json"),
            "--port",
            "0",
            "--base-path",
            BASE_PATH,
        ],
        cwd=source,
        env={
            "PATH": os.defpath,
            "PYTHONNOUSERSITE": "1",
            "PYTHONIOENCODING": "utf-8",
            **(environment or {}),
        },
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )


def eventually(read, predicate):
    deadline = monotonic() + 12
    while monotonic() < deadline:
        value = read()
        if predicate(value):
            return value
        sleep(0.02)
    pytest.fail("The expected fixture state did not arrive.")


@pytest.fixture
def server(tmp_path, source):
    state_path = tmp_path / "ready.json"
    process = launch(tmp_path, source)
    try:

        def read_state():
            if process.poll() is not None:
                pytest.fail("Fixture startup failed. " + process.stderr.read())
            return json.loads(state_path.read_text()) if state_path.exists() else None

        state = eventually(read_state, bool)
        url = httpx.URL(state["base_url"])
        assert (url.scheme, url.host, url.path) == ("http", "127.0.0.1", BASE_PATH)
        assert state_path.stat().st_mode & 0o077 == 0
        with httpx.Client(base_url=state["base_url"] + "/", trust_env=False) as client:
            yield client, state
            assert client.post("__test/shutdown").status_code == 200
            assert process.wait(timeout=12) == 0
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()


def read(client, path, *, device=None, binding=None, params=None):
    response = client.get(path, headers=headers(device, binding), params=params)
    return body(response)


def post(client, path, payload=None, *, device=None, binding=None):
    response = client.post(path, headers=headers(device, binding), json=payload)
    return body(response)


def body(response):
    assert response.status_code == 200, (
        f"{response.request.url.path}: HTTP {response.status_code}"
    )
    value = response.json()
    return value.get("data", value)


def headers(device, binding=None):
    values = {"Authorization": "Bearer " + device["token"]} if device else {}
    if binding:
        values["X-Yuki-Entry"] = binding
    return values


def pair(client, code, label):
    return post(client, "client/v1/pair", {"code": code, "device_label": label})


def message(client, device, text):
    request_id = str(uuid4())
    return request_id, post(
        client,
        "client/v1/messages",
        {
            "request_id": request_id,
            "text": text,
        },
        device=device,
    )


def waiting_work(client, device, trigger):
    def read_work():
        home = read(client, "client/v1/home", device=device)
        return next(
            (work for work in home["works"] if work["trigger_event_id"] == trigger),
            None,
        )

    return eventually(read_work, lambda work: work and work["state"] == "waiting_user")


def work_data(client, device, work_id):
    return read(client, f"client/v1/works/{work_id}", device=device)


def facts(client, work_id, **params):
    return read(client, "__test/facts", params={"work_id": work_id, **params})


def resume(client, device, work_id):
    post(
        client,
        f"client/v1/works/{work_id}/inputs",
        {
            "request_id": str(uuid4()),
            "text": "台北",
        },
        device=device,
    )
    return eventually(
        lambda: work_data(client, device, work_id),
        lambda work: work["state"] == "completed",
    )


def test_pinned_revision_protocol_and_inherited_controls(server, request):
    client, state = server
    assert state["yuki_commit"] == COMMIT
    assert state["api_revision"] == read(client, "client/v1/meta")["api_revision"] == 14
    owner = pair(client, state["owner_pairing_code"], "owner")
    assert (
        state["connector_protocol"]
        == read(client, "agents/v1/devices", device=owner)["connector_protocol"]
        == 5
    )
    schema = read(client, "openapi.json")
    test_routes = sorted(
        path for path in schema["paths"] if path.startswith("/__test/")
    )
    assert set(test_routes) >= {
        "/__test/work-scenario-artifact",
        "/__test/qq-group-work",
        "/__test/workspace-change/{artifact_id}",
        "/__test/second-app-entry",
        "/__test/facts",
        "/__test/superuser",
        "/__test/source-enabled",
        "/__test/model-script",
    }
    request.node.user_properties.append(
        (
            "fixture",
            {
                "yuki_commit": state["yuki_commit"],
                "client_api_revision": state["api_revision"],
                "connector_protocol": state["connector_protocol"],
                "test_routes": test_routes,
            },
        )
    )
    assert read(client, "__test/uploads") == {"requests": []}
    assert post(client, "__test/disconnect") == {"ok": True}
    unmounted = httpx.URL(state["base_url"]).copy_with(path="/__test/facts")
    assert client.get(unmounted).status_code == 404
    post(client, "__test/work-scenario")
    _, initial = message(client, owner, "Inherited work scenario")
    original = waiting_work(client, owner, initial["event_id"])
    assert original["waiting_question"]["text"] == "需要你提供城市"
    assert resume(client, owner, original["work_id"])["results"]


def test_app_artifact_arm_trigger_settlement_and_exact_paired_issuer(server, tmp_path):
    client, state = server
    desktop = pair(client, state["owner_pairing_code"], "desktop")
    other = pair(client, state["nonowner_pairing_code"], "nonowner")
    me = read(client, "client/v1/me", device=desktop)

    previous_id, previous = message(client, desktop, "Sent before arming")
    armed = post(client, "__test/work-scenario-artifact")
    _, triggered = message(client, desktop, "Actual artifact request")
    assert previous["event_id"] <= armed["armed_after_event_id"] < triggered["event_id"]
    waiting = waiting_work(client, desktop, triggered["event_id"])
    work_id = waiting["work_id"]
    before = facts(client, work_id)
    assert before["state"] == "waiting_user"
    assert before["trigger_event_id"] == triggered["event_id"]
    assert before["principal_id"] == desktop["device_id"]
    assert (
        read(client, f"client/v1/requests/{previous_id}", device=desktop).get("work")
        is None
    )
    assert (
        client.get(f"client/v1/works/{work_id}", headers=headers(other)).status_code
        == 404
    )

    handoff = post(client, "client/v1/link-codes", device=desktop)
    issued = facts(client, work_id, code_id=handoff["code_id"])["code"]
    assert issued["code_id"] == handoff["code_id"]
    assert issued["issuer_principal_id"] == desktop["device_id"]
    assert issued["issuer_source"] == "device"
    assert issued["source_binding_id"] == me["binding_id"]
    phone = pair(client, handoff["code"], "identical labels")
    operator_code = post(client, "__test/link-code")
    alternative = pair(client, operator_code["code"], "identical labels")
    paired = facts(client, work_id, paired_device_id=phone["device_id"])["pair"]
    assert paired == issued
    alternative_facts = facts(
        client, work_id, paired_device_id=alternative["device_id"]
    )["pair"]
    assert alternative_facts["code_id"] == operator_code["code_id"]
    assert alternative_facts["issuer_source"] == "control"
    assert alternative_facts["source_binding_id"] is None
    assert read(client, "client/v1/me", device=phone)["binding_id"] == me["binding_id"]

    final = resume(client, phone, work_id)
    published = facts(client, work_id, paired_device_id=phone["device_id"])
    assert published["work_id"] == work_id
    assert published["revision"] == final["revision"] > before["revision"]
    assert published["state"] == "completed"
    assert published["trigger_event_id"] == triggered["event_id"]
    assert published["principal_id"] == desktop["device_id"]
    assert published["result_event_ids"] == [
        result["event_id"] for result in final["results"]
    ]
    (artifact,) = published["artifacts"]
    assert artifact["artifact_id"] == armed["artifact_id"]
    assert artifact["version"] == armed["version"]
    assert artifact["event_id"] == final["results"][-1]["event_id"]

    changed = post(client, f"__test/workspace-change/{artifact['artifact_id']}")
    (workspace_file,) = (tmp_path / "runtime" / "workspace").rglob(changed["name"])
    assert hashlib.sha256(workspace_file.read_bytes()).hexdigest() == changed["sha256"]
    path = f"client/v1/artifacts/{artifact['artifact_id']}"
    downloaded = client.get(
        path, params={"version": artifact["version"]}, headers=headers(phone)
    )
    assert downloaded.status_code == 200
    assert downloaded.headers["x-yuki-sha256"] == artifact["version"]
    assert hashlib.sha256(downloaded.content).hexdigest() == artifact["version"]
    assert downloaded.content != workspace_file.read_bytes()
    assert (
        client.get(
            path, params={"version": changed["sha256"]}, headers=headers(phone)
        ).status_code
        == 404
    )
    assert (
        client.get(
            path, params={"version": artifact["version"]}, headers=headers(other)
        ).status_code
        == 404
    )

    evidence = json.dumps(published)
    if any(
        secret in evidence
        for secret in (desktop["token"], phone["token"], handoff["code"])
    ):
        pytest.fail("Fixture facts expose a credential.")


def test_second_entry_exists_before_merge_and_keeps_source_authority(server):
    client, state = server
    desktop = pair(client, state["owner_pairing_code"], "desktop")
    outsider = pair(client, state["nonowner_pairing_code"], "outsider")
    before = read(client, "client/v1/me", device=desktop)
    assert before["is_superuser"] is True
    second = post(client, "__test/second-app-entry")
    after = read(client, "client/v1/me", device=desktop)
    (entry,) = [b for b in after["bindings"] if b["binding_id"] == second["binding_id"]]
    assert entry["platform"] == "app"
    assert entry["source_person_id"] == second["source_person_id"]
    assert second["conversation_id"] != before["conversation_id"]
    assert second["seed_device_id"] != desktop["device_id"]
    explicit = read(
        client, "client/v1/me", device=desktop, binding=second["binding_id"]
    )
    assert explicit["source_person_id"] == before["source_person_id"]
    assert explicit["request_person_id"] == second["source_person_id"]
    assert explicit["person_id"] == before["person_id"]
    assert explicit["conversation_id"] == second["conversation_id"]
    assert (
        client.get(
            "client/v1/me", headers=headers(outsider, second["binding_id"])
        ).status_code
        == 401
    )

    link = post(
        client, "client/v1/link-codes", device=desktop, binding=second["binding_id"]
    )
    phone = pair(client, link["code"], "second entry phone")
    assert (
        read(client, "client/v1/me", device=phone)["binding_id"] == second["binding_id"]
    )
    demoted = post(
        client, "__test/superuser", {"person_id": before["person_id"], "enabled": False}
    )
    assert demoted["is_superuser"] is False
    assert read(client, "client/v1/me", device=phone)["is_superuser"] is False
    assert read(client, "client/v1/me", device=desktop)["is_superuser"] is False

    source = {"person_id": second["source_person_id"]}
    assert (
        post(client, "__test/source-enabled", {**source, "enabled": False})["enabled"]
        is False
    )
    assert client.get("client/v1/me", headers=headers(phone)).status_code == 401
    assert (
        read(client, "client/v1/me", device=desktop)["binding_id"]
        == before["binding_id"]
    )
    assert (
        post(client, "__test/source-enabled", {**source, "enabled": True})["enabled"]
        is True
    )
    assert (
        read(client, "client/v1/me", device=phone)["source_person_id"]
        == source["person_id"]
    )


def test_group_work_continues_from_app_to_original_onebot_group(server):
    client, state = server
    phone = pair(client, state["owner_pairing_code"], "phone")
    outsider = pair(client, state["nonowner_pairing_code"], "outsider")
    group = post(client, "__test/qq-group-work")
    work_id = group["work_id"]
    waiting = work_data(client, phone, work_id)
    assert waiting["state"] == "waiting_user"
    assert waiting["result_delivery"]["conversation_id"] == group["conversation_id"]
    assert waiting["result_delivery"]["platform"] == "qq"
    assert (
        client.get(f"client/v1/works/{work_id}", headers=headers(outsider)).status_code
        == 404
    )

    initial = facts(client, work_id)
    assert initial["principal_id"] is None
    denied = client.post(
        f"client/v1/works/{work_id}/inputs",
        headers=headers(outsider),
        json={"request_id": str(uuid4()), "text": "Outsider input"},
    )
    assert denied.status_code == 404
    after_denial = facts(client, work_id)
    assert after_denial["revision"] == initial["revision"]
    assert after_denial["group"] == initial["group"]
    final = resume(client, phone, work_id)
    delivered = facts(client, work_id)["group"]
    assert delivered["group_id"] == group["group_id"]
    assert delivered["send_count"] == initial["group"]["send_count"] + 1
    result = final["results"][-1]
    assert result["conversation_id"] == group["conversation_id"]
    assert (
        delivered["body_sha256"][-1]
        == hashlib.sha256(result["text"].encode()).hexdigest()
    )


@pytest.mark.parametrize(
    "nonowner", [False, True], ids=["owner-default", "explicit-nonowner"]
)
def test_model_script_consumes_tools_in_order_for_selected_person(server, nonowner):
    client, state = server
    owner = pair(client, state["owner_pairing_code"], "scripting owner")
    other = pair(client, state["nonowner_pairing_code"], "separate person")
    actor, outsider = (other, owner) if nonowner else (owner, other)
    selected_person = (
        {"person_id": read(client, "client/v1/me", device=other)["person_id"]}
        if nonowner
        else {}
    )
    armed = post(
        client,
        "__test/model-script",
        {
            **selected_person,
            "tools": [
                {
                    "name": "manage_task",
                    "arguments": {"action": "start", "goal": "scripted work"},
                },
                {"name": "send", "arguments": {"text": "scripted question"}},
                {"name": "wait", "arguments": {"for": [{"message": True}]}},
                {"name": "send", "arguments": {"text": "scripted result"}},
                {
                    "name": "manage_task",
                    "arguments": {"action": "finish", "result": "done"},
                },
            ],
        },
    )
    _, outsider_message = message(client, outsider, "Other person's message")
    assert outsider_message["event_id"] > armed["armed_after_event_id"]
    _, trigger = message(client, actor, "Start script")
    waiting = waiting_work(client, actor, trigger["event_id"])
    assert waiting["goal"] == "scripted work"
    assert waiting["waiting_question"]["text"] == "scripted question"

    other_arm = post(client, "__test/work-scenario-artifact")
    assert other_arm["armed_after_event_id"] >= trigger["event_id"]
    result = resume(client, actor, waiting["work_id"])
    assert result["results"][-1]["text"] == "scripted result"
    _, next_trigger = message(client, owner, "Start next independent script")
    next_work = waiting_work(client, owner, next_trigger["event_id"])
    assert next_work["work_id"] != waiting["work_id"]


@pytest.mark.parametrize("target", ["root", "state"])
def test_refuses_to_write_into_yuki_archive(source, tmp_path, target):
    path = source / f"mobile-fixture-test-{uuid4()}"
    options = {target: path}
    with launch(tmp_path, source, **options) as process:
        _, error = process.communicate(timeout=10)
        assert process.returncode == 2
        assert "outside the Yuki and Desktop sources" in error
    assert not path.exists()


def test_refuses_incompatible_commit(tmp_path, source):
    fake_archive = tmp_path / "wrong-pin"
    fake_archive.mkdir()
    (fake_archive / ".yuki-commit").write_text("f" * 40)
    with launch(
        tmp_path, source, environment={"YUKI_BACKEND_TEST_ROOT": str(fake_archive)}
    ) as process:
        _, error = process.communicate(timeout=10)
        assert process.returncode == 2
        assert "does not match the pinned Yuki commit" in error
    assert not (tmp_path / "runtime").exists()


def test_archive_marker_precedes_environment(tmp_path, monkeypatch):
    (tmp_path / ".yuki-commit").write_text(COMMIT, encoding="ascii")
    monkeypatch.setenv("YUKI_BACKEND_COMMIT", "f" * 40)
    assert runpy.run_path(str(ENTRY))["pinned_commit"](tmp_path) == COMMIT


def test_git_checkout_rejects_conflicting_environment_commit(tmp_path, monkeypatch):
    git = ["git", "-C", str(tmp_path)]
    subprocess.run([*git, "init", "-q"], check=True, capture_output=True)
    subprocess.run(
        [
            *git,
            "-c",
            "user.name=QA",
            "-c",
            "user.email=qa@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "setup",
        ],
        check=True,
        capture_output=True,
    )
    monkeypatch.setenv("YUKI_BACKEND_COMMIT", COMMIT)
    with pytest.raises(ValueError, match="disagrees with the Yuki checkout"):
        runpy.run_path(str(ENTRY))["pinned_commit"](tmp_path)


class FixtureEvidence:
    def __init__(self):
        self.fixtures = {}
        self.cases = {}

    def pytest_runtest_logreport(self, report):
        properties = dict(report.user_properties)
        if "fixture" in properties:
            self.fixtures.update(properties["fixture"])
        if report.when == "call" or report.failed or report.skipped:
            self.cases[report.nodeid.rsplit("::", 1)[-1]] = report.outcome


def verify():
    evidence = FixtureEvidence()
    exit_code = int(
        pytest.main(
            [str(Path(__file__).resolve()), "-p", "no:cacheprovider", "-q"],
            plugins=[evidence],
        )
    )
    passed = bool(evidence.fixtures and evidence.cases) and all(
        status == "passed" for status in evidence.cases.values()
    )
    if not passed and exit_code == 0:
        exit_code = 1
    path = ENTRY.resolve().parent.parent / "out" / "fixture-server.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        **evidence.fixtures,
        "status": "passed" if exit_code == 0 else "failed",
        "verification": {
            "fixture_tests": {
                "exit_code": exit_code,
                "passed": sum(status == "passed" for status in evidence.cases.values()),
                "cases": evidence.cases,
            }
        },
    }
    with NamedTemporaryFile(
        mode="w", dir=path.parent, encoding="utf-8", delete=False
    ) as f:
        pending = Path(f.name)
        json.dump(data, f, indent=2)
        f.write("\n")
    try:
        pending.replace(path)
    finally:
        pending.unlink(missing_ok=True)
    print("QA-002 evidence written to acceptance/out/fixture-server.json")
    return exit_code


if __name__ == "__main__":
    sys.exit(verify())
