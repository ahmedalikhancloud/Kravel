function unixSecondsToIso(value) {
  return new Date(Number(value) * 1000).toISOString();
}

export class PrometheusSampler {
  constructor({ url, intervalMs, queries, clusterId, store, onError = console.error }) {
    this.url = url?.replace(/\/$/, "");
    this.intervalMs = intervalMs;
    this.queries = queries;
    this.clusterId = clusterId;
    this.store = store;
    this.onError = onError;
    this.timer = null;
    this.stopped = false;
  }

  async sampleOnce() {
    if (!this.url) return { sampled: 0 };
    let sampled = 0;
    for (const [metricName, query] of Object.entries(this.queries)) {
      const endpoint = new URL(`${this.url}/api/v1/query`);
      endpoint.searchParams.set("query", query);
      const response = await fetch(endpoint, { headers: { accept: "application/json" } });
      if (!response.ok) throw new Error(`Prometheus query ${metricName} returned ${response.status}`);
      const payload = await response.json();
      if (payload.status !== "success") throw new Error(`Prometheus query ${metricName} failed: ${payload.error ?? "unknown error"}`);
      for (const result of payload.data?.result ?? []) {
        if (!result.value || result.value.length < 2) continue;
        const outcome = this.store.recordMetricSample(
          this.clusterId,
          metricName,
          unixSecondsToIso(result.value[0]),
          result.value[1],
          result.metric ?? {}
        );
        if (outcome.inserted) sampled += 1;
      }
    }
    return { sampled };
  }

  async start() {
    if (!this.url) return;
    this.stopped = false;
    while (!this.stopped) {
      try {
        await this.sampleOnce();
      } catch (error) {
        this.onError(`[prometheus] ${error.stack ?? error.message}`);
      }
      if (this.stopped) break;
      await new Promise((resolve) => {
        this.timer = setTimeout(resolve, this.intervalMs);
      });
    }
  }

  stop() {
    this.stopped = true;
    if (this.timer) clearTimeout(this.timer);
  }
}
