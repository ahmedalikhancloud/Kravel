"""General Kubernetes plans. Only the independent approval broker executes them.

No host shell, plugins, remote manifests, kubeconfig overrides or implicit retries.
Resource kinds are deliberately NOT restricted: kubectl's discovery handles CRDs.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shlex
import subprocess
import tempfile
from pathlib import Path
import yaml

from .guardrails import guard_model_input
from .utils import sanitize_object, stable_json

READ_VERBS = {"get", "logs", "top", "explain", "api-resources", "api-versions", "version"}
VERBS = READ_VERBS | {"describe", "apply", "create", "replace", "patch", "delete", "scale", "label", "annotate", "set", "rollout", "cordon", "uncordon", "taint", "expose", "run", "drain", "exec", "debug", "wait", "auth", "diff", "cp"}
IDENTITY_FLAGS = {"--kubeconfig", "--context", "--cluster", "--user", "--server", "-s", "--token", "--username", "--password", "--certificate-authority", "--client-certificate", "--client-key", "--insecure-skip-tls-verify", "--tls-server-name", "--as", "--as-group", "--as-uid", "--proxy-url", "--raw"}
HOST_FLAGS = {"--output-directory", "--output-file", "--template", "--template-file", "--log-file", "--profile", "--profile-output", "--cache-dir", "--kustomize", "-k", "--kuberc", "--certificate-authority-data"}
SAFE_READ_FLAGS = {"--namespace", "-n", "--all-namespaces", "-A", "--selector", "-l", "--field-selector", "--limit", "--chunk-size", "--ignore-not-found", "--container", "-c", "--previous", "-p", "--tail", "--since", "--since-time", "--timestamps", "--prefix", "--all-containers", "--limit-bytes", "--sort-by", "--recursive", "--api-group", "--namespaced", "--verbs", "--api-version", "--client", "--use-protocol-buffers"}


def operator_enabled(config=None):
    value = getattr(config, "cluster_operator_mode", None) or os.getenv("KRAVEL_CLUSTER_OPERATOR_MODE", "disabled")
    if value not in {"disabled", "cluster"}:
        raise ValueError("KRAVEL_CLUSTER_OPERATOR_MODE must be disabled or cluster")
    return value == "cluster"


def require_operator(config=None):
    if not operator_enabled(config):
        raise ValueError("General cluster operations are not enabled")


def _visible(value, label):
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError(f"{label} must be nonempty text")
    guarded = guard_model_input(value, "reviewed plan", 262144)
    if guarded["value"] != value or guarded["findings"]:
        raise ValueError(f"{label} contains credentials, hidden instructions or oversized content. Use existing Secret references; never paste credentials into chat or a plan.")
    return value


def _filename(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,100}", value) or value in {".", "..", "kubeconfig"}:
        raise ValueError("Generated file names must be flat relative names, never host paths")
    return value


def validate_argv(argv, files=None, *, read_only=False):
    files = files or {}
    if not isinstance(argv, list) or not 1 <= len(argv) <= 100 or any(not isinstance(a, str) or not a or len(a) > 16000 or "\x00" in a for a in argv):
        raise ValueError("Use a bounded kubectl argv array without the executable name")
    if argv[0] not in (READ_VERBS if read_only else VERBS):
        raise ValueError("Unsupported kubectl verb. Use a noninteractive built-in command, not a shell or plugin")
    if argv[0] == "auth" and (len(argv) < 2 or argv[1] not in {"can-i", "reconcile"}):
        raise ValueError("Only auth can-i or reviewed auth reconcile is supported")
    if argv[0] == "rollout" and (len(argv) < 2 or argv[1] not in {"status", "history", "restart", "undo", "pause", "resume"}):
        raise ValueError("Unknown rollout operation")
    if argv[0] == "set" and (len(argv) < 2 or argv[1] not in {"image", "env", "resources", "serviceaccount", "selector", "subject"}):
        raise ValueError("Unknown set operation")
    # Payload flags after exec's -- are a POD command, not kubectl identity flags.
    end = argv.index("--") if argv[0] in {"exec", "run", "debug"} and "--" in argv else len(argv)
    if argv[0] == "exec" and "--" not in argv:
        raise ValueError("exec must explicitly delimit its reviewed container command with --")
    for index, arg in enumerate(argv[:end]):
        flag = arg.split("=", 1)[0]
        if flag in IDENTITY_FLAGS | HOST_FLAGS or any(arg.startswith(f + "=") for f in IDENTITY_FLAGS | HOST_FLAGS) or arg.startswith("-s=") or arg.startswith("--dry-run") or flag in {"--help", "-h"}:
            raise ValueError("Plan cannot override credentials, cluster identity, host files or broker validation")
        if flag in {"--watch", "-w", "--watch-only", "--follow", "-f" if argv[0] == "logs" else "--follow", "--tty", "-t", "--stdin", "-i", "-it", "-ti", "--attach", "--interactive", "--edit"}:
            raise ValueError("Streaming, interactive or editable operations are not executable approval plans")
        if flag in {"-f", "--filename", "--patch-file", "--from-file", "--from-env-file", "--cert", "--key"}:
            value = arg.split("=", 1)[1] if "=" in arg else argv[index+1] if index+1 < end else ""
            value = value.split("=", 1)[-1] if flag == "--from-file" else value
            if value not in files:
                raise ValueError("File arguments must name an exact generated file included in this review")
        if flag in {"-o", "--output"}:
            value = arg.split("=", 1)[1] if "=" in arg else argv[index+1] if index+1 < end else ""
            if value not in {"json", "yaml", "name", "wide"}:
                raise ValueError("Output formats must be json, yaml, name or wide; host template files are forbidden")
        if arg.startswith("-o") and arg != "-o" and not arg.startswith("-o="):
            raise ValueError("Use separated output flags; host template files are forbidden")
        if arg.startswith("-k") or (arg.startswith("-f") and arg != "-f" and not arg.startswith("-f=")) or arg.startswith("-s") and flag not in {"--selector", "--since", "--since-time", "--sort-by", "--server-side"}:
            raise ValueError("Use explicit, separated flags; compact host/identity flags are forbidden")
        if read_only and arg.startswith("-") and flag not in SAFE_READ_FLAGS:
            raise ValueError("Read tools do not accept output templates, files, raw APIs, streaming or identity overrides")
    if argv[0] == "cp":
        operands = [a for a in argv[1:] if not a.startswith("-")]
        if len(operands) != 2 or sum(":" in a for a in operands) != 1 or next((a for a in operands if ":" not in a), "") not in files:
            raise ValueError("cp only uploads a reviewed generated file into a container; host downloads are unsupported")
    if read_only and argv[0] == "logs" and not any(a.startswith("--tail") for a in argv):
        argv = [*argv, "--tail=120", "--limit-bytes=24000"]
    return list(argv)


def canonical_plan(value, namespace="kravel-demo"):
    if not isinstance(namespace, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", namespace):
        raise ValueError("Plan's default namespace must be an explicit Kubernetes namespace")
    if not isinstance(value, dict) or set(value) - {"title", "summary", "files", "steps"}:
        raise ValueError("Plan requires title, summary, files and steps only")
    title = _visible(value.get("title"), "Title")
    summary = _visible(value.get("summary"), "Summary")
    files = value.get("files", {})
    if len(title) > 180 or len(summary) > 4000 or not isinstance(files, dict) or len(files) > 20:
        raise ValueError("Plan is too large")
    files = {_filename(name): _visible(content, "Generated file") for name, content in files.items()}
    # Secret objects are supported by kubectl but literal secret payloads must not
    # be sent to the LLM, browser history, MLflow or external Slack.
    import yaml
    for name, content in files.items():
        if name.endswith((".yaml", ".yml", ".json")):
            for obj in yaml.safe_load_all(content):
                if isinstance(obj, dict):
                    objects = obj.get("items", []) if obj.get("kind") in {"List", "SecretList"} else [obj]
                    if any(isinstance(o, dict) and (o.get("kind") == "Secret" or obj.get("kind") == "SecretList") and (o.get("data") or o.get("stringData")) for o in objects):
                        raise ValueError("Do not put literal Secret values in reviewed files; reference an existing Secret or provision it separately")
    raw_steps = value.get("steps")
    if not isinstance(raw_steps, list) or not 1 <= len(raw_steps) <= 20:
        raise ValueError("Plan needs between 1 and 20 ordered steps")
    steps = []
    for index, step in enumerate(raw_steps):
        if not isinstance(step, dict) or set(step) - {"label", "argv", "dependsOn"}:
            raise ValueError("Step fields must be label, argv and optional dependsOn")
        label = _visible(step.get("label"), "Step label")
        argv = validate_argv(step.get("argv"), files)
        _visible(stable_json(argv), "Command")
        deps = step.get("dependsOn", [])
        if len(label) > 180 or not isinstance(deps, list) or any(type(d) is not int or d < 1 or d > index for d in deps):
            raise ValueError("dependsOn must reference preceding step numbers (1-based)")
        steps.append({"label": label, "argv": argv, "dependsOn": sorted(set(deps))})
    plan = {"title": title, "summary": summary, "files": files, "steps": steps}
    if len(stable_json(plan).encode()) > 262144:
        raise ValueError("Plan exceeds 256 KiB")
    digest = hashlib.sha256(stable_json({"namespace": namespace, "plan": plan}).encode()).hexdigest()
    return {"id": "plan-" + digest[:24], "planHash": digest, "namespace": namespace, "resource": title, "plan": plan,
            "command": "\n".join(f"{i}. kubectl {shlex.join(s['argv'])}" for i, s in enumerate(steps, 1)), "eligible": True}


def safe_output(text):
    try:
        try:
            obj = json.loads(text)
        except ValueError:
            # Approved commands may request YAML. Decode structured resource
            # output before redacting; opaque base64 Secret values are not regex
            # detectable and must never reach Slack, traces or browser history.
            obj = yaml.safe_load(text)
            if not isinstance(obj, dict) or "kind" not in obj:
                raise ValueError("Not structured Kubernetes output")
        def clean(value, inherited=""):
            if isinstance(value, list): return [clean(v, inherited) for v in value]
            if isinstance(value, str): return guard_model_input(value, "kubectl field", 24000)["value"] if value.strip() else value
            if not isinstance(value, dict): return value
            value = copy.deepcopy(value)
            if inherited == "SecretList": value.setdefault("kind", "Secret")
            value = sanitize_object(value)
            return {k: "<redacted>" if re.fullmatch(r"(?i)password|passwd|token|api[_-]?key|client[_-]?secret|authorization", k) else clean(v, value.get("kind", "") if k == "items" else "") for k, v in value.items()}
        encoded = json.dumps(clean(obj), indent=2)
        # Redact decoded VALUES, not serialized key/value syntax: scanning the
        # whole JSON would corrupt token/password properties into invalid JSON.
        return encoded if len(encoded) <= 24000 else json.dumps({"notice": "Structured output was too large after safe decoding; request a smaller focused read."})
    except (ValueError, TypeError, yaml.YAMLError):
        if text.lstrip().startswith(("{", "[")) or re.search(r"(?m)^\s*(?:kind|apiVersion)\s*:", text):
            return "[Structured output omitted because it was incomplete or could not be safely decoded. Request a smaller, focused read.]"
    return guard_model_input(text or "(no output)", "kubectl output", 24000)["value"]


class KubectlExecutor:
    def __init__(self, kube_config, binary="/usr/local/bin/kubectl", runner=None):
        self.kube_config, self.binary, self.runner = kube_config, binary, runner
        self._supports = {}

    def run(self, argv, files=None, namespace="kravel-demo", timeout=90):
        if self.runner:
            return self.runner(list(argv), files or {}, namespace, timeout)
        with tempfile.TemporaryDirectory(prefix="kravel-plan-") as folder:
            workspace = Path(folder)
            for name, source in (files or {}).items():
                (workspace / _filename(name)).write_text(source, encoding="utf-8")
            # The model never chooses the identity or sees its token. kubectl
            # loads tokenFile directly, even when approval waits across rotation.
            kc = self.kube_config
            kubeconfig = {"apiVersion": "v1", "kind": "Config", "clusters": [{"name": "fixed", "cluster": {"server": f"https://{kc.host}:{kc.port}", "certificate-authority": kc.ca_path}}], "users": [{"name": "executor", "user": {"tokenFile": kc.token_path}}], "contexts": [{"name": "fixed", "context": {"cluster": "fixed", "user": "executor", "namespace": namespace}}], "current-context": "fixed"}
            identity = workspace / ".broker-identity"
            identity.write_text(json.dumps(kubeconfig), encoding="utf-8")
            os.chmod(identity, 0o600)
            env = {**os.environ, "KUBECONFIG": str(identity), "HOME": str(workspace)}
            # Files bound RAM usage; subprocess does not evaluate shell syntax.
            with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as error:
                process = subprocess.Popen([self.binary, "--cache-dir=" + str(workspace / ".cache"), "--request-timeout=30s", *argv], cwd=workspace, env=env, stdin=subprocess.DEVNULL, stdout=output, stderr=error, shell=False)
                try:
                    code = process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait()
                    # A timed-out request may have reached the API. Never retry.
                    raise RuntimeError("kubectl timed out; inspect the cluster before drafting a new plan. Execution was not retried.") from None
                output.seek(0); error.seek(0)
                stdout = output.read(24001).decode("utf-8", "replace")
                stderr = error.read(24001).decode("utf-8", "replace")
                return {"exitCode": code, "stdout": safe_output(stdout), "stderr": safe_output(stderr), "outputTruncated": len(stdout) > 24000 or len(stderr) > 24000}

    @staticmethod
    def is_read(argv):
        return argv[0] in READ_VERBS | {"describe", "wait"} or argv[:2] in (["rollout", "status"], ["rollout", "history"], ["auth", "can-i"])

    def supports_dry_run(self, argv):
        if self.is_read(argv) or argv[0] in {"exec", "cp", "diff"}: return False
        prefix = tuple(argv[:2] if argv[0] in {"create", "set", "rollout", "auth"} and len(argv) > 1 and not argv[1].startswith("-") else argv[:1])
        if prefix not in self._supports:
            result = self.run([*prefix, "--help"], timeout=15)
            self._supports[prefix] = result["exitCode"] == 0 and "--dry-run" in result["stdout"]
        return self._supports[prefix]

    @staticmethod
    def target_query(argv):
        """Freeze declarative/named targets; selectors/exec/node-wide actions
        remain explicit command-level approvals, not transactional promises.
        """
        query = None
        for index, arg in enumerate(argv):
            if arg.split("=", 1)[0] in {"-f", "--filename"}:
                file = arg.split("=", 1)[1] if "=" in arg else argv[index+1]
                query = ["get", "-f", file]
                break
        if query is None and argv[0] in {"patch", "scale", "delete", "label", "annotate"}:
            operands = []
            for arg in argv[1:]:
                if arg.startswith("-") or "=" in arg: break
                operands.append(arg)
            if operands and not any(a in {"all", "--all"} for a in argv) and not any(a.startswith(("-l", "--selector", "--field-selector")) for a in argv):
                query = ["get", *operands]
        if query:
            for index, arg in enumerate(argv):
                if arg in {"-n", "--namespace"}: query += [arg, argv[index+1]]
                elif arg.startswith("--namespace="): query += [arg]
            query += ["--ignore-not-found", "-o", "json"]
        return query

    @staticmethod
    def target_fingerprint(result):
        if result["exitCode"] or result.get("outputTruncated"): return None
        text = result["stdout"]
        if not text.strip() or text == "(no output)": objects = []
        else:
            try: obj = json.loads(text)
            except ValueError: return None
            objects = obj.get("items", []) if obj.get("kind", "").endswith("List") or obj.get("kind") == "List" else [obj]
        normalized = []
        for obj in objects:
            obj = copy.deepcopy(obj); obj.pop("status", None)
            metadata = obj.get("metadata", {})
            for key in ("managedFields", "creationTimestamp"): metadata.pop(key, None)
            if obj.get("kind") != "Secret": metadata.pop("resourceVersion", None)
            normalized.append(obj)
        return hashlib.sha256(stable_json(sorted(normalized, key=stable_json)).encode()).hexdigest()

    def preview(self, draft):
        rows = []
        for index, step in enumerate(draft["plan"]["steps"], 1):
            argv = step["argv"]
            row = {"step": index, "label": step["label"], "command": "kubectl " + shlex.join(argv), "planHash": draft["planHash"]}
            query = self.target_query(argv) if not self.is_read(argv) else None
            fingerprint = self.target_fingerprint(self.run(query, draft["plan"]["files"], draft["namespace"])) if query else None
            if fingerprint:
                row.update(targetQuery=query, beforeFingerprint=fingerprint, targetStability="Captured targets will be rechecked before the first change.")
            else:
                row["targetStability"] = "Command-level approval only: exact resource targets could not be frozen. Cluster operations are not transactional."
            if self.is_read(argv):
                row.update(validation="read_only", note="Read/check executes after approval in the ordered plan.")
            elif self.supports_dry_run(argv):
                result = self.run([*argv, "--dry-run=server"], draft["plan"]["files"], draft["namespace"])
                if result["exitCode"] and not step["dependsOn"]:
                    raise ValueError(f"Step {index} failed Kubernetes server dry-run: {result['stderr']}")
                row.update(validation="deferred" if result["exitCode"] else "passed", output=result, note="Must pass server dry-run after preceding dependencies execute." if result["exitCode"] else "Kubernetes server dry-run passed; no change persisted.")
            else:
                row.update(validation="not_available", note="This command has no supported server dry-run. It has NOT been executed. Approval explicitly accepts this risk.")
            rows.append(row)
        # Persist exact canonical files/argv, not a truncated/redacted lookalike.
        rows[0]["reviewedPlan"] = draft
        return rows

    def revalidate(self, draft, reviewed):
        for row in reviewed:
            if row.get("beforeFingerprint"):
                result = self.run(row["targetQuery"], draft["plan"]["files"], draft["namespace"])
                if self.target_fingerprint(result) != row["beforeFingerprint"]:
                    raise ValueError("A reviewed target changed, appeared or disappeared. Prepare a new plan and obtain new approval.")

    def read(self, argv, namespace):
        argv = validate_argv(argv, read_only=True)
        if argv[0] == "get": argv += ["-o", "json"]
        return self.run(argv, namespace=namespace, timeout=30)

    def execute(self, draft, progress, tracer, completed, actor="approved-human"):
        for index, step in enumerate(draft["plan"]["steps"], 1):
            argv = step["argv"]
            with progress.step(f"apply_{index}", step["label"], {"command": "kubectl " + shlex.join(argv)}), tracer.span("cluster.operation", "TOOL", {"step": index, "read_only": self.is_read(argv)}) as span:
                span.set_content_inputs({"argv": argv, "plan_hash": draft["planHash"]})
                if self.supports_dry_run(argv):
                    dry = self.run([*argv, "--dry-run=server"], draft["plan"]["files"], draft["namespace"])
                    if dry["exitCode"]: raise ValueError(f"Step {index} revalidation failed: {dry['stderr']}")
                result = self.run(argv, draft["plan"]["files"], draft["namespace"], timeout=120)
                span.set_content_outputs(result)
                if result["exitCode"]: raise ValueError(f"Step {index} failed: {result['stderr']}")
                completed.append({"step": index, "label": step["label"], "readOnly": self.is_read(argv), **result})
                progress.store.record("approval-broker", "cluster.operation", actor=actor, resource=draft["resource"], outcome="success", trace_id=tracer.trace_id, details={"planId": draft["id"], "step": index, "command": "kubectl " + shlex.join(argv), "exitCode": result["exitCode"]})


CLUSTER_TOOLS = [
    {"type": "function", "function": {"name": "kubectl_read", "description": "Read arbitrary kinds, CRDs or namespaces with built-in kubectl get/logs/top/explain/api-resources/api-versions/version. argv excludes kubectl. get always returns redacted JSON; no secrets, raw endpoints, host files or templates. Never execs or mutates.", "parameters": {"type": "object", "properties": {"argv": {"type": "array", "items": {"type": "string"}}}, "required": ["argv"]}}},
    {"type": "function", "function": {"name": "draft_cluster_plan", "description": "Generate a complete general Kubernetes operation plan: arbitrary kinds/namespaces/CRDs/RBAC/storage, YAML and application code files, ordered kubectl argv commands, then verification commands. Nothing executes. Existing-resource changes need live inspection. New resource names may be proposed, not claimed observed. No credentials in files. dependsOn contains 1-based preceding step numbers when new namespaces/CRDs/resources are prerequisites. Files are flat relative names; -f must reference one of them. Explicit exec payload uses --. Request human approval with the returned planId.", "parameters": {"type": "object", "properties": {"title": {"type": "string"}, "summary": {"type": "string"}, "files": {"type": "object", "additionalProperties": {"type": "string"}}, "steps": {"type": "array", "items": {"type": "object", "properties": {"label": {"type": "string"}, "argv": {"type": "array", "items": {"type": "string"}}, "dependsOn": {"type": "array", "items": {"type": "integer"}}}, "required": ["label", "argv"]}}}, "required": ["title", "summary", "files", "steps"]}}},
]
