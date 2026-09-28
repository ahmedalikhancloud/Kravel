import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";

test("observability assets are parseable and contain no committed credential values", () => {
  const launcher = fs.readFileSync("demo/codespaces/laya-server.sh", "utf8");
  assert.match(launcher, /laya\[serve\]==0\.3\.20/);
  assert.match(launcher, /secrets\.token_urlsafe\(32\)/);
  assert.match(launcher, /codespace ports visibility/);
  assert.doesNotMatch(launcher, /gsk_[A-Za-z0-9_-]{16,}|trycloudflare|cloudflared/i);

  const manifest = fs.readFileSync("deploy/observability-killercoda.yaml", "utf8");
  assert.match(manifest, /kravel_benchmark_latency_distribution_seconds_bucket/);
  assert.match(manifest, /storage\.tsdb\.retention\.time=1h/);
  assert.doesNotMatch(manifest, /kind:\s+Secret/);
  assert.doesNotMatch(manifest, /(api[_-]?key|token|password):\s*[^\s{}]/i);
});
