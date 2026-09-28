export function toIso(value, field = "timestamp") {
  if (value === undefined || value === null || value === "") return new Date().toISOString();
  const date = value instanceof Date ? value : new Date(value);
  if (!Number.isFinite(date.getTime())) throw new Error(`${field} is not a valid timestamp`);
  return date.toISOString();
}

export function parseDurationSeconds(value, fallback = 900) {
  if (value === undefined || value === null || value === "") return fallback;
  if (typeof value === "number" && Number.isFinite(value) && value >= 0) return value;
  const match = String(value).trim().match(/^(\d+(?:\.\d+)?)\s*(s|m|h|d)?$/i);
  if (!match) throw new Error("duration must look like 30s, 15m, 2h, or 1d");
  const factors = { s: 1, m: 60, h: 3600, d: 86400 };
  return Number(match[1]) * factors[(match[2] ?? "s").toLowerCase()];
}

export function subtractSeconds(timestamp, seconds) {
  return new Date(new Date(timestamp).getTime() - seconds * 1000).toISOString();
}
