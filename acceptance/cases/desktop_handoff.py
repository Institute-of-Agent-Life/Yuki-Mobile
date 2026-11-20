from dataclasses import replace
from getpass import getpass
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys
from tempfile import NamedTemporaryFile
from urllib.parse import parse_qsl, urlsplit
from uuid import uuid4

from acceptance.backend import BackendUnavailable, loopback_endpoint, start_backend
from acceptance.cases.a01_handoff import (
    FixtureAPI, HandoffBlocked, HandoffContext, HandoffFailure, PhoneDownload, PhonePhase,
    check_work_origin, is_sha256, require, sha256, wait_for,
)
from acceptance.cases.smoke import SmokeFailure, _CLI
from acceptance.fault_proxy import FaultProxy


DESKTOP_COMMIT = "a5687a66cf74485c94394e1008ddfbc0ebd926a2"
SCREENSHOT = "desktop-result.png"


def prompt(stage, message, *, secret=False):
    print(f"[{stage}] {message}", flush=True)
    if secret and sys.stdin.isatty():
        return getpass("").strip()
    line = sys.stdin.readline()
    if line == "":
        raise EOFError
    return line.strip()


def pair_code(link, client_url):
    try:
        if any(ord(char) <= 32 or ord(char) >= 127 for char in link) or "#" in link:
            raise ValueError
        parsed = urlsplit(link)
        fields = parse_qsl(parsed.query, strict_parsing=True, max_num_fields=2, errors="strict")
        values = dict(fields)
        if (
            not link.startswith("yuki://pair?")
            or (parsed.scheme, parsed.netloc, parsed.path) != ("yuki", "pair", "")
            or len(fields) != 2 or set(values) != {"server", "code"}
            or values["server"] != client_url
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", values["code"])
        ):
            raise ValueError
        return values["code"]
    except ValueError:
        raise HandoffFailure("The Desktop handoff link did not match the fixture URL and expected fields.") from None


def process_alive(pid):
    # A POSIX signal-0 probe only tests whether the identified process exists.
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def live_pid(value):
    require(
        re.fullmatch(r"[0-9]{1,10}", value) and 1 < int(value) <= 2**31 - 1,
        "Expected a positive Desktop main process PID.",
    )
    pid = int(value)
    require(process_alive(pid), "The reported Desktop process was not running.")
    return pid


