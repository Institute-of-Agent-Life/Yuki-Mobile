"""Serve QA-002 over the pinned Yuki reference container on loopback."""

import argparse
import asyncio
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
from inspect import signature
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from tempfile import NamedTemporaryFile
from unittest.mock import patch
from uuid import uuid4


YUKI_COMMIT = "6cf15321575391f217d1c9129e6f56e0010b09b9"
API_REVISION = 14
CONNECTOR_PROTOCOL = 5
ARTIFACT_NAME = "same.md"
ORIGINAL_BYTES = b"# Original mobile fixture artifact\n"
REPLACEMENT_BYTES = b"# Workspace file changed after publication\n"


def pinned_commit(source):
    marker = source / ".yuki-commit"
    if marker.is_file():
        return marker.read_text(encoding="ascii").strip()
    commit = os.environ.get("YUKI_BACKEND_COMMIT")
    if commit and not (source / ".git").exists():
        return commit
    result = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "--show-toplevel", "HEAD"],
        env={"PATH": os.defpath},
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        checkout, head = result.stdout.splitlines()
        if Path(checkout).resolve() == source:
            if commit and head != commit:
                raise ValueError(
                    "YUKI_BACKEND_COMMIT disagrees with the Yuki checkout."
                )
            return head
    return None


def overlapping(path, other):
    return path.is_relative_to(other) or other.is_relative_to(path)


def configure_paths(root, source):
    sys.dont_write_bytecode = True
    sys.path[:0] = [
        str(source / "src"),
        str(source),
        str(source / "packages" / "yuki-agent-connector" / "src"),
    ]
    # The reference server composes settings and relative stores inside its cwd.
    # Keep the local process independent of provider keys and fixture feature flags.
    system_root = os.environ.get("SYSTEMROOT") if os.name == "nt" else None
    os.environ.clear()
    if system_root:
        os.environ["SYSTEMROOT"] = system_root
    os.environ.update(
        PATH=os.defpath,
        XDG_CONFIG_HOME=str(root),
        XDG_DATA_HOME=str(root),
        XDG_CACHE_HOME=str(root),
        TMPDIR=str(root),
        TEMP=str(root),
        TMP=str(root),
    )
    os.umask(0o077)


def workspace_file(root, artifact_id):
    return root / "workspace" / "files" / artifact_id / ARTIFACT_NAME


def continuation_script(question, send):
    return [
        ("manage_task", {"action": "start", "goal": "原工作整合測試"}),
        ("send", {"text": question}),
        ("wait", {"for": [{"message": True}]}),
        ("send", send),
        ("manage_task", {"action": "finish", "result": "original work finished"}),
    ]


@dataclass(frozen=True)
class AppScript:
    after_event_id: int
    person_id: str
    tools: deque[tuple[str, dict]]


