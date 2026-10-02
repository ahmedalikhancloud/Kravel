"""Structured repairs with a deterministic, approval-gated execution boundary."""
from __future__ import annotations

import copy
import hashlib
import os
import shlex

from .remediation import CONTAINER_FIELDS, POD_FIELDS, KINDS, NAME, load_profiles, no_directives, validate_profile
from .utils import stable_json


def field_keys(patch, kind):
    if kind == "configmaps": return {"data"}
    if kind == "services": return set(patch["spec"])
    spec = patch.get("spec", {}).get("template", {}).get("spec", {})
    return (set(spec) - {"containers"}) | {key for c in spec.get("containers", []) for key in c if key != "name"}


def validate_draft(draft, namespace):
    if namespace != "kravel-demo" or not isinstance(draft, dict) or set(draft) - {"kind", "name", "patch", "rationale", "evidenceIds"}:
        raise ValueError("Novel repairs must be structured and restricted to the disposable namespace")
    if draft.get("kind") not in KINDS or not NAME.fullmatch(str(draft.get("name", ""))):
        raise ValueError("Unsupported repair target")
    no_directives(draft.get("patch"))
    # Reuse the strict profile schema/path checks with a synthetic field-specific
    # case; this does NOT load a runbook or grant any capability.
    patch = copy.deepcopy(draft.get("patch"))
    kind = draft["kind"]
    from . import remediation
    allowed_fields = CONTAINER_FIELDS | POD_FIELDS | {"data", "selector", "ports"}
    # A separate validator can use a reviewed field union without editing catalog.
    profile = {"id": "profile-draft", "title": "Draft repair", "namespace": namespace,
        "kind": kind, "name": draft["name"], "scenarioId": "novel", "patch": patch}
    remediation.validate_profile(profile, allowed_fields=allowed_fields)
    rationale = str(draft.get("rationale", ""))
    from .guardrails import guard_model_input
    if not 1 <= len(rationale) <= 2000 or guard_model_input(rationale, "draft rationale")["findings"]:
        raise ValueError("Provide a bounded, non-sensitive evidence-based repair rationale")
    evidence_ids = draft.get("evidenceIds", [])
    if not isinstance(evidence_ids, list) or len(evidence_ids) > 20 or not all(isinstance(e, str) and len(e) < 80 for e in evidence_ids):
        raise ValueError("Invalid repair evidence references")
    return {"kind": kind, "name": draft["name"], "patch": patch, "rationale": rationale, "evidenceIds": evidence_ids}


def authorized(draft):
    mode = repair_mode()
    if mode == "approval_gated":
        return True, "Repair-capable agent: server dry-run and a separate human approval are mandatory; no resource enrollment is needed."
    kind, name = draft["kind"], draft["name"]
    profiles = [p for p in load_profiles() if p["kind"] == kind and p["name"] == name]
    # Existing demo capabilities remain exact, not a namespace-wide write grant.
    from .fixes import FIX_CATALOG
    patches = [op["patch"] for fix in FIX_CATALOG.values() for op in fix["operations"] if op["kind"] == kind and op["name"] == name]
    patches += [p["patch"] for p in profiles]
    fields = field_keys(draft["patch"], kind)
    allowed = set().union(*(field_keys(p, kind) for p in patches)) if patches else set()
    if fields - allowed:
        return False, "Operator enrollment is required for this exact resource and these repair fields."
    if kind == "configmaps":
        permitted_keys = {key for p in patches for key in p.get("data", {})}
        if set(draft["patch"]["data"]) - permitted_keys:
            return False, "These ConfigMap data keys have not been enrolled for repairs."
    if kind in {"deployments", "daemonsets"}:
        permitted_containers = {}
        for patch in patches:
            for container in patch.get("spec", {}).get("template", {}).get("spec", {}).get("containers", []):
                permitted_containers.setdefault(container["name"], set()).update(set(container) - {"name"})
        for container in draft["patch"]["spec"]["template"]["spec"].get("containers", []):
            if (set(container) - {"name"}) - permitted_containers.get(container["name"], set()):
                return False, "This container and these fields have not been enrolled for repairs."
    return True, "Named field capability exists; server dry-run and separate human approval are still mandatory."


def repair_mode():
    mode = os.getenv("KRAVEL_REPAIR_MODE", "approval_gated")
    if mode not in {"approval_gated", "enrolled_only"}:
        raise ValueError("KRAVEL_REPAIR_MODE must be approval_gated or enrolled_only")
    return mode


def draft_fix(draft, namespace, *, require_authority=True):
    draft = validate_draft(draft, namespace)
    eligible, reason = authorized(draft)
    if require_authority and not eligible:
        raise ValueError(reason)
    content = "application/merge-patch+json" if draft["kind"] == "configmaps" else "application/strategic-merge-patch+json"
    operation = {"kind": draft["kind"], "name": draft["name"], "contentType": content, "patch": draft["patch"]}
    identity = "draft-" + hashlib.sha256(stable_json([namespace, operation]).encode()).hexdigest()[:24]
    command = f"kubectl -n {namespace} patch {draft['kind']} {draft['name']} --type={'merge' if draft['kind'] == 'configmaps' else 'strategic'} -p {shlex.quote(stable_json(draft['patch']))}"
    return {"id": identity, "namespace": namespace, "resource": KINDS[draft["kind"]] + "/" + draft["name"],
        "title": "Review a newly drafted repair", "command": command, "operations": [operation],
        "draft": draft, "eligible": eligible, "authorizationReason": reason}


def resolve_proposal(proposal):
    if str(proposal["fix_id"]).startswith("draft-"):
        reviewed = proposal["dryRun"][0].get("reviewedDraft")
        fix = draft_fix(reviewed, proposal["namespace"])
        if fix["id"] != proposal["fix_id"]:
            raise ValueError("Reviewed repair identity does not match")
        return fix
    from .fixes import get_fix
    return get_fix(proposal["fix_id"], proposal["namespace"], proposal["id"])