def desktop_checkout():
    value = os.environ.get("YUKI_DESKTOP_ROOT")
    if not value:
        return None
    try:
        commit = subprocess.check_output(
            ["git", "-C", value, "rev-parse", "HEAD"],
            text=True, stderr=subprocess.DEVNULL, timeout=3,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        raise HandoffBlocked("Could not verify the configured Desktop checkout.") from None
    require(commit == DESKTOP_COMMIT, "The configured Desktop checkout does not match the QA-003 pin.")
    return commit


def before(backend, proxy, evidence, ask=prompt, *, client_url=None):
    """Pair and close real Desktop, then return A01 state for the phone."""
    client_url = client_url or proxy.base_url
    control = FixtureAPI(backend.base_url)
    owner_code = control.data("/__test/link-code", method="POST")["code"]
    inspector_cli = _CLI(backend, proxy.base_url, "handoff-inspector")
    inspector_id = inspector_cli.pair(owner_code)
    inspector = FixtureAPI(proxy.base_url, inspector_cli.token)
    nonowner_cli = _CLI(backend, proxy.base_url, "handoff-nonowner")
    nonowner_cli.pair(backend.nonowner_pairing_code)
    nonowner = FixtureAPI(proxy.base_url, nonowner_cli.token)
    me = inspector.client("me")
    binding, conversation = me["binding_id"], me["conversation_id"]
    require(binding and conversation and me["platform"] == "app", "The inspector did not resolve an App entry.")
    inspector = replace(inspector, binding=binding)
    baseline = {row["device_id"] for row in me["devices"]}
    require(inspector_id in baseline, "The inspector device was missing from its own device list.")

    print(f"Client URL: {client_url}", flush=True)
    print(f"Pairing state: {backend.workspace / 'backend-state.json'}", flush=True)
    pid = live_pid(ask(
        "before.pair", "Pair a fresh Desktop profile using the state file's owner code, then enter the Desktop main PID."
    ))

    def new_device():
        current = inspector.client("me")
        devices = {row["device_id"] for row in current["devices"]}
        added = devices - baseline
        require(
            current["binding_id"] == binding and current["conversation_id"] == conversation
            and baseline <= devices and len(added) <= 1,
            "Desktop pairing did not preserve the entry or produced multiple new devices.",
        )
        return next(iter(added), None)

    desktop_device = wait_for(new_device, "Desktop pairing did not add a device to the owner entry.")
    evidence["desktop_pair"] = {
        "desktop_device_alias": "device-1",
        "entry_alias": "entry-1",
        "new_owner_device_count": 1,
        "desktop_pid_alive": True,
    }

    armed = control.data("/__test/work-scenario-artifact", method="POST")
    require(
        type(armed["armed_after_event_id"]) is int and is_sha256(armed["version"]),
        "The fixture did not report an armed boundary and original artifact version.",
    )
    message = f"QA-003 Desktop handoff {uuid4().hex}"
    print(f"Message: {message}", flush=True)
    ask("before.send", "Send the exact message in the paired Desktop chat, then press Enter.")

    def waiting():
        matching = [
            event for event in control.data("/__test/events")
            if event["direction"] == "inbound" and event["conversation_id"] == conversation
            and event["content"] == message
        ]
        require(len(matching) <= 1, "The tagged Desktop trigger was received more than once.")
        if not matching:
            return None
        trigger = matching[0]["event_id"]
        require(
            type(trigger) is int and trigger > armed["armed_after_event_id"],
            "The Desktop message was not received after arming.",
        )
        return next((
            work for work in inspector.client("home")["works"]
            if work["trigger_event_id"] == trigger and work["state"] == "waiting_user"
        ), None)

    work = wait_for(waiting, "The tagged Desktop message did not start a waiting Work.")
    facts = control.data("/__test/facts", params={"work_id": work["work_id"]})
    check_work_origin(facts, work, armed, desktop_device)
    require(
        work["conversation_id"] == conversation and work["source"]["binding_id"] == binding
        and facts["state"] == "waiting_user" and facts["revision"] == work["revision"],
        "The settled original Work did not wait on the owner App entry.",
    )
    nonowner_status, _, _ = nonowner.request(f"/client/v1/works/{work['work_id']}")
    require(nonowner_status == 404, "The nonowner did not receive HTTP 404 for the Work.")
    evidence["trigger"] = {
        "trigger_event_alias": "event-1",
        "matched_tagged_ledger_event": True,
        "trigger_after_arm": True,
        "settled_principal_matches_desktop_device": True,
        "nonowner_work_status": nonowner_status,
    }

    code_source = ask(
        "before.link", "Issue a new chat-device code in Desktop Device Security and paste the full pairing link.",
        secret=True,
    )
    context = HandoffContext(
        backend=backend, proxy=proxy, client_url=client_url, inspector=inspector, nonowner=nonowner,
        pairing_code=pair_code(code_source, client_url), binding_id=binding, conversation_id=conversation,
        desktop_device_id=desktop_device, work_id=work["work_id"], armed=armed, initial_work=work,
        evidence=evidence,
    )

    ask("before.close", "Quit Desktop completely and press Enter after its main process has stopped.")
    wait_for(lambda: True if not process_alive(pid) else None, "The original Desktop process is still running.")
    closed = inspector.client(context.work_path)
    require(
        closed["state"] == "waiting_user" and closed["work_id"] == work["work_id"]
        and closed["revision"] == work["revision"],
        "The original Work stopped waiting after Desktop quit.",
    )
    evidence["closed_desktop"] = {"desktop_pid_exited": True, "backend_work_state": "waiting_user"}
    return context, pid


def reference_phone(context):
    phone = _CLI(context.backend, context.client_url, "handoff-phone")
    device_id = phone.pair(context.pairing_code)
    me, _ = phone.run("me")
    context.verify_phone_pair(device_id, me)
    home, _ = phone.run("home")
    work, _ = phone.run("work", context.work_id)
    require(
        work["work_id"] == context.work_id
        and work["state"] == "waiting_user"
        and work["revision"] == context.initial_work["revision"]
        and any(item["work_id"] == context.work_id for item in home["works"]),
        "The reference phone did not sync and read the original waiting Work.",
    )

    request_id = str(uuid4())
    text = f"Ship to Taipei and include the shipping cost. QA-003 {uuid4().hex}"
    path = f"{context.work_path}/inputs"
    body = {"request_id": request_id, "text": text}
    _, prefix = loopback_endpoint(context.client_url)
    proxy_path = prefix + "/client/v1/" + path
    context.proxy.arm("POST", proxy_path, "drop-after-upstream")
    lost, _ = phone.run("input", context.work_id, text, "--request-id", request_id, expected_exit=2)
    transfer = context.proxy.forwardings("POST", proxy_path)
    require(
        lost.get("problem") == "transport_unknown" and lost.get("request_id") == request_id
        and len(transfer) == 1 and transfer[0].upstream_status == 200 and transfer[0].full_body_read,
        "The proxy did not discard one fully committed original Work input response.",
    )

    original, _ = phone.run("request", request_id)
    receipt = original.get("result") or {}
    event_id, input_id = receipt.get("event_id"), receipt.get("input_id")
    require(
        original.get("found") is True and original.get("target") == context.work_id
        and original.get("operation_kind") == "client.work_input" and not original.get("problem_code")
        and type(event_id) is int and type(input_id) is int,
        "The original input receipt did not identify its authoritative event and mailbox input.",
    )
    recovered, _ = phone.run("retry", request_id)
    recovery = recovered.get("result") or {}
    require(
        recovery.get("event_id") == event_id and recovery.get("input_id") == input_id
        and len(context.proxy.forwardings("POST", proxy_path)) == 1,
        "Reference recovery did not resolve the original request without a second POST.",
    )

    replay = context.inspector.client(path, method="POST", body=body)
    require(
        replay.get("replay") is True and replay.get("event_id") == event_id
        and replay.get("input_id") == input_id
        and len(context.proxy.forwardings("POST", proxy_path)) == 2,
        "The independent same-entry credential did not replay the original event and input.",
    )
    matches = [
        event["event_id"] for event in context.control.data("/__test/events")
        if event["direction"] == "inbound" and event["conversation_id"] == context.conversation_id
        and event["content"] == text
    ]
    require(matches == [event_id], "The original Work input did not appear exactly once in the event ledger.")
    context.evidence["input_replay"] = {
        "request_alias": "request-1",
        "event_alias": "event-2",
        "input_alias": "input-1",
        "lost_after_upstream_http_200": True,
        "phone_posts_after_recovery": 1,
        "same_entry_independent_replay": True,
        "same_event_and_input": True,
        "matching_input_event_count": len(matches),
    }
    artifact = context.publish_and_change()
    downloaded = context.backend.workspace / "reference-phone-original"
    result, _ = phone.run(
        "artifact", artifact.artifact_id, "--version", artifact.version, "--output", str(downloaded)
    )

    # The pinned CLI checks bytes. Read the corresponding response headers using its credential.
    phone_api = FixtureAPI(context.client_url, phone.token, context.binding_id)
    status, headers, response = phone_api.request(
        f"/client/v1/artifacts/{artifact.artifact_id}", params={"version": artifact.version}
    )
    require(
        status == 200 and headers.get("X-Yuki-Sha256") == artifact.version
        and headers.get("ETag") == f'"{artifact.version}"'
        and hashlib.sha256(response).hexdigest() == artifact.version,
        "The original artifact response headers and bytes did not match its Work version.",
    )
    context.evidence["phone_artifact"] = {
        "artifact_alias": "artifact-1", "etag_matches": True, "sha256_header_matches": True,
    }
    return PhoneDownload(artifact, downloaded, result.get("sha256"))


def existing_file(value):
    path = Path(value).expanduser()
    require(path.is_absolute() and path.is_file(), "Enter the absolute path of an existing file.")
    return path


def preserve_screenshot(source, output):
    limit = 32 * 1024 * 1024
    with source.open("rb") as stream:
        contents = stream.read(limit + 1)
    require(
        45 <= len(contents) <= limit and contents.startswith(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")
        and contents.endswith(b"\x00\x00\x00\x00IEND\xaeB\x60\x82"),
        "The Desktop screenshot is not a complete PNG within the evidence size limit.",
    )
    output.mkdir(parents=True, exist_ok=True)
    path = None
    try:
        with NamedTemporaryFile(dir=output, delete=False) as pending:
            path = Path(pending.name)
            pending.write(contents)
        path.replace(output / SCREENSHOT)
    finally:
        if path is not None:
            path.unlink(missing_ok=True)


def after(context, old_pid, download, output, ask=prompt):
    """Obtain operator confirmation and check Desktop saved its own original bytes."""
    require(not process_alive(old_pid), "The original Desktop process returned during the phone phase.")
    context.verify_phone_download(download)
    artifact = download.artifact
    _, prefix = loopback_endpoint(context.proxy.base_url)
    artifact_route = f"{prefix}/client/v1/artifacts/{artifact.artifact_id}"
    before_reopen = len(context.proxy.forwardings("GET", artifact_route))
    pid = live_pid(ask("after.reopen", "Reopen the same Desktop profile, then enter the new Desktop main PID."))
    require(pid != old_pid, "Desktop reopen must have a new main process PID.")
    saved = existing_file(ask("after.save", "Save the original Work result in Desktop and enter the absolute saved file path."))
    digest = sha256(saved)
    require(
        not saved.samefile(download.path) and digest == download.sha256 == artifact.version
        and digest != artifact.changed_version,
        "The separate Desktop save, phone download, and Work artifact did not share the original bytes.",
    )
    transfers = context.proxy.forwardings("GET", artifact_route)[before_reopen:]
    require(
        any(transfer.upstream_status == 200 for transfer in transfers),
        "No artifact retrieval succeeded during the Desktop save phase.",
    )
    context.evidence["download_comparison"] = {
        "desktop_pid_changed": True,
        "independent_saved_files": True,
        "artifact_get_http_200_after_reopen": True,
        "desktop_sha256": digest,
        "phone_sha256": download.sha256,
        "work_version_sha256": artifact.version,
        "workspace_sha256": artifact.changed_version,
    }

    source = existing_file(ask(
        "after.screenshot", "Capture the synthetic Desktop result as PNG and enter its absolute screenshot path."
    ))
    preserve_screenshot(source, output)
    context.evidence["screenshot_path"] = SCREENSHOT
    confirmation = ask(
        "after.confirm", "Type yes if this is the same Desktop profile and the original Work shows one matching result."
    )
    context.evidence["operator_confirmed_matching_single_result"] = confirmation.lower() == "yes"
    require(
        context.evidence["operator_confirmed_matching_single_result"] and process_alive(pid),
        "Matching Desktop UI or the reopened Desktop process was not confirmed.",
    )


def run_desktop_handoff(output, phone_phase: PhonePhase = reference_phone, *, ask=prompt):
    evidence = {}
    backend_commit = None
    try:
        if os.name != "posix":
            raise HandoffBlocked("Desktop process verification currently requires a POSIX runner.")
        require(output.resolve() == output, "The run output resolves through a symbolic link.")
        evidence["desktop_commit"] = desktop_checkout()
        with start_backend("mobile-fixture") as backend:
            backend_commit = backend.commit
            with FaultProxy(backend.base_url) as proxy:
                context, pid = before(backend, proxy, evidence, ask)
                download = phone_phase(context)
                after(context, pid, download, output, ask)
        evidence.update(
            status="passed",
            id_aliases={
                "binding_id": "binding-1", "conversation_id": "conversation-1", "device_id": "device-1",
                "work_id": "work-1", "request_id": "request-1", "event_id": "event-1",
                "artifact_id": "artifact-1",
            },
        )
    except (HandoffBlocked, BackendUnavailable) as error:
        evidence.update(status="blocked", reason=str(error))
    except (EOFError, KeyboardInterrupt):
        evidence.update(status="blocked", reason="The guided Desktop operation ended before confirmation.")
    except (OSError, subprocess.TimeoutExpired):
        evidence.update(status="blocked", reason="A local process or evidence file was unavailable.")
    except (HandoffFailure, SmokeFailure) as error:
        evidence.update(status="failed", reason=str(error))
    except (KeyError, ValueError, TypeError, AttributeError):
        evidence.update(status="failed", reason="A fixture or client response did not match the pinned contract.")
    return evidence, backend_commit
