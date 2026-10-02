export const resourceId = (resource) => resource.id || `${resource.namespace}/${resource.kind}/${resource.name}`;

export function matchingResources(resources, query = "", kind = "all") {
  const needle = query.trim().toLowerCase();
  return resources.filter((resource) => (kind === "all" || (kind === "controllers" ? ["Deployment", "DaemonSet", "StatefulSet", "ReplicaSet"].includes(resource.kind) : resource.kind === kind)) && `${resource.name} ${resource.kind} ${resource.namespace}`.toLowerCase().includes(needle));
}

export function layoutTopology(resources, connections) {
  const byId = new Map(resources.map((resource) => [resourceId(resource), resource]));
  const owners = new Map(connections.filter((edge) => edge.relation === "owns").map((edge) => [edge.target, edge.source]));
  const root = (id, seen = new Set()) => {
    if (seen.has(id)) return id;
    seen.add(id);
    return owners.has(id) ? root(owners.get(id), seen) : id;
  };
  const roots = [...new Set(resources.filter((resource) => ["Deployment", "DaemonSet", "StatefulSet", "ReplicaSet", "Pod"].includes(resource.kind)).map((resource) => root(resourceId(resource))))].sort();
  if (!roots.length) roots.push("unowned");
  const group = (resource) => {
    const id = resourceId(resource);
    if (["Deployment", "DaemonSet", "StatefulSet", "ReplicaSet", "Pod"].includes(resource.kind)) return root(id);
    const targets = connections.filter((edge) => edge.source === id).map((edge) => edge.target).filter((target) => byId.has(target));
    return targets.length ? root(targets.sort()[0]) : "unowned";
  };
  const lanes = {ConfigMap: -7, Deployment: -3.5, DaemonSet: -3.5, StatefulSet: -3.5, ReplicaSet: 0, Pod: 3.5, Service: 7};
  const groups = new Map();
  for (const resource of [...resources].sort((a, b) => resourceId(a).localeCompare(resourceId(b)))) {
    const groupId = group(resource), index = roots.indexOf(groupId);
    const x = index < 0 ? 0 : (index - (roots.length - 1) / 2) * 4.2;
    const bucket = `${groupId}/${resource.kind}`;
    if (!groups.has(bucket)) groups.set(bucket, []);
    groups.get(bucket).push({resource, x, z: lanes[resource.kind] || 0, group: groupId});
  }
  const result = new Map();
  for (const entries of groups.values()) entries.forEach((entry, index) => {
    const offset = (index % 3 - (Math.min(entries.length, 3) - 1) / 2) * 1.65;
    result.set(resourceId(entry.resource), {...entry, position: [entry.x + offset, 0, entry.z + Math.floor(index / 3) * 1.6]});
  });
  return result;
}
