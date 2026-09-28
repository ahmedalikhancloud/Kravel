import { objectIdentity } from "./kubernetes-object.mjs";

function podSpec(object) {
  if (object.kind === "Pod") return object.spec;
  if (["Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Job"].includes(object.kind)) return object.spec?.template?.spec;
  if (object.kind === "CronJob") return object.spec?.jobTemplate?.spec?.template?.spec;
  return undefined;
}

function podLabels(object) {
  if (object.kind === "Pod") return object.metadata?.labels ?? {};
  return object.spec?.template?.metadata?.labels ?? {};
}

function labelsMatch(selector, labels) {
  const entries = Object.entries(selector ?? {});
  return entries.length > 0 && entries.every(([key, value]) => labels?.[key] === value);
}

export function buildStateGraph(objects) {
  const identities = new Map();
  const byUid = new Map();
  const byKindName = new Map();
  for (const object of objects) {
    const identity = { ...objectIdentity(object), observed: true };
    identities.set(identity.key, identity);
    if (identity.uid) byUid.set(identity.uid, identity.key);
    byKindName.set(`${identity.kind}|${identity.namespace}|${identity.name}`, identity.key);
  }

  const resolve = (kind, namespace, name, apiVersion = "v1") => {
    if (!name) return undefined;
    const existing = byKindName.get(`${kind}|${namespace}|${name}`);
    if (existing) return existing;
    const key = `${apiVersion}|${kind}|${namespace || "_cluster"}|${name}`;
    identities.set(key, { apiVersion, kind, namespace, name, uid: "", resourceVersion: "", key, observed: false });
    byKindName.set(`${kind}|${namespace}|${name}`, key);
    return key;
  };

  const edges = [];
  const add = (from, to, type) => {
    if (from && to && from !== to && !edges.some((edge) => edge.from === from && edge.to === to && edge.type === type)) {
      edges.push({ from, to, type });
    }
  };

  for (const object of objects) {
    const source = objectIdentity(object);
    for (const owner of object.metadata?.ownerReferences ?? []) {
      const target = (owner.uid && byUid.get(owner.uid)) ?? resolve(owner.kind, source.namespace, owner.name, owner.apiVersion ?? "v1");
      add(source.key, target, "owned-by");
    }

    const spec = podSpec(object);
    if (spec) {
      if (spec.serviceAccountName) {
        add(source.key, resolve("ServiceAccount", source.namespace, spec.serviceAccountName), "uses-service-account");
      }
      for (const volume of spec.volumes ?? []) {
        if (volume.configMap?.name) add(source.key, resolve("ConfigMap", source.namespace, volume.configMap.name), "mounts-config");
        if (volume.secret?.secretName) add(source.key, resolve("Secret", source.namespace, volume.secret.secretName), "mounts-secret");
        if (volume.persistentVolumeClaim?.claimName) add(source.key, resolve("PersistentVolumeClaim", source.namespace, volume.persistentVolumeClaim.claimName), "mounts-volume");
      }
      for (const container of [...(spec.initContainers ?? []), ...(spec.containers ?? [])]) {
        for (const entry of container.envFrom ?? []) {
          if (entry.configMapRef?.name) add(source.key, resolve("ConfigMap", source.namespace, entry.configMapRef.name), "reads-config");
          if (entry.secretRef?.name) add(source.key, resolve("Secret", source.namespace, entry.secretRef.name), "reads-secret");
        }
        for (const entry of container.env ?? []) {
          if (entry.valueFrom?.configMapKeyRef?.name) add(source.key, resolve("ConfigMap", source.namespace, entry.valueFrom.configMapKeyRef.name), "reads-config");
          if (entry.valueFrom?.secretKeyRef?.name) add(source.key, resolve("Secret", source.namespace, entry.valueFrom.secretKeyRef.name), "reads-secret");
        }
      }
    }

    if (object.kind === "Service") {
      for (const target of objects) {
        if (target.kind === "Pod" && objectIdentity(target).namespace === source.namespace && labelsMatch(object.spec?.selector, podLabels(target))) {
          add(source.key, objectIdentity(target).key, "selects");
        }
      }
    }
  }

  return { nodes: [...identities.values()].sort((left, right) => left.key.localeCompare(right.key)), edges };
}

export function traceGraph(graph, resourceKey, maxDepth = 2) {
  const seen = new Set([resourceKey]);
  const frontier = [{ key: resourceKey, depth: 0 }];
  const edges = [];
  while (frontier.length) {
    const current = frontier.shift();
    if (current.depth >= maxDepth) continue;
    for (const edge of graph.edges) {
      if (edge.from !== current.key && edge.to !== current.key) continue;
      edges.push(edge);
      const other = edge.from === current.key ? edge.to : edge.from;
      if (!seen.has(other)) {
        seen.add(other);
        frontier.push({ key: other, depth: current.depth + 1 });
      }
    }
  }
  return {
    nodes: graph.nodes.filter((node) => seen.has(node.key)),
    edges: [...new Map(edges.map((edge) => [`${edge.from}|${edge.to}|${edge.type}`, edge])).values()]
  };
}
