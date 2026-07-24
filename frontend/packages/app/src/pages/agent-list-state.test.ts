import { describe, expect, it } from "vitest";
import type { Agent } from "@valuz/core";
import { isCloudOnlyAgent } from "./agent-list-state";

const agent = {
  id: "agent-1",
  slug: "course-builder",
  name: "Course Builder",
} as Agent;

describe("isCloudOnlyAgent", () => {
  it("identifies organization catalog rows that are not installed locally", () => {
    expect(
      isCloudOnlyAgent({
        ...agent,
        _sync: { status: "cloud_only", cloud_id: "org-agent-1" },
      } as unknown as Agent),
    ).toBe(true);
  });

  it("keeps local and synced organization agents selectable", () => {
    expect(isCloudOnlyAgent(agent)).toBe(false);
    expect(
      isCloudOnlyAgent({
        ...agent,
        _sync: { status: "synced", cloud_id: "org-agent-1" },
      } as unknown as Agent),
    ).toBe(false);
  });
});
