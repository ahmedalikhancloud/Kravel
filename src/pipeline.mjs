import { performance } from "node:perf_hooks";
import { runTemporalAgent } from "./agent.mjs";
import { guardLayaOutput, guardModelInput, guardQwenOutput } from "./guardrails.mjs";
import { buildClassifierEvidence, runLayaClassifier } from "./laya.mjs";
import { createHumanReviewProposal, decidePolicy, runPredefinedAutomation } from "./policy.mjs";

function elapsed(started) {
  return Math.max(0, performance.now() - started);
}

function qwenQuestion(policy, diagnosis) {
  const probabilities = Object.entries(diagnosis)
    .sort((left, right) => right[1] - left[1])
    .map(([name, probability]) => `${name}=${probability.toFixed(3)}`)
    .join(", ");
  return `Investigate the complete incident window because the policy gate selected deep analysis. Laya probabilities: ${probabilities}. Policy reasons: ${policy.reasons.join(", ")}. Identify every independent failure, cite absolute timestamps and resource keys, and do not propose or execute mutations.`;
}

export async function runIncidentPipeline({
  store,
  config,
  embeddingClient,
  baselineAt,
  incidentAt,
  namespace = "",
  scenario = "unspecified",
  fetchImpl = globalThis.fetch,
  onToolCall = () => {}
}) {
  const pipelineStarted = performance.now();
  const stageMetrics = {};
  const guardrails = {};

  let started = performance.now();
  const evidence = buildClassifierEvidence({
    store,
    clusterId: config.clusterId,
    baselineAt,
    incidentAt,
    namespace
  });
  stageMetrics.evidence_reconstruction = elapsed(started);

  const layaInput = guardModelInput({ target: "laya", value: evidence.state, maxCharacters: 1_600 });
  guardrails.layaInput = { decision: layaInput.decision, findings: layaInput.findings };
  stageMetrics.laya_input_guardrail = layaInput.latencyMs;

  started = performance.now();
  const rawLaya = await runLayaClassifier({
    url: config.layaUrl,
    apiKey: config.layaApiKey,
    model: config.layaModel,
    state: layaInput.value,
    fetchImpl
  });
  stageMetrics.laya_inference = elapsed(started);

  const layaOutput = guardLayaOutput(rawLaya);
  guardrails.layaOutput = { decision: layaOutput.decision, findings: layaOutput.findings };
  stageMetrics.laya_output_guardrail = layaOutput.latencyMs;
  const laya = layaOutput.value;

  const policy = decidePolicy(laya, config.policy);
  stageMetrics.policy_gate = policy.latencyMs;

  let predefinedAutomation = null;
  if (policy.route === "predefined_runbook") {
    const automated = runPredefinedAutomation({
      store,
      clusterId: config.clusterId,
      baselineAt,
      incidentAt,
      namespace,
      diagnosis: policy.topDiagnosis
    });
    predefinedAutomation = automated.value;
    stageMetrics.predefined_automation = automated.latencyMs;
  }

  let qwen = null;
  let qwenOutput = null;
  if (policy.route === "qwen_investigation") {
    started = performance.now();
    qwen = await runTemporalAgent({
      store,
      config,
      embeddingClient,
      baselineAt,
      incidentAt,
      namespace,
      question: qwenQuestion(policy, laya.diagnosis),
      fetchImpl,
      onToolCall
    });
    stageMetrics.qwen_agent_total = elapsed(started);
    stageMetrics.qwen_input_guardrail = qwen.inputGuardrailMs;
    guardrails.qwenInput = qwen.inputGuardrail;
    stageMetrics.qwen_inference = qwen.modelMs;
    stageMetrics.qwen_temporal_tools = qwen.toolMs;
    qwenOutput = guardQwenOutput(qwen.answer);
    guardrails.qwenOutput = { decision: qwenOutput.decision, findings: qwenOutput.findings };
    stageMetrics.qwen_output_guardrail = qwenOutput.latencyMs;
  }

  const proposal = createHumanReviewProposal({
    policy,
    diagnosis: laya.diagnosis,
    predefinedAutomation,
    qwenReport: qwenOutput?.value ?? "",
    qwenGuardrail: qwenOutput
  });
  stageMetrics.human_review_package = proposal.latencyMs;
  stageMetrics.pipeline_without_mlflow = elapsed(pipelineStarted);

  return {
    scenario,
    status: "success",
    route: policy.route,
    decision: policy.topDiagnosis,
    reviewStatus: proposal.value.status,
    stageMetrics,
    guardrails,
    evidence: { changeCount: evidence.changeCount, eventCount: evidence.eventCount },
    laya,
    policy,
    predefinedAutomation,
    qwen: qwen ? {
      model: qwen.model,
      turns: qwen.turns,
      toolCalls: qwen.toolCalls,
      inputGuardrail: qwen.inputGuardrail,
      report: qwenOutput.value
    } : null,
    proposal: proposal.value
  };
}