def fixture_backend():
    from fastapi import HTTPException
    from pydantic import BaseModel, ConfigDict, Field
    from sqlalchemy import func, select, text
    from tests.conftest import MemorySender
    from tests.support import desktop_fault_fixture, soul_reference_server
    from tests.support.account_observations import apply_observations
    from tests.support.canonical_identity import ensure_presence, ensure_space
    from tests.support.person_merge import merge_persons
    from tests.support.social_identity_cases import Bot
    from tests.support.workspace_snapshots import snapshot_bytes

    from yuki.client_api.db_models import ClientDeviceModel, ClientLinkCodeModel
    from yuki.client_api.identity import ClientIdentityService
    from yuki.client_api.version import CLIENT_API_REVISION
    from yuki.client_api.works import ClientWorks
    from yuki.conversation.canonical_db_models import ControlCommandReceiptModel
    from yuki.domain.conversations import ScopeType
    from yuki.domain.messages import (
        ChatResponse,
        InboundMessage,
        SenderIdentity,
        ToolCall,
        ToolFunction,
    )
    from yuki.external_agents.service import CONNECTOR_PROTOCOL as backend_protocol
    from yuki.gateway.providers.app.ingress import AppGateway
    from yuki.identity.canonical_repository import (
        ensure_account,
        find_identity_binding,
        replace_assignment,
    )
    from yuki.identity.credentials import secret_hash
    from yuki.identity.db_models import (
        AccountAssignmentModel,
        AccountModel,
        CanonicalPersonModel,
    )
    from yuki.identity.person_identity import same_person_condition
    from yuki.identity.routing import PresenceRouter
    from yuki.persistence.models import ChatEventModel
    from yuki.runtime.work_activation import current_work_control
    from yuki.runtime.work_schema_v1 import work
    from yuki.services.processor import MessageProcessor

    if (CLIENT_API_REVISION, backend_protocol) != (API_REVISION, CONNECTOR_PROTOCOL):
        raise ValueError(
            "QA-002 requires Client API revision 14 and connector protocol 5."
        )

    base = desktop_fault_fixture.DesktopFaultFixture
    serve = soul_reference_server.serve
    checks = (
        (
            serve,
            (None, None, 0, "/role/test"),
            {"desktop": True, "real_model_source": None},
        ),
        (base, (None, None, None, None, None), {}),
        (base.attach, (None, None), {}),
        (base.respond, (None, None), {}),
        (base.close, (None,), {}),
        (snapshot_bytes, (None, None, None), {"artifact_id": None}),
        (
            ClientIdentityService.issue_for_operator,
            (None, None, None, None),
            {"purpose": "chat"},
        ),
        (ClientIdentityService.pair, (None, None, None), {}),
        (ClientWorks.public_many, (None, None, []), {}),
        (AppGateway.request_task, (None, None, None), {}),
        (MessageProcessor.handle, (None, None, None), {"on_committed": None}),
        (PresenceRouter.cas_takeover_space, (None, None), {}),
        (merge_persons, (None, None, None), {"now": None}),
    )
    for method, args, kwargs in checks:
        try:
            signature(method).bind(*args, **kwargs)
        except TypeError as exc:
            raise ValueError(
                f"Incompatible pinned Yuki interface: {method.__qualname__}."
            ) from exc

    class PersonEnabled(BaseModel):
        model_config = ConfigDict(extra="forbid")
        person_id: str
        enabled: bool = Field(strict=True)

    class ScriptTool(BaseModel):
        model_config = ConfigDict(extra="forbid")
        name: str = Field(min_length=1)
        arguments: dict

    class ModelScript(BaseModel):
        model_config = ConfigDict(extra="forbid")
        tools: list[ScriptTool] = Field(min_length=1)
        person_id: str | None = Field(default=None, min_length=1)

    class MobileFixture(base):
        def __init__(self, runtime, root, person, other_code, model):
            super().__init__(runtime, root, person, other_code, model)
            self.pending_app_script = None
            self.scripts = {}
            self.bot = Bot("80001")
            self.group_calls = {}
            self.source_operator_id = str(uuid4())

        async def arm_app(self, script, *, person_id=None):
            async with self.runtime.database.read_session() as session:
                if (
                    person_id is not None
                    and await session.get(CanonicalPersonModel, person_id) is None
                ):
                    raise HTTPException(404, "Fixture person is unknown.")
                boundary = (
                    await session.scalar(select(func.max(ChatEventModel.id))) or 0
                )
            self.pending_app_script = AppScript(
                boundary, person_id or self.person, deque(script)
            )
            return {"armed_after_event_id": boundary}

        async def respond(self, request):
            control = current_work_control.get()
            if control is None:
                return await super().respond(request)
            event_id = control.source.get("trigger_event_id")
            script = self.scripts.get(event_id)
            pending = self.pending_app_script
            if (
                script is None
                and pending is not None
                and control.current is None
                and isinstance(event_id, int)
                and event_id > pending.after_event_id
            ):
                async with self.runtime.database.read_session() as session:
                    original = await session.scalar(
                        select(ChatEventModel.id)
                        .join(
                            AccountAssignmentModel,
                            AccountAssignmentModel.id
                            == ChatEventModel.ingress_presence_id,
                        )
                        .join(
                            AccountModel,
                            AccountModel.id == AccountAssignmentModel.account_id,
                        )
                        .where(
                            ChatEventModel.id == event_id,
                            ChatEventModel.direction == "inbound",
                            AccountModel.platform == "app",
                            same_person_condition(
                                ChatEventModel.author_person_id, pending.person_id
                            ),
                        )
                    )
                if original is not None and pending is self.pending_app_script:
                    script = self.scripts[event_id] = pending.tools
                    self.pending_app_script = None
            if script is None:
                return await super().respond(request)
            name, arguments = script.popleft()
            if not script:
                del self.scripts[event_id]
            return ChatResponse(
                content="",
                latency_seconds=0,
                tool_calls=(
                    ToolCall(str(uuid4()), ToolFunction(name, json.dumps(arguments))),
                ),
            )

        async def qq_group(self):
            runtime = self.runtime
            user_id = "10001"
            group_id = str(20000 + (uuid4().int % 1000000000))
            now = datetime.now(UTC)
            async with runtime.database.immediate_session() as session:
                presence_id = await ensure_presence(session, self.bot.self_id, now=now)
                if await find_identity_binding(session, user_id) is None:
                    account = await ensure_account(
                        session, user_id, platform="qq", now=now
                    )
                    person = await session.get(CanonicalPersonModel, self.person)
                    await replace_assignment(session, account, None, person, now=now)
                space_id = await ensure_space(session, group_id, enabled=True, now=now)
            runtime.gateway_registry.connect(
                self.bot, provider_id="snowluma", presence_id=presence_id
            )
            nonce = str(uuid4())
            inbound = InboundMessage(
                ingress_key=nonce,
                native_message_id=nonce,
                event_type="message:group:normal",
                scope_type=ScopeType.GROUP,
                sender=SenderIdentity(
                    user_id, nickname="Fixture owner", nickname_known=True
                ),
                text="請建立測試群組工作",
                bot_user_id=self.bot.self_id,
                group_id=group_id,
                mentions_bot=True,
            )
            await apply_observations(runtime.database, inbound)
            if await runtime.presence_router.cas_takeover_space(space_id) != "taken":
                raise HTTPException(409, "Could not take over the fixture group route.")

            def committed(result):
                self.scripts[result.event.id] = deque(
                    continuation_script(
                        "群組問題：你在哪個城市？", {"text": "群組原工作已完成"}
                    )
                )

            sender = MemorySender()
            sender.bot = self.bot
            await runtime.processor.handle(inbound, sender, on_committed=committed)
            async with runtime.database.read_session() as session:
                result = (
                    await session.execute(
                        select(work.c.id, work.c.conversation_id, work.c.state)
                        .join(
                            ChatEventModel,
                            ChatEventModel.id
                            == func.json_extract(
                                work.c.source_json, "$.trigger_event_id"
                            ),
                        )
                        .where(ChatEventModel.origin_key == nonce)
                    )
                ).one_or_none()
            if result is None or result.state != "waiting_user":
                raise HTTPException(
                    409, "The fixture group Work did not reach waiting_user."
                )
            self.group_calls[result.id] = group_id
            return {
                "work_id": result.id,
                "conversation_id": result.conversation_id,
                "group_id": group_id,
            }

        async def second_entry(self):
            database = self.runtime.database
            identity = self.runtime.client_identity
            source_id = str(uuid4())
            now = datetime.now(UTC)
            async with database.immediate_session() as session:
                session.add(
                    CanonicalPersonModel(
                        id=source_id,
                        display_name="Second App entry",
                        is_superuser=False,
                        created_at=now,
                        updated_at=now,
                    )
                )
            issued = await identity.issue_for_operator(
                source_id, self.source_operator_id, str(uuid4()), purpose="chat"
            )
            seed = await identity.pair(issued["code"], "Second App entry seed")
            original = await identity.authenticate(seed["token"])
            async with database.immediate_session() as session:
                await merge_persons(
                    session, source_id, self.person, now=datetime.now(UTC)
                )
            return {
                "source_person_id": source_id,
                "binding_id": original.binding_id,
                "conversation_id": original.conversation_id,
                "seed_device_id": seed["device_id"],
            }

        async def settled_work(self, work_id):
            async with self.runtime.database.read_session() as session:
                row = (
                    (await session.execute(select(work).where(work.c.id == work_id)))
                    .mappings()
                    .one_or_none()
                )
                if row is None:
                    raise HTTPException(404, "Fixture Work is unknown.")
                trigger = json.loads(row["source_json"]).get("trigger_event_id")
                event = (
                    await session.get(ChatEventModel, trigger)
                    if trigger is not None
                    else None
                )
            if row["state"] in {
                "waiting_user",
                "waiting_external",
                "suspended",
                "completed",
                "failed",
                "cancelled",
            }:
                if event is not None and event.origin_key:
                    # App ingress keys end in the request ID. Its specific task owns
                    # on_finished, including the receipt commit in its finally block.
                    request_id = event.origin_key.rsplit(":", 1)[-1]
                    task = self.runtime.app_gateway.request_task(
                        row["conversation_id"], request_id
                    )
                    if task is not None:
                        try:
                            await asyncio.wait_for(asyncio.shield(task), timeout=8)
                        except TimeoutError as exc:
                            raise HTTPException(
                                503, "The App trigger receipt is still settling."
                            ) from exc

        @staticmethod
        def code_facts(code):
            if code is None:
                raise HTTPException(404, "Fixture pairing code is unknown.")
            return {
                "code_id": code.id,
                "issuer_principal_id": code.issuer_principal_id,
                "issuer_source": code.issuer_source,
                "source_binding_id": code.source_binding_id,
            }

        async def paired_code(self, session, device_id):
            device = await session.get(ClientDeviceModel, device_id)
            receipt = await session.scalar(
                select(ControlCommandReceiptModel).where(
                    ControlCommandReceiptModel.operation_kind == "client.pair",
                    ControlCommandReceiptModel.principal_id == device_id,
                    ControlCommandReceiptModel.result_resource_id == device_id,
                )
            )
            if device is None or receipt is None:
                raise HTTPException(
                    404, "Fixture chat device or pair receipt is unknown."
                )
            candidates = await session.scalars(
                select(ClientLinkCodeModel).where(
                    ClientLinkCodeModel.person_id == device.person_id,
                    ClientLinkCodeModel.purpose == "chat",
                    ClientLinkCodeModel.used_at.is_not(None),
                )
            )
            code = next(
                (
                    candidate
                    for candidate in candidates
                    if secret_hash(f"client.pair:{candidate.id}:{device.label}")
                    == receipt.payload_hash
                ),
                None,
            )
            return self.code_facts(code)

        def group_facts(self, group_id):
            hashes = []
            for action, params in self.bot.calls:
                if action == "send_group_msg" and str(params["group_id"]) == group_id:
                    message = params["message"]
                    body = "".join(
                        part["data"]["text"]
                        for part in message
                        if part["type"] == "text"
                    )
                    hashes.append(hashlib.sha256(body.encode()).hexdigest())
            return {
                "group_id": group_id,
                "send_count": len(hashes),
                "body_sha256": hashes,
            }

        async def facts(self, work_id, paired_device_id, code_id):
            await self.settled_work(work_id)
            async with self.runtime.database.read_session() as session:
                await session.execute(text("BEGIN"))
                row = (
                    (await session.execute(select(work).where(work.c.id == work_id)))
                    .mappings()
                    .one()
                )
                works = self.runtime.client_api.works
                (public,) = await works.public_many(session, [dict(row)])
                trigger = public["trigger_event_id"]
                principal_id = await session.scalar(
                    select(ControlCommandReceiptModel.principal_id).where(
                        ControlCommandReceiptModel.operation_kind == "client.message",
                        ControlCommandReceiptModel.operation_ref
                        == public["conversation_id"],
                        ControlCommandReceiptModel.result_resource_id == str(trigger),
                    )
                )
                results = public["results"]
                facts = {
                    key: public[key]
                    for key in (
                        "work_id",
                        "conversation_id",
                        "state",
                        "revision",
                        "trigger_event_id",
                    )
                }
                facts["principal_id"] = principal_id
                facts["result_event_ids"] = [result["event_id"] for result in results]
                facts["artifacts"] = [
                    {
                        "event_id": result["event_id"],
                        "artifact_id": artifact["artifact_id"],
                        "version": artifact["version"],
                    }
                    for result in results
                    for artifact in result["artifacts"]
                ]
                if paired_device_id is not None:
                    facts["pair"] = await self.paired_code(session, paired_device_id)
                if code_id is not None:
                    facts["code"] = self.code_facts(
                        await session.get(ClientLinkCodeModel, code_id)
                    )
            if work_id in self.group_calls:
                facts["group"] = self.group_facts(self.group_calls[work_id])
            return facts

        def attach(self, app):
            super().attach(app)
            app.debug = False

            @app.post("/__test/work-scenario-artifact")
            async def work_scenario_artifact():
                artifact_id = str(uuid4())
                original = workspace_file(self.root, artifact_id)

                def snapshot():
                    original.parent.mkdir(parents=True)
                    original.write_bytes(ORIGINAL_BYTES)
                    return snapshot_bytes(
                        self.runtime.workspace_service.store,
                        original.name,
                        original.read_bytes(),
                        artifact_id=artifact_id,
                    )

                artifact = await asyncio.to_thread(snapshot)
                armed = await self.arm_app(
                    continuation_script(
                        "需要你提供城市",
                        {
                            "text": "已依原工作完成",
                            "file": {"artifact_id": artifact_id},
                        },
                    )
                )
                return {
                    **armed,
                    "artifact_id": artifact_id,
                    "version": artifact["sha256"],
                }

            @app.post("/__test/workspace-change/{artifact_id}")
            async def workspace_change(artifact_id: str):
                if not re.fullmatch(r"[0-9a-f-]{36}", artifact_id):
                    raise HTTPException(404, "Fixture workspace file is unknown.")
                original = workspace_file(self.root, artifact_id)
                if not original.is_file():
                    raise HTTPException(404, "Fixture workspace file is unknown.")
                await asyncio.to_thread(original.write_bytes, REPLACEMENT_BYTES)
                return {
                    "name": original.name,
                    "sha256": hashlib.sha256(REPLACEMENT_BYTES).hexdigest(),
                }

            @app.post("/__test/qq-group-work")
            async def qq_group_work():
                return await self.qq_group()

            @app.post("/__test/second-app-entry")
            async def second_app_entry():
                return await self.second_entry()

            @app.get("/__test/facts")
            async def facts(
                work_id: str,
                paired_device_id: str | None = None,
                code_id: str | None = None,
            ):
                return await self.facts(work_id, paired_device_id, code_id)

            @app.post("/__test/source-enabled")
            async def source_enabled(body: PersonEnabled):
                async with self.runtime.database.immediate_session() as session:
                    person = await session.get(CanonicalPersonModel, body.person_id)
                    if person is None:
                        raise HTTPException(404, "Fixture Person is unknown.")
                    person.enabled = body.enabled
                return {"enabled": body.enabled}

            @app.post("/__test/model-script")
            async def model_script(body: ModelScript):
                return await self.arm_app(
                    [(tool.name, tool.arguments) for tool in body.tools],
                    person_id=body.person_id,
                )

    return serve, desktop_fault_fixture, MobileFixture


