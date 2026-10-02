import test from "node:test";
import assert from "node:assert/strict";
import { retrievalSteps, comparisonRows } from "../kravel/web/runbooks.mjs";

test("UI labels only the stages that actually ran", () => {
  assert.deepEqual(retrievalSteps({timings:{bm25Ms:2}}).map(x => x.active), [true,false,false,false]);
  assert.equal(retrievalSteps({ready:true}).every(x => x.active), true);
});
test("Ablation table keeps stages in pipeline order and omits absent results", () => {
  assert.deepEqual(comparisonRows({reranked:{mrrAt3:.5},bm25:{mrrAt3:.8}}).map(x => x.key), ["bm25","reranked"]);
});
