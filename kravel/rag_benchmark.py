"""Small, authored regression set: not a production-accuracy benchmark."""
import hashlib
import json
import math

VERSION = "synthetic-retrieval-v1"
QUERIES = [
    ("exit code 137 previous state OOMKilled", ["oomkilled"]),
    ("Pod cannot download its container image manifest unknown tag", ["image_not_found"]),
    ("media type prettyjws manifest v1 is no longer supported", ["legacy_image_format"]),
    ("my container repeatedly starts and dies due to bad startup command", ["crash_command"]),
    ("application config changed and startup validation fails", ["bad_configmap"]),
    ("service endpoints are empty but the app Pods are healthy", ["service_selector"]),
    ("container is running but not receiving traffic readiness", ["readiness_failure"]),
    ("Pod stuck Pending insufficient cpu", ["cpu_request"]),
    ("PersistentVolumeClaim stays Pending no provisioned disk", ["pvc_pending"]),
    ("hostname lookup fails inside the Pod dnsPolicy dnsConfig", ["dns_config"]),
    ("registry responds unauthorized when downloading image", ["registry_auth"]),
    ("CPU throttling p99 slow even with idle node", ["cpu_throttling"]),
    ("node eviction ephemeral storage disk pressure", ["ephemeral_storage_eviction"]),
    ("rollout is stuck ProgressDeadlineExceeded", ["rollout_stalled"]),
    ("disruption budget blocks node drain minAvailable", ["pdb_deadlock"]),
    ("admission webhook timeout denies new Pods", ["admission_webhook_timeout"]),
    ("two replicas try to mount the same ReadWriteOnce volume", ["csi_multi_attach"]),
    ("MTU fragmentation only large network responses hang", ["cni_mtu"]),
    ("node clock drift makes TLS certificate not yet valid", ["clock_skew"]),
    ("GitOps keeps undoing the manual patch", ["gitops_reversion"]),
    ("synthetic support queue age rises consumers cannot keep up", ["support_worker_backlog"]),
    ("integration rejects authentication after wrong issuer audience config", ["support_auth_config"]),
    ("support API retries amplify upstream deadline failures", ["support_dependency_timeout"]),
    ("knowledge answers cite old document versions", ["support_stale_index"]),
    ("inference image has incompatible tokenizer and model weights checksum", ["artifact_mismatch"]),
    ("inference service killed during model cold start", ["inference_oom"]),
]


def dataset(cases):
    ids = {c["id"] for c in cases}
    return [{"query": query, "relevantIds": relevant} for query, relevant in QUERIES if set(relevant) <= ids]


def dataset_hash(rows):
    return hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()


def score(ids, relevant, k=3):
    relevant = set(relevant)
    ranked = ids[:k]
    matches = [index for index, sid in enumerate(ranked, 1) if sid in relevant]
    ideal = sum(1 / math.log2(i+1) for i in range(1, min(len(relevant), k)+1))
    return {"recallAt3": len(set(ranked) & relevant) / max(1, len(relevant)),
        "mrrAt3": 1 / matches[0] if matches else 0,
        "ndcgAt3": sum(1 / math.log2(i+1) for i in matches) / ideal if ideal else 0}


def summarize(rows):
    variants = {}
    for name in ("bm25", "dense", "rrf", "reranked"):
        samples = [row["variants"][name] for row in rows if name in row["variants"]]
        if not samples:
            continue
        variants[name] = {key: sum(s[key] for s in samples)/len(samples) for key in ("recallAt3", "mrrAt3", "ndcgAt3", "latencyMs")}
        variants[name]["queryCount"] = len(samples)
    return variants
