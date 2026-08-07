import { describe, expect, it } from "vitest";
import { buildEgressDiagnosticsExport } from "./network-diagnostics";

describe("buildEgressDiagnosticsExport", () => {
  it("copies only the documented diagnostic schema", () => {
    const exported = buildEgressDiagnosticsExport(
      {
        mode: "auto",
        enabled: true,
        started: true,
        emergencyOverride: false,
        snapshotCount: 1,
        diagnosticEventCount: 1,
      },
      [
        {
          runtime: "codex",
          frontend: "model_ingress",
          targetOrigin: "https://api.example",
          mode: "auto",
          route: "http_proxy",
          health: "healthy",
          fallbackCount: 0,
          updatedAt: 100,
          clientId: "must-not-be-exported",
          secret: "must-not-be-exported",
        } as never,
      ],
      [
        {
          event: "egress.stream.established",
          runtime: "codex",
          targetOrigin: "https://api.example",
          prompt: "must-not-be-exported",
          proxyUrl: "http://user:secret@proxy.example",
        },
      ],
      [
        {
          phase: "model_first_event",
          monotonicMs: 200,
          observedAt: 300,
          clientId: "must-not-be-exported",
          turnAttemptId: "must-not-be-exported",
        },
      ],
    );

    const serialized = JSON.stringify(exported);
    expect(serialized).not.toContain("must-not-be-exported");
    expect(serialized).not.toContain("proxyUrl");
    expect(exported.snapshots[0]).toMatchObject({
      runtime: "codex",
      targetOrigin: "https://api.example",
    });
    expect(exported.runtimePhases[0]).toEqual({
      phase: "model_first_event",
      monotonicMs: 200,
      observedAt: 300,
    });
  });
});
