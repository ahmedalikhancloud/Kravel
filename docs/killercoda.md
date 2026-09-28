# Killercoda demo

This demo runs Kravel against Killercoda's disposable, single-node Kubernetes playground. It creates a healthy workload, records a baseline, introduces a ConfigMap regression and rollout, and asks Kravel to reconstruct the sequence that led to the resulting crash loop.

Kravel must collect the healthy baseline **before** the breaking script runs. You run the investigation after the failure, but the collector is already watching in the background.

## Fast path: clone into the generic playground

1. Open the [Killercoda Kubernetes playground](https://killercoda.com/playgrounds/scenario/kubernetes).
2. Clone your repository and enter it:

   ```bash
   git clone https://github.com/ahmedalikhancloud/Kravel.git
   cd Kravel
   ```

   The public repository requires no GitHub credentials. Every helper script resolves repository assets from its own file location, so its internal paths do not depend on the checkout directory name.

3. Bootstrap Kravel and the healthy workload:

   ```bash
   bash demo/killercoda/bootstrap.sh
   ```

4. Introduce the failure:

   ```bash
   bash demo/killercoda/break-cluster.sh
   ```

5. Print the deterministic investigation (no API key required):

   ```bash
   bash demo/killercoda/investigate.sh
   ```

6. Run the real hosted-LLM agent. Create a key in the [Groq console](https://console.groq.com/keys), then enter it without putting the value in shell history:

   ```bash
   read -rsp 'Groq API key: ' GROQ_API_KEY && echo
   export GROQ_API_KEY
   bash demo/killercoda/agent-investigate.sh
   unset GROQ_API_KEY
   ```

   Groq's free-plan limits are published on its [rate-limits page](https://console.groq.com/docs/rate-limits) and can change. The demo uses compact evidence shards to stay within small token quotas.

The bootstrap builds the repository's Dockerfile, imports the image into the playground's `k8s.io` containerd namespace, and deploys it with `imagePullPolicy: Never`. This means no registry is required for the initial demo.

In Killercoda's two-node playground, the locally imported image exists only on `controlplane`. The demo manifest pins Kravel to that node and tolerates its control-plane taint; the intentionally broken sample workload may run on either node.

If you publish an image later, skip the local build:

```bash
KRAVEL_IMAGE=ghcr.io/ahmedalikhancloud/kravel:0.1.0 bash demo/killercoda/bootstrap.sh
```

## What the failure does

Only the `kravel-demo` namespace is changed:

1. `ConfigMap/api-config` changes `STARTUP_MODE=healthy` to `STARTUP_MODE=broken` and lowers a simulated database timeout.
2. `Deployment/checkout-api` is restarted so the environment variables are reloaded.
3. The replacement container logs a simulated connection failure and exits with code 42.
4. Kubernetes restarts it and emits lifecycle events.

Kravel then shows the ConfigMap before and after, the deterministic state diff, and the ordered evidence timeline. The hosted agent independently chooses read-only temporal tools and returns an evidence-grounded causal assessment.

## What the LLM harness does

The agent runs inside the existing Kravel Pod, where it can read the captured SQLite history. The shell script streams the API key over `kubectl exec` standard input; it does not add the key to the image, repository, Pod environment, or Kubernetes Secret, and it does not restart the Pod. Avoiding a restart matters because this fast demo intentionally stores history in `emptyDir`.

The loop is bounded to six tool turns by default:

1. Send the question and four function schemas to the hosted model.
2. Execute requested `rewind_cluster_state`, `diff_states`, `get_incident_context`, or `trace_resource` calls locally.
3. Return capped, sanitized tool results to the model.
4. Stop when the model writes a final report or force a final response at the turn limit.

To ask a custom question:

```bash
bash demo/killercoda/agent-investigate.sh 'Did the ConfigMap edit precede the first pod failure, and what evidence is missing?'
```

To use another OpenAI-compatible provider with local function calling, set all three values before running the script:

```bash
export KRAVEL_LLM_API_KEY='provider-key'
export KRAVEL_LLM_BASE_URL='https://provider.example/v1'
export KRAVEL_LLM_MODEL='tool-capable-model'
bash demo/killercoda/agent-investigate.sh
```

## Reset

```bash
bash demo/killercoda/reset.sh
```

The reset script removes only `kravel-demo`, `kravel-system`, their demo ClusterRole/ClusterRoleBinding, and the local timestamp file.

## Troubleshooting

If an older checkout reports `ErrImageNeverPull` on `node01`, update it and rerun bootstrap from the beginning:

```bash
git pull --ff-only
bash demo/killercoda/bootstrap.sh
```

Bootstrap deletes and recreates only the disposable Kravel demo namespaces, so rerunning it after this startup failure is expected.

## Scope of this fast demo

The playground path demonstrates Kubernetes watches, state reconstruction, topology, Kubernetes Events, and an optional external LLM tool loop. It deliberately does not reconfigure the playground API server for audit webhooks or install Prometheus; those are separate integration demos. `investigate.sh` is the deterministic no-key report, while `agent-investigate.sh` makes the hosted model call.

Only use the hosted-agent path with synthetic or approved data. The model provider receives the evidence returned by its selected tools. Kravel redacts Kubernetes Secret values, but ConfigMaps, object names, labels, annotations, event messages, and other metadata may still be sensitive.

Killercoda environments are ephemeral. The demo uses `emptyDir` storage so its history disappears when the Pod or playground is removed.
