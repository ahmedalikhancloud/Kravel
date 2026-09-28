import crypto from "node:crypto";
import http from "node:http";
import { prometheusMetrics } from "./metrics.mjs";
import { parseDurationSeconds } from "./time.mjs";

function send(response, status, payload) {
  const body = JSON.stringify(payload, null, 2);
  response.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "content-length": Buffer.byteLength(body),
    "cache-control": "no-store"
  });
  response.end(body);
}

function sendText(response, status, body, contentType) {
  response.writeHead(status, {
    "content-type": contentType,
    "content-length": Buffer.byteLength(body),
    "cache-control": "no-store"
  });
  response.end(body);
}

function authorized(request, token) {
  if (!token) return true;
  const actual = request.headers.authorization ?? "";
  const expected = `Bearer ${token}`;
  const actualBuffer = Buffer.from(actual);
  const expectedBuffer = Buffer.from(expected);
  return actualBuffer.length === expectedBuffer.length && crypto.timingSafeEqual(actualBuffer, expectedBuffer);
}

function readJson(request, maxBytes) {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks = [];
    request.on("data", (chunk) => {
      size += chunk.length;
      if (size > maxBytes) {
        const error = new Error(`request body exceeds ${maxBytes} bytes`);
        error.statusCode = 413;
        reject(error);
        request.destroy();
        return;
      }
      chunks.push(chunk);
    });
    request.on("end", () => {
      try {
        resolve(chunks.length ? JSON.parse(Buffer.concat(chunks).toString("utf8")) : {});
      } catch (error) {
        error.statusCode = 400;
        reject(error);
      }
    });
    request.on("error", reject);
  });
}

function listParameter(searchParams, name) {
  const value = searchParams.get(name);
  return value ? value.split(",").map((item) => item.trim()).filter(Boolean) : undefined;
}

export function createApiServer({ store, config, embeddingClient }) {
  return http.createServer(async (request, response) => {
    const url = new URL(request.url, `http://${request.headers.host ?? "localhost"}`);
    try {
      if (url.pathname === "/healthz") return send(response, 200, { status: "ok" });
      if (url.pathname === "/readyz") return send(response, 200, { status: "ready", store: store.stats() });
      if (request.method === "GET" && url.pathname === "/metrics") {
        return sendText(response, 200, prometheusMetrics(store, config.clusterId), "text/plain; version=0.0.4; charset=utf-8");
      }
      if (!authorized(request, config.apiToken)) return send(response, 401, { error: "unauthorized" });

      if (request.method === "POST" && url.pathname === "/v1/ingest/resource") {
        const body = await readJson(request, config.maxBodyBytes);
        const result = store.recordResourceChange({
          clusterId: body.clusterId ?? config.clusterId,
          source: body.source ?? "manual",
          action: body.action,
          object: body.object,
          observedAt: body.observedAt,
          eventAt: body.eventAt,
          actor: body.actor,
          auditId: body.auditId
        });
        return send(response, result.duplicate ? 200 : 201, result);
      }

      if (request.method === "POST" && url.pathname === "/v1/ingest/audit") {
        const body = await readJson(request, config.maxBodyBytes);
        const events = Array.isArray(body) ? body : body.kind === "EventList" ? body.items ?? [] : [body];
        const results = events.map((event) => store.recordAuditEvent(url.searchParams.get("clusterId") ?? config.clusterId, event));
        return send(response, 202, { accepted: results.length, inserted: results.filter((item) => item.inserted).length });
      }

      if (request.method === "POST" && url.pathname === "/v1/ingest/kubernetes-event") {
        const body = await readJson(request, config.maxBodyBytes);
        return send(response, 202, store.recordKubernetesEvent(body.clusterId ?? config.clusterId, body.object ?? body));
      }

      if (request.method === "POST" && url.pathname === "/v1/ingest/metric") {
        const body = await readJson(request, config.maxBodyBytes);
        const result = store.recordMetricSample(
          body.clusterId ?? config.clusterId,
          body.metricName,
          body.sampledAt,
          body.value,
          body.labels
        );
        return send(response, result.inserted ? 201 : 200, result);
      }

      if (request.method === "GET" && url.pathname === "/v1/state/rewind") {
        return send(response, 200, store.stateAt({
          clusterId: url.searchParams.get("clusterId") ?? config.clusterId,
          timestamp: url.searchParams.get("timestamp"),
          namespace: url.searchParams.get("namespace"),
          kinds: listParameter(url.searchParams, "kinds"),
          resourceKey: url.searchParams.get("resourceKey")
        }));
      }

      if (request.method === "GET" && url.pathname === "/v1/state/diff") {
        return send(response, 200, store.diffStates({
          clusterId: url.searchParams.get("clusterId") ?? config.clusterId,
          from: url.searchParams.get("from"),
          to: url.searchParams.get("to"),
          namespace: url.searchParams.get("namespace"),
          kinds: listParameter(url.searchParams, "kinds")
        }));
      }

      if (request.method === "GET" && url.pathname === "/v1/state/graph") {
        return send(response, 200, store.graphAt({
          clusterId: url.searchParams.get("clusterId") ?? config.clusterId,
          timestamp: url.searchParams.get("timestamp"),
          namespace: url.searchParams.get("namespace")
        }));
      }

      if (request.method === "GET" && url.pathname === "/v1/state/trace") {
        return send(response, 200, store.traceResource({
          clusterId: url.searchParams.get("clusterId") ?? config.clusterId,
          timestamp: url.searchParams.get("timestamp"),
          resourceKey: url.searchParams.get("resourceKey"),
          maxDepth: Number(url.searchParams.get("maxDepth") ?? 2)
        }));
      }

      if (request.method === "GET" && url.pathname === "/v1/context") {
        const question = url.searchParams.get("question") ?? "";
        const queryEmbedding = question && embeddingClient.enabled ? await embeddingClient.embed(question) : null;
        return send(response, 200, store.contextShard({
          clusterId: url.searchParams.get("clusterId") ?? config.clusterId,
          incidentAt: url.searchParams.get("incidentAt"),
          lookback: parseDurationSeconds(url.searchParams.get("lookback") ?? 900),
          namespace: url.searchParams.get("namespace") ?? "",
          resourceKey: url.searchParams.get("resourceKey") ?? "",
          queryEmbedding,
          limit: Number(url.searchParams.get("limit") ?? 100)
        }));
      }

      if (request.method === "POST" && url.pathname === "/v1/admin/prune") {
        return send(response, 200, store.prune(config.retentionDays));
      }

      return send(response, 404, { error: "not_found" });
    } catch (error) {
      const status = error.statusCode && error.statusCode >= 400 && error.statusCode < 600 ? error.statusCode : 400;
      send(response, status, { error: error.message });
    }
  });
}

export function listen(server, host, port) {
  return new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(port, host, () => {
      server.removeListener("error", reject);
      resolve(server.address());
    });
  });
}
