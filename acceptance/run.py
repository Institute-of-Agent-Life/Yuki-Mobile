#!/usr/bin/env python3
"""Run Yuki-Mobile acceptance cases and check the development HTTPS endpoint."""

import argparse
from datetime import datetime, timezone
import http.client
import json
import os
from pathlib import Path
import platform
import re
import shlex
import signal
import ssl
import subprocess
import sys
import tempfile
from threading import Event
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = ROOT / "acceptance" / "out"
CORE_CASES = tuple(f"A{number:02d}" for number in range(1, 13))
CASES = set(CORE_CASES) | {"A13", "A14", "A15", "A16", "A18", "A19", "A20", "smoke", "desktop-handoff"}
SERVERS = ("reference", "mobile-fixture")
CONTROL_PATHS = ("/__test/events", "/__test/asr-started")


class EvidenceWriteError(OSError):
    pass


def output_directory(value):
    try:
        output = Path(value).resolve()
        output.relative_to(OUTPUT_ROOT)
    except (OSError, RuntimeError, ValueError):
        raise argparse.ArgumentTypeError("output must stay under acceptance/out") from None
    return output


def write_json(path, data):
    temporary = None
    try:
        if path.parent.resolve() != path.parent:
            raise OSError("output directory resolves through a symbolic link")
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, indent=2, ensure_ascii=True, allow_nan=False)
            stream.write("\n")
        temporary.replace(path)
    except (OSError, RuntimeError):
        raise EvidenceWriteError from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def checkout_metadata():
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, stderr=subprocess.DEVNULL,
            text=True, timeout=3,
        ).strip()
        dirty = bool(subprocess.check_output(
            ["git", "--no-optional-locks", "status", "--porcelain"],
            cwd=ROOT, stderr=subprocess.DEVNULL, timeout=3,
        ))
    except (OSError, subprocess.SubprocessError):
        return None, None
    return commit if re.fullmatch("[0-9a-f]{40}", commit) else None, dirty


def manifest(case_id, args, command, *, status, observations, steps=(), expected=(),
             evidence=(), limits=(), commit=None):
    host_evidence = args.client in (None, "reference")
    report = {
        "case_id": case_id,
        "started_at": args.started_at,
        "client_platform": args.client or "desktop",
        "device_model": platform.machine() if host_evidence else None,
        "os_build": platform.release() if host_evidence else None,
        "backend_commit": commit,
        "desktop_commit": None,
        "mobile_commit": args.mobile_commit,
        "mobile_source_modified": args.mobile_source_modified,
        "build_type": args.build,
        "build_sha256": None,
        "entry_kind": "app" if case_id in ("smoke", "env-check") else None,
        "test_entry_alias": "entry-1" if status == "passed" and case_id in ("smoke", "env-check") else None,
        "id_aliases": observations.get("id_aliases", {}),
        "fault_point": observations.get("fault_point"),
        "steps": list(steps),
        "expected": list(expected),
        "observed": observations,
        "evidence_paths": list(evidence),
        "status": status,
        "limitations": list(limits),
        "rerun_command": command,
    }
    for field in ("tls_trusted", "test_route_blocked"):
        if isinstance(observations.get(field), bool):
            report[field] = observations[field]
    return report


def https_get(host, port, path, context, *, read_body=False):
    connection = http.client.HTTPSConnection(host, port, timeout=5, context=context)
    try:
        connection.request("GET", path, headers={"Accept": "application/json"})
        with connection.getresponse() as response:
            body = response.read(65537) if read_body and response.status == 200 else b""
            return response.status, body
    finally:
        connection.close()


