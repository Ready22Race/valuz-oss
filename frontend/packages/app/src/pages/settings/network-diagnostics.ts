const allowlistedRecord = (
  value: unknown,
  keys: readonly string[],
): Record<string, unknown> => {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return {};
  }
  const source = value as Record<string, unknown>;
  return Object.fromEntries(
    keys.filter((key) => key in source).map((key) => [key, source[key]]),
  );
};

/** Build a second, explicit allowlist before diagnostics leave the UI. */
export const buildEgressDiagnosticsExport = (
  status: unknown,
  snapshots: unknown[],
  diagnostics: unknown[],
  runtimePhases: unknown[],
) => ({
  status: allowlistedRecord(status, [
    "mode",
    "enabled",
    "started",
    "emergencyOverride",
    "snapshotCount",
    "diagnosticEventCount",
    "lastErrorCode",
  ]),
  snapshots: snapshots.map((item) =>
    allowlistedRecord(item, [
      "runtime",
      "frontend",
      "targetOrigin",
      "mode",
      "route",
      "health",
      "source",
      "redactedProxy",
      "resolveMs",
      "connectMs",
      "fallbackCount",
      "lastErrorCode",
      "updatedAt",
    ]),
  ),
  diagnostics: diagnostics.map((item) =>
    allowlistedRecord(item, [
      "event",
      "runtime",
      "frontend",
      "targetOrigin",
      "mode",
      "timestamp",
      "resolveMs",
      "route",
      "source",
      "redactedProxy",
      "candidateCount",
      "errorCode",
      "candidateIndex",
      "connectMs",
      "fallbackCount",
    ]),
  ),
  runtimePhases: runtimePhases.map((item) =>
    allowlistedRecord(item, ["phase", "monotonicMs", "observedAt"]),
  ),
});
