#!/usr/bin/env python3

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

REPORT = Path("acceptance/out/repo-check.json")
PRIVATE_NAMES = {
    b"local.properties", b"ca.key", b"ca-key.pem", b"test-ca.key", b"test-ca-key.pem",
    b"pairing-handoff", b"pairing_handoff",
}
PRIVATE_SUFFIXES = (b".jks", b".keystore", b".p12", b".mobileprovision", b".p8")
PRIVATE_PREFIXES = (b".env", b"pairing-handoff.", b"pairing_handoff.")
GIT_ENV = {**os.environ, "GIT_ALLOW_PROTOCOL": "", "GIT_NO_REPLACE_OBJECTS": "1"}

PAIR_CODE = rb"[A-Za-z0-9_-]{32}(?![A-Za-z0-9_-])"
SECRET_PATTERNS = (
    ("device_token", re.compile(rb"(?<![A-Za-z0-9_-])yuki_device_[A-Za-z0-9_-]+")),
    (
        "pairing_code",
        re.compile(
            rb"""yuki://pair\?(?:[^&#\x00-\x20"'\x60<>]*&)*(?:amp;)?code="""
            + PAIR_CODE,
            re.IGNORECASE,
        ),
    ),
    (
        "pairing_code",
        re.compile(
            rb"(?<![A-Za-z0-9_-])(?:pair(?:ing)?[ _-]*code|link[ _-]*code|"
            rb"(?:owner|nonowner|chat|connector)_code|"
            + "配對碼|配对码".encode()
            + rb""")["'`]*[ \t]*(?:[:=]|"""
            + "：".encode()
            + rb""")[ \t]*["'`]?"""
            + PAIR_CODE,
            re.IGNORECASE,
        ),
    ),
    (
        "pairing_code",
        re.compile(rb'"code"\s*:\s*"' + PAIR_CODE + rb'"'),
    ),
    (
        "pairing_code",
        re.compile(rb"""(?<![A-Za-z0-9_-])--code(?:=|[ \t]+)["']?""" + PAIR_CODE),
    ),
    ("private_key", re.compile(rb"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----")),
    (
        "authorization_header",
        re.compile(
            rb"""(?<![A-Za-z0-9_-])authorization["']?[ \t]*:[ \t]*["']?"""
            rb"(?:bearer|basic|digest|negotiate)[ \t]+\.*[A-Za-z0-9_~+/-]",
            re.IGNORECASE,
        ),
    ),
)


def secret_rules(data):
    return {rule for rule, pattern in SECRET_PATTERNS if pattern.search(data)}


def path_rules(path):
    parts = path.lower().split(b"/")
    rules = set()
    if any(
        part.startswith(b"._") or part in {b".ds_store", b".spotlight-v100", b".trashes"}
        for part in parts
    ):
        rules.add("apple_metadata")
    if any(
        part in PRIVATE_NAMES or part.startswith(PRIVATE_PREFIXES) or part.endswith(PRIVATE_SUFFIXES)
        for part in parts
    ):
        rules.add("private_file")
    if parts[:2] == [b"acceptance", b"out"] and len(parts) > 2:
        rules.add("local_evidence")
    return rules


def git_output(*args, cwd=None):
    return subprocess.check_output(
        ["git", *args], cwd=cwd, env=GIT_ENV, stderr=subprocess.DEVNULL
    )


def read_blob(process, oid):
    process.stdin.write(oid + b"\n")
    process.stdin.flush()
    header = process.stdout.readline().split()

    if len(header) != 3 or header[:2] != [oid, b"blob"]:
        raise ValueError("Could not read indexed blob")
    size = int(header[2])
    data = process.stdout.read(size + 1)
    if len(data) != size + 1 or not data.endswith(b"\n"):
        raise ValueError("Could not read indexed blob")
    return data[:-1]


def scan_index(root):
    entries = git_output("ls-files", "--stage", "-z", cwd=root).split(b"\0")[:-1]
    findings = []
    with subprocess.Popen(
        ["git", "cat-file", "--batch"],
        cwd=root,
        env=GIT_ENV,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    ) as blobs:
        for entry in entries:
            metadata, path = entry.split(b"\t", 1)
            mode, oid, stage = metadata.split()
            path_secrets = secret_rules(path)
            safe_path = (
                "<redacted path>" if path_secrets else path.decode("utf-8", "surrogateescape")
            )
            rules = path_rules(path) | path_secrets

            if stage != b"0":
                rules.add("index_conflict")
            elif mode != b"160000":  # Gitlinks store a commit in another repository.
                try:
                    rules.update(secret_rules(read_blob(blobs, oid)))
                except ValueError:
                    findings.append({"rule": "git_blob_error", "path": safe_path})
                    return len(entries), findings

            for rule in sorted(rules):
                findings.append({"rule": rule, "path": safe_path})

        blobs.stdin.close()
        if blobs.wait():
            findings.append({"rule": "git_scan_error", "path": "."})
    return len(entries), findings


def write_report(root, report):
    destination = root / REPORT
    if destination.parent.resolve() != destination.parent:
        raise OSError("Report directory resolves through a symbolic link")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=destination.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(json.dumps(report, indent=2) + "\n")
        temporary.replace(destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    try:
        top_level = git_output("rev-parse", "--show-toplevel").removesuffix(b"\n")
        root = Path(os.fsdecode(top_level)).resolve()
    except (OSError, subprocess.SubprocessError):
        print("repo-check: cannot locate a Git worktree", file=sys.stderr)
        return 2

    try:
        count, findings = scan_index(root)
    except (OSError, subprocess.SubprocessError, ValueError):
        count, findings = 0, [{"rule": "git_scan_error", "path": "."}]

    errors = {"git_blob_error", "git_scan_error", "index_conflict"}
    if any(item["rule"] in errors for item in findings):
        status = "error"
    else:
        status = "failed" if findings else "passed"
    report = {"status": status, "index_entries": count, "findings": findings}
    try:
        write_report(root, report)
    except (OSError, RuntimeError):
        print("repo-check: cannot write acceptance/out/repo-check.json", file=sys.stderr)
        return 2

    for finding in findings:
        print(f'{finding["rule"]} {json.dumps(finding["path"])}', file=sys.stderr)
    print(f"repo-check: {status} ({REPORT})")
    return {"passed": 0, "failed": 1, "error": 2}[status]


if __name__ == "__main__":
    sys.exit(main())
