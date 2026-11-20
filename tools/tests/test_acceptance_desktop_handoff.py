from base64 import b64decode
from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlencode
from uuid import uuid4

from acceptance.cases.a01_handoff import FixtureAPI, HandoffContext, HandoffFailure
from acceptance.cases.desktop_handoff import pair_code, run_desktop_handoff


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "acceptance" / "out"
BASE = "http://127.0.0.1:43125/role/test"
LINK = "yuki://pair?server=http%3A%2F%2F127.0.0.1%3A43125%2Frole%2Ftest&code=local-code"


class HandoffTests(unittest.TestCase):
    def setUp(self):
        OUT.mkdir(parents=True, exist_ok=True)
        directory = tempfile.TemporaryDirectory(dir=OUT, prefix="qa-003-runner-test-")
        self.addCleanup(directory.cleanup)
        self.output = Path(directory.name)

    def test_pair_link_must_match_the_exact_origin_and_prefix(self):
        self.assertEqual(pair_code(LINK, BASE), "local-code")
        invalid = (
            LINK.replace("43125", "43126"),
            LINK.replace("role%2Ftest", "role%2Fother"),
            LINK.replace("yuki://pair?", "yuki://user@pair?"),
            LINK.replace("yuki://pair?", "yuki://pair/?"),
            LINK + "&binding_id=guessed",
            LINK + "&code=second-code",
            LINK + "#fragment",
            LINK.replace("local-code", "%0Asecret"),
        )
        for link in invalid:
            with self.subTest(link=link), self.assertRaises(HandoffFailure):
                pair_code(link, BASE)

    def run_cli(self, case, client, *, server="mobile-fixture"):
        env = dict(os.environ)
        env.pop("YUKI_BACKEND_TEST_ROOT", None)
        env.pop("YUKI_BACKEND_TEST_PYTHON", None)
        return subprocess.run(
            [sys.executable, "-B", "acceptance/run.py", "--case", case, "--client", client,
             "--server", server, "--output", str(self.output)],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=15, input="",
        )

    def test_missing_fixture_writes_blocked_report(self):
        result = self.run_cli("desktop-handoff", "reference")
        self.assertNotEqual(result.returncode, 0)
        report = json.loads((self.output / "desktop-handoff.json").read_text())
        manifest = json.loads((self.output / "run-manifest.json").read_text())
        self.assertEqual(report["status"], manifest["status"])
        self.assertEqual(report["status"], "blocked")

    def test_reference_run_leaves_a01_not_run(self):
        result = self.run_cli("A01", "reference")
        self.assertNotEqual(result.returncode, 0)
        manifest = json.loads((self.output / "run-manifest.json").read_text())
        self.assertEqual(manifest["status"], "not_run")
        self.assertFalse((self.output / "desktop-handoff.json").exists())

    def test_eof_is_blocked_with_only_sanitized_evidence(self):
        with patch("acceptance.cases.desktop_handoff.start_backend") as backend:
            backend.return_value.__enter__.return_value.commit = "a" * 40
            backend.return_value.__enter__.return_value.base_url = BASE
            with patch("acceptance.cases.desktop_handoff.before", side_effect=EOFError):
                with redirect_stdout(StringIO()):
                    report, _ = run_desktop_handoff(self.output)
        self.assertEqual(report["status"], "blocked")
        self.assertNotIn(BASE, json.dumps(report))


    def test_same_entry_operator_code_fails_issuer_check_before_input(self):
        context = HandoffContext(
            Mock(), Mock(), BASE, Mock(), Mock(), "operator-code",
            "binding", "conversation", "desktop", "work", {"armed_after_event_id": 2},
            {"work_id": "work", "conversation_id": "conversation", "trigger_event_id": 3}, {},
        )
        facts = {
            "work_id": "work", "conversation_id": "conversation", "trigger_event_id": 3,
            "principal_id": "desktop", "state": "waiting_user",
            "pair": {
                "code_id": "alternate", "issuer_source": "control",
                "issuer_principal_id": "control", "source_binding_id": None,
            },
        }
        me = {
            "binding_id": "binding", "conversation_id": "conversation",
            "devices": [{"device_id": "phone"}],
        }
        with patch.object(context, "facts", return_value=facts) as report:
            with self.assertRaisesRegex(HandoffFailure, "redeemed pairing code"):
                context.verify_phone_pair("phone", me)
        report.assert_called_once_with(paired_device_id="phone")
        self.assertIsNone(context.phone_device_id)
        self.assertNotIn("pair_receipt", context.evidence)

    @unittest.skipUnless(
        os.name == "posix" and os.environ.get("YUKI_BACKEND_TEST_ROOT") and os.environ.get("YUKI_BACKEND_TEST_PYTHON"),
        "requires the pinned QA-001 backend archive and virtualenv",
    )
    def test_guided_phases_against_pinned_fixture(self):
        transcript = StringIO()
        desktop = {}
        pids = []
        markers = []
        private = []

        def main_process():
            process = subprocess.Popen(
                [sys.executable, "-B", "-c", "import time; time.sleep(120)"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            pids.append(process)
            return str(process.pid)

        def output_value(prefix):
            return next(line[len(prefix):] for line in transcript.getvalue().splitlines() if line.startswith(prefix))

        def interactive(stage, message, **kwargs):
            markers.append(stage)
            if stage == "before.pair":
                base = output_value("Client URL: ")
                state_path = Path(output_value("Pairing state: "))
                with state_path.open(encoding="utf-8") as stream:
                    credentials = json.load(stream)
                code = credentials["owner_pairing_code"]
                private.extend([base, str(state_path), code])
                receipt = FixtureAPI(base).client("pair", method="POST", body={
                    "code": code, "device_label": "desktop-driver",
                })
                private.extend(receipt[k] for k in ("token", "device_id"))
                desktop["api"] = FixtureAPI(base, receipt["token"])
                return main_process()
            if stage == "before.send":
                receipt = desktop["api"].client("messages", method="POST", body={
                    "request_id": str(uuid4()), "text": output_value("Message: "),
                })
                desktop["trigger"] = receipt["event_id"]
                return ""
            if stage == "before.link":
                receipt = desktop["api"].client("link-codes", method="POST")
                private.append(receipt["code"])
                self.assertIs(kwargs["secret"], True)
                return "yuki://pair?" + urlencode({
                    "server": desktop["api"].base_url, "code": receipt["code"],
                })
            if stage == "before.close":
                pids[-1].terminate()
                pids[-1].wait(timeout=5)
                return ""
            if stage == "after.reopen":
                return main_process()
            if stage == "after.save":
                completed, = desktop["api"].client("works", params={"group": "ended"})["works"]
                self.assertEqual(completed["trigger_event_id"], desktop["trigger"])
                private.append(completed["work_id"])
                artifact, = completed["results"][-1]["artifacts"]
                private.append(artifact["artifact_id"])
                receipt = desktop["api"].request(
                    f"/client/v1/artifacts/{artifact['artifact_id']}",
                    params={"version": artifact["version"]},
                )
                self.assertEqual(receipt[0], 200)
                path = self.output / "test-saved.md"
                path.write_bytes(receipt[2])
                return str(path)
            if stage == "after.screenshot":
                path = self.output / "test-screen.png"
                path.write_bytes(b64decode(
                    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8"
                    "/x8AAwMCAO+jDp4AAAAASUVORK5CYII="
                ))
                return str(path)
            if stage == "after.confirm":
                return "yes"
            self.fail(f"Unexpected prompt {stage}")

        try:
            with redirect_stdout(transcript):
                report, commit = run_desktop_handoff(self.output.resolve(), ask=interactive)
        finally:
            for process in pids:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=5)
        self.assertEqual(report["status"], "passed", report)
        self.assertEqual(len(commit), 40)
        self.assertEqual(markers, [
            "before.pair", "before.send", "before.link", "before.close",
            "after.reopen", "after.save", "after.screenshot", "after.confirm",
        ])
        self.assertTrue((self.output / report["screenshot_path"]).is_file())
        self.assertEqual(report["input_replay"]["phone_posts_after_recovery"], 1)
        self.assertEqual(report["original_result"]["new_result_count"], 1)
        self.assertEqual(report["download_comparison"]["desktop_sha256"], report["download_comparison"]["phone_sha256"])
        serialized = json.dumps(report)
        for value in private:
            self.assertNotIn(value, serialized)


if __name__ == "__main__":
    unittest.main()
