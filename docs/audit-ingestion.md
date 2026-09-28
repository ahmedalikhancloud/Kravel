# Audit ingestion

Kravel accepts Kubernetes `audit.k8s.io/v1` Event objects at:

```text
POST /v1/ingest/audit
```

The body can be a single Event, an EventList, or an array. Add `?clusterId=<stable-id>` when the receiver serves multiple clusters.

Audit configuration is control-plane specific. On a self-managed API server, configure an audit policy and a webhook backend that can reach Kravel. Managed Kubernetes offerings often do not expose API server flags; route the provider's audit log stream through a small adapter instead.

The sample [`deploy/audit-policy.yaml`](../deploy/audit-policy.yaml) records metadata for reads and request/response bodies for common workload changes. It is only a starting point. The first matching audit rule wins, so order matters.

For reconstruction, the list/watch collector remains authoritative. Audit events add actor and request context because:

- policies can omit bodies;
- admission can mutate the final object;
- a request can fail;
- audit delivery can be delayed.

Kravel sanitizes recognized Secret objects before storing audit request/response bodies, but custom resources and ConfigMaps can still contain sensitive values. Review [`security.md`](security.md) first.
