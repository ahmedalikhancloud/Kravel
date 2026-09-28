import { stableStringify } from "./json-diff.mjs";

function clone(value) {
  return value === undefined ? undefined : JSON.parse(JSON.stringify(value));
}

export function sanitizeObject(input) {
  const object = clone(input);
  if (!object || typeof object !== "object") return object;

  if (object.metadata) {
    delete object.metadata.managedFields;
    delete object.metadata.selfLink;
  }
  if (object.kind === "Secret") {
    if (object.data) object.data = Object.fromEntries(Object.keys(object.data).map((key) => [key, "<redacted>"]));
    if (object.stringData) object.stringData = Object.fromEntries(Object.keys(object.stringData).map((key) => [key, "<redacted>"]));
  }
  return object;
}

export function objectIdentity(object) {
  const metadata = object?.metadata ?? {};
  if (!object?.kind || !metadata.name) throw new Error("Kubernetes object requires kind and metadata.name");
  const apiVersion = object.apiVersion ?? "v1";
  const namespace = metadata.namespace ?? "";
  return {
    apiVersion,
    kind: object.kind,
    namespace,
    name: metadata.name,
    uid: metadata.uid ?? "",
    resourceVersion: metadata.resourceVersion ?? "",
    key: `${apiVersion}|${object.kind}|${namespace || "_cluster"}|${metadata.name}`
  };
}

export function objectSummary(action, identity, patch) {
  const location = identity.namespace ? `${identity.namespace}/${identity.name}` : identity.name;
  if (!patch?.length) return `${action} ${identity.kind} ${location}`;
  const paths = patch.slice(0, 4).map((item) => item.path || "(root)").join(", ");
  const remainder = patch.length > 4 ? ` (+${patch.length - 4})` : "";
  return `${action} ${identity.kind} ${location}: ${paths}${remainder}`;
}

export function memoryText({ action, identity, patch, actor = "", eventAt }) {
  return [
    eventAt,
    action,
    identity.kind,
    identity.namespace,
    identity.name,
    actor && `actor=${actor}`,
    ...patch.map((operation) => `${operation.op} ${operation.path || "(root)"}${operation.value === undefined ? "" : ` ${stableStringify(operation.value).slice(0, 240)}`}`)
  ].filter(Boolean).join(" ");
}
