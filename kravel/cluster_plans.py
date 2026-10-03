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
        raise ValueError("Generated file names must be flat relative names, never host paths. Use 'settings.yaml', not 'manifests/settings.yaml'; use that same name in files and argv. Resubmit the complete corrected draft_cluster_plan, not an error JSON answer.")
    return value


class PlanValidationError(ValueError):
    def __init__(self, row, result):
        diagnostic = safe_output(result.get("stderr") or result.get("stdout") or "Kubernetes rejected the change")
        # Kubernetes sometimes prints an entire rejected patch before the cause.
        # Keep that bounded output in the trace/details, not in the headline.
        reason = diagnostic.strip().splitlines()[-1]
        if len(reason) > 650:
            reason = reason.rsplit('\": ', 1)[-1]
            if len(reason) > 650: reason = "…" + reason[-650:]
        self.details = {**row, "validation": "failed", "output": {**result, "stderr": diagnostic}, "note": "Validation failed. No approval request was created and no change was applied."}
        super().__init__(f"Step {row['step']} failed Kubernetes server dry-run: {reason}")


class MissingRepairInput(ValueError):
    pass


def image_update_plan(args, namespace):
    """A minimal reviewed image change, never regenerated placement or code."""
    if not isinstance(args, dict) or set(args) - {"kind", "name", "container", "image", "rationale", "namespace"} or not {"kind", "name", "container", "image", "rationale"}.issubset(args):
        raise ValueError("Image update requires kind, name, container, verified image and rationale")
    if args["kind"] not in {"deployment", "daemonset", "statefulset", "pod"}:
        raise ValueError("Use deployment, daemonset, statefulset or pod for a minimal image update")
    ns = args.get("namespace") or namespace
    for value, label, limit in ((args["name"], "name", 253), (args["container"], "container", 63), (ns, "namespace", 63)):
        if not isinstance(value, str) or len(value) > limit or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", value): raise ValueError(f"Image update {label} must be a literal Kubernetes name")
    image = _visible(args["image"], "replacement image")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./:@-]{0,255}", image): raise ValueError("Use one exact image reference, not shell syntax or a URL")
    rationale = _visible(args["rationale"], "image update rationale")
    target = args["kind"] + "/" + args["name"]
    check = ["wait", "--for=condition=Ready", target] if args["kind"] == "pod" else ["rollout", "status", target]
    return canonical_plan({"title": "Update image · " + target, "summary": rationale + " Only the named container image changes; placement, configuration and other fields are preserved. Published metadata is not proof of runtime compatibility. Approved rollout/readiness checks must confirm the result.", "files": {}, "steps": [{"label": "Update only the container image", "argv": ["set", "image", target, args["container"] + "=" + image, "-n", ns]}, {"label": "Verify rollout/readiness", "argv": [*check, "--timeout=90s", "-n", ns]}]}, namespace)


