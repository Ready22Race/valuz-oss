export type EgressMode = "auto" | "direct" | "off";

export interface EgressManagerStatus {
  mode: EgressMode;
  enabled: boolean;
  started: boolean;
  emergencyOverride: boolean;
  snapshotCount: number;
  diagnosticEventCount: number;
  lastErrorCode?: string;
}
export type EgressRuntime =
  | "codex"
  | "claude"
  | "deepagents"
  | "provider_test";
export type EgressFrontend =
  | "shadow"
  | "model_ingress"
  | "forward_proxy"
  | "legacy";

export type EgressRoute =
  | {
      kind: "direct";
      source: "local" | "no_proxy" | "env" | "system" | "policy";
    }
  | {
      kind: "http_proxy";
      url: string;
      source: "env" | "system" | "policy";
    }
  | {
      kind: "socks5_proxy";
      url: string;
      source: "env" | "system" | "policy";
    };

export interface EgressResolution {
  targetOrigin: string;
  candidates: EgressRoute[];
  resolvedAt: number;
  ttlMs: number;
  status: "resolved" | "unknown";
  reason?: string;
}

export type PacParseResult =
  | { status: "resolved"; candidates: EgressRoute[] }
  | { status: "unknown"; reason: string };

export type EgressDiagnosticEvent = {
  event:
    | "egress.attempt.started"
    | "egress.route.resolved"
    | "egress.resolve.failed"
    | "egress.stream.established"
    | "egress.connect.failed";
  connectionAttemptId: string;
  clientId: string;
  runtime: EgressRuntime;
  frontend: EgressFrontend;
  targetOrigin: string;
  mode: Exclude<EgressMode, "off">;
  timestamp: number;
  resolveMs?: number;
  route?: EgressRoute["kind"];
  source?: EgressRoute["source"];
  redactedProxy?: string;
  candidateCount?: number;
  errorCode?: string;
  candidateIndex?: number;
  connectMs?: number;
  fallbackCount?: number;
};

export interface EgressSnapshot {
  clientId: string;
  runtime: EgressRuntime;
  frontend: EgressFrontend;
  targetOrigin: string;
  mode: EgressMode;
  route: EgressRoute["kind"] | "unknown";
  health: "unknown" | "healthy" | "degraded" | "failed";
  source?: EgressRoute["source"];
  redactedProxy?: string;
  resolveMs?: number;
  connectMs?: number;
  reconnectCount: number;
  fallbackCount: number;
  lastErrorCode?: string;
  correlationConfidence: "exact_runtime" | "time_origin" | "none";
  updatedAt: number;
}

export interface EgressConnectionOutcome {
  success: boolean;
  connectMs?: number;
  fallbackCount?: number;
  reconnectCount?: number;
  errorCode?: string;
}
