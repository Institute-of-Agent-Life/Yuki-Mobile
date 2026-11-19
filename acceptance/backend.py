from contextlib import contextmanager
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory
from time import monotonic, sleep
from urllib.parse import urlsplit


REFERENCE_COMMIT = "6cf15321575391f217d1c9129e6f56e0010b09b9"
BASE_PATH = "/role/test"
LOOPBACK = "127.0.0.1"
STARTUP_TIMEOUT = 30
_SEGMENT = re.compile(r"[A-Za-z0-9._~:-]+")


class BackendUnavailable(RuntimeError):
    """The pinned fixture could not be started."""


def canonical_path(path):
    return path.startswith("/") and all(
        _SEGMENT.fullmatch(part) and part not in {".", ".."}
        for part in path[1:].split("/")
    )


def loopback_endpoint(base_url):
    """Return the port and prefix after checking the exact local HTTP origin."""
    parsed = urlsplit(base_url)
    port = parsed.port
    if (
        not port
        or (parsed.path and not canonical_path(parsed.path))
        or base_url != f"http://{LOOPBACK}:{port}{parsed.path}"
    ):
        raise ValueError("Expected a canonical loopback HTTP fixture URL.")
    return port, parsed.path


@dataclass(frozen=True)
class BackendSession:
    base_url: str
    workspace: Path
    python: Path
    source: Path
    commit: str
    owner_pairing_code: str = field(repr=False)
    nonowner_pairing_code: str = field(repr=False)


def fixture_environment(workspace, source):
    scratch = str(workspace)
    env = {
        "XDG_CONFIG_HOME": scratch,
        "XDG_DATA_HOME": scratch,
        "XDG_CACHE_HOME": scratch,
        "TMPDIR": scratch,
        "TEMP": scratch,
        "TMP": scratch,
        "PATH": os.defpath,
        "PYTHONPATH": os.pathsep.join(map(str, (
            source / "src", source, source / "packages" / "yuki-agent-connector" / "src",
        ))),
        "PYTHONNOUSERSITE": "1",
        "PYTHONIOENCODING": "utf-8",
    }
    if os.name == "nt" and "SYSTEMROOT" in os.environ:
        env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
    return env


def _fixture_command(server):
    if server == "reference":
        return ["-m", "tests.support.desktop_reference_server"]
    if server == "mobile-fixture":
        entry = Path(__file__).resolve().parent / "server" / "yuki_fixture_server.py"
        if entry.is_file():
            return [str(entry)]
        raise BackendUnavailable("The QA-002 mobile fixture is unavailable.")
    raise BackendUnavailable("Unsupported backend fixture.")


def _pinned_source():
    source = Path(os.environ.get("YUKI_BACKEND_TEST_ROOT", ""))
    if not source.is_absolute() or (source / ".git").exists():
        raise BackendUnavailable("Set YUKI_BACKEND_TEST_ROOT to the absolute path of the pinned Yuki archive.")
    try:
        commit = (source / ".yuki-commit").read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        raise BackendUnavailable("The Yuki archive commit marker is unreadable.") from None
    if commit != REFERENCE_COMMIT:
        raise BackendUnavailable("The Yuki archive commit marker does not match the QA-001 pin.")
    return source, commit


def _pinned_python():
    python = Path(os.environ.get("YUKI_BACKEND_TEST_PYTHON", ""))
    if not python.is_absolute() or not python.is_file() or not os.access(python, os.X_OK):
        raise BackendUnavailable("Set YUKI_BACKEND_TEST_PYTHON to the absolute path of the Yuki virtualenv Python.")
    return python


def _wait_for_state(process, state_file):
    deadline = monotonic() + STARTUP_TIMEOUT
    while monotonic() < deadline:
        if process.poll() is not None:
            raise BackendUnavailable("The Yuki fixture exited before it was ready.")
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            sleep(0.05)
            continue
        except (ValueError, OSError):
            raise BackendUnavailable("The Yuki fixture state is unreadable.") from None
        try:
            base_url = state["base_url"]
            _, path = loopback_endpoint(base_url)
            owner = state["owner_pairing_code"]
            nonowner = state["nonowner_pairing_code"]
            if path == BASE_PATH and all(isinstance(code, str) and code for code in (owner, nonowner)):
                return base_url, owner, nonowner
        except (AttributeError, KeyError, TypeError, ValueError):
            pass
        raise BackendUnavailable("The Yuki fixture state has an unexpected endpoint or credentials.")
    raise BackendUnavailable("Timed out while starting the Yuki fixture.")


def _stop_process(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


@contextmanager
def start_backend(server):
    command = _fixture_command(server)
    source, commit = _pinned_source()
    python = _pinned_python()
    with TemporaryDirectory(prefix="yuki-acceptance-") as directory:
        workspace = Path(directory)
        state_file = workspace / "backend-state.json"
        try:
            process = subprocess.Popen(
                [
                    str(python), "-B", *command,
                    "--root", str(workspace / "data"),
                    "--state-file", str(state_file),
                    "--port", "0",
                    "--base-path", BASE_PATH,
                ],
                cwd=source,
                env=fixture_environment(workspace, source),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            raise BackendUnavailable("Could not launch the pinned Yuki fixture.") from None
        try:
            base_url, owner, nonowner = _wait_for_state(process, state_file)
            yield BackendSession(base_url, workspace, python, source, commit, owner, nonowner)
        finally:
            _stop_process(process)
