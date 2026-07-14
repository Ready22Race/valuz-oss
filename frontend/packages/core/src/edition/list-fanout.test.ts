import { afterEach, describe, expect, it, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";
import {
  fanOutTargets,
  getListFanOutTargets,
  useDegradedListTargets,
} from "./list-fanout";
import { setExecutionTargets } from "./execution-targets";

const LOCAL = {
  id: "local",
  labelKey: "l",
  baseUrl: "http://localhost:8000",
  isDefault: true,
};
const CLOUD = { id: "cloud", labelKey: "c", baseUrl: "http://cloud:8010" };

afterEach(async () => {
  setExecutionTargets([]);
  // A zero-target fan-out publishes an empty failure set — resets the
  // module-level degraded store between tests.
  await fanOutTargets(() => Promise.resolve(null));
});

describe("getListFanOutTargets", () => {
  it("is empty with zero or one registered target", () => {
    expect(getListFanOutTargets()).toEqual([]);
    setExecutionTargets([LOCAL]);
    expect(getListFanOutTargets()).toEqual([]);
  });

  it("returns all targets when two or more are registered", () => {
    setExecutionTargets([LOCAL, CLOUD]);
    expect(getListFanOutTargets().map((t) => t.id)).toEqual(["local", "cloud"]);
  });
});

describe("fanOutTargets", () => {
  it("collects fulfilled values in registration order", async () => {
    setExecutionTargets([LOCAL, CLOUD]);
    const outcome = await fanOutTargets((target) =>
      Promise.resolve(`from-${target.id}`),
    );
    expect(outcome.values.map((v) => v.value)).toEqual([
      "from-local",
      "from-cloud",
    ]);
    expect(outcome.failedTargets).toEqual([]);
  });

  it("keeps the healthy side when one target fails (degraded)", async () => {
    setExecutionTargets([LOCAL, CLOUD]);
    const outcome = await fanOutTargets((target) =>
      target.id === "cloud"
        ? Promise.reject(new Error("down"))
        : Promise.resolve("ok"),
    );
    expect(outcome.values).toHaveLength(1);
    expect(outcome.values[0]!.target.id).toBe("local");
    expect(outcome.failedTargets).toEqual(["cloud"]);
  });

  it("throws only when every target fails", async () => {
    setExecutionTargets([LOCAL, CLOUD]);
    await expect(
      fanOutTargets(() => Promise.reject(new Error("all down"))),
    ).rejects.toThrow("all down");
  });

  it("publishes and clears the degraded-targets store", async () => {
    setExecutionTargets([LOCAL, CLOUD]);
    const { result } = renderHook(() => useDegradedListTargets());
    expect(result.current).toEqual([]);
    await act(async () => {
      await fanOutTargets((target) =>
        target.id === "cloud"
          ? Promise.reject(new Error("down"))
          : Promise.resolve("ok"),
      );
    });
    expect(result.current).toEqual(["cloud"]);
    await act(async () => {
      await fanOutTargets(() => Promise.resolve("ok"));
    });
    expect(result.current).toEqual([]);
  });
});

describe("sessions list fan-out (api integration)", () => {
  it("merges, tags exec_origin, and hits both bases", async () => {
    setExecutionTargets([LOCAL, CLOUD]);
    const fetchSpy = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      const sessions = url.startsWith(CLOUD.baseUrl)
        ? [{ id: "s-cloud", project_id: "p2" }]
        : [{ id: "s-local", project_id: "p1" }];
      return Promise.resolve(
        new Response(JSON.stringify({ sessions }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
    });
    vi.stubGlobal("fetch", fetchSpy);
    try {
      const { sessionsApi } = await import("../api/sessions-api");
      const { sessions } = await sessionsApi.list();
      expect(sessions.map((s) => s.id).sort()).toEqual(["s-cloud", "s-local"]);
      expect(sessions.find((s) => s.id === "s-cloud")?.exec_origin).toBe(
        "cloud",
      );
      expect(sessions.find((s) => s.id === "s-local")?.exec_origin).toBe(
        "local",
      );
      const urls = fetchSpy.mock.calls.map((c) => String(c[0]));
      expect(urls.some((u) => u.startsWith(LOCAL.baseUrl))).toBe(true);
      expect(urls.some((u) => u.startsWith(CLOUD.baseUrl))).toBe(true);
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
