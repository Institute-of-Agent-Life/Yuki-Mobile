#!/usr/bin/env python3
"""Project a private acceptance manifest onto the public release fields."""

import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import subprocess
import sys

from check_repo import secret_rules

CASE_ID = r"A(?:0[1-9]|1[0-9]|20)|smoke|desktop-handoff|env-check"
OUTCOMES = ("passed", "failed", "blocked", "not_run")
LABEL = r"[A-Za-z0-9][A-Za-z0-9 .,+_()-]{0,63}"
ENUM_FIELDS = {
    "client_platform": ("android", "ios", "desktop", "reference"),
    "build_type": ("debug", "release"),
    "entry_kind": ("app", "chat", "qq_private", "qq_group"),
    "network": ("wifi", "cellular"),
}
TEXT_FIELDS = {
    "device_model": LABEL,
    "os_build": LABEL,
    "script_model_version": LABEL,
    "backend_commit": r"[0-9a-fA-F]{40}",
    "desktop_commit": r"[0-9a-fA-F]{40}",
    "mobile_commit": r"[0-9a-fA-F]{40}",
    "build_sha256": r"[0-9a-fA-F]{64}",
    "file_sha256": r"[0-9a-fA-F]{64}",
    "artifact_sha256": r"[0-9a-fA-F]{64}",
}
ALIAS_KINDS = (
    "work", "binding", "conversation", "request", "event", "artifact", "attachment",
    "operation", "person", "device",
)


def matches(pattern, value):
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def has_text(value):
    return isinstance(value, str) and bool(value.strip())


def is_timestamp(value):
    if not matches(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})", value):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def public_manifest(manifest):
    if not isinstance(manifest, dict):
        raise ValueError("Manifest must be a JSON object")
    case_id = manifest.get("case_id")
    status = manifest.get("status")
    if not matches(CASE_ID, case_id) or status not in OUTCOMES:
        raise ValueError("Invalid case_id or status")

    public = {"case_id": case_id, "status": status}
    for key, choices in ENUM_FIELDS.items():
        value = manifest.get(key)
        if value in choices:
            public[key] = value

    for key, pattern in TEXT_FIELDS.items():
        value = manifest.get(key)
        if value in ("", None):
            continue
        if not matches(pattern, value) or secret_rules(value.encode("ascii")):
            raise ValueError(f"Invalid {key}")
        public[key] = value

    for key in ("tls_trusted", "test_route_blocked"):
        value = manifest.get(key)
        if isinstance(value, bool):
            public[key] = value

    started_at = manifest.get("started_at")
    if is_timestamp(started_at):
        public["started_at"] = started_at

    given_aliases = manifest.get("id_aliases")
    if not isinstance(given_aliases, dict):
        given_aliases = {}
    aliases = {}
    for kind in ALIAS_KINDS:
        key = f"{kind}_id"
        value = given_aliases.get(key)
        if matches(rf"{kind}-[1-9][0-9]{{0,2}}", value):
            aliases[key] = value
        elif has_text(manifest.get(key)):
            aliases[key] = f"{kind}-1"
    if aliases:
        public["id_aliases"] = aliases

    test_entry_alias = manifest.get("test_entry_alias")
    if matches(r"entry-[1-9][0-9]{0,2}", test_entry_alias):
        public["test_entry_alias"] = test_entry_alias
    elif has_text(manifest.get("test_entry")) or has_text(manifest.get("server_url")):
        public["test_entry_alias"] = "entry-1"

    return public


def reject_constant(_):
    raise ValueError("Invalid JSON constant")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", nargs="?", type=Path, help="Private manifest JSON")
    parser.add_argument("output", nargs="?", type=Path, help="New public manifest JSON")
    parser.add_argument("--self-test", action="store_true", help="Run synthetic CLI tests")
    args = parser.parse_args()
    if args.self_test:
        if args.input or args.output:
            parser.error("--self-test takes no input or output paths")
        test_file = Path(__file__).resolve().parent / "tests" / "test_repo_tools.py"
        return subprocess.call([sys.executable, str(test_file)])
    if args.input is None or args.output is None:
        parser.error("input and output are required")

    try:
        with args.input.open(encoding="utf-8") as stream:
            manifest = json.load(stream, parse_constant=reject_constant)
    except (OSError, ValueError, RecursionError):
        print("redact-evidence: cannot read a valid acceptance manifest", file=sys.stderr)
        return 1

    try:
        data = json.dumps(public_manifest(manifest), indent=2) + "\n"
    except ValueError as error:
        print(f"redact-evidence: {error}", file=sys.stderr)
        return 1

    try:
        stream = args.output.open("x", encoding="utf-8")
        try:
            with stream:
                stream.write(data)
        except OSError:
            args.output.unlink()
            raise
    except OSError:
        print("redact-evidence: cannot create a new public manifest", file=sys.stderr)
        return 1

    print("redact-evidence: public manifest written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
