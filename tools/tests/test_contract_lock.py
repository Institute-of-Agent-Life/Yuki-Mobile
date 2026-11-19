import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from contract_lock import ContractError, ensure_backward_compatible


REPO = Path(__file__).resolve().parents[2]
ROUTE = "/client/v1/example"
OPENAPI = "client-api.openapi.json"
BACKEND_COMMIT = "1" * 40


def schema_ref(name):
    return {"$ref": f"#/components/schemas/{name}"}


def document(revision):
    return {
        "openapi": "3.1.0",
        "info": {"version": "4.0.2", "x-yuki-api-revision": revision},
        "paths": {
            ROUTE: {"post": {
                "parameters": [
                    {"in": "query", "name": "filter", "schema": {"type": "string"}},
                ],
                "requestBody": {"content": {
                    "application/json": {"schema": schema_ref("Input")},
                }},
                "responses": {"200": {"content": {
                    "application/json": {"schema": schema_ref("Output")},
                }}},
            }},
        },
        "components": {"schemas": {
            "Input": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            "Output": {
                "type": "object",
                "properties": {"record": schema_ref("Record")},
                "required": ["record"],
            },
            "Record": {
                "type": "object",
                "properties": {
                    "label": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                    "children": {"type": "array", "items": schema_ref("Record")},
                },
                "required": ["label"],
            },
        }},
    }


class CompatibilityTest(unittest.TestCase):
    def setUp(self):
        self.desktop = document(12)
        self.backend = copy.deepcopy(self.desktop)
        self.backend["info"]["x-yuki-api-revision"] = 14
        self.operation = self.backend["paths"][ROUTE]["post"]
        self.schemas = self.backend["components"]["schemas"]

    def test_optional_request_field_and_extra_response_fields_are_compatible(self):
        self.operation["parameters"].append(
            {"in": "query", "name": "page", "schema": {"type": "integer"}}
        )
        self.schemas["Input"]["properties"]["note"] = {"type": "string"}
        self.schemas["Record"]["properties"]["kind"] = {"type": "string"}
        self.schemas["Record"]["required"].append("kind")
        self.backend["paths"]["/client/v1/new"] = {}
        ensure_backward_compatible(self.desktop, self.backend)

    def test_desktop_method_must_remain_available(self):
        self.backend["paths"][ROUTE].pop("post")
        with self.assertRaisesRegex(ContractError, "POST /client/v1/example"):
            ensure_backward_compatible(self.desktop, self.backend)

    def test_old_input_cannot_become_required(self):
        self.operation["parameters"][0]["required"] = True
        with self.assertRaisesRegex(ContractError, "filter.*required"):
            ensure_backward_compatible(self.desktop, self.backend)

    def test_new_required_request_field_is_incompatible(self):
        self.schemas["Input"]["properties"]["note"] = {"type": "string"}
        self.schemas["Input"]["required"].append("note")
        with self.assertRaisesRegex(ContractError, "note.*required"):
            ensure_backward_compatible(self.desktop, self.backend)

    def test_old_request_field_must_remain_supported(self):
        self.schemas["Input"]["properties"].clear()
        self.schemas["Input"]["required"].clear()
        with self.assertRaisesRegex(ContractError, "text.*removed"):
            ensure_backward_compatible(self.desktop, self.backend)

    def test_old_request_constraint_cannot_narrow(self):
        self.schemas["Input"]["properties"]["text"]["maxLength"] = 5
        with self.assertRaisesRegex(ContractError, "text.*maxLength"):
            ensure_backward_compatible(self.desktop, self.backend)

    def test_old_request_media_type_must_remain_supported(self):
        self.operation["requestBody"]["content"] = {"text/plain": {}}
        with self.assertRaisesRegex(ContractError, "requestBody.*application/json"):
            ensure_backward_compatible(self.desktop, self.backend)

    def test_referenced_response_nullability_and_type_are_checked(self):
        label = self.schemas["Record"]["properties"]["label"]
        label["anyOf"][0]["type"] = "integer"
        with self.assertRaisesRegex(ContractError, "response.*record.*label.*type"):
            ensure_backward_compatible(self.desktop, self.backend)
        label["anyOf"] = [{"type": "string"}]
        with self.assertRaisesRegex(ContractError, "response.*record.*label.*anyOf"):
            ensure_backward_compatible(self.desktop, self.backend)

    def test_old_required_response_field_cannot_become_optional(self):
        self.schemas["Record"]["required"].remove("label")
        with self.assertRaisesRegex(ContractError, "response.*label.*required"):
            ensure_backward_compatible(self.desktop, self.backend)

    def test_closed_response_cannot_emit_new_fields(self):
        self.desktop["components"]["schemas"]["Record"]["additionalProperties"] = False
        self.schemas["Record"]["additionalProperties"] = False
        self.schemas["Record"]["properties"]["note"] = {"type": "string"}
        with self.assertRaisesRegex(ContractError, "response.*note"):
            ensure_backward_compatible(self.desktop, self.backend)


class CheckCommandTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.mobile = root / "mobile"
        self.desktop = root / "desktop"
        self.lock = self.mobile / "contract-lock"
        self.archive = root / "archive"
        self.script = self.mobile / "tools" / "contract_lock.py"
        self.script.parent.mkdir(parents=True)
        for filename in ("contract_lock.py", "check_repo.py"):
            shutil.copyfile(REPO / "tools" / filename, self.script.parent / filename)
        self.desktop.mkdir()
        self.git("init", "-q")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        revision_file = self.desktop / "src" / "shared" / "api.ts"
        revision_file.parent.mkdir(parents=True)
        revision_file.write_text("export const REQUIRED_API_REVISION = 11\n", encoding="utf-8")
        backend_version = self.archive / "src" / "yuki" / "client_api" / "version.py"
        backend_version.parent.mkdir(parents=True)
        backend_version.write_text("CLIENT_API_REVISION = 14\n", encoding="utf-8")
        (self.archive / ".yuki-commit").write_text(BACKEND_COMMIT + "\n", encoding="ascii")
        backend_bytes = self.write_json(self.lock / OPENAPI, document(14))
        golden = {}
        for filename in ("payload-hash.json", "text-normalize.json"):
            name = "golden/" + filename
            golden[name] = self.digest(self.write_json(self.lock / name, {}))
        self.set_desktop_schema(document(12))
        self.write_json(self.lock / "source.json", {
            "backend": {
                "file": OPENAPI,
                "commit": BACKEND_COMMIT,
                "sha256": self.digest(backend_bytes),
                "apiRevision": 14,
                "pathCount": 1,
                "serverVersion": "4.0.2",
            },
            "desktop": {},
            "golden": golden,
        })

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.desktop), *args], text=True).strip()

    def write_json(self, path, document):
        path.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(document, indent=2).encode() + b"\n"
        path.write_bytes(data)
        return data

    def digest(self, data):
        return hashlib.sha256(data).hexdigest()

    def set_desktop_schema(self, schema, extra_bytes=b""):
        locks = self.desktop / "contracts"
        path = locks / OPENAPI
        data = self.write_json(path, schema) + extra_bytes
        path.write_bytes(data)
        self.write_json(locks / "source.json", {
            "file": OPENAPI,
            "commit": "2" * 40,
            "sha256": self.digest(data),
            "apiRevision": schema["info"]["x-yuki-api-revision"],
        })

    def check(self, success, commit_inputs=True):
        if commit_inputs:
            self.git("add", "--", "contracts", "src/shared/api.ts")
            self.git("commit", "--allow-empty", "-qm", "Fixture")
        env = {
            name: value for name, value in os.environ.items()
            if name != "YUKI_BACKEND_TEST_PYTHON"
        }
        env["YUKI_BACKEND_TEST_ROOT"] = str(self.archive)
        result = subprocess.run(
            [sys.executable, str(self.script), "check", "--desktop-root", str(self.desktop)],
            env=env, check=False, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0 if success else 1, result.stderr)
        report = self.mobile / "acceptance" / "out" / "contract-check.json"
        summary = json.loads(report.read_bytes())
        self.assertEqual(summary["status"], "passed" if success else "failed")
        return summary

    def test_older_desktop_is_compatible_offline(self):
        self.check(True)

    def test_same_revision_requires_identical_bytes(self):
        self.set_desktop_schema(document(14))
        self.check(True)
        self.set_desktop_schema(document(14), b" ")
        self.check(False)

    def test_newer_desktop_lock_fails(self):
        self.set_desktop_schema(document(15))
        self.check(False)

    def test_changed_desktop_bytes_fail_declared_digest(self):
        path = self.desktop / "contracts" / OPENAPI
        path.write_bytes(path.read_bytes() + b" ")
        findings = self.check(False)["findings"]
        self.assertIn("SHA-256 differs", findings[0]["issue"])

    def test_desktop_runtime_file_must_match_reported_commit(self):
        self.check(True)
        revision_file = self.desktop / "src" / "shared" / "api.ts"
        revision_file.write_text("export const REQUIRED_API_REVISION = 12", encoding="utf-8")
        findings = self.check(False, commit_inputs=False)["findings"]
        self.assertEqual(findings[0]["location"], "Desktop src/shared/api.ts")
        self.assertIn("working file differs", findings[0]["issue"])

    def test_desktop_runtime_gate_cannot_exceed_backend(self):
        revision_file = self.desktop / "src" / "shared" / "api.ts"
        revision_file.write_text("export const REQUIRED_API_REVISION = 15", encoding="utf-8")
        self.check(False)

    def test_wrong_pinned_archive_fails(self):
        (self.archive / ".yuki-commit").write_text("3" * 40 + "\n", encoding="ascii")
        self.check(False)

    def test_stale_golden_artifact_fails_offline(self):
        (self.lock / "golden" / "payload-hash.json").write_bytes(b"[]")
        self.check(False)


if __name__ == "__main__":
    unittest.main()
