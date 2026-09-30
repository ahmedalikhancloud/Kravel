# Local validation — 30 September 2026

Tested against the existing Docker Desktop Kubernetes cluster. These are individual local measurements, not latency guarantees or an independent security certification.

All 28 automated tests passed. Bash and browser-script syntax checks also passed.

- All four live failures were detected: actual OOMKilled, ImagePullBackOff, CrashLoopBackOff, and invalid ConfigMap data.
- Each repair passed Kubernetes server dry-run, remained pending, and executed only after an explicitly labelled implementation-validation approval.
- All four Deployments recovered. A final reset restored the healthy baseline.
- Resetting only the image-pull lab completed while the crash-loop lab was still broken.
- Approving a proposal after that reset was rejected as stale, with zero applied operations.
- Live RBAC allowed the debugger to read Pods and logs, but denied Secret reads, Deployment patching, and container exec.
- The broker could patch a named demo Deployment, but could not patch an arbitrary name or delete the allowed object.
- Laya's namespace, model-cache PVC, and local image were absent after removal. The removed image/model data can be downloaded again; prior source remains in Git history.
- Prometheus reported both debugger and broker targets as `up`.
- MLflow persisted the successful four-incident trace with state `OK` and 14 spans.

The four-incident read-only investigation measured:

| Step | Time |
|---|---:|
| End to end | 18.02 s |
| Qwen inference | 16.63 s |
| Kubernetes read tools | 29.05 ms |
| Input and tool-evidence guardrails | 7.99 ms |
| Output guardrail | 0.51 ms |
| MLflow setup | 195.37 ms |
| MLflow span overhead | 25.86 ms |
| MLflow flush | 1.06 s |

The model used only read tools and reported `mutationExecuted: false`. Timings are diagnostic measurements with some overlap; they are not an additive accounting ledger.

The self-contained Local Slack workflow was tested. The optional real-Slack adapter was not tested with live Slack credentials.
