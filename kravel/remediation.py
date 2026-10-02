"""Operator-authored repair capabilities, separate from runbooks and model output.

Profiles are mounted read-only from kravel-system. There is deliberately no HTTP
endpoint or agent tool that can enroll a resource or supply a new patch.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import sys

from .guardrails import guard_model_input
from .scenarios import by_id
from .utils import stable_json

NAMESPACE = "kravel-demo"
KINDS = {"deployments": "Deployment", "daemonsets": "DaemonSet", "configmaps": "ConfigMap", "services": "Service"}
CONTAINER_FIELDS = {"image", "command", "args", "resources", "livenessProbe", "readinessProbe", "startupProbe"}
POD_FIELDS = {"nodeSelector", "affinity", "tolerations", "dnsPolicy", "dnsConfig", "topologySpreadConstraints"}
NAME = re.compile(r"[a-z0-9](?:[-a-z0-9.]{0,251}[a-z0-9])?")


def no_directives(value):
    """Never permit strategic-merge deletion/replacement through nested fields."""
    if isinstance(value, dict):
        if any(not isinstance(key, str) or key.startswith("$") for key in value):
            raise ValueError("Patch directives are forbidden")
        for item in value.values():
            no_directives(item)
    elif isinstance(value, list):
        for item in value:
            no_directives(item)
    elif value is None:
        raise ValueError("Field deletion is not supported by reviewed repairs")


def validate_profile(profile, *, allowed_fields=None):
    expected = {"id", "title", "namespace", "kind", "name", "scenarioId", "patch"}
    if not isinstance(profile, dict) or set(profile) != expected:
        raise ValueError("Repair profile schema is invalid")
    if profile["namespace"] != NAMESPACE or profile["kind"] not in KINDS:
        raise ValueError("Profile is outside the disposable namespace or supported named resources")
    for key in ("id", "name"):
        if not isinstance(profile[key], str) or not NAME.fullmatch(profile[key]):
            raise ValueError("Invalid profile/resource name")
    if not profile["id"].startswith("profile-") or not isinstance(profile["title"], str) or not 1 <= len(profile["title"]) <= 160:
        raise ValueError("Invalid profile identity/title")
    case = by_id(profile["scenarioId"])
    if allowed_fields is None and (not case or not case["restorableFields"]):
        raise ValueError("This scenario requires an operator-led repair, not a broker patch")
    patch = profile["patch"]
    if not isinstance(patch, dict) or not patch or len(stable_json(patch)) > 32_000:
        raise ValueError("Invalid or oversized repair patch")
    no_directives(patch)
    checked = guard_model_input(stable_json(profile), "repair profile", 40_000)
    if checked["findings"]:
        raise ValueError("Sensitive or instruction-like profile content is not allowed")
    allowed = set(case["restorableFields"]) if allowed_fields is None else set(allowed_fields)
    if profile["kind"] == "configmaps":
        if set(patch) != {"data"} or "data" not in allowed or not isinstance(patch["data"], dict) or not patch["data"]:
            raise ValueError("Only reviewed ConfigMap data keys may be restored")
        if not all(isinstance(v, str) for v in patch["data"].values()):
            raise ValueError("ConfigMap values must be strings")
    elif profile["kind"] == "services":
        if set(patch) != {"spec"} or not isinstance(patch["spec"], dict) or not patch["spec"] or set(patch["spec"]) - (allowed & {"selector", "ports"}):
            raise ValueError("Only reviewed Service selectors/ports may be restored")
    else:
        if (set(patch) != {"spec"} or not isinstance(patch["spec"], dict)
                or set(patch["spec"]) != {"template"} or not isinstance(patch["spec"]["template"], dict)
                or set(patch["spec"]["template"]) != {"spec"}):
            raise ValueError("Only reviewed Pod-template fields may be restored")
        spec = patch["spec"]["template"]["spec"]
        if not isinstance(spec, dict) or not spec or set(spec) - ((allowed & POD_FIELDS) | {"containers"}):
            raise ValueError("Pod-template patch contains a forbidden field")
        if "containers" in spec:
            containers = spec["containers"]
            if not isinstance(containers, list) or not containers or len(containers) > 10:
                raise ValueError("Named container patches are required")
            names = set()
            for container in containers:
                if not isinstance(container, dict) or set(container) - ((allowed & CONTAINER_FIELDS) | {"name"}) or len(container) < 2:
                    raise ValueError("Container patch contains a forbidden field")
                name = container.get("name", "")
                if not isinstance(name, str) or not NAME.fullmatch(name) or name in names:
                    raise ValueError("Container identities must be unique and explicit")
                names.add(name)
                if "image" in container and (not isinstance(container["image"], str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._/:@-]{0,511}", container["image"])):
                    raise ValueError("Image must be an explicit credential-free image reference")
                for key in ("command", "args"):
                    if key in container and (not isinstance(container[key], list) or not all(isinstance(v, str) for v in container[key])):
                        raise ValueError("Commands/arguments must be operator-authored string lists")
                if any(k in container and container[k] is None for k in container):
                    raise ValueError("Removing safety fields is not an allowed repair")
    return copy.deepcopy(profile)


def validate_document(value):
    if not isinstance(value, dict) or set(value) != {"version", "profiles"} or value["version"] != 1 or not isinstance(value["profiles"], list) or len(value["profiles"]) > 100:
        raise ValueError("Invalid repair policy document")
    profiles = [validate_profile(p) for p in value["profiles"]]
    if len({p["id"] for p in profiles}) != len(profiles):
        raise ValueError("Duplicate repair profile IDs")
    return profiles


def load_profiles():
    path = os.getenv("KRAVEL_REPAIR_PROFILES_PATH", "")
    if not path or not Path(path).exists():
        return []
    if Path(path).stat().st_size > 1024*1024:
        raise ValueError("Repair policy is too large")
    return validate_document(json.loads(Path(path).read_text(encoding="utf-8")))


def profile_fix(profile):
    p = validate_profile(profile)
    return {"id": p["id"], "namespace": p["namespace"], "title": p["title"],
        "resource": KINDS[p["kind"]] + "/" + p["name"], "scenarioId": p["scenarioId"],
        "operations": [{"kind": p["kind"], "name": p["name"],
            "contentType": "application/merge-patch+json" if p["kind"] == "configmaps" else "application/strategic-merge-patch+json",
            "patch": copy.deepcopy(p["patch"])}]}


def matching_profiles(kind, name, scenario_ids):
    # Only observed exact object identity + scenario can suggest a capability.
    return [p for p in load_profiles() if KINDS[p["kind"]] == kind and p["name"] == name and p["scenarioId"] in scenario_ids]


def make_profile(obj, scenario_id, *, container="", image=""):
    case = by_id(scenario_id)
    if not case or not case["restorableFields"]:
        raise ValueError("Scenario has no supported broker patch; use its operator-led runbook")
    kind = next((key for key, value in KINDS.items() if value == obj.get("kind")), "")
    metadata = obj.get("metadata", {})
    if kind in {"deployments", "daemonsets"}:
        source = obj.get("spec", {}).get("template", {}).get("spec", {})
        spec = {k: copy.deepcopy(source[k]) for k in case["restorableFields"] if k in POD_FIELDS and k in source}
        containers = []
        for c in source.get("containers", []):
            if container and c.get("name") != container:
                continue
            values = {k: copy.deepcopy(c[k]) for k in case["restorableFields"] if k in CONTAINER_FIELDS and k in c}
            if image:
                if case["restorableFields"] != ["image"] or not container:
                    raise ValueError("Image override requires an image scenario and explicit container")
                values = {"image": image}
            if values:
                containers.append({"name": c["name"], **values})
        if containers:
            spec["containers"] = containers
        patch = {"spec": {"template": {"spec": spec}}}
    elif kind == "services":
        patch = {"spec": {k: copy.deepcopy(obj["spec"][k]) for k in case["restorableFields"] if k in obj.get("spec", {})}}
    elif kind == "configmaps":
        patch = {"data": copy.deepcopy(obj.get("data", {}))}
    else:
        raise ValueError("Enrollment supports named Deployments, DaemonSets, Services and ConfigMaps only")
    identity = hashlib.sha256(stable_json([kind, metadata.get("name"), scenario_id, patch]).encode()).hexdigest()[:16]
    return validate_profile({"id": "profile-" + identity, "title": "Restore reviewed " + case["title"],
        "namespace": metadata.get("namespace", ""), "kind": kind, "name": metadata.get("name", ""),
        "scenarioId": scenario_id, "patch": patch})


def enrollment_manifest(profiles):
    profiles = validate_document({"version": 1, "profiles": profiles})
    rules = []
    for kind in sorted({p["kind"] for p in profiles}):
        rules.append({"apiGroups": ["apps" if kind in {"deployments", "daemonsets"} else ""], "resources": [kind],
            "resourceNames": sorted({p["name"] for p in profiles if p["kind"] == kind}), "verbs": ["get", "patch"]})
    return {"apiVersion": "v1", "kind": "List", "items": [
        {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "kravel-repair-profiles", "namespace": "kravel-system"},
            "data": {"profiles.json": json.dumps({"version": 1, "profiles": profiles}, indent=2)}},
        {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "Role", "metadata": {"name": "kravel-enrolled-repairs", "namespace": NAMESPACE}, "rules": rules},
        {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "RoleBinding", "metadata": {"name": "kravel-enrolled-repairs", "namespace": NAMESPACE},
            "subjects": [{"kind": "ServiceAccount", "name": "kravel-approval-broker", "namespace": "kravel-system"}],
            "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "Role", "name": "kravel-enrolled-repairs"}},
    ]}


def main():
    parser = argparse.ArgumentParser(description="Generate, but do not apply, a named repair policy from operator-reviewed Kubernetes JSON on stdin")
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--container", default="")
    parser.add_argument("--image", default="", help="Operator-verified replacement for the same application, not an arbitrary healthy image")
    parser.add_argument("--policy", default="data/repair-profiles.json")
    parser.add_argument("--output", default="data/repair-enrollment.json")
    args = parser.parse_args()
    obj = json.loads(sys.stdin.read(1024*1024))
    profile = make_profile(obj, args.scenario, container=args.container, image=args.image)
    path = Path(args.policy)
    profiles = validate_document(json.loads(path.read_text())) if path.exists() else []
    profiles = [p for p in profiles if p["id"] != profile["id"]] + [profile]
    # These files are generated by an explicitly invoked human CLI, not by Karl.
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "profiles": profiles}, indent=2), encoding="utf-8")
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(enrollment_manifest(profiles), indent=2), encoding="utf-8")
    print("Generated named repair enrollment. Review its patch and resourceNames before kubectl apply. No cluster resources changed.")


if __name__ == "__main__":
    main()
