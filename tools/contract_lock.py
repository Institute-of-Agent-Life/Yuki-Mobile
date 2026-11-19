#!/usr/bin/env python3
"""Export and verify Yuki-Mobile's pinned personal API contract."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from check_repo import GIT_ENV, secret_rules

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "contract-lock"
OPENAPI = "client-api.openapi.json"
REPORT = ROOT / "acceptance" / "out" / "contract-check.json"
GOLDEN_FILES = ("golden/payload-hash.json", "golden/text-normalize.json")
METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}
ANNOTATIONS = {"title", "summary", "description", "example", "examples"}
UNCONSTRAINED = {}
SCHEMA_CHILDREN = {
    "$ref", "required", "properties", "additionalProperties", "items", "anyOf", "oneOf", "allOf",
}


class ContractError(ValueError):
    def __init__(self, location, issue):
        if secret_rules(location.encode("utf-8", "replace")):
            location = "<redacted location>"
        elif not location.isprintable():
            location = json.dumps(location, ensure_ascii=True)
        self.location = location
        self.issue = issue
        super().__init__(f"{self.location}: {self.issue}")


def require(condition, location, issue):
    if not condition:
        raise ContractError(location, issue)


def object_at(value, location):
    require(isinstance(value, dict), location, "expected a JSON object")
    return value


def array_at(value, location):
    require(isinstance(value, list), location, "expected a JSON array")
    return value


def read_bytes(path, location):
    try:
        return path.read_bytes()
    except OSError:
        raise ContractError(location, "cannot read file") from None


def parse_json(data, location):
    def reject_constant(_value):
        raise ValueError

    try:
        value = json.loads(data, parse_constant=reject_constant)
    except (ValueError, RecursionError):
        raise ContractError(location, "invalid JSON") from None
    return object_at(value, location)


def read_json(path, location):
    return parse_json(read_bytes(path, location), location)


def json_bytes(value):
    return (json.dumps(value, indent=2, ensure_ascii=True) + "\n").encode("ascii")


def write_bytes(path, data):
    require(path.parent.resolve() == path.parent, path.name, "parent resolves through a symbolic link")
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
        temporary.replace(path)
    except OSError:
        raise ContractError(path.name, "cannot write file") from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def is_hex(value, size):
    return isinstance(value, str) and re.fullmatch(rf"[0-9a-f]{{{size}}}", value) is not None


def positive_int(value, location):
    require(type(value) is int and value > 0, location, "expected a positive integer")
    return value


def check_digest(value, expected, location):
    require(is_hex(expected, 64), location, "expected a SHA-256 in source.json")
    require(value == expected, location, "SHA-256 differs from source.json")


def openapi_info(document, label):
    info = object_at(document.get("info"), f"{label}.info")
    paths = object_at(document.get("paths"), f"{label}.paths")
    version = info.get("version")
    require(isinstance(version, str) and re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[.+-][0-9A-Za-z.+-]+)?", version),
            f"{label}.info.version", "expected a server version")
    return {
        "apiRevision": positive_int(info.get("x-yuki-api-revision"), f"{label}.info.x-yuki-api-revision"),
        "pathCount": len(paths),
        "serverVersion": version,
    }


def compare_keys(old, new, excluded, location):
    for name in sorted((old.keys() | new.keys()) - excluded - ANNOTATIONS):
        if name not in old or name not in new:
            raise ContractError(f"{location}.{name}", "contract keyword was added or removed")
        require(json.dumps(old[name], sort_keys=True) == json.dumps(new[name], sort_keys=True),
                f"{location}.{name}", "contract keyword changed")


def resolve(document, value, location):
    value = object_at(value, location)
    followed = set()
    while "$ref" in value:
        reference = value["$ref"]
        require(isinstance(reference, str) and reference.startswith("#/"), location,
                "expected a local OpenAPI reference")
        require(reference not in followed, location, "circular reference without a schema")
        require(not (value.keys() - {"$ref"} - ANNOTATIONS), location,
                "reference has unsupported sibling keywords")
        followed.add(reference)
        value = document
        for part in reference[2:].split("/"):
            name = part.replace("~1", "/").replace("~0", "~")
            require(isinstance(value, dict) and name in value, location, "unresolved OpenAPI reference")
            value = value[name]
        value = object_at(value, location)
        location = f"{location} ({reference})"
    return value, location


class Compatibility:
    def __init__(self, desktop, backend):
        self.desktop = desktop
        self.backend = backend
        self.visited = set()

    def schema(self, old, new, location, request):
        old, old_location = resolve(self.desktop, old, location)
        new, new_location = resolve(self.backend, new, location)
        location = new_location if new_location != location else old_location
        key = (id(old), id(new), request)
        if key in self.visited:
            return
        self.visited.add(key)

        if not request and not (old.keys() - ANNOTATIONS):
            return
        compare_keys(old, new, SCHEMA_CHILDREN, location)

        old_fields = object_at(old.get("properties", {}), f"{location}.properties")
        new_fields = object_at(new.get("properties", {}), f"{location}.properties")
        old_required = self.required_fields(old, old_fields, location)
        new_required = self.required_fields(new, new_fields, location)
        if request:
            for name in sorted(old_fields.keys() - new_fields.keys()):
                raise ContractError(f"{location}.{name}", "request field was removed")
            for name in sorted(new_required - old_required):
                raise ContractError(f"{location}.{name}", "request field became required")
        else:
            for name in sorted(old_required - new_required):
                raise ContractError(f"{location}.{name}", "required response field was removed or became optional")

        for name in sorted(old_fields.keys() & new_fields.keys()):
            self.schema(old_fields[name], new_fields[name], f"{location}.{name}", request)

        self.additional_properties(old, new, old_fields, new_fields, location, request)
        if "items" in old or "items" in new:
            require("items" in old and "items" in new, f"{location}.items", "array item schema changed")
            self.schema(old["items"], new["items"], f"{location}.items", request)
        for keyword in ("anyOf", "oneOf", "allOf"):
            if keyword in old or keyword in new:
                alternatives = f"{location}.{keyword}"
                old_choices = array_at(old.get(keyword), alternatives)
                new_choices = array_at(new.get(keyword), alternatives)
                require(len(old_choices) == len(new_choices), alternatives, "schema alternatives changed")
                for index, (left, right) in enumerate(zip(old_choices, new_choices)):
                    self.schema(left, right, f"{alternatives}[{index}]", request)

    @staticmethod
    def required_fields(schema, fields, location):
        names = array_at(schema.get("required", []), f"{location}.required")
        for name in names:
            require(isinstance(name, str), f"{location}.required", "expected a property name")
            require(name in fields, f"{location}.{name}", "required field is absent from properties")
        return set(names)

    def additional_properties(self, old, new, old_fields, new_fields, location, request):
        old_extra = old.get("additionalProperties", True)
        new_extra = new.get("additionalProperties", True)
        extras = f"{location}.additionalProperties"
        for value in (old_extra, new_extra):
            require(isinstance(value, (bool, dict)), extras, "expected a boolean or schema")
        if not request:
            for name in sorted(new_fields.keys() - old_fields.keys()):
                require(old_extra is not False, f"{location}.{name}", "old response schema rejects extra fields")
                if isinstance(old_extra, dict):
                    self.schema(old_extra, new_fields[name], f"{location}.{name}", False)

        source, consumer = (old_extra, new_extra) if request else (new_extra, old_extra)
        if source is False or consumer is True:
            return
        require(consumer is not False, extras, "additional property support changed")
        self.schema(UNCONSTRAINED if old_extra is True else old_extra,
                    UNCONSTRAINED if new_extra is True else new_extra, extras, request)

    def schema_field(self, old, new, location, request):
        if "schema" in old or "schema" in new:
            require("schema" in old and "schema" in new, location, "schema was added or removed")
            self.schema(old["schema"], new["schema"], location, request)

    def content(self, old, new, location, request):
        old_content = object_at(old.get("content", {}), location)
        new_content = object_at(new.get("content", {}), location)
        for media_type in sorted(old_content):
            label = f"{location}.{media_type}"
            require(media_type in new_content, label, "media type was removed")
            left = object_at(old_content[media_type], label)
            right = object_at(new_content[media_type], label)
            self.schema_field(left, right, label, request)
            compare_keys(left, right, {"schema"}, label)

    def parameters(self, old, new, location):
        def by_name(document, parameters):
            result = {}
            for value in array_at(parameters, location):
                item, _ = resolve(document, value, location)
                kind, name = item.get("in"), item.get("name")
                require(isinstance(kind, str) and isinstance(name, str), location,
                        "parameter needs an in and a name")
                require((kind, name) not in result, location, "duplicate parameter")
                result[kind, name] = item
            return result

        left = by_name(self.desktop, old.get("parameters", []))
        right = by_name(self.backend, new.get("parameters", []))
        for (plane, name), parameter in sorted(left.items()):
            label = f"{location}.parameter {plane} {name}"
            require((plane, name) in right, label, "parameter was removed")
            candidate = right[plane, name]
            require(not candidate.get("required", False) or parameter.get("required", False),
                    label, "parameter became required")
            self.schema_field(parameter, candidate, label, True)
            self.content(parameter, candidate, label, True)
            compare_keys(parameter, candidate, {"schema", "content", "required"}, label)
        for plane, name in sorted(right.keys() - left.keys()):
            require(not right[plane, name].get("required", False),
                    f"{location}.parameter {plane} {name}", "new parameter is required")

    def body(self, old, new, location):
        if "requestBody" not in old and "requestBody" not in new:
            return
        label = f"{location}.requestBody"
        require("requestBody" not in old or "requestBody" in new, label, "request body was removed")
        left, _ = resolve(self.desktop, old.get("requestBody", {}), label)
        right, _ = resolve(self.backend, new["requestBody"], label)
        require(not right.get("required", False) or left.get("required", False),
                label, "request body became required")
        self.content(left, right, label, True)
        compare_keys(left, right, {"content", "required"}, label)

    def responses(self, old, new, location):
        old_responses = object_at(old.get("responses"), f"{location}.responses")
        new_responses = object_at(new.get("responses"), f"{location}.responses")
        for status, response in sorted(old_responses.items()):
            label = f"{location}.response {status}"
            require(status in new_responses, label, "response status was removed")
            left, _ = resolve(self.desktop, response, label)
            right, _ = resolve(self.backend, new_responses[status], label)
            self.content(left, right, label, False)
            old_headers = object_at(left.get("headers", {}), f"{label}.headers")
            new_headers = object_at(right.get("headers", {}), f"{label}.headers")
            for name, header in sorted(old_headers.items()):
                header_label = f"{label}.header {name}"
                require(name in new_headers, header_label, "response header was removed")
                previous, _ = resolve(self.desktop, header, header_label)
                candidate, _ = resolve(self.backend, new_headers[name], header_label)
                self.schema_field(previous, candidate, header_label, False)
                self.content(previous, candidate, header_label, False)
                compare_keys(previous, candidate, {"schema", "content"}, header_label)
            compare_keys(left, right, {"content", "headers"}, label)

    def operations(self):
        desktop_paths = object_at(self.desktop.get("paths"), "Desktop.paths")
        backend_paths = object_at(self.backend.get("paths"), "Yuki.paths")
        for path, desktop_path in sorted(desktop_paths.items()):
            left, _ = resolve(self.desktop, desktop_path, path)
            right, _ = resolve(self.backend, backend_paths.get(path, {}), path)
            for verb in sorted(left.keys() & METHODS):
                label = f"{verb.upper()} {path}"
                require(verb in right, label, "operation was removed")
                old = object_at(left[verb], label)
                new = object_at(right[verb], label)
                self.visited.clear()
                self.parameters(old, new, label)
                self.body(old, new, label)
                self.responses(old, new, label)
                compare_keys(old, new, {"operationId", "parameters", "requestBody", "responses"}, label)
            compare_keys(left, right, METHODS, path)


def ensure_backward_compatible(desktop, backend):
    for keyword in ("openapi", "jsonSchemaDialect", "servers", "security"):
        require(desktop.get(keyword) == backend.get(keyword), keyword, "top-level contract changed")
    Compatibility(desktop, backend).operations()


def variable_value(data, variable, location, prefix=""):
    expression = rf"^[ \t]*{prefix}{variable}[ \t]*=[ \t]*([0-9]+)[ \t]*;?[ \t]*(?://[^\r\n]*)?\r?$"
    values = re.findall(expression, data.decode("utf-8", errors="replace"), re.MULTILINE)
    require(len(values) == 1, location, f"expected one integer declaration of {variable}")
    return positive_int(int(values[0]), location)


def validate_archive(source, archive):
    marker = read_bytes(archive / ".yuki-commit", "Yuki .yuki-commit").strip()
    require(marker == source["commit"].encode("ascii"), "Yuki .yuki-commit",
            "archive commit differs from contract-lock/source.json")
    label = "Yuki src/yuki/client_api/version.py"
    version = read_bytes(archive / "src" / "yuki" / "client_api" / "version.py", label)
    require(variable_value(version, "CLIENT_API_REVISION", label) == source["apiRevision"],
            label, "CLIENT_API_REVISION differs from contract-lock/source.json")


BACKEND_BOOTSTRAP = """\
from pathlib import Path
import sys

source = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(source))
import yuki

if Path(yuki.__file__).resolve() != source / "yuki" / "__init__.py":
    raise RuntimeError("expected Yuki from the pinned archive")
"""

EXPORT_SCRIPT = """\
from yuki.client_api.openapi import export_openapi

export_openapi(Path(sys.argv[2]))
"""

GOLDEN_SCRIPT = """\
import hashlib
import json

from yuki.client_api.agent_target import AgentTarget, input_payload, target_data
from yuki.client_api.identity import fingerprint

def case(name, operation, target, payload):
    return {
        "id": name, "operation": operation, "target": target,
        "payload": payload, "payload_hash": fingerprint(operation, target, payload),
    }

values = json.load(sys.stdin)
agent_target = AgentTarget(device_id="device-fixture", session_id="session-fixture",
                           intent="prompt", instance_id=None)
message = values["message"].strip()
work_input = values["work_input"].strip()
upload_bytes = b"Yuki Mobile contract fixture"
upload = {
    "filename": values["filename"],
    "kind": "file",
    "sha256": hashlib.sha256(upload_bytes).hexdigest(),
    "size": len(upload_bytes),
}
binding = {"source_binding_id": "binding-fixture"}
vectors = {
    "golden/payload-hash.json": {"vectors": [
        case("message-no-attachments", "message", "conversation-fixture",
             {"text": "Hello, Yuki!", "attachments": []}),
        case("message-with-attachments-and-target", "message", "conversation-fixture",
             {"text": message, "attachments": ["media-fixture-b", "media-fixture-a"],
              "agent_target": target_data(agent_target)}),
        case("work-input", "client.work_input", "work-fixture", input_payload(work_input, None)),
        case("work-input-with-target", "client.work_input", "work-fixture",
             input_payload(work_input, agent_target)),
        case("work-cancel", "client.work_cancel", "work-fixture", {"expected_revision": 7}),
        case("upload", "client.upload", "person-fixture", upload),
        case("device-revoke", "client.revoke", "device-fixture", {}),
        case("chat-link-code", "client.link_code", "person-fixture", binding),
        case("connector-link-code", "client.link_code", "person-fixture",
             {**binding, "purpose": "connector"}),
    ]},
    "golden/text-normalize.json": {"vectors": [
        {"id": name, "input": raw, "normalized": raw.strip()}
        for name, raw in values["normalization"].items()
    ]},
}
print(json.dumps(vectors))
"""

GOLDEN_INPUTS = {
    "message": "\u00a0é 😀\x7f\u0085",
    "work_input": "\x1c\x1d\u0085é 😀\x1c\x1d\x1e\x1f\u0085\u00a0\ufeff\u00a0\x1e\x1f",
    "filename": "é😀.txt\u00a0",
    "normalization": {
        "ascii": " \t\r\nHello, Yuki!\n\f\v ",
        "accent": " é e\u0301 ",
        "emoji": "\u00a0😀\u0085",
        "u007f-retained": " \x7fexample\x7f ",
        "u00a0-stripped": "\u00a0example\u00a0",
        "u0085-stripped": "\u0085example\u0085",
        "u001c-stripped": "\x1cexample\x1c",
        "u001d-stripped": "\x1dexample\x1d",
        "u001e-stripped": "\x1eexample\x1e",
        "u001f-stripped": "\x1fexample\x1f",
        "ufeff-retained": " \ufeffexample\ufeff ",
        "interior-retained": " \u00a0A\x1c\x1d\x1e\x1f\u0085\u00a0B ",
        "all-whitespace": "\x1c\x1d\x1e\x1f\u0085\u00a0",
        "empty": "",
    },
}


def run_backend(python, archive, script, *args, input_data=None):
    try:
        result = subprocess.run(
            [str(python), "-IB", "-c", BACKEND_BOOTSTRAP + script, str(archive / "src"), *args],
            input=input_data, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            check=False, timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        raise ContractError("Yuki Python", "could not run the pinned backend interpreter") from None
    require(result.returncode == 0, "Yuki Python", "pinned backend command failed; check its installed dependencies")
    return result.stdout


def export_bytes(python, archive):
    with tempfile.TemporaryDirectory() as directory:
        target = Path(directory) / OPENAPI
        run_backend(python, archive, EXPORT_SCRIPT, str(target))
        return read_bytes(target, "Yuki OpenAPI export")


def backend_document(data, source, report):
    label = f"contract-lock/{OPENAPI}"
    observed = sha256(data)
    report["sha256"] = observed
    check_digest(observed, source["sha256"], label)
    document = parse_json(data, label)
    for name, value in openapi_info(document, label).items():
        report[name] = value
        require(value == source[name], label, f"{name} differs from source.json")
    return document


def source_manifest():
    manifest = read_json(LOCK / "source.json", "contract-lock/source.json")
    label = "contract-lock/source.json backend"
    source = object_at(manifest.get("backend"), label)
    require(is_hex(source.get("commit"), 40), label, "expected a full Yuki commit hash")
    require(source.get("file") == OPENAPI, label, "unexpected OpenAPI filename")
    require(is_hex(source.get("sha256"), 64), label, "expected a SHA-256")
    require(isinstance(source.get("serverVersion"), str), label, "expected a server version")
    positive_int(source.get("apiRevision"), f"{label}.apiRevision")
    positive_int(source.get("pathCount"), f"{label}.pathCount")
    return manifest, source


def desktop_git(root, *args):
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, check=False, timeout=10,
            env={**GIT_ENV, "GIT_OPTIONAL_LOCKS": "0", "GIT_NO_LAZY_FETCH": "1"},
        )
    except (OSError, subprocess.SubprocessError):
        raise ContractError("Desktop Git", "cannot read the Desktop repository") from None


def desktop_commit(root):
    result = desktop_git(root, "rev-parse", "--show-toplevel", "HEAD")
    lines = result.stdout.splitlines()
    require(result.returncode == 0 and len(lines) == 2 and Path(os.fsdecode(lines[0])).resolve() == root,
            "Desktop HEAD", "expected a Desktop Git worktree")
    commit = lines[1].decode("ascii", errors="replace")
    require(is_hex(commit, 40), "Desktop HEAD", "invalid Desktop commit hash")
    return commit


def desktop_file(root, commit, filename):
    label = f"Desktop {filename}"
    working = read_bytes(root / filename, label)
    committed = desktop_git(root, "cat-file", "blob", f"{commit}:{filename}")
    require(committed.returncode == 0, label, "file is absent from the reported Desktop commit")
    require(working == committed.stdout, label, "working file differs from the reported Desktop commit")
    return working


def desktop_document(root, summary):
    commit = desktop_commit(root)
    summary["commit"] = commit
    filename = "contracts/source.json"
    label = f"Desktop {filename}"
    source = parse_json(desktop_file(root, commit, filename), label)
    require(is_hex(source.get("commit"), 40), label, "expected a full Desktop source Yuki commit")
    summary["backendCommit"] = source["commit"]
    summary["apiRevision"] = positive_int(source.get("apiRevision"), f"{label}.apiRevision")
    require(source.get("file") == OPENAPI, label, "unexpected OpenAPI filename")
    filename = f"contracts/{OPENAPI}"
    label = f"Desktop {filename}"
    data = desktop_file(root, commit, filename)
    summary["sha256"] = sha256(data)
    check_digest(summary["sha256"], source.get("sha256"), label)
    document = parse_json(data, label)
    observed = openapi_info(document, label)
    require(observed["apiRevision"] == summary["apiRevision"], label,
            "OpenAPI revision differs from Desktop contracts/source.json")
    if "serverVersion" in source:
        require(observed["serverVersion"] == source["serverVersion"], label,
                "serverVersion differs from Desktop contracts/source.json")
    summary.update(observed)
    filename = "src/shared/api.ts"
    summary["requiredApiRevision"] = variable_value(
        desktop_file(root, commit, filename), "REQUIRED_API_REVISION", f"Desktop {filename}",
        prefix="export[ \t]+const[ \t]+",
    )
    return document, data


def check(args):
    report = {"status": "failed", "backend": {}, "desktop": {}, "findings": []}
    try:
        manifest, source = source_manifest()
        backend = report["backend"]
        backend.update({
            "commit": source["commit"], "expectedSha256": source["sha256"],
            "archiveVerified": False, "exportVerified": False,
        })
        backend_data = read_bytes(LOCK / OPENAPI, f"contract-lock/{OPENAPI}")
        backend_schema = backend_document(backend_data, source, backend)

        if args.archive:
            validate_archive(source, args.archive)
            backend["archiveVerified"] = True
        if args.yuki_python:
            require(args.archive, "YUKI_BACKEND_TEST_ROOT", "set this to the pinned Yuki archive")
            actual = export_bytes(args.yuki_python, args.archive)
            backend["exportSha256"] = sha256(actual)
            require(actual == backend_data, "Yuki OpenAPI export", "export differs from the checked-in file")
            backend["exportVerified"] = True

        golden = object_at(manifest.get("golden"), "contract-lock/source.json golden")
        for filename in GOLDEN_FILES:
            label = f"contract-lock/{filename}"
            check_digest(sha256(read_bytes(LOCK / filename, label)), golden.get(filename), label)
        report["goldenDigestsVerified"] = True

        require(args.desktop_root, "Desktop root", "pass --desktop-root or set YUKI_DESKTOP_ROOT")
        desktop = report["desktop"]
        desktop_schema, desktop_data = desktop_document(args.desktop_root.resolve(), desktop)
        require(desktop["requiredApiRevision"] <= backend["apiRevision"],
                "Desktop src/shared/api.ts", "REQUIRED_API_REVISION exceeds the Yuki OpenAPI revision")
        require(desktop["apiRevision"] <= backend["apiRevision"], "Desktop contracts/source.json",
                "Desktop OpenAPI revision exceeds the Yuki OpenAPI revision")
        if desktop["apiRevision"] == backend["apiRevision"]:
            require(desktop_data == backend_data, f"Desktop contracts/{OPENAPI}",
                    "OpenAPI bytes differ at the same API revision")
            report["compatibility"] = "same-revision"
        else:
            ensure_backward_compatible(desktop_schema, backend_schema)
            report["compatibility"] = "backward-compatible-superset"
        report["status"] = "passed"
    except ContractError as error:
        report["findings"].append({"location": error.location, "issue": error.issue})
    except RecursionError:
        report["findings"].append({"location": "OpenAPI", "issue": "schema nesting exceeds the checker limit"})

    write_bytes(REPORT, json_bytes(report))
    if report["findings"]:
        finding = report["findings"][0]
        raise ContractError(finding["location"], finding["issue"])
    print(f"contract-lock: Yuki rev{backend['apiRevision']} and Desktop rev{desktop['apiRevision']} verified")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    exporter = commands.add_parser("export", help="export the pinned backend OpenAPI")
    golden = commands.add_parser("golden", help="generate vectors with the pinned backend")
    golden.add_argument("--check", action="store_true", help="compare vectors with checked-in artifacts")
    verify = commands.add_parser("check", help="verify the lock and Desktop compatibility")
    verify.add_argument("--desktop-root", type=Path, default=os.getenv("YUKI_DESKTOP_ROOT"))
    for command in (exporter, golden, verify):
        command.add_argument("--yuki-python", type=Path, default=os.getenv("YUKI_BACKEND_TEST_PYTHON"))
    args = parser.parse_args()
    archive = os.getenv("YUKI_BACKEND_TEST_ROOT")
    args.archive = Path(archive).resolve() if archive else None

    try:
        if args.command == "check":
            return check(args)
        require(args.archive, "YUKI_BACKEND_TEST_ROOT", "set this to the pinned Yuki archive")
        require(args.yuki_python, "Yuki Python", "pass --yuki-python or set YUKI_BACKEND_TEST_PYTHON")
        _, source = source_manifest()
        validate_archive(source, args.archive)
        if args.command == "export":
            data = export_bytes(args.yuki_python, args.archive)
            backend_document(data, source, {})
            write_bytes(LOCK / OPENAPI, data)
            print(f"contract-lock: exported rev{source['apiRevision']} with {source['pathCount']} paths")
        else:
            vectors = parse_json(
                run_backend(args.yuki_python, args.archive, GOLDEN_SCRIPT, input_data=json_bytes(GOLDEN_INPUTS)),
                "Yuki golden vectors",
            )
            for filename in GOLDEN_FILES:
                target = LOCK / filename
                data = json_bytes(vectors[filename])
                if args.check:
                    require(read_bytes(target, f"contract-lock/{filename}") == data,
                            f"contract-lock/{filename}", "vectors differ from those generated by pinned Yuki")
                else:
                    write_bytes(target, data)
            print(f"contract-lock: golden vectors {'verified' if args.check else 'generated'}")
        return 0
    except ContractError as error:
        print(f"contract-lock: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
