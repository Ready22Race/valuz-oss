import { describe, expect, it } from "vitest";

import type { NotificationEntry } from "@valuz/core";

import { notificationDisplay } from "./notification-display";

const base: NotificationEntry = {
  id: "n1",
  kind: "question",
  title: "architect",
  body: "选哪种布局？",
  route: "/tasks/t1",
  action: "answer",
  urgency: "actionable",
  task_id: "t1",
  project_id: "w1",
  session_id: "s1",
  pending_id: "p1",
  payload: {},
  created_at: 1,
  read_at: null,
  resolved_at: null,
};

describe("notificationDisplay", () => {
  it("question: composes agent title, keeps route, tags by pending", () => {
    const d = notificationDisplay(base);
    expect(d.title).toContain("architect");
    expect(d.body).toBe("选哪种布局？");
    expect(d.route).toBe("/tasks/t1");
    expect(d.tag).toBe("question:p1");
  });

  it("task_failed: failure title, per-task tag", () => {
    const d = notificationDisplay({
      ...base,
      kind: "task_failed",
      title: "季度报告",
      body: "lead crashed",
      action: "resume",
      route: "/tasks/t9",
      task_id: "t9",
    });
    expect(d.body).toBe("lead crashed");
    expect(d.route).toBe("/tasks/t9");
    expect(d.tag).toBe("failure:t9");
  });

  it("task_failed with no body falls back to a generic line", () => {
    const d = notificationDisplay({
      ...base,
      kind: "task_failed",
      title: "季度报告",
      body: "",
      task_id: "t9",
    });
    expect(d.body).toContain("季度报告");
  });

  it("falls back to session route when there is no task", () => {
    const d = notificationDisplay({ ...base, route: null, task_id: null });
    expect(d.route).toBe("/conversation/s1");
  });
});
