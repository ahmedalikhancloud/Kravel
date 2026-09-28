import fs from "node:fs";
import https from "node:https";

function delay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

export class KubernetesWatcher {
  constructor({ kube, resources, clusterId, store, onError = console.error, onInfo = console.log }) {
    this.kube = kube;
    this.resources = resources;
    this.clusterId = clusterId;
    this.store = store;
    this.onError = onError;
    this.onInfo = onInfo;
    this.stopped = false;
    this.requests = new Set();
    this.token = "";
    this.ca = undefined;
  }

  initializeCredentials() {
    if (!this.kube.host) throw new Error("KUBERNETES_SERVICE_HOST is not set; the watcher is intended to run in-cluster");
    this.token = fs.readFileSync(this.kube.tokenPath, "utf8").trim();
    this.ca = fs.readFileSync(this.kube.caPath);
  }

  request(path, { stream = false } = {}) {
    return new Promise((resolve, reject) => {
      const request = https.request({
        hostname: this.kube.host,
        port: this.kube.port,
        path,
        method: "GET",
        ca: this.ca,
        headers: { authorization: `Bearer ${this.token}`, accept: "application/json" }
      }, (response) => {
        if (stream && response.statusCode === 200) return resolve(response);
        let body = "";
        response.setEncoding("utf8");
        response.on("data", (chunk) => { body += chunk; });
        response.on("end", () => {
          if ((response.statusCode ?? 500) >= 400) {
            const error = new Error(`Kubernetes API ${path} returned ${response.statusCode}: ${body.slice(0, 500)}`);
            error.statusCode = response.statusCode;
            reject(error);
            return;
          }
          try {
            resolve(body ? JSON.parse(body) : {});
          } catch (error) {
            reject(new Error(`Invalid Kubernetes API response: ${error.message}`));
          }
        });
      });
      this.requests.add(request);
      request.on("close", () => this.requests.delete(request));
      request.on("error", reject);
      request.end();
    });
  }

  ingest(type, object) {
    if (!object || object.kind === "Status") return;
    if (object.kind === "Event") {
      this.store.recordKubernetesEvent(this.clusterId, object);
      return;
    }
    this.store.recordResourceChange({ clusterId: this.clusterId, source: "watch", action: type, object });
  }

  async list(path) {
    const payload = await this.request(path);
    for (const object of payload.items ?? []) this.ingest("ADDED", object);
    return payload.metadata?.resourceVersion ?? "";
  }

  async watch(path, resourceVersion) {
    const separator = path.includes("?") ? "&" : "?";
    const watchPath = `${path}${separator}watch=1&allowWatchBookmarks=true&timeoutSeconds=300&resourceVersion=${encodeURIComponent(resourceVersion)}`;
    const response = await this.request(watchPath, { stream: true });
    response.setEncoding("utf8");
    let buffer = "";
    return new Promise((resolve, reject) => {
      response.on("data", (chunk) => {
        buffer += chunk;
        let newline;
        while ((newline = buffer.indexOf("\n")) >= 0) {
          const line = buffer.slice(0, newline).trim();
          buffer = buffer.slice(newline + 1);
          if (!line) continue;
          try {
            const event = JSON.parse(line);
            if (event.type === "ERROR") {
              const error = new Error(`Kubernetes watch error: ${event.object?.message ?? "unknown"}`);
              error.statusCode = event.object?.code;
              reject(error);
              return;
            }
            if (event.type !== "BOOKMARK") this.ingest(event.type, event.object);
            resourceVersion = event.object?.metadata?.resourceVersion ?? resourceVersion;
          } catch (error) {
            reject(new Error(`Invalid Kubernetes watch event: ${error.message}`));
            return;
          }
        }
      });
      response.on("end", () => resolve(resourceVersion));
      response.on("error", reject);
    });
  }

  async runResource(path) {
    let resourceVersion = "";
    let failures = 0;
    while (!this.stopped) {
      try {
        if (!resourceVersion) {
          resourceVersion = await this.list(path);
          this.onInfo(`[watch] listed ${path} at resourceVersion=${resourceVersion}`);
        }
        resourceVersion = await this.watch(path, resourceVersion);
        failures = 0;
      } catch (error) {
        if (this.stopped) break;
        failures += 1;
        if (error.statusCode === 410) resourceVersion = "";
        this.onError(`[watch] ${path}: ${error.message}`);
        await delay(Math.min(1000 * 2 ** failures, 30_000));
      }
    }
  }

  async start() {
    this.initializeCredentials();
    this.stopped = false;
    await Promise.all(this.resources.map((path) => this.runResource(path)));
  }

  stop() {
    this.stopped = true;
    for (const request of this.requests) request.destroy();
  }
}