def verify_replacement_images(plan, evidence, question, namespace):
    """An existing workload is not a blank canvas for guessed replacement tags.

    Operator-supplied exact references and fetched documentation can support a
    proposal; a current broken tag, runbook example, or model assertion cannot.
    This does NOT claim registry availability or application compatibility.
    """
    observed = {}
    references = []
    verified_images = set()
    from .image_research import normalize_reference
    aliases = {"deployment": "Deployment", "deployments": "Deployment", "deploy": "Deployment", "daemonset": "DaemonSet", "daemonsets": "DaemonSet", "ds": "DaemonSet", "statefulset": "StatefulSet", "statefulsets": "StatefulSet", "sts": "StatefulSet", "pod": "Pod", "pods": "Pod"}
    collection_kinds = {"Container states & readiness": "Pod", "Rollout generations & conditions": "Deployment", "Per-node workload ownership & rollout": "DaemonSet", "Stateful workload revisions": "StatefulSet"}
    def walk(value, inherited=""):
        if isinstance(value, list):
            for item in value: walk(item, inherited)
        elif isinstance(value, dict):
            kind = value.get("kind") or inherited
            if kind in set(aliases.values()) and value.get("metadata", {}).get("name"):
                meta = value["metadata"]
                observed[(kind, meta.get("namespace") or namespace, meta["name"])] = value
            for key, item in value.items(): walk(item, kind.removesuffix("List") if key == "items" else "")
    for item in evidence:
        if item.get("status") != "observed": continue
        body = item.get("body", {})
        if item.get("sourceType", "live") == "live":
            walk(body, collection_kinds.get(item.get("label"), ""))
            if isinstance(body, dict) and body.get("guardedExcerpt"):
                try: walk(json.loads(body["guardedExcerpt"]))
                except ValueError: pass
        elif item.get("label") in {"Focused read · search_image_tags", "Focused read · inspect_image_tag", "Published image candidates"}:
            try: registry = json.loads(body["guardedExcerpt"]) if body.get("guardedExcerpt") else body
            except (ValueError, TypeError): continue
            if registry.get("status") != "verified": continue
            for candidate in registry.get("candidates", []) + ([registry["candidate"]] if isinstance(registry.get("candidate"), dict) else []):
                if candidate.get("verified") is True:
                    verified_images.update(normalize_reference(candidate[key]) for key in ("reference", "digestReference") if candidate.get(key))
        elif item.get("label") == "Focused read · fetch_reference":
            references.append(stable_json(body))
    support = question + "\n" + "\n".join(references)
    def check(kind, name, ns, proposed):
        old = observed.get((kind, ns, name))
        if not old: return
        spec = old.get("spec", {})
        pod_spec = spec if kind == "Pod" else spec.get("template", {}).get("spec", {})
        prior = {c.get("image") for key in ("containers", "initContainers") for c in pod_spec.get(key, [])}
        for image in proposed:
            if image in prior or normalize_reference(image) in verified_images or re.search(r"(?<![\w./:-])" + re.escape(image) + r"(?![\w./:-])", support): continue
            raise MissingRepairInput(f"I need a verified replacement image for {kind}/{name}. The proposed '{image}' is not established by your request, public registry research or fetched documentation. Use search_image_tags/inspect_image_tag and vendor documentation to find a suitable published version, then resubmit a minimal plan. Ask the operator only if application-specific intent remains unknown. No approval request was created and no change was applied.")
    def images(value):
        if isinstance(value, list): return [image for item in value for image in images(item)]
        if not isinstance(value, dict): return []
        return [v for k, v in value.items() if k == "image" and isinstance(v, str)] + [image for k, v in value.items() if k != "image" for image in images(v)]
    for source in plan["files"].values():
        try:
            for obj in yaml.safe_load_all(source):
                if not isinstance(obj, dict): continue
                for manifest in obj.get("items", []) if obj.get("kind") == "List" else [obj]:
                    if isinstance(manifest, dict):
                        meta = manifest.get("metadata", {})
                        check(manifest.get("kind"), meta.get("name"), meta.get("namespace") or namespace, images(manifest.get("spec", {})))
        except yaml.YAMLError:
            continue  # Application code files are not Kubernetes manifests.
    for step in plan["steps"]:
        argv = step["argv"]
        ns = namespace
        for i, arg in enumerate(argv):
            if arg in {"-n", "--namespace"} and i + 1 < len(argv): ns = argv[i + 1]
            elif arg.startswith("--namespace="): ns = arg.split("=", 1)[1]
        offset = 2 if argv[:2] == ["set", "image"] else 1 if argv[0] == "patch" else None
        if offset is None or len(argv) <= offset: continue
        target = argv[offset].split("/", 1)
        kind, name = aliases.get(target[0]), target[1] if len(target) > 1 else argv[offset + 1] if len(argv) > offset + 1 else ""
        if offset == 2:
            proposed = [arg.split("=", 1)[1] for arg in argv[offset + 1:] if not arg.startswith("-") and "=" in arg]
        else:
            payload = next((arg.split("=", 1)[1] if "=" in arg else argv[i + 1] if i + 1 < len(argv) else "{}" for i, arg in enumerate(argv) if arg.split("=", 1)[0] in {"-p", "--patch"}), "{}")
            try:
                patch = json.loads(payload)
                proposed = images(patch) + [op["value"] for op in patch if isinstance(op, dict) and op.get("path", "").endswith("/image") and isinstance(op.get("value"), str)] if isinstance(patch, list) else images(patch)
            except ValueError: continue
        check(kind, name, ns, proposed)


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
        if query is None and (argv[0] in {"patch", "scale", "delete", "label", "annotate"} or argv[:2] in (["set", "image"], ["set", "resources"], ["set", "env"], ["rollout", "restart"], ["rollout", "undo"])):
            operands = []
            for arg in argv[2:] if argv[0] in {"set", "rollout"} else argv[1:]:
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
                    raise PlanValidationError(row, result)
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
    {"type": "function", "function": {"name": "draft_image_update", "description": "PREFERRED for an existing workload's missing/wrong image tag. Given a researched exact image, generate a minimal set-image + bounded rollout/readiness plan. Reads the actual named container, verifies replacement provenance and preserves ALL placement, selectors, tolerations, config and other fields. No YAML regeneration, no writes or approval. Use a published candidate already supplied in evidence, not guessed tags. Returned planId is staged for separate human review.", "parameters": {"type": "object", "properties": {"kind": {"type": "string", "enum": ["deployment", "daemonset", "statefulset", "pod"]}, "name": {"type": "string"}, "container": {"type": "string"}, "image": {"type": "string"}, "rationale": {"type": "string"}, "namespace": {"type": "string"}}, "required": ["kind", "name", "container", "image", "rationale"]}}},
    {"type": "function", "function": {"name": "draft_cluster_plan", "description": "Generate a complete general Kubernetes operation plan: arbitrary kinds/namespaces/CRDs/RBAC/storage, YAML and application code files, ordered kubectl argv commands, then verification commands. Nothing executes. Existing-resource changes need live inspection. New resource names may be proposed, not claimed observed. No credentials in files. dependsOn contains 1-based preceding step numbers when new namespaces/CRDs/resources are prerequisites. Files are flat relative names; -f must reference one of them. Explicit exec payload uses --. Request human approval with the returned planId.", "parameters": {"type": "object", "properties": {"title": {"type": "string"}, "summary": {"type": "string"}, "files": {"type": "object", "additionalProperties": {"type": "string"}}, "steps": {"type": "array", "items": {"type": "object", "properties": {"label": {"type": "string"}, "argv": {"type": "array", "items": {"type": "string"}}, "dependsOn": {"type": "array", "items": {"type": "integer"}}}, "required": ["label", "argv"]}}}, "required": ["title", "summary", "files", "steps"]}}},
]