def check_host(url):
    result = {
        "probe": "development_host", "network": "development_host", "status": "blocked",
        "tls_trusted": None, "meta_status": None, "api_revision": None,
        "prefixed_control_status": None, "root_control_status": None, "test_route_blocked": None,
        "nonpublic_route_statuses": {},
    }
    if not url:
        result["reason"] = "YUKI_DEVICE_TEST_URL is not configured"
        return result
    try:
        parsed = urlsplit(url)
        host, port = parsed.hostname, parsed.port
        prefix = parsed.path.rstrip("/")
        valid_prefix = not prefix or (
            re.fullmatch(r"(?:/[A-Za-z0-9._~-]+)+", prefix)
            and not {".", ".."}.intersection(prefix.split("/"))
        )
        if (
            parsed.scheme != "https" or not host or port == 0 or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment or not valid_prefix
            or any(ord(char) <= 32 or ord(char) == 127 for char in url)
        ):
            raise ValueError
    except ValueError:
        result.update(status="failed", reason="expected an HTTPS base URL without credentials")
        return result

    result.update(host=host, port=port or 443, base_path=prefix)
    try:
        context = ssl.create_default_context()
        status, body = https_get(host, port, prefix + "/client/v1/meta", context, read_body=True)
        result.update(tls_trusted=True, meta_status=status, status="failed")
        if status != 200:
            result["reason"] = "the prefixed meta route did not return HTTP 200"
            return result
        try:
            revision = json.loads(body)["data"]["api_revision"]
        except (ValueError, KeyError, TypeError):
            revision = None
        if type(revision) is not int or revision != 14:
            result["reason"] = "meta did not expose the fixed test API revision 14"
            return result
        result["api_revision"] = revision
        controls = [path for route in CONTROL_PATHS for path in (prefix + route, route)]
        paths = dict.fromkeys([*controls, prefix + "/healthz", prefix + "/openapi.json"])
        statuses = result["nonpublic_route_statuses"]
        for path in paths:
            statuses[path], _ = https_get(host, port, path, context)
        blocked = all(statuses[path] == 404 for path in controls)
        result.update(
            prefixed_control_status=statuses[prefix + CONTROL_PATHS[0]],
            root_control_status=statuses[CONTROL_PATHS[0]],
            test_route_blocked=blocked,
        )
        if all(status == 404 for status in statuses.values()):
            result["status"] = "passed"
        else:
            result["reason"] = "the public endpoint did not return HTTP 404 for all nonpublic probes"
    except ssl.SSLError:
        result.update(status="failed", tls_trusted=False, reason="TLS or hostname verification failed")
    except (OSError, http.client.HTTPException):
        result["reason"] = "the HTTPS test endpoint did not respond"
    return result


def env_check(args, output):
    command = 'python3 acceptance/run.py env-check --public-url "$YUKI_DEVICE_TEST_URL"'
    if args.client:
        command += f" --client {args.client} --build {args.build} --networks {args.networks}"
        observations = {
            "probe": "device", "status": "blocked", "requested_networks": args.networks.split(","),
            "reason": "the platform device probe belongs to its installation card",
        }
    else:
        observations = check_host(args.public_url)
    command += " --output " + shlex.quote(str(output.relative_to(ROOT)))
    status = observations["status"]
    write_json(output / "device-endpoint.json", observations)
    write_json(output / "run-manifest.json", manifest(
        "env-check", args, command, status=status, observations=observations,
        steps=("HTTPS meta", "prefixed and root test controls", "nonpublic fixture routes"),
        expected=("Trusted TLS, API revision 14 and HTTP 404 for every nonpublic probe",),
        evidence=("device-endpoint.json",),
        limits=(("Host probe only. Device Wi-Fi and cellular evidence comes from the platform cards.",)
                if not args.client else ("No device build or network was tested.",)),
    ))
    print(f"env-check: {status}" + (f" ({observations['reason']})" if "reason" in observations else ""))
    return int(status != "passed")


def smoke_case(args, output, command):
    from acceptance.backend import BackendUnavailable, start_backend
    from acceptance.cases.smoke import SmokeFailure, run_smoke

    commit = None
    try:
        with start_backend(args.server) as backend:
            commit = backend.commit
            observations = run_smoke(backend)
        observations["status"] = "passed"
        observations["fault_point"] = "before-forward and after-upstream"
        observations["id_aliases"] = {
            "binding_id": "binding-1", "conversation_id": "conversation-1", "device_id": "device-1",
            "request_id": "request-1", "event_id": "event-1",
        }
    except BackendUnavailable as error:
        observations = {"status": "blocked", "reason": str(error)}
    except SmokeFailure as error:
        observations = {**(error.evidence or {}), "status": "failed", "reason": str(error)}
    status = observations["status"]
    write_json(output / "harness-smoke.json", observations)
    return manifest(
        "smoke", args, command, status=status, observations=observations, commit=commit,
        steps=("Same-entry pairing", "After-upstream disconnect", "After-upstream HTTP 503",
               "Before-forward disconnect", "Original request lookup and replay"),
        expected=("Forwarding counts agree with the original receipt and event count",),
        evidence=("harness-smoke.json",), limits=("Reference CLI and scripted model fixture.",),
    )


