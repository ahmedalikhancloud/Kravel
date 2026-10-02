"""Curated, versioned runbooks. Retrieval relevance is NOT diagnosis or authority."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from pathlib import Path

VERSION = "2026-10-02.2"
DOCS = "https://kubernetes.io/docs/"
SOURCES = {
    "workload": DOCS + "tasks/debug/debug-application/debug-running-pod/",
    "scheduling": DOCS + "concepts/scheduling-eviction/assign-pod-node/",
    "storage": DOCS + "concepts/storage/persistent-volumes/",
    "network": DOCS + "tasks/debug/debug-application/debug-service/",
    "dns": DOCS + "tasks/administer-cluster/dns-debugging-resolution/",
    "control": DOCS + "tasks/debug/debug-cluster/",
    "admission": DOCS + "concepts/cluster-administration/admission-webhooks-good-practices/",
    "disruption": DOCS + "tasks/run-application/configure-pdb/",
    "autoscaling": DOCS + "tasks/run-application/horizontal-pod-autoscale/",
    "image": DOCS + "concepts/containers/images/",
}

# (id, title, category, search signals, evidence needed, repair guidance,
#  verification, restorable fields). Empty fields = operator-led, never executable.
COMMON = [
    ("oomkilled", "Container OOMKilled", "workload", "OOMKilled exit 137 memory limit", "Current termination state, limits, memory measurements and previous logs; distinguish node eviction from cgroup OOM.", "Restore an operator-tested memory configuration after checking node capacity; investigate leaks separately.", "Updated owned Pods remain Ready; confirm memory headroom over a representative workload.", ["resources"]),
    ("image_not_found", "Missing image or invalid tag", "image", "ImagePullBackOff ErrImagePull manifest unknown not found", "Pull Event with registry response, exact image and container name.", "Restore a verified image tag or digest for the same application; do not substitute an unrelated healthy container.", "New owned Pods use the intended image and pass application readiness.", ["image"]),
    ("registry_auth", "Registry authentication failure", "image", "unauthorized authentication required denied imagePullSecrets", "Pull Events and imagePullSecret references, without reading secret values.", "Have the credential owner refresh registry credentials and check namespace and ServiceAccount references.", "Authorized image pull succeeds; validate that credentials were not logged.", []),
    ("registry_connectivity", "Registry DNS, TLS or reachability failure", "image", "pull image timeout x509 no such host connection refused", "Exact pull error, registry hostname and node-side DNS/TLS/network evidence from an operator.", "Correct registry trust or connectivity at its owner; retries alone do not repair the underlying problem.", "Pull succeeds on every affected node and certificate validation remains enabled.", []),
    ("crash_command", "CrashLoopBackOff from startup configuration", "workload", "CrashLoopBackOff exit command args executable not found", "Current restart state, previous logs, entrypoint and configuration references.", "Restore the application's reviewed startup command or arguments; sleeping forever is not a production fix.", "Updated owned Pods pass readiness and restart counts remain stable.", ["command", "args"]),
    ("bad_configmap", "Invalid ConfigMap values", "workload", "ConfigMap invalid configuration parse error MODE config", "Referenced ConfigMap, application validation error and independently reviewed healthy values.", "Restore only reviewed non-sensitive keys; separately approve consumer restart if reload is not automatic.", "Consumers load the intended configuration and application checks pass; matching data alone is insufficient.", ["data"]),
    ("missing_configmap", "Missing ConfigMap or key", "workload", "CreateContainerConfigError configmap not found key missing", "Pod Events and volume/env references; inspect the named ConfigMap if readable.", "Restore missing configuration through the deployment source of truth or correct a reviewed reference.", "Container starts and validates its configuration; confirm all consumers.", []),
    ("missing_secret", "Missing Secret reference", "workload", "CreateContainerConfigError secret not found key", "Events and reference names only; secret content is outside Karl's access.", "Ask the secret owner to restore the correct namespace/key via the secret-management workflow.", "Affected containers start without exposing credential values.", []),
    ("init_failure", "Init container failure", "workload", "Init CrashLoopBackOff initContainerStatuses dependency migration", "Init container state, bounded previous logs and dependency references.", "Resolve the failing initialization dependency; review migrations for reversibility before retrying.", "Init containers complete successfully and main containers become Ready.", []),
    ("liveness_failure", "Liveness probe restart loop", "workload", "Unhealthy Liveness probe failed killing probe timeout", "Probe Events correlated with restart times, application logs and configured thresholds.", "Restore a tested liveness probe after distinguishing real deadlock from slow startup; do not simply remove it.", "Probe succeeds under load and restart count stabilizes.", ["livenessProbe"]),
    ("readiness_failure", "Readiness probe failure", "workload", "Unhealthy Readiness probe failed endpoints unavailable", "Current Ready condition, readiness Events and application dependency health.", "Fix the dependency or restore a tested readiness configuration; do not route traffic to an unhealthy Pod.", "Owned Pods become Ready and EndpointSlices contain expected ready targets.", ["readinessProbe"]),
    ("startup_failure", "Startup probe budget too small", "workload", "Startup probe failed startupProbe slow boot", "Startup Events, actual boot durations and application initialization logs.", "Restore a tested startup budget with measured startup time; retain eventual failure detection.", "Cold starts complete within the budget and liveness takes over correctly.", ["startupProbe"]),
    ("cpu_request", "Unschedulable CPU requests", "scheduling", "FailedScheduling Insufficient cpu Pending requests", "Scheduling Events, requests and node allocatable capacity; limits are not usage.", "Restore tested requests only when capacity permits, or have the operator add capacity.", "Pod schedules without starving neighbors and meets latency objectives.", ["resources"]),
    ("memory_request", "Unschedulable memory requests", "scheduling", "FailedScheduling Insufficient memory Pending requests", "Scheduling Events, memory requests and node allocatable headroom.", "Restore tested requests or add capacity; lowering requests must not hide actual memory demand.", "Pod schedules and measured working set has safe headroom.", ["resources"]),
    ("node_selector", "Node selector or affinity mismatch", "scheduling", "FailedScheduling node selector affinity didn't match", "Placement Events, workload constraints and permitted node labels.", "Restore reviewed placement constraints; preserve isolation and hardware requirements.", "Pods schedule onto intended eligible nodes.", ["nodeSelector", "affinity"]),
    ("taint_untolerated", "Untolerated node taint", "scheduling", "FailedScheduling untolerated taint tolerations", "Scheduling Events, intended placement and taint/toleration configuration.", "Restore a specifically approved toleration only if the workload belongs on those nodes; do not tolerate all taints.", "Pods schedule on intended nodes without bypassing isolation policy.", ["tolerations"]),
    ("pvc_pending", "PersistentVolumeClaim Pending", "storage", "PVC Pending unbound immediate PersistentVolumeClaims provision", "PVC status/Events, StorageClass and provisioner evidence; cluster reads may require an operator.", "Repair provisioning or restore the intended storage class in the source of truth; never delete data to clear Pending.", "Claim binds to the intended volume and the workload mounts its existing data.", []),
    ("failed_mount", "Volume mount failure", "storage", "FailedMount MountVolume timeout attach filesystem", "Pod/PVC Events and operator-provided CSI/node logs.", "Repair the proven CSI, filesystem or dependency failure using the storage owner's runbook.", "Mount succeeds and the application validates data integrity.", []),
    ("service_selector", "Service selector mismatch", "network", "Service selector no matching pods empty endpoints", "Service selector, full Pod label coverage and EndpointSlices.", "Restore a reviewed selector that points to the intended workload, not merely any Ready Pod.", "Ready endpoints target the intended Pods; operator validates end-to-end traffic.", ["selector"]),
    ("service_target_port", "Incorrect Service targetPort", "network", "Service targetPort connection refused port mismatch", "Service ports, intended application listener and endpoint port mapping.", "Restore the reviewed Service port mapping; a declared containerPort is not proof of a listener.", "Endpoint port is correct and operator confirms a real request succeeds.", ["ports"]),
    ("no_ready_endpoints", "Service without Ready endpoints", "network", "EndpointSlice ready false no ready endpoints readiness", "Matching Pods, Ready conditions and EndpointSlice target UIDs.", "Repair workload readiness or endpoint configuration; do not force readiness flags.", "Expected target UIDs become Ready and traffic is tested independently.", []),
    ("dns_config", "Workload DNS configuration error", "dns", "dnsPolicy dnsConfig nameserver search NXDOMAIN", "Pod DNS configuration and operator-provided lookup results; Karl does not exec.", "Restore reviewed Pod DNS settings and confirm that custom resolvers are intended.", "Lookups work from the affected workload and DNS policy remains correct.", ["dnsPolicy", "dnsConfig"]),
    ("ingress_backend", "Ingress backend routing failure", "network", "Ingress 502 503 backend service port", "Ingress backend references, controller Events, Service endpoints and listener mapping.", "Repair the proven route or backend readiness in the ingress deployment workflow.", "A request through the real ingress reaches the intended application.", []),
    ("job_backoff", "Job retry budget exhausted", "workload", "Job BackoffLimitExceeded DeadlineExceeded failed", "Job conditions, failed Pod logs and task idempotency/side effects.", "Fix task inputs or dependencies; create a new run only after reviewing whether retries duplicate writes.", "A reviewed task run completes and its business result is validated.", []),
    ("rollout_stalled", "Deployment rollout stalled", "workload", "ProgressDeadlineExceeded rollout ReplicaFailure unavailable", "Controller generation, new ReplicaSet ownership, Pod Events and readiness.", "Repair the new template's actual failure or restore a reviewed baseline through the delivery workflow.", "Updated generation and owned current Pods are Ready; old replicas alone are not recovery.", []),
]

DIFFICULT = [
    ("legacy_image_format", "Obsolete image manifest format", "image", "ImagePullBackOff schema1 prettyjws manifest v1 media type no longer supported containerd", "Exact pull Event reporting unsupported schema1/media type, current image and container.", "Use an operator-verified equivalent image rebuilt as OCI/schema2; preserve the application's role and configuration.", "Runtime pulls the replacement and updated owned Pods pass application readiness.", ["image"]),
    ("memory_leak", "Slow memory leak behind repeated OOM", "workload", "OOMKilled rising working set leak heap memory", "Long-window memory trend, allocation profile and repeated restarts; a single OOM is not proof of a leak.", "Fix the application leak with the application owner; use only a tested temporary capacity mitigation.", "Memory plateaus over a representative window and service objectives recover.", []),
    ("cpu_throttling", "CPU throttling without obvious failures", "workload", "throttled periods cpu cfs latency p99", "CPU throttling metrics correlated with latency, requests/limits and node load.", "Restore a load-tested CPU configuration or optimize the workload; first exclude dependency latency.", "Throttling and tail latency fall under equivalent load without starving neighbors.", ["resources"]),
    ("ephemeral_storage_eviction", "Ephemeral storage or inode eviction", "control", "Evicted DiskPressure ephemeral-storage inode logs", "Eviction message, ephemeral usage and operator node filesystem evidence.", "Repair log/cache growth or node storage capacity; preserve forensic evidence and avoid blind deletion.", "DiskPressure clears and workloads remain stable under representative disk usage.", []),
    ("coredns_saturation", "Intermittent CoreDNS saturation", "dns", "CoreDNS SERVFAIL timeout saturation DNS latency", "DNS latency/error metrics, CoreDNS logs and request-rate correlation supplied by an operator.", "Address proven capacity, upstream resolver or query amplification issues with the DNS owner.", "Lookup error rate and tail latency recover from affected workloads.", []),
    ("dns_ndots", "DNS search-path amplification", "dns", "ndots search amplification external hostname DNS latency", "Actual query sequence, search domains, ndots setting and resolver load.", "Restore tested workload resolver settings or use intended fully qualified names; test internal service resolution.", "External lookup amplification falls and internal names still resolve.", ["dnsConfig"]),
    ("conntrack_exhaustion", "Node conntrack exhaustion", "network", "conntrack table full intermittent drops SYN timeout", "Operator-provided conntrack utilization/drop counters and node traffic correlation.", "Reduce connection churn or tune validated node capacity with the network owner; no agent node shell.", "Drops stop and connection success recovers across affected nodes.", []),
    ("cni_mtu", "CNI MTU mismatch or fragmentation", "network", "MTU fragmentation PMTU large packet TLS hangs CNI", "Operator packet/path measurements, overlay MTU and size-dependent failure evidence.", "Correct the proven MTU configuration through the CNI owner's rollout procedure.", "Large and small requests succeed across node and external paths.", []),
    ("networkpolicy_asymmetric", "Asymmetric NetworkPolicy denial", "network", "NetworkPolicy ingress egress default deny one way timeout", "Applicable policy selectors, both directions and operator-provided connectivity tests.", "Review a narrow intended traffic rule with the security owner; never remove default-deny to make tests pass.", "Required flows succeed and explicitly forbidden flows remain blocked.", []),
    ("kube_proxy_stale", "Stale node Service dataplane", "network", "kube-proxy iptables IPVS stale service node specific", "Correct API endpoints plus operator dataplane rules and node-specific failure evidence.", "Repair the proven node dataplane component via its owner; do not flush all network rules.", "Service traffic succeeds on every affected node without weakening isolation.", []),
    ("csi_multi_attach", "CSI multi-attach conflict", "storage", "Multi-Attach VolumeAttachment ReadWriteOnce attached node", "Volume attachment identity, access mode, old owner/node status and CSI evidence.", "Storage owner must establish fencing and safe detach before reassignment; never force-detach a live writer.", "Exactly the intended writer mounts the volume and data integrity is checked.", []),
    ("volume_node_affinity", "Volume zone or node affinity conflict", "storage", "volume node affinity conflict zone topology PV", "Scheduling Events, PV topology, PVC binding and workload placement constraints.", "Align placement with existing data locality or plan a storage migration; do not mutate PV identity blindly.", "Workload schedules in a valid topology and mounts the correct data.", []),
    ("volume_permissions", "Volume UID/GID permission mismatch", "storage", "permission denied fsGroup runAsUser read-only filesystem", "Application filesystem errors, volume ownership and intended non-root identity.", "Use the storage/application owner's least-privilege ownership procedure; never resolve by making the container privileged.", "Required paths work as the intended non-root identity with no broader permissions.", []),
    ("statefulset_quorum", "StatefulSet ordered rollout or quorum deadlock", "storage", "StatefulSet quorum OrderedReady partition stuck rollout", "Ordinal readiness, revisions, update strategy and application replication/quorum status.", "Application owner follows a quorum-aware recovery plan; no generic force-delete or bulk restart.", "Application quorum/data health and intended StatefulSet revision are both restored.", []),
    ("admission_webhook_timeout", "Admission webhook dependency deadlock", "admission", "failed calling webhook context deadline exceeded timeout admission", "Exact API rejection, webhook dependency health and operator control-plane evidence.", "Restore the webhook's availability with its owner; do not bypass admission by changing failurePolicy to Ignore.", "Reviewed requests pass while the required admission checks remain enforced.", []),
    ("admission_webhook_ca", "Webhook CA or certificate mismatch", "admission", "webhook x509 certificate unknown authority caBundle", "Exact TLS rejection and owner-provided certificate/CA metadata, never private keys.", "Certificate owner repairs the trust chain and rotation workflow.", "Admission TLS validates and both valid/invalid requests are handled correctly.", []),
    ("api_throttling", "API server or client throttling", "control", "429 TooManyRequests client rate limiter APF latency", "API/client latency and rejection metrics correlated with request rate and controllers.", "Find the noisy client or capacity bottleneck; tune only with control-plane owners.", "Rejection and latency rates recover without reducing authorization or audit coverage.", []),
    ("etcd_latency", "etcd storage latency or quorum loss", "control", "etcd fsync wal latency leader quorum request timed out", "Operator etcd quorum, disk latency, leader-change and control-plane metrics.", "Control-plane owner follows the documented quorum/backup procedure; no generic restore, compaction or member deletion.", "Quorum, durable writes and API service objectives recover; backups are validated.", []),
    ("cert_rotation", "Kubelet or control-plane certificate rotation failure", "control", "certificate expired kubelet CSR rotation Unauthorized", "Certificate expiry/CSR metadata and component errors supplied by an operator.", "PKI owner renews the appropriate certificate and validates bootstrap/rotation policy.", "Component authenticates normally; certificate validation is not disabled.", []),
    ("clock_skew", "Clock skew causing auth or lease failures", "control", "not yet valid token expired clock skew lease NTP", "Operator clock-offset measurements correlated with certificate/token/lease errors.", "Node owner corrects time synchronization before changing authentication settings.", "Clocks align and affected authentication/lease operations succeed.", []),
    ("pdb_deadlock", "Disruption budget blocks safe maintenance", "disruption", "PodDisruptionBudget Cannot evict violates disruption budget allowedDisruptions", "PDB selection, current unhealthy replicas and workload availability requirements.", "Recover unhealthy capacity first or review the maintenance budget with its owner; do not force drain.", "Allowed disruptions support maintenance while application availability is preserved.", []),
    ("hpa_missing_metrics", "HPA missing or misleading metrics", "autoscaling", "FailedGetResourceMetric ScalingActive Unknown HPA metrics", "HPA conditions, metric availability, workload requests and scaling history.", "Repair metrics delivery or reviewed scaling inputs with the autoscaling owner.", "Metrics are current and desired replicas respond correctly under controlled load.", []),
    ("topology_spread", "Topology spread constraints prevent scheduling", "scheduling", "FailedScheduling topology spread maxSkew minDomains DoNotSchedule", "Scheduling Events, eligible domains and intended resilience requirements.", "Restore reviewed spread constraints or add eligible capacity; preserve required failure-domain isolation.", "Pods schedule with the intended distribution across eligible domains.", ["topologySpreadConstraints"]),
    ("ip_exhaustion", "CNI Pod IP allocation exhaustion", "network", "FailedCreatePodSandBox IPAM no available IP addresses exhaustion", "Sandbox Events and operator CNI/IPAM allocation evidence.", "Network owner safely expands or reclaims address capacity after checking real allocation ownership.", "New sandboxes allocate unique addresses and reach intended endpoints.", []),
    ("gitops_reversion", "GitOps reconciliation undoes an approved repair", "workload", "Argo Flux GitOps drift reverted reconciliation managedFields", "Repeated spec changes, delivery controller status and source-of-truth revision.", "Repair desired state through the delivery workflow; do not fight the reconciler or disable policy blindly.", "The source and live state agree through several reconciliation cycles.", []),
]


def catalog() -> list[dict]:
    result = []
    for group, rows in (("common", COMMON), ("difficult", DIFFICULT)):
        for sid, title, category, signals, evidence, remediation, verification, fields in rows:
            result.append({"id": sid, "title": title, "group": group, "category": category,
                "signals": signals, "evidenceRequired": evidence, "remediation": remediation,
                "verification": verification, "restorableFields": fields,
                "executionMode": "reviewed_profile_required" if fields else "operator_led",
                "source": SOURCES[category], "version": VERSION,
                "risk": "elevated" if group == "difficult" else "context_dependent", "collection": "kubernetes"})
    if os.getenv("KRAVEL_SYNTHETIC_RUNBOOKS", "") == "1":
        from .support_knowledge import support_runbooks
        result.extend(support_runbooks())
    path = os.getenv("KRAVEL_RUNBOOKS_PATH", "")
    if path:
        from .guardrails import guard_model_input
        from .research import validate_url
        file = Path(path)
        if file.stat().st_size > 1024*1024:
            raise ValueError("Custom runbooks exceed the local library size limit")
        extra = json.loads(file.read_text(encoding="utf-8"))
        if not isinstance(extra, list) or len(extra) > 200:
            raise ValueError("Custom runbooks must be a bounded JSON array")
        keys = {"id", "title", "category", "signals", "evidenceRequired", "remediation", "verification", "source"}
        ids = {case["id"] for case in result}
        for case in extra:
            if not isinstance(case, dict) or set(case) - {"collection"} != keys or not all(isinstance(v, str) and 1 <= len(v) <= 3000 for v in case.values()) or case["id"] in ids:
                raise ValueError("Invalid or duplicate custom runbook")
            if not re.fullmatch(r"[a-z0-9_-]{1,60}", case.get("collection", "team")):
                raise ValueError("Invalid runbook collection")
            validate_url(case["source"])
            if guard_model_input(json.dumps(case), "custom runbook", 24_000)["findings"]:
                raise ValueError("Custom runbook contains sensitive or instruction-like material")
            ids.add(case["id"])
            result.append({**case, "collection": case.get("collection", "team"), "group": "custom", "version": hashlib.sha256(json.dumps(case, sort_keys=True).encode()).hexdigest()[:16], "restorableFields": [], "executionMode": "operator_led", "risk": "operator_review_required"})
    return copy.deepcopy(result)


def catalog_hash() -> str:
    return hashlib.sha256(json.dumps(catalog(), sort_keys=True).encode()).hexdigest()


def by_id(scenario_id: str) -> dict:
    return next((case for case in catalog() if case["id"] == scenario_id), {})
