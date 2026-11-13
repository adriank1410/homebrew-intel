# SPDX-License-Identifier: BSD-2-Clause
"""Conservative runtime holds for a repeatable, classified source-build failure."""
from __future__ import annotations

import json
import os
import re
import zipfile
from io import BytesIO
from pathlib import Path

from .core import Error, canonical_name, require_sha, run

class RepeatableHold(Exception):
    """A classified source-build failure confirmed across consecutive runs."""

# Only these signatures may become a runtime hold. Network, timeouts, disk and
# cancelled jobs stay red; a misclassified flake must not hide a regression.
SIGNATURES = (
    (re.compile(r"\bCA1307\b"), "dotnet_analyzer_CA1307"),
    (re.compile(r"The arm64 architecture is required for this software"), "arch_arm64_required"),
    (re.compile(r"failed assertion `Target device architecture is nil'"), "metal_device_arch_nil"),
    (re.compile(r"static assertion failed|static_assert\s*\(", re.I), "static_assertion"),
)
TRANSIENT = re.compile(
    r"timed? out|cancelled|no space left|connection reset|could not resolve|"
    r"tls|certificate|curl:|HTTP [45]\d\d|429|502|503|504|EOF|network is unreachable|"
    r"failed to download|temporary failure",
    re.I,
)
MAX_DEPS = 2
MAX_HISTORY = 10
ARTIFACT_PREFIX = "intelbrew-failure-"
RECORD_SCHEMA = 1


def classify_build_failure(output):
    """Return a stable signature or None when the failure must stay red."""
    if not isinstance(output, str) or not output.strip():
        return None
    if TRANSIENT.search(output):
        return None
    hits = [name for pattern, name in SIGNATURES if pattern.search(output)]
    if len(hits) != 1:
        return None
    return hits[0]


def hold_descriptor(root, plan, records):
    """Describe the root plus at most two formula-hash dependencies that drifted."""
    nodes = plan.get("nodes") if isinstance(plan, dict) else None
    meta = nodes.get(root) if isinstance(nodes, dict) else None
    if not isinstance(meta, dict):
        return None
    require_sha(meta.get("formula_sha256"))
    published = records.get(root) if isinstance(records, dict) else None
    published_deps = {}
    if isinstance(published, dict):
        for dep in published.get("runtime_dependencies", []):
            if isinstance(dep, dict) and dep.get("name") and dep.get("formula_sha256"):
                published_deps[dep["name"]] = dep["formula_sha256"]
    changed = []
    names = set(meta.get("runtime") or []) | set(meta.get("build") or []) | set(meta.get("test") or [])
    for name in sorted(names):
        dep_meta = nodes.get(name)
        if not isinstance(dep_meta, dict) or not dep_meta.get("formula_sha256"):
            continue
        previous = published_deps.get(name)
        if previous is None or previous != dep_meta["formula_sha256"]:
            require_sha(dep_meta["formula_sha256"])
            changed.append({"name": name, "formula_sha256": dep_meta["formula_sha256"]})
    if len(changed) > MAX_DEPS:
        return None
    return {
        "name": canonical_name(root),
        "formula_sha256": meta["formula_sha256"],
        "dependencies": changed,
    }


def same_hold_pair(left, right):
    if not left or not right:
        return False
    if left.get("name") != right.get("name") or left.get("formula_sha256") != right.get("formula_sha256"):
        return False
    return _dep_key(left.get("dependencies")) == _dep_key(right.get("dependencies"))


def _dep_key(deps):
    if not isinstance(deps, list):
        return ()
    return tuple(sorted((item.get("name"), item.get("formula_sha256")) for item in deps if isinstance(item, dict)))


def validate_failure_record(record):
    if not isinstance(record, dict) or record.get("schema") != RECORD_SCHEMA:
        raise Error("Invalid build-failure record")
    name = canonical_name(record.get("name"))
    require_sha(record.get("formula_sha256"))
    signature = record.get("signature")
    if not isinstance(signature, str) or not signature or len(signature) > 80:
        raise Error("Invalid build-failure record")
    deps = record.get("dependencies")
    if not isinstance(deps, list) or len(deps) > MAX_DEPS:
        raise Error("Invalid build-failure record")
    normalized = []
    for dep in deps:
        if not isinstance(dep, dict) or set(dep) != {"name", "formula_sha256"}:
            raise Error("Invalid build-failure record")
        normalized.append({"name": canonical_name(dep["name"]), "formula_sha256": require_sha(dep["formula_sha256"])})
    return {
        "schema": RECORD_SCHEMA,
        "name": name,
        "formula_sha256": record["formula_sha256"],
        "dependencies": normalized,
        "signature": signature,
        "run_id": str(record.get("run_id") or ""),
    }