def desktop_handoff_case(args, output, command):
    from acceptance.cases.desktop_handoff import run_desktop_handoff

    if args.server == "mobile-fixture":
        observations, commit = run_desktop_handoff(output)
    else:
        observations = {"status": "blocked", "reason": "Desktop handoff requires the QA-002 mobile fixture."}
        commit = None
    if "input_replay" in observations:
        observations["fault_point"] = "drop-after-upstream"
    status = observations["status"]
    write_json(output / "desktop-handoff.json", observations)
    evidence = ["desktop-handoff.json"]
    if "screenshot_path" in observations:
        evidence.append(observations["screenshot_path"])
    report = manifest(
        "desktop-handoff", args, command, status=status, observations=observations, commit=commit,
        steps=("Pair Desktop and send after fixture arming", "Verify the waiting Work after Desktop quits",
               "Verify the phone pairing receipt before input", "Recover and independently replay the input",
               "Reopen Desktop and compare original artifact bytes"),
        expected=("Original Work, exactly one new result and matching original SHA-256 from both clients",),
        evidence=evidence,
        limits=("Desktop-side reference self-check with a scripted local fixture. Android A01 is recorded separately.",),
    )
    report["desktop_commit"] = observations.get("desktop_commit")
    report["entry_kind"] = "app"
    if "desktop_pair" in observations:
        report["test_entry_alias"] = "entry-1"
    return report


def run_cases(args, output):
    requested = args.case.split(",")
    cases = list(dict.fromkeys(
        case_id for value in requested
        for case_id in (CORE_CASES if value == "all-core" else (value,))
    ))
    results = []
    for case_id in cases:
        directory = output / case_id if len(cases) > 1 else output
        command = shlex.join([
            "python3", "acceptance/run.py", "--case", case_id, "--client", args.client,
            "--build", args.build, "--server", args.server, "--output", str(directory.relative_to(ROOT)),
        ])
        if case_id == "smoke" and args.client == "reference":
            evidence = smoke_case(args, directory, command)
        elif case_id == "desktop-handoff" and args.client == "reference":
            evidence = desktop_handoff_case(args, directory, command)
        else:
            reason = "the requested case or client belongs to a later acceptance card"
            evidence = manifest(case_id, args, command, status="not_run", observations={"reason": reason},
                                limits=(reason,))
        write_json(directory / "run-manifest.json", evidence)
        results.append({"case_id": case_id, "status": evidence["status"]})
        print(f"{case_id}: {evidence['status']}")

    status = next((priority for priority in ("failed", "blocked", "not_run")
                   if any(result["status"] == priority for result in results)), "passed")
    if len(results) > 1:
        write_json(output / "run-summary.json", {"status": status, "cases": results})
    return int(status != "passed")


def serve(args):
    from acceptance.backend import BackendUnavailable, start_backend
    from acceptance.fault_proxy import FaultProxy

    stop = Event()
    previous = signal.signal(signal.SIGTERM, lambda _number, _frame: stop.set())
    try:
        with start_backend(args.server) as backend:
            with FaultProxy(backend.base_url, port=args.port) as proxy:
                print(f"Loopback mobile proxy ready at {proxy.base_url}", flush=True)
                stop.wait()
    except KeyboardInterrupt:
        pass
    except (BackendUnavailable, OSError):
        print("serve: could not start the local fixture and proxy", file=sys.stderr)
        return 1
    finally:
        signal.signal(signal.SIGTERM, previous)
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    command = argv[0] if argv and argv[0] in ("env-check", "serve") else "cases"
    if command != "cases":
        argv = argv[1:]
    parser = argparse.ArgumentParser(
        description=__doc__, epilog="Other entry points: env-check and serve.",
        prog=f"acceptance/run.py {command}" if command != "cases" else "acceptance/run.py",
    )
    if command == "serve":
        parser.add_argument("--server", choices=SERVERS, required=True)
        parser.add_argument("--port", type=int, required=True)
        args = parser.parse_args(argv)
        if not 0 <= args.port <= 65535:
            parser.error("port must be between 0 and 65535")
        return serve(args)

    parser.add_argument("--output", required=True, type=output_directory)
    if command == "env-check":
        parser.add_argument("--public-url", default=os.getenv("YUKI_DEVICE_TEST_URL"))
        parser.add_argument("--client", choices=("android",))
        parser.add_argument("--build", choices=("release",), default="release")
        parser.add_argument("--networks", choices=("wifi,cellular",), default="wifi,cellular")
    else:
        parser.add_argument("--case", required=True)
        parser.add_argument("--client", choices=("reference", "android", "desktop"), required=True)
        parser.add_argument("--build", choices=("debug", "release"), default="release")
        parser.add_argument("--server", choices=SERVERS, required=True)
    args = parser.parse_args(argv)
    if command == "cases" and any(case not in CASES | {"all-core"} for case in args.case.split(",")):
        parser.error("unknown acceptance case")
    args.started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    args.mobile_commit, args.mobile_source_modified = checkout_metadata()
    try:
        return env_check(args, args.output) if command == "env-check" else run_cases(args, args.output)
    except EvidenceWriteError:
        print("acceptance: could not write run evidence", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    sys.exit(main())