async def run(serve, module, fixture_class, root, state_file, port, base_path):
    upstream_state = root / ".mobile-reference-state.json"
    previous = upstream_state.stat().st_ino if upstream_state.exists() else None
    soul = root / "character" / "SOUL.md"
    soul.parent.mkdir(parents=True, exist_ok=True)
    if not soul.exists():
        soul.write_text("你是 {{name}}。\n", encoding="utf-8")
    os.chdir(root)
    # The reference server imports this class inside serve(). Substitute only
    # inside the isolated fixture process and keep all other wiring intact.
    with patch.object(module, "DesktopFaultFixture", fixture_class):
        async with asyncio.TaskGroup() as tasks:
            serving = tasks.create_task(
                serve(
                    root,
                    upstream_state,
                    port,
                    base_path,
                    desktop=True,
                    real_model_source=None,
                )
            )
            while not serving.done():
                if upstream_state.exists() and upstream_state.stat().st_ino != previous:
                    state = json.loads(upstream_state.read_text(encoding="utf-8"))
                    state.update(
                        yuki_commit=YUKI_COMMIT,
                        api_revision=API_REVISION,
                        connector_protocol=CONNECTOR_PROTOCOL,
                    )
                    with NamedTemporaryFile(
                        mode="w", dir=state_file.parent, encoding="utf-8", delete=False
                    ) as pending:
                        json.dump(state, pending)
                    try:
                        Path(pending.name).replace(state_file)
                    finally:
                        Path(pending.name).unlink(missing_ok=True)
                    break
                await asyncio.sleep(0.01)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--state-file", required=True, type=Path)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--base-path", default="/role/test")
    args = parser.parse_args()
    root = args.root.resolve()
    state_file = args.state_file.resolve()
    source = Path(os.environ.get("YUKI_BACKEND_TEST_ROOT") or Path.cwd()).resolve()
    protected = [source]
    if desktop := os.environ.get("YUKI_DESKTOP_ROOT"):
        protected.append(Path(desktop).resolve())
    if any(
        overlapping(root, tree) or overlapping(state_file, tree) for tree in protected
    ):
        parser.error(
            "--root and --state-file must be outside the Yuki and Desktop sources."
        )
    if state_file == root / ".mobile-reference-state.json":
        parser.error("--state-file must differ from the internal reference state.")
    if not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535.")
    if not re.fullmatch(r"(?:/[A-Za-z0-9_-]+)+", args.base_path):
        parser.error("--base-path must contain canonical path segments.")
    try:
        if pinned_commit(source) != YUKI_COMMIT:
            parser.error("The backend source does not match the pinned Yuki commit.")
        if not (source / "tests" / "support" / "soul_reference_server.py").is_file():
            parser.error("The pinned source is missing the Yuki reference server.")
        configure_paths(root, source)
        backend = fixture_backend()
    except (ImportError, OSError, ValueError) as exc:
        parser.error(str(exc))

    root.mkdir(parents=True, exist_ok=True)
    state_file.parent.mkdir(parents=True, exist_ok=True)
    asyncio.run(run(*backend, root, state_file, args.port, args.base_path))


if __name__ == "__main__":
    main()