def make_failure_record(descriptor, signature, run_id=""):
    record = {
        "schema": RECORD_SCHEMA,
        "name": descriptor["name"],
        "formula_sha256": descriptor["formula_sha256"],
        "dependencies": list(descriptor["dependencies"]),
        "signature": signature,
        "run_id": str(run_id or ""),
    }
    return validate_failure_record(record)


def consecutive_matches(history, descriptor, signature):
    """Return how many leading history records match this pair and signature."""
    count = 0
    for record in history:
        try:
            item = validate_failure_record(record)
        except (Error, TypeError, ValueError, KeyError):
            break
        if item["signature"] != signature or not same_hold_pair(item, descriptor):
            break
        count += 1
    return count


def describe_hold(descriptor, signature, evidence_count):
    deps = ", ".join(item["name"] for item in descriptor.get("dependencies") or []) or "formula file only"
    return (
        f"Repeatable source-build hold: {descriptor['name']} "
        f"({deps}; {signature}, {evidence_count} consecutive classified failures)"
    )


def artifact_name(root):
    return ARTIFACT_PREFIX + canonical_name(root)


def write_failure_record(directory, record):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "intelbrew-failure.json"
    validated = validate_failure_record(record)
    path.write_text(json.dumps(validated, indent=2) + "\n")
    return path


def load_failure_history(repository, root, *, run=run, env=None):
    """Return recent failure records for this root, newest first. Fail closed on errors."""
    if not repository or "/" not in repository:
        return []
    env = env if env is not None else _gh_env()
    if not env.get("GH_TOKEN") and not env.get("GITHUB_TOKEN"):
        return []
    name = artifact_name(root)
    try:
        payload = run(
            ["gh", "api", f"repos/{repository}/actions/artifacts",
             "-f", f"name={name}", "-F", "per_page=20"],
            env=env if env is not None else _gh_env(),
        )
        data = json.loads(payload)
    except (Error, OSError, ValueError, TypeError, json.JSONDecodeError):
        return []
    artifacts = data.get("artifacts") if isinstance(data, dict) else None
    if not isinstance(artifacts, list):
        return []
    artifacts = sorted(
        artifacts,
        key=lambda item: (str(item.get("created_at") or ""), int(item.get("id") or 0)),
        reverse=True,
    )
    records = []
    for artifact in artifacts:
        if len(records) >= MAX_HISTORY:
            break
        if not isinstance(artifact, dict) or artifact.get("expired") or artifact.get("name") != name:
            continue
        artifact_id = artifact.get("id")
        if not artifact_id:
            continue
        record = _download_failure_record(repository, artifact_id, run=run, env=env)
        if record is not None:
            records.append(record)
    return records


def _download_failure_record(repository, artifact_id, *, run, env):
    try:
        import tempfile
        with tempfile.TemporaryDirectory(prefix="intelbrew-failure-art-") as folder:
            archive = Path(folder) / "artifact.zip"
            run(
                ["gh", "api", f"repos/{repository}/actions/artifacts/{artifact_id}/zip",
                 "--output", str(archive)],
                env=env if env is not None else _gh_env(),
            )
            with zipfile.ZipFile(archive) as zf:
                names = [item for item in zf.namelist() if item.endswith("intelbrew-failure.json")]
                if len(names) != 1:
                    return None
                record = json.loads(zf.read(names[0]).decode("utf-8"))
        return validate_failure_record(record)
    except (Error, OSError, ValueError, TypeError, KeyError, zipfile.BadZipFile, json.JSONDecodeError):
        return None


def _gh_env():
    # Keep GH_TOKEN in the subprocess environment; do not log it.
    env = os.environ.copy()
    return env
