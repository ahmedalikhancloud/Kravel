import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";

test("observability assets are parseable and contain no committed credential values", () => {
  const notebook = JSON.parse(fs.readFileSync("demo/colab/laya_server.ipynb", "utf8"));
  assert.equal(notebook.nbformat, 4);
  assert.equal(notebook.cells.some((cell) => cell.outputs?.length), false);

  const manifest = fs.readFileSync("deploy/observability-killercoda.yaml", "utf8");
  assert.match(manifest, /kravel_benchmark_latency_distribution_seconds_bucket/);
  assert.match(manifest, /storage\.tsdb\.retention\.time=1h/);
  assert.doesNotMatch(manifest, /kind:\s+Secret/);
  assert.doesNotMatch(manifest, /(api[_-]?key|token|password):\s*[^\s{}]/i);
});
