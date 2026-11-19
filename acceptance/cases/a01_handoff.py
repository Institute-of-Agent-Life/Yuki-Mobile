from dataclasses import dataclass, field
import hashlib
from http.client import HTTPConnection, HTTPException
import json
from pathlib import Path
import re
from time import monotonic, sleep
from typing import Any, Callable, Optional
from urllib.parse import urlencode

from acceptance.backend import BackendSession, LOOPBACK, loopback_endpoint
from acceptance.fault_proxy import FaultProxy


class HandoffFailure(RuntimeError):
    pass


class HandoffBlocked(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise HandoffFailure(message)


def wait_for(check, message, *, timeout=20):
    deadline = monotonic() + timeout
    while True:
        result = check()
        if result is not None:
            return result
        require(monotonic() < deadline, message)
        sleep(0.15)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_sha256(value):
    return isinstance(value, str) and re.fullmatch("[0-9a-f]{64}", value) is not None


@dataclass(frozen=True)
class FixtureAPI:
    base_url: str
    credential: Optional[str] = field(default=None, repr=False)
    binding: Optional[str] = field(default=None, repr=False)

    def request(self, path, *, method="GET", body=None, params=None):
        port, prefix = loopback_endpoint(self.base_url)
        headers = {}
        if self.credential:
            headers["Authorization"] = "Bearer " + self.credential
        if self.binding:
            headers["X-Yuki-Entry"] = self.binding
        if body is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(body).encode()
        target = prefix + path
        if params:
            target += "?" + urlencode(params)

        connection = HTTPConnection(LOOPBACK, port, timeout=12)
        try:
            connection.request(method, target, body=body, headers=headers)
            with connection.getresponse() as response:
                return response.status, response.headers, response.read()
        except (OSError, HTTPException):
            raise HandoffBlocked("The loopback fixture stopped responding.") from None
        finally:
            connection.close()

    def data(self, path, **kwargs):
        status, _, body = self.request(path, **kwargs)
        require(status == 200, f"A required fixture or client API returned HTTP {status}.")
        try:
            value = json.loads(body)
        except ValueError:
            raise HandoffFailure("A required API did not return JSON.") from None
        return value.get("data", value) if isinstance(value, dict) else value

    def client(self, path, **kwargs):
        return self.data("/client/v1/" + path, **kwargs)


def check_work_origin(facts, work, armed, desktop_device_id):
    trigger = work["trigger_event_id"]
    require(
        facts["work_id"] == work["work_id"]
        and facts["conversation_id"] == work["conversation_id"]
        and type(trigger) is int
        and trigger == facts["trigger_event_id"]
        and trigger > armed["armed_after_event_id"]
        and facts["principal_id"] == desktop_device_id,
        "The settled Work trigger did not match the armed Desktop message receipt.",
    )


@dataclass(frozen=True)
class HandoffArtifact:
    artifact_id: str = field(repr=False)
    version: str
    changed_version: str
    result_event_id: int = field(repr=False)


@dataclass(frozen=True)
class PhoneDownload:
    artifact: HandoffArtifact
    path: Path = field(repr=False)
    sha256: str


@dataclass
class HandoffContext:
    """Fixture state passed to the injected phone phase after Desktop has exited."""

    backend: BackendSession
    proxy: FaultProxy
    client_url: str
    inspector: FixtureAPI
    nonowner: FixtureAPI
    pairing_code: str = field(repr=False)
    binding_id: str = field(repr=False)
    conversation_id: str = field(repr=False)
    desktop_device_id: str = field(repr=False)
    work_id: str = field(repr=False)
    armed: dict[str, Any] = field(repr=False)
    initial_work: dict[str, Any] = field(repr=False)
    evidence: dict[str, Any] = field(repr=False)
    phone_device_id: Optional[str] = field(default=None, init=False, repr=False)
    _published: Optional[HandoffArtifact] = field(default=None, init=False, repr=False)

    @property
    def control(self):
        return FixtureAPI(self.backend.base_url)

    @property
    def work_path(self):
        return "works/" + self.work_id

    def facts(self, **filters):
        return self.control.data(
            "/__test/facts", params={"work_id": self.work_id, **filters}
        )

    def verify_phone_pair(self, phone_device_id, me):
        """Call with actual client /me before allowing the injected phone to submit."""
        require(
            isinstance(phone_device_id, str) and phone_device_id
            and phone_device_id != self.desktop_device_id
            and phone_device_id in {row["device_id"] for row in me["devices"]}
            and me["binding_id"] == self.binding_id
            and me["conversation_id"] == self.conversation_id,
            "The paired phone did not resolve the expected entry and conversation.",
        )
        facts = self.facts(paired_device_id=phone_device_id)
        check_work_origin(facts, self.initial_work, self.armed, self.desktop_device_id)
        pair = facts.get("pair", {})
        require(
            facts["state"] == "waiting_user"
            and pair.get("code_id")
            and pair.get("issuer_source") == "device"
            and pair.get("issuer_principal_id") == self.desktop_device_id
            and pair.get("source_binding_id") == self.binding_id,
            "The phone's redeemed pairing code was not issued by this Desktop on the expected entry.",
        )
        self.phone_device_id = phone_device_id
        self.evidence["pair_receipt"] = {
            "pair_code_alias": "code-1",
            "paired_device_alias": "device-2",
            "issuer_device_alias": "device-1",
            "issuer_source": "device",
            "source_binding_alias": "binding-1",
            "paired_binding_matches": True,
        }

    def publish_and_change(self):
        """Wait for the single original result and overwrite its workspace peer."""
        require(self.phone_device_id, "Verify the actual phone device before continuing the Work.")
        if self._published is not None:
            return self._published

        def completed():
            work = self.inspector.client(self.work_path)
            require(work["state"] not in ("cancelled", "failed"), "The original Work ended without success.")
            return work if work["state"] == "completed" else None

        final = wait_for(completed, "The original Work did not complete after the phone input.")
        facts = self.facts(paired_device_id=self.phone_device_id)
        check_work_origin(facts, self.initial_work, self.armed, self.desktop_device_id)
        require(
            final["work_id"] == self.work_id
            and final["conversation_id"] == self.conversation_id
            and final["generation"] == self.initial_work["generation"]
            and facts["state"] == "completed"
            and facts["revision"] == final["revision"] > self.initial_work["revision"],
            "The completed Work changed identity, generation, or entry.",
        )
        original_budget, budget = self.initial_work["budget"], final["budget"]
        require(
            all(budget[k] == original_budget[k] for k in ("root_id", "model_limit", "tool_limit"))
            and all(budget[k] >= original_budget[k] for k in ("models", "tools")),
            "The budget root, limits, or usage did not continue with the original Work.",
        )
        previous_events = [result["event_id"] for result in self.initial_work["results"]]
        final_events = [result["event_id"] for result in final["results"]]
        artifacts = facts["artifacts"]
        require(
            final_events == facts["result_event_ids"]
            and final_events[:-1] == previous_events
            and len(final_events) == len(previous_events) + 1
            and final_events[-1] not in previous_events
            and final["results"][-1]["conversation_id"] == self.conversation_id
            and len(artifacts) == 1,
            "The original Work did not acquire exactly one additional result.",
        )
        artifact = artifacts[0]
        version = artifact["version"]
        require(
            artifact["event_id"] == final_events[-1]
            and artifact["artifact_id"] == self.armed["artifact_id"]
            and version == self.armed["version"]
            and is_sha256(version),
            "The completed Work did not publish the armed artifact version.",
        )
        artifact_path = "/client/v1/artifacts/" + artifact["artifact_id"]
        nonowner_status, _, _ = self.nonowner.request(artifact_path, params={"version": version})
        require(nonowner_status == 404, "The nonowner did not receive HTTP 404 for the original artifact.")
        changed = self.control.data(
            "/__test/workspace-change/" + artifact["artifact_id"], method="POST"
        )["sha256"]
        require(is_sha256(changed) and changed != version, "The same-name workspace file did not change.")
        self.evidence["original_result"] = {
            "result_event_alias": "event-3",
            "new_result_count": 1,
            "work_completed": True,
            "same_generation_and_budget": True,
            "nonowner_artifact_status": nonowner_status,
            "artifact_sha256": version,
            "workspace_sha256": changed,
        }
        self._published = HandoffArtifact(artifact["artifact_id"], version, changed, final_events[-1])
        return self._published

    def verify_phone_download(self, download):
        require(
            self._published is not None and download.artifact == self._published,
            "The phone phase did not download the published artifact version.",
        )
        require(
            download.path.is_file()
            and sha256(download.path) == download.sha256 == self._published.version,
            "The phone's saved bytes did not match the Work artifact version.",
        )


PhonePhase = Callable[[HandoffContext], PhoneDownload]
