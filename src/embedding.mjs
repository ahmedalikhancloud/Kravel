export class EmbeddingClient {
  constructor({ url = "", model = "", apiKey = "" } = {}) {
    this.url = url;
    this.model = model;
    this.apiKey = apiKey;
  }

  get enabled() {
    return Boolean(this.url);
  }

  async embed(input) {
    if (!this.enabled) return Array.isArray(input) ? input.map(() => null) : null;
    const values = Array.isArray(input) ? input : [input];
    const headers = { "content-type": "application/json" };
    if (this.apiKey) headers.authorization = `Bearer ${this.apiKey}`;
    const response = await fetch(this.url, {
      method: "POST",
      headers,
      body: JSON.stringify({ input: values, ...(this.model ? { model: this.model } : {}) })
    });
    if (!response.ok) throw new Error(`Embedding service returned ${response.status}: ${(await response.text()).slice(0, 500)}`);
    const payload = await response.json();
    const embeddings = payload.data?.sort((left, right) => left.index - right.index).map((item) => item.embedding);
    if (!embeddings || embeddings.length !== values.length) throw new Error("Embedding service returned an unexpected response");
    return Array.isArray(input) ? embeddings : embeddings[0];
  }
}

export class EmbeddingWorker {
  constructor({ client, store, intervalMs = 10_000, onError = console.error } = {}) {
    this.client = client;
    this.store = store;
    this.intervalMs = intervalMs;
    this.onError = onError;
    this.stopped = false;
    this.timer = null;
  }

  async runOnce() {
    if (!this.client.enabled) return { embedded: 0 };
    const rows = this.store.pendingEmbeddings();
    if (!rows.length) return { embedded: 0 };
    const embeddings = await this.client.embed(rows.map((row) => row.memoryText));
    rows.forEach((row, index) => this.store.attachEmbedding(row.id, embeddings[index]));
    return { embedded: rows.length };
  }

  async start() {
    if (!this.client.enabled) return;
    this.stopped = false;
    while (!this.stopped) {
      try {
        await this.runOnce();
      } catch (error) {
        this.onError(`[embeddings] ${error.stack ?? error.message}`);
      }
      if (this.stopped) break;
      await new Promise((resolve) => { this.timer = setTimeout(resolve, this.intervalMs); });
    }
  }

  stop() {
    this.stopped = true;
    if (this.timer) clearTimeout(this.timer);
  }
}
