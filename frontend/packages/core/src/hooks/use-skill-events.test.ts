/** @vitest-environment jsdom */
import { renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { SSEFrame } from "../api/fetch-event-source";

const closeStream = vi.fn();
type FetchEventSourceMock = (
  getUrl: () => string,
  onFrame: (frame: SSEFrame) => void,
) => () => void;
const fetchEventSource = vi.fn<FetchEventSourceMock>(() => closeStream);

vi.mock("../api/fetch-event-source", () => ({
  fetchEventSource: (
    getUrl: () => string,
    onFrame: (frame: SSEFrame) => void,
  ) => fetchEventSource(getUrl, onFrame),
}));

vi.mock("../api/skills-api", () => ({
  skillsApi: {
    eventsStreamUrl: () => "http://localhost:8000/v1/skills/events/stream",
  },
}));

import { useSkillEvents } from "./use-skill-events";

describe("useSkillEvents", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("shares one SSE connection across multiple hook consumers", () => {
    const firstChanged = vi.fn();
    const secondChanged = vi.fn();

    const first = renderHook(() => useSkillEvents(firstChanged));
    const second = renderHook(() => useSkillEvents(secondChanged));

    expect(fetchEventSource).toHaveBeenCalledTimes(1);
    const onFrame = fetchEventSource.mock.calls[0]![1];

    onFrame({ event: "skill.changed", data: "{}", id: null });
    expect(firstChanged).toHaveBeenCalledTimes(1);
    expect(secondChanged).toHaveBeenCalledTimes(1);

    first.unmount();
    expect(closeStream).not.toHaveBeenCalled();

    onFrame({ event: "project.skills_changed", data: "{}", id: null });
    expect(firstChanged).toHaveBeenCalledTimes(1);
    expect(secondChanged).toHaveBeenCalledTimes(2);

    second.unmount();
    expect(closeStream).toHaveBeenCalledTimes(1);
  });
});
