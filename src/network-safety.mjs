export function isInternalHostname(hostname) {
  const value = String(hostname ?? "").toLowerCase();
  return value === "localhost"
    || value === "127.0.0.1"
    || value === "::1"
    || value === "host.docker.internal"
    || value === "model-runner.docker.internal"
    || value.endsWith(".docker.internal")
    || value.endsWith(".svc")
    || value.endsWith(".svc.cluster.local")
    || (!value.includes(".") && /^[a-z0-9-]+$/.test(value));
}

export function safeServiceUrl(value, label) {
  const url = new URL(String(value ?? ""));
  if (!["https:", "http:"].includes(url.protocol)) throw new Error(`${label} URL must use http or https`);
  if (url.protocol === "http:" && !isInternalHostname(url.hostname)) {
    throw new Error(`Remote ${label} must use HTTPS`);
  }
  return url;
}
