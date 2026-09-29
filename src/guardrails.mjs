import { performance } from "node:perf_hooks";
import { incidentDiagnoses } from "./laya.mjs";

const secretPatterns = [
  ["private_key", /-----BEGIN [^-\r\n]{0,40}PRIVATE KEY-----[\s\S]*?-----END [^-\r\n]{0,40}PRIVATE KEY-----/gi],
  ["bearer_token", /\bBearer\s+[A-Za-z0-9._~+/=-]{8,}/gi],
  ["jwt", /\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b/g],
  ["credential_assignment", /\b(api[_-]?key|password|passwd|access[_-]?token|client[_-]?secret)\s*[:=]\s*["']?[^\s"',;]{4,}/gi]
];

const instructionPatterns = [
  /ignore (?:all |any )?(?:previous|prior) instructions/i,
  /(?:reveal|print|return).{0,30}(?:system prompt|developer message|hidden instruction)/i,
  /(?:you are now|act as) (?:an? )?(?:assistant|system|administrator|root)/i,
  /<\/?(?:tool_call|system|assistant|developer)>/i
];

function redactSecrets(value) {
  let text = value;
  const findings = [];
  for (const [name, pattern] of secretPatterns) {
    let count = 0;
    text = text.replace(pattern, () => {
      count += 1;
      return `<redacted:${name}>`;
    });
    if (count) findings.push({ code: name, count });
  }
  return { text, findings };
}

function normalizeText(value) {
  return String(value ?? "")
    .replace(/\r\n?/g, "\n")
    .replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g, "")
    .normalize("NFKC");
}

export function guardModelInput({ value, target, maxCharacters = 20_000 }) {
  const started = performance.now();
  let text = normalizeText(value);
  const redacted = redactSecrets(text);
  text = redacted.text;
  const findings = [...redacted.findings];
  let quarantinedLines = 0;
  text = text.split("\n").map((line) => {
    if (!instructionPatterns.some((pattern) => pattern.test(line))) return line;
    quarantinedLines += 1;
    return "[quarantined instruction-like text from untrusted cluster evidence]";
  }).join("\n");
  if (quarantinedLines) findings.push({ code: "prompt_injection_pattern", count: quarantinedLines });
  const truncated = text.length > maxCharacters;
  if (truncated) {
    text = `${text.slice(0, Math.max(0, maxCharacters - 80))}\n[truncated by ${target} input guardrail]`;
    findings.push({ code: "input_truncated", count: 1 });
  }
  if (!text.trim()) throw new Error(`${target} input guardrail rejected empty input`);
  return {
    value: text,
    decision: findings.length ? "allow_with_redactions" : "allow",
    findings,
    latencyMs: performance.now() - started
  };
}

export function guardLayaOutput(result) {
  const started = performance.now();
  if (!result || typeof result !== "object") throw new Error("Laya output guardrail rejected a non-object response");
  const diagnosis = {};
  for (const name of incidentDiagnoses) {
    const value = Number(result.diagnosis?.[name]);
    if (!Number.isFinite(value) || value < 0 || value > 1) {
      throw new Error(`Laya output guardrail rejected invalid probability for ${name}`);
    }
    diagnosis[name] = value;
  }
  const confidence = result.confidence === null ? null : Number(result.confidence);
  if (confidence !== null && (!Number.isFinite(confidence) || confidence < 0 || confidence > 1)) {
    throw new Error("Laya output guardrail rejected invalid confidence");
  }
  return {
    value: { ...result, diagnosis, confidence },
    decision: "allow",
    findings: [],
    latencyMs: performance.now() - started
  };
}

export function guardQwenOutput(value, { maxCharacters = 12_000 } = {}) {
  const started = performance.now();
  let text = normalizeText(value);
  const redacted = redactSecrets(text);
  text = redacted.text;
  const findings = [...redacted.findings];
  const unsafeMutation = /^.*\bkubectl\s+(?:delete|apply|patch|replace|scale|set|edit|create|rollout)\b.*$/gim;
  let mutationLines = 0;
  text = text.replace(unsafeMutation, () => {
    mutationLines += 1;
    return "[mutation command withheld; remediation requires human approval]";
  });
  if (mutationLines) findings.push({ code: "mutation_command_withheld", count: mutationLines });
  if (!/\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(text)) findings.push({ code: "missing_absolute_timestamp", count: 1 });
  if (!/(uncertainty|unknown|missing evidence)/i.test(text)) findings.push({ code: "missing_uncertainty_statement", count: 1 });
  if (text.length > maxCharacters) {
    text = `${text.slice(0, Math.max(0, maxCharacters - 80))}\n[truncated by Qwen output guardrail]`;
    findings.push({ code: "output_truncated", count: 1 });
  }
  if (!text.trim()) throw new Error("Qwen output guardrail rejected empty output");
  return {
    value: text,
    decision: findings.length ? "allow_with_warnings" : "allow",
    findings,
    latencyMs: performance.now() - started
  };
}
