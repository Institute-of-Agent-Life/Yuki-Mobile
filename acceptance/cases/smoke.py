from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
from http.client import HTTPConnection, HTTPException
import json
import subprocess
from uuid import uuid4

from acceptance.backend import LOOPBACK, fixture_environment, loopback_endpoint
from acceptance.fault_proxy import FaultProxy


class SmokeFailure(RuntimeError):
    """A failed smoke check with partial, sanitized evidence."""

    def __init__(self, message, evidence=None):
        super().__init__(message)
        self.evidence = evidence


def _require(condition, message):
    if not condition:
        raise SmokeFailure(message)


class _CLI:
    def __init__(self, backend, base_url, label):
        self.backend = backend
        self.base_url = base_url
        self.state = backend.workspace / f"{label}.sqlite"
        self.label = label
        self.token = None

    def run(self, *args, pairing_code=None, expected_exit=0):
        env = fixture_environment(self.backend.workspace, self.backend.source)
        if self.token:
            env["QA_FIXTURE_TOKEN"] = self.token
        if pairing_code:
            env["QA_FIXTURE_CODE"] = pairing_code
        process = subprocess.run(
            [
                str(self.backend.python), "-B", "-m", "yuki.cli", "client",
                "--url", self.base_url, "--state", str(self.state),
                "--token-env", "QA_FIXTURE_TOKEN", *args,
            ],
            cwd=self.backend.source,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=45,
        )
        _require(process.returncode == expected_exit, f"Unexpected reference CLI exit during {args[0]}.")
        return json.loads(process.stdout), process.returncode

    def pair(self, code):
        paired, _ = self.run(
            "pair", "--label", f"acceptance-{self.label}", "--code-env", "QA_FIXTURE_CODE",
            pairing_code=code,
        )
        self.token = paired.get("token")
        device = paired.get("device_id")
        _require(
            isinstance(self.token, str) and self.token and isinstance(device, str) and device,
            "Reference CLI pairing did not return a device credential.",
        )
        return device


def _get(base_url, path, *, read_json=False):
    port, _ = loopback_endpoint(base_url)
    connection = HTTPConnection(LOOPBACK, port, timeout=10)
    try:
        connection.request("GET", path)
        with connection.getresponse() as response:
            body = response.read()
            return response.status, json.loads(body) if read_json and response.status == 200 else None
    finally:
        connection.close()


def _route_boundary(backend, proxy, prefix, evidence):
    control_path = prefix + "/__test/events"
    status, _ = _get(backend.base_url, control_path, read_json=True)
    evidence["fixture_control_status"] = status
    evidence["proxy_prefixed_control_status"], _ = _get(proxy.base_url, control_path)
    evidence["proxy_root_control_status"], _ = _get(proxy.base_url, "/__test/events")
    _require(
        evidence == {
            "fixture_control_status": 200,
            "proxy_prefixed_control_status": 404,
            "proxy_root_control_status": 404,
        },
        "The fixture control route boundary did not match expectations.",
    )


def _pair_devices(backend, a, b, evidence):
    a_device = a.pair(backend.owner_pairing_code)
    link, _ = a.run("link-code")
    code = link.get("code")
    _require(isinstance(code, str) and code, "Reference CLI did not issue a second device code.")
    b_device = b.pair(code)
    a_me, _ = a.run("me")
    b_me, _ = b.run("me")
    evidence.update(
        distinct_devices=a_device != b_device,
        distinct_credentials=a.token != b.token,
        same_entry_binding=bool(a_me.get("binding_id")) and a_me["binding_id"] == b_me.get("binding_id"),
        same_conversation=bool(a_me.get("conversation_id")) and a_me["conversation_id"] == b_me.get("conversation_id"),
    )
    _require(all(evidence.values()), "The two reference devices must share one entry and conversation.")
    evidence["device_aliases"] = {"a": "device-1", "b": "device-2"}
    return a_me["conversation_id"]


def _fixture_events(backend, prefix, text):
    status, events = _get(backend.base_url, prefix + "/__test/events", read_json=True)
    _require(status == 200 and isinstance(events, list), "The fixture event ledger was unavailable.")
    return [event for event in events if event.get("direction") == "inbound" and event.get("content") == text]


