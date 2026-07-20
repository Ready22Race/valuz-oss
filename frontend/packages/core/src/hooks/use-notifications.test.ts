import { beforeEach, describe, expect, it, vi } from "vitest";

const markReadMock = vi.fn((_id: string) => Promise.resolve({ ok: true }));
vi.mock("../api/notifications-api", () => ({
  notificationsApi: {
    markRead: (id: string) => markReadMock(id),
  },
}));

import type { NotificationEntry } from "../api/notifications-api";
import { useNotificationStore } from "../store/notification-store";
import { markSessionNotificationsRead } from "./use-notifications";

const entry = (over: Partial<NotificationEntry>): NotificationEntry => ({
  id: "n1",
  kind: "question",
  title: "",
  body: "",
  route: null,
  action: "none",
  urgency: "info",
  task_id: null,
  project_id: null,
  session_id: null,
  pending_id: null,
  payload: {},
  created_at: 1,
  read_at: null,
  resolved_at: null,
  ...over,
});

const readAt = (id: string): number | null =>
  useNotificationStore.getState().entries.get(id)?.read_at ?? null;

beforeEach(() => {
  markReadMock.mockClear();
  useNotificationStore.setState({
    entries: new Map(),
    freshIds: new Set(),
    alertedIds: new Set(),
    _everReset: false,
    _inited: false,
  });
});

describe("markSessionNotificationsRead", () => {
  it("should mark a session's open unread notifications read when the conversation opens", () => {
    useNotificationStore
      .getState()
      .reset([entry({ id: "n1", session_id: "s1" })]);

    markSessionNotificationsRead("s1");

    expect(readAt("n1")).not.toBeNull(); // optimistic badge decrement
    expect(markReadMock).toHaveBeenCalledExactlyOnceWith("n1"); // persisted
  });

  it("should not touch notifications belonging to other sessions", () => {
    useNotificationStore
      .getState()
      .reset([entry({ id: "n2", session_id: "s2" })]);

    markSessionNotificationsRead("s1");

    expect(readAt("n2")).toBeNull();
    expect(markReadMock).not.toHaveBeenCalled();
  });

  it("should skip notifications already read (no redundant persist)", () => {
    useNotificationStore
      .getState()
      .reset([entry({ id: "n3", session_id: "s1", read_at: 123 })]);

    markSessionNotificationsRead("s1");

    expect(markReadMock).not.toHaveBeenCalled();
  });

  it("should no-op for an empty session id", () => {
    useNotificationStore
      .getState()
      .reset([entry({ id: "n4", session_id: "s1" })]);

    markSessionNotificationsRead("");

    expect(readAt("n4")).toBeNull();
    expect(markReadMock).not.toHaveBeenCalled();
  });
});
