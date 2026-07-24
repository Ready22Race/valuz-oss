import type { Agent } from "@valuz/core";

interface AgentSyncInfo {
  status?: string;
}

/** Cloud-only organization resources are catalog entries, not local targets. */
export function isCloudOnlyResource(resource: unknown): boolean {
  if (!resource || typeof resource !== "object") return false;
  const outer = resource as Record<string, unknown>;
  const target =
    outer.kind === "installed" && outer.item && typeof outer.item === "object"
      ? (outer.item as Record<string, unknown>)
      : outer;
  const sync = target._sync as AgentSyncInfo | undefined;
  return sync?.status === "cloud_only";
}

/** Cloud-only organization Agents are catalog entries, not local edit targets. */
export function isCloudOnlyAgent(agent: Agent): boolean {
  return isCloudOnlyResource(agent);
}
