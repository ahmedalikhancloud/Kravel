function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function escapePath(value) {
  return String(value).replaceAll("~", "~0").replaceAll("/", "~1");
}

export function stableStringify(value) {
  if (Array.isArray(value)) return `[${value.map(stableStringify).join(",")}]`;
  if (isObject(value)) {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${stableStringify(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

// RFC 6902-shaped operations. Arrays are deliberately replaced atomically: list
// merge semantics vary by Kubernetes schema, while reconstruction must be exact.
export function diffJson(before, after, path = "") {
  if (stableStringify(before) === stableStringify(after)) return [];
  if (before === undefined) return [{ op: "add", path, value: after }];
  if (after === undefined) return [{ op: "remove", path }];
  if (!isObject(before) || !isObject(after)) {
    return [{ op: "replace", path, value: after }];
  }

  const operations = [];
  const keys = new Set([...Object.keys(before), ...Object.keys(after)]);
  for (const key of [...keys].sort()) {
    operations.push(...diffJson(before[key], after[key], `${path}/${escapePath(key)}`));
  }
  return operations;
}

export function changedPaths(operations, limit = 12) {
  const paths = operations.map((operation) => operation.path);
  return paths.length <= limit ? paths : [...paths.slice(0, limit), `… +${paths.length - limit} more`];
}
