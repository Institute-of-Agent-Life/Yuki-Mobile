from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import ssl
import subprocess
import sys
import tempfile
from threading import Thread
import unittest


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "acceptance" / "out"


class RunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        scratch = tempfile.TemporaryDirectory(prefix="qa-001-tls-")
        cls.addClassCleanup(scratch.cleanup)
        cls.cert = Path(scratch.name) / "fixture.pem"
        cls.key = Path(scratch.name) / "fixture.key"
        subprocess.run([
            "openssl", "req", "-x509", "-nodes", "-newkey", "rsa:2048", "-days", "1",
            "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
            "-keyout", str(cls.key), "-out", str(cls.cert),
        ], check=True, capture_output=True, timeout=15)

    def setUp(self):
        OUT.mkdir(parents=True, exist_ok=True)
        directory = tempfile.TemporaryDirectory(dir=OUT, prefix="qa-001-runner-test-")
        self.addCleanup(directory.cleanup)
        self.output = Path(directory.name)

    def run_cli(self, *arguments, trust_fixture=False):
        env = dict(os.environ)
        for name in ("YUKI_BACKEND_TEST_ROOT", "YUKI_BACKEND_TEST_PYTHON",
                     "YUKI_DEVICE_TEST_URL", "SSL_CERT_FILE", "SSL_CERT_DIR"):
            env.pop(name, None)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        arguments = [*arguments, "--output", str(self.output)]
        if trust_fixture:
            trust_runner = """import ssl
import sys
from unittest.mock import patch
from acceptance import run

fixture = ssl.create_default_context(cafile=sys.argv[1])
with patch.object(run.ssl, "create_default_context", return_value=fixture):
    sys.exit(run.main(sys.argv[2:]))
"""
            command = ["-c", trust_runner, str(self.cert), *arguments]
        else:
            command = ["acceptance/run.py", *arguments]
        return subprocess.run([
            sys.executable, *command,
        ], cwd=ROOT, env=env, capture_output=True, text=True, timeout=15)

    def read_output(self, name, result):
        path = self.output / name
        self.assertTrue(path.is_file(), result.stderr)
        return json.loads(path.read_text(encoding="utf-8"))

    @contextmanager
    def endpoint(self, *, exposed_path=None, redirect=False):
        paths = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                paths.append(self.path)
                meta = self.path == "/role/test/client/v1/meta"
                status = 200 if meta or self.path == exposed_path else 404
                if meta and redirect:
                    status = 302
                body = b'{"data":{"api_revision":14}}' if meta else b"{}"
                self.send_response(status)
                if status == 302:
                    self.send_header("Location", "/elsewhere")
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_):
                pass

        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(self.cert, self.key)
        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            server.socket = context.wrap_socket(server.socket, server_side=True)
            worker = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
            worker.start()
            try:
                yield f"https://127.0.0.1:{server.server_port}/role/test", paths
            finally:
                server.shutdown()
                worker.join(timeout=3)

    def test_untrusted_https_fails_without_contacting_http_routes(self):
        with self.endpoint() as (url, paths):
            result = self.run_cli("env-check", "--public-url", url)
        manifest = self.read_output("run-manifest.json", result)
        report = self.read_output("device-endpoint.json", result)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(manifest["status"], "failed")
        self.assertIs(report["tls_trusted"], False)
        self.assertEqual(paths, [])

    def test_https_probe_preserves_prefix_and_checks_both_control_paths(self):
        with self.endpoint() as (url, paths):
            result = self.run_cli("env-check", "--public-url", url, trust_fixture=True)
        manifest = self.read_output("run-manifest.json", result)
        report = self.read_output("device-endpoint.json", result)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(manifest["status"], "passed")
        self.assertNotIn(url, result.stdout + result.stderr)
        self.assertIs(report["tls_trusted"], True)
        self.assertIs(report["test_route_blocked"], True)
        self.assertEqual(report["api_revision"], 14)
        self.assertEqual(paths, [
            "/role/test/client/v1/meta",
            "/role/test/__test/events", "/__test/events",
            "/role/test/__test/asr-started", "/__test/asr-started",
            "/role/test/healthz", "/role/test/openapi.json",
        ])

    def test_https_probe_rejects_exposed_prefixed_control_route(self):
        with self.endpoint(exposed_path="/role/test/__test/asr-started") as (url, _):
            result = self.run_cli("env-check", "--public-url", url, trust_fixture=True)
        report = self.read_output("device-endpoint.json", result)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(report["status"], "failed")
        self.assertIs(report["tls_trusted"], True)
        self.assertIs(report["test_route_blocked"], False)

    def test_https_probe_rejects_nonpublic_health_route(self):
        with self.endpoint(exposed_path="/role/test/healthz") as (url, _):
            result = self.run_cli("env-check", "--public-url", url, trust_fixture=True)
        report = self.read_output("device-endpoint.json", result)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["nonpublic_route_statuses"]["/role/test/healthz"], 200)

    def test_https_probe_does_not_follow_redirect(self):
        with self.endpoint(redirect=True) as (url, paths):
            result = self.run_cli("env-check", "--public-url", url, trust_fixture=True)
        report = self.read_output("device-endpoint.json", result)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(paths, ["/role/test/client/v1/meta"])

    def test_missing_host_url_writes_blocked_evidence(self):
        result = self.run_cli("env-check")
        manifest = self.read_output("run-manifest.json", result)
        report = self.read_output("device-endpoint.json", result)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(manifest["status"], "blocked")
        self.assertEqual(report["status"], "blocked")
        self.assertIsNone(report["tls_trusted"])

    def test_mobile_probe_is_blocked_until_platform_runner_exists(self):
        result = self.run_cli("env-check", "--public-url", "https://example.invalid/role/test",
                              "--client", "android", "--build", "release", "--networks", "wifi,cellular")
        manifest = self.read_output("run-manifest.json", result)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(manifest["client_platform"], "android")
        self.assertEqual(manifest["status"], "blocked")

    def test_retired_ios_client_and_a17_are_rejected(self):
        retired = (
            ("env-check", "--client", "ios"),
            ("--case", "A01", "--client", "ios", "--server", "reference"),
            ("--case", "A17", "--client", "android", "--server", "reference"),
        )
        for command in retired:
            with self.subTest(command=command):
                result = self.run_cli(*command)
                self.assertEqual(result.returncode, 2, result.stdout)
                self.assertFalse((self.output / "run-manifest.json").exists())

    def test_unimplemented_all_core_writes_twelve_nonpassing_manifests(self):
        result = self.run_cli("--case", "all-core", "--client", "android", "--server", "reference")
        self.assertNotEqual(result.returncode, 0)
        for index in range(1, 13):
            case_id = f"A{index:02d}"
            manifest = self.read_output(f"{case_id}/run-manifest.json", result)
            self.assertEqual(manifest["case_id"], case_id)
            self.assertEqual(manifest["status"], "not_run")
        summary = self.read_output("run-summary.json", result)
        self.assertEqual(summary["status"], "not_run")
        self.assertEqual(len(summary["cases"]), 12)
        path = self.output / "A01"
        public = subprocess.run([
            sys.executable, "tools/redact_evidence.py", str(path / "run-manifest.json"),
            str(path / "public-manifest.json"),
        ], cwd=ROOT, capture_output=True, text=True, timeout=15)
        self.assertEqual(public.returncode, 0, public.stderr)
        self.assertEqual(self.read_output("A01/public-manifest.json", public)["case_id"], "A01")
        self.assertFalse((self.output / "run-manifest.json").exists())

    def test_smoke_without_backend_writes_blocked_manifest(self):
        result = self.run_cli("--case", "smoke", "--client", "reference", "--server", "reference")
        manifest = self.read_output("run-manifest.json", result)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(manifest["status"], "blocked")
        self.assertEqual(self.read_output("harness-smoke.json", result)["status"], "blocked")

    def test_outputs_cannot_leave_ignored_tree(self):
        with tempfile.TemporaryDirectory(prefix="qa-001-outside-") as scratch:
            self.output = Path(scratch) / "result"
            result = self.run_cli("env-check")
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(self.output.exists())

    def test_case_subdirectory_symlinks_cannot_redirect_evidence(self):
        with tempfile.TemporaryDirectory(prefix="qa-001-case-link-") as scratch:
            (self.output / "A01").symlink_to(scratch, target_is_directory=True)
            result = self.run_cli("--case", "A01,A02", "--client", "android", "--server", "reference")
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((Path(scratch) / "run-manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
