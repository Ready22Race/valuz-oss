import type { Agent } from "@valuz/core";

interface AgentSyncInfo {
  status?: string;
}

/** Cloud-only organization Agents are catalog entries, not local edit targets. */
export function isCloudOnlyAgent(agent: Agent): boolean {
  const sync = (agent as unknown as Record<string, unknown>)._sync as
    | AgentSyncInfo
    | undefined;
  return sync?.status === "cloud_only";
}
