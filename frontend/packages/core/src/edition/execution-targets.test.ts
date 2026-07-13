import { afterEach, describe, expect, it } from "vitest";
import { renderHook, act } from "@testing-library/react";
import {
  getDefaultExecutionTarget,
  getExecutionTargets,
  setExecutionTargets,
  useExecutionTargets,
  type ExecutionTarget,
} from "./execution-targets";

const LOCAL: ExecutionTarget = {
  id: "local",
  labelKey: "commercial.exec.local",
  baseUrl: "http://localhost:8000",
  isDefault: true,
};
const CLOUD: ExecutionTarget = {
  id: "cloud",
  labelKey: "commercial.exec.cloud",
  baseUrl: "http://cloud:8010",
};

afterEach(() => {
  setExecutionTargets([]);
});

describe("execution targets registry", () => {
  it("is empty by default (OSS single-backend)", () => {
    expect(getExecutionTargets()).toEqual([]);
    expect(getDefaultExecutionTarget()).toBeUndefined();
  });

  it("returns registered targets and the flagged default", () => {
    setExecutionTargets([CLOUD, LOCAL]);
    expect(getExecutionTargets()).toHaveLength(2);
    expect(getDefaultExecutionTarget()?.id).toBe("local");
  });

  it("falls back to the first target when none is flagged default", () => {
    setExecutionTargets([CLOUD, { ...LOCAL, isDefault: false }]);
    expect(getDefaultExecutionTarget()?.id).toBe("cloud");
  });

  it("copies the input array so later caller mutation is invisible", () => {
    const input = [LOCAL];
    setExecutionTargets(input);
    input.push(CLOUD);
    expect(getExecutionTargets()).toHaveLength(1);
  });

  it("useExecutionTargets re-renders on registry change", () => {
    const { result } = renderHook(() => useExecutionTargets());
    expect(result.current).toEqual([]);
    act(() => {
      setExecutionTargets([LOCAL, CLOUD]);
    });
    expect(result.current.map((t) => t.id)).toEqual(["local", "cloud"]);
  });
});
