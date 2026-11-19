import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
REPORT = Path("acceptance/out/repo-check.json")
TOKEN = "yuki_device_" + "F" * 43
PAIR_CODE = "Q2_b" * 8
PAIR_LINK = "yuki://pair?server=https%3A%2F%2Fexample.invalid&code=" + PAIR_CODE
PRIVATE_KEY = "-----BEGIN OPENSSH " + "PRIVATE KEY-----"
BASIC_CREDENTIAL = "c3ludGhldGljOnRlc3Q="
CLEAN_DOC = (
    "Device tokens start with `yuki_device_`.\n"
    "Pairing: `yuki://pair?server=…&code=…` or `pairing_code: <code>`.\n"
    "`Authorization:` and `Authorization: Bearer <token>` are placeholders.\n"
    '{"pairing_code":"<code>","Authorization":"Bearer <token>"}\n'
    "Example: `Authorization: Bearer ...`.\n"
    "A 32-character text value: " + PAIR_CODE + "\n"
    '{"code": "<code>"}\n'
)
ENV = {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}


class RepoToolsTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.repo = Path(directory.name)
        self.git("init", "-q")
        self.git("config", "core.quotepath", "false")
        self.write(".gitignore", (ROOT / ".gitignore").read_bytes())
        self.git("add", ".gitignore")

    def write(self, filename, body):
        path = self.repo / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        return path

    def run_command(self, *args):
        return subprocess.run(
            args, cwd=self.repo, env=ENV, capture_output=True, text=True, check=False
        )

    def git(self, *args):
        result = self.run_command("git", *args)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def tool(self, script, *args):
        return self.run_command(sys.executable, str(ROOT / "tools" / script), *args)

    def scan(self, exit_code):
        result = self.tool("check_repo.py")
        self.assertEqual(result.returncode, exit_code, result.stderr)
        return result, json.loads((self.repo / REPORT).read_bytes())

    def test_forced_apple_metadata_and_signing_files(self):
        filenames = (
            "外接卷/._照片.png",
            "android/.DS_Store",
            "android/production.keystore",
            "ios/development.mobileprovision",
            "qa/test-ca.key",
            "qa/pairing-handoff.json",
            "acceptance/out/raw.json",
        )
        for filename in filenames:
            with self.subTest(filename=filename):
                self.write(filename, b"\x00\x05\x16\x07\x00\x02\x00\x00\xff\xfe")
                self.assertEqual(self.git("check-ignore", filename).strip(), filename)
        self.git("add", "-f", "--", *filenames)

        _, report = self.scan(1)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(
            {item["path"]: item["rule"] for item in report["findings"]},
            dict(
                zip(
                    filenames,
                    ("apple_metadata", "apple_metadata", "private_file", "private_file",
                     "private_file", "private_file", "local_evidence"),
                )
            ),
        )

    def test_staged_credentials_are_reported_by_rule_without_values(self):
        evidence = {
            "token.bin": ("device_token", b"\xff\x00" + TOKEN.encode()),
            "short-token.txt": ("device_token", TOKEN[:20].encode()),
            "pair-link.txt": ("pairing_code", PAIR_LINK.encode()),
            "pair-code.json": (
                "pairing_code", json.dumps({"pairing_code": PAIR_CODE}).encode()
            ),
            "pair-request.json": (
                "pairing_code",
                json.dumps({"code": PAIR_CODE, "device_label": "Test phone"}, indent=2).encode(),
            ),
            "pair-request-reordered.json": (
                "pairing_code",
                json.dumps({"device_label": 'Test }" phone', "code": PAIR_CODE}).encode(),
            ),
            "pair-request-with-metadata.json": (
                "pairing_code",
                json.dumps({"code": PAIR_CODE, "device_label": "Test phone", "fixture": True}).encode(),
            ),
            "link-code-reply.json": (
                "pairing_code",
                json.dumps({"data": {"code_id": "lc_1", "code": PAIR_CODE}}).encode(),
            ),
            "cli-pair-equals.txt": ("pairing_code", ("pair --code=" + PAIR_CODE).encode()),
            "cli-pair-quoted.txt": ("pairing_code", ('pair --code "' + PAIR_CODE + '"').encode()),
            **{
                f"{key}.json": ("pairing_code", json.dumps({key: PAIR_CODE}).encode())
                for key in ("owner_code", "nonowner_code", "chat_code", "connector_code")
            },
            "pair-chat.txt": ("pairing_code", ("配對碼： " + PAIR_CODE).encode()),
            "key.txt": ("private_key", PRIVATE_KEY.encode()),
            "request.txt": (
                "authorization_header", b"Authorization: Basic " + BASIC_CREDENTIAL.encode()
            ),
            "request.json": (
                "authorization_header",
                json.dumps({"Authorization": "Bearer " + "synthetic-opaque-credential"}).encode(),
            ),
        }
        for filename, (_, content) in evidence.items():
            self.write(filename, content)
        self.git("add", "--", *evidence)

        self.write("token.bin", b"working tree was sanitized")
        result, report = self.scan(1)
        self.assertEqual(
            {item["path"]: item["rule"] for item in report["findings"]},
            {filename: rule for filename, (rule, _) in evidence.items()},
        )
        emitted = result.stdout + result.stderr + (self.repo / REPORT).read_text()
        for value in (TOKEN, PAIR_CODE, PAIR_LINK, PRIVATE_KEY, BASIC_CREDENTIAL,
                      "synthetic-opaque-credential"):
            self.assertNotIn(value, emitted)

        for filename in evidence:
            self.write(filename, CLEAN_DOC.encode())
        self.git("add", "--", *evidence)
        _, report = self.scan(0)
        self.assertEqual(report["status"], "passed")

    def test_safe_placeholders_binary_and_ignored_worktree(self):
        self.write("docs/example.md", CLEAN_DOC.encode())
        self.write("safe.bin", b"\xff\x00\x80 ordinary binary")
        self.write("acceptance/out/raw.json", TOKEN.encode())
        self.write("nested/.env.local", TOKEN.encode())
        self.git("add", ".")
        self.assertNotIn("raw.json", self.git("ls-files"))
        self.assertNotIn(".env.local", self.git("ls-files"))
        self.git("check-ignore", str(REPORT))
        _, report = self.scan(0)
        self.assertEqual(report["findings"], [])

    def test_missing_index_blob_fails_the_scan(self):
        self.git("update-index", "--add", "--cacheinfo", "100644," + "a" * 40 + ",missing.bin")
        _, report = self.scan(2)
        self.assertEqual(report["status"], "error")
        self.assertEqual(report["findings"], [{"rule": "git_blob_error", "path": "missing.bin"}])

    def test_safe_projection_and_scan_after_redaction(self):
        original = {
            "case_id": "A10",
            "status": "passed",
            "started_at": "2026-11-18T00:00:00Z",
            "client_platform": "android",
            "device_model": "Pixel 8 (test_phone)",
            "os_build": "AP3A.241005.015_A2",
            "backend_commit": "1" * 40,
            "desktop_commit": "2" * 40,
            "mobile_commit": "3" * 40,
            "build_type": "release",
            "build_sha256": "4" * 64,
            "file_sha256": "5" * 64,
            "entry_kind": "chat",
            "id_aliases": {"work_id": "work-2", "device_id": "device-1", "binding_id": "binding-1"},
            "test_entry_alias": "entry-1",
            "network": "wifi",
            "tls_trusted": True,
            "script_model_version": "scripted_v1",
        }
        private = {
            **original,
            "token": TOKEN,
            "pairing_code": PAIR_CODE,
            "pairing_uri": PAIR_LINK,
            "server_url": "https://example.invalid/private",
            "device_id": "raw-device-id",
            "binding_id": "raw-binding-id",
            "device_serial": "raw-serial",
            "observed": "Private chat text https://example.invalid/embedded",
            "steps": ["Screenshot saved at raw/phone.png"],
            "evidence_paths": ["raw/phone.png"],
            "screenshot": {"url": "https://example.invalid/raw.png", "image_data": "private"},
            "model_notes": "raw/phone.png",
            "id_aliases": {"work_id": "work-2", "device_id": "raw-device-id",
                           "request_id": "https://example.invalid/embedded"},
        }
        del private["test_entry_alias"]
        serialized = json.dumps(private).encode()
        raw = self.write("acceptance/out/raw.json", serialized)
        self.git("add", "-f", str(raw))
        self.scan(1)
        self.git("rm", "--cached", str(raw))

        public = self.repo / "public.json"
        result = self.tool("redact_evidence.py", str(raw), str(public))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(public.read_bytes()), original)
        self.assertEqual(raw.read_bytes(), serialized)
        self.git("add", str(public))
        self.scan(0)

    def test_invalid_public_metadata_does_not_create_an_output(self):
        for key, value in (
            ("artifact_sha256", "too-short"),
            ("os_build", "https://example.invalid/private"),
            ("device_model", TOKEN),
        ):
            with self.subTest(field=key):
                candidate = self.repo / f"public-{key}.json"
                raw = self.write("raw.json", json.dumps({
                    "case_id": "A01", "status": "passed", key: value,
                }).encode())
                result = self.tool("redact_evidence.py", str(raw), str(candidate))
                self.assertEqual(result.returncode, 1)
                self.assertIn(key, result.stderr)
                self.assertNotIn(value, result.stderr)
                self.assertFalse(candidate.exists())

    def test_invalid_manifest_and_existing_destination(self):
        candidate = self.repo / "public.json"
        for original in (b"{broken", b'{"case_id":"A01","status":"maybe"}', b"[]"):
            with self.subTest():
                raw = self.write("raw.json", original)
                result = self.tool("redact_evidence.py", str(raw), str(candidate))
                self.assertEqual(result.returncode, 1)
                self.assertFalse(candidate.exists())
                self.assertEqual(raw.read_bytes(), original)

        original = b'{"case_id":"A01","status":"not_run","device_id":"private"}'
        raw = self.write("raw.json", original)
        result = self.tool("redact_evidence.py", str(raw), str(raw))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(raw.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