def _receipt_evidence(receipt, conversation, payload_hash, event, event_alias):
    _require(
        receipt.get("found") is True
        and receipt.get("operation_kind") == "message"
        and receipt.get("target") == receipt.get("conversation_id") == conversation
        and receipt.get("payload_hash") == payload_hash
        and receipt.get("event_id") == event
        and type(event) is int
        and not receipt.get("problem_code"),
        "The authoritative receipt did not match the submitted operation and ledger event.",
    )
    return {
        "found": True,
        "operation_kind": "message",
        "target_alias": "conversation-1",
        "event_alias": event_alias,
        "payload_hash": receipt["payload_hash"],
        "target_matches": True,
        "event_matches": True,
    }


def _case(backend, proxy, a, b, conversation, prefix, evidence, *, fault, response):
    before_forward = fault == "drop-before-forward"
    case_alias = evidence["case"]
    message_path = prefix + "/client/v1/messages"
    request_id = str(uuid4())
    receipt_path = f"{prefix}/client/v1/requests/{request_id}"
    text = f"QA-001 fixture message {case_alias} {uuid4()}"
    original_body = {"text": text, "attachments": []}
    payload_hash = hashlib.sha256(
        json.dumps(
            ["message", conversation, original_body], sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    prior_forwards = len(proxy.forwardings("POST", message_path))
    prior_events = _fixture_events(backend, prefix, text)
    evidence["matching_events"] = {"before_send": len(prior_events)}
    _require(not prior_events, "The synthetic message already exists in the fixture.")

    proxy.arm("POST", message_path, fault, response=response)
    http_503 = response == "http-503"
    evidence["send"] = {"started_at": datetime.now(timezone.utc).isoformat(timespec="microseconds")}
    failed, exit_code = a.run("send", text, "--request-id", request_id, expected_exit=1 if http_503 else 2)
    problem_code = failed.get("problem", {}).get("code") if http_503 else failed.get("problem")
    _require(
        problem_code == ("unknown" if http_503 else "transport_unknown")
        and (failed["problem"] if http_503 else failed).get("request_id") == request_id,
        "The original submit did not report the injected unknown outcome.",
    )

    after_send = proxy.forwardings("POST", message_path)[prior_forwards:]
    evidence["send"].update({
        "exit_code": exit_code,
        "problem_code": problem_code,
        "observed_at": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
        "post_count": len(after_send),
        "upstream": [asdict(item) for item in after_send],
    })
    _require(len(after_send) == (0 if before_forward else 1), "Unexpected upstream POST count after the fault.")
    if not before_forward:
        transfer = after_send[0]
        _require(
            transfer.upstream_status == 200
            and transfer.full_body_read
            and evidence["send"]["started_at"] <= transfer.forwarded_at
            <= transfer.upstream_finished_at <= evidence["send"]["observed_at"],
            "The proxy did not observe the completed original upstream response before injecting the fault.",
        )

    pending, _ = a.run("pending")
    rows = [row for row in pending if row.get("request_id") == request_id]
    _require(
        len(rows) == 1
        and rows[0].get("state") == "submitted"
        and rows[0].get("path") == "messages"
        and rows[0].get("body") == {"request_id": request_id, **original_body},
        "The original CLI outbox did not retain the submitted message with its original request ID.",
    )
    evidence["send"]["outbox_state"] = rows[0]["state"]

    after_fault_events = _fixture_events(backend, prefix, text)
    evidence["matching_events"]["after_send"] = len(after_fault_events)
    _require(
        len(after_fault_events) == (0 if before_forward else 1),
        "The fixture ledger did not reflect the expected admission at the fault point.",
    )
    receipt, _ = a.run("request", request_id)
    if before_forward:
        _require(receipt.get("found") is False, "The before-forward fault unexpectedly had a receipt.")
        evidence["receipt_before_retry"] = {"found": False}
    else:
        evidence["receipt_before_retry"] = _receipt_evidence(
            receipt, conversation, payload_hash, after_fault_events[0]["event_id"], evidence["event_alias"]
        )

    prior_lookups = len(proxy.forwardings("GET", receipt_path))
    retried, _ = a.run("retry", request_id)
    retry_result = retried.get("result")
    after_retry_count = len(proxy.forwardings("POST", message_path)) - prior_forwards
    lookups = proxy.forwardings("GET", receipt_path)[prior_lookups:]
    evidence["a_retry"] = {
        "post_count": after_retry_count,
        "receipt_lookups": [asdict(lookup) for lookup in lookups],
    }
    _require(
        lookups and all(lookup.upstream_status == 200 and lookup.full_body_read for lookup in lookups),
        "The outbox retry did not complete a lookup of the original receipt.",
    )
    _require(
        retried.get("request_id") == request_id
        and retried.get("state") in {"accepted", "result"}
        and isinstance(retry_result, dict)
        and after_retry_count == 1,
        "The reference outbox retry did not converge with one upstream POST.",
    )
    if before_forward:
        _require(retry_result.get("replay") is False, "The first delivery was unexpectedly a replay.")
        evidence["a_retry"]["replay"] = False
        completed = proxy.forwardings("POST", message_path)[-1]
        receipt_first = lookups[0].upstream_finished_at <= completed.forwarded_at
        evidence["a_retry"]["receipt_completed_before_post"] = receipt_first
        _require(receipt_first, "The outbox retry forwarded the POST before reading the original receipt.")
        _require(
            completed.upstream_status == 200 and completed.full_body_read,
            "The first delivery did not complete its upstream response.",
        )
        evidence["a_retry"]["upstream"] = asdict(completed)

    after_retry_events = _fixture_events(backend, prefix, text)
    evidence["matching_events"]["after_retry"] = len(after_retry_events)
    _require(
        len(after_retry_events) == 1
        and after_retry_events[0].get("conversation_id") == conversation
        and after_retry_events[0].get("event_id") == retry_result.get("event_id")
        and (before_forward or after_retry_events[0]["event_id"] == receipt["event_id"]),
        "The retry did not converge on the single original event in the paired conversation.",
    )
    event_id = after_retry_events[0]["event_id"]
    evidence["a_retry"].update(outbox_state=retried["state"], event_matches=True)
    observed_receipt, _ = a.run("request", request_id)
    evidence["receipt_after_retry"] = _receipt_evidence(
        observed_receipt, conversation, payload_hash, event_id, evidence["event_alias"]
    )

    if not before_forward:
        replay, _ = b.run("send", text, "--request-id", request_id)
        replay_result = replay.get("result")
        post_count = len(proxy.forwardings("POST", message_path)) - prior_forwards
        evidence["b_replay"] = {"post_count": post_count}
        _require(
            replay.get("request_id") == request_id
            and replay.get("state") in {"accepted", "result"}
            and isinstance(replay_result, dict)
            and replay_result.get("replay") is True
            and replay_result.get("event_id") == event_id
            and replay_result.get("conversation_id") == conversation
            and post_count == 2,
            "The second device did not obtain a replay of the original accepted message.",
        )
        replay_events = _fixture_events(backend, prefix, text)
        evidence["matching_events"]["after_replay"] = len(replay_events)
        _require(
            len(replay_events) == 1 and replay_events[0].get("event_id") == event_id,
            "The cross-device replay changed the original fixture event.",
        )
        b_receipt, _ = b.run("request", request_id)
        evidence["b_replay"].update(
            replay=True,
            event_matches=True,
            receipt=_receipt_evidence(b_receipt, conversation, payload_hash, event_id, evidence["event_alias"]),
        )


def run_smoke(backend):
    evidence = {"route_boundary": {}, "pairing": {}, "cases": []}
    try:
        _, prefix = loopback_endpoint(backend.base_url)
        with FaultProxy(backend.base_url) as proxy:
            _route_boundary(backend, proxy, prefix, evidence["route_boundary"])
            a, b = (_CLI(backend, proxy.base_url, label) for label in ("a", "b"))
            conversation = _pair_devices(backend, a, b, evidence["pairing"])
            cases = (
                ("after-upstream-disconnect", "drop-after-upstream", "disconnect"),
                ("after-upstream-http-503", "drop-after-upstream", "http-503"),
                ("before-forward-disconnect", "drop-before-forward", "disconnect"),
            )
            for number, (name, fault, response) in enumerate(cases, start=1):
                case = {
                    "case": name, "request_alias": f"request-{number}", "event_alias": f"event-{number}",
                    "fault": fault, "response": response,
                }
                evidence["cases"].append(case)
                _case(backend, proxy, a, b, conversation, prefix, case, fault=fault, response=response)
    except SmokeFailure as error:
        error.evidence = evidence
        raise
    except subprocess.TimeoutExpired:
        raise SmokeFailure("The reference CLI timed out.", evidence) from None
    except (AttributeError, HTTPException, KeyError, OSError, TypeError, ValueError):
        raise SmokeFailure("The smoke fixture or reference CLI returned an invalid response.", evidence) from None
    return evidence
