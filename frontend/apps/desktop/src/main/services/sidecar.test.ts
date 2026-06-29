import { beforeEach, describe, expect, it, vi } from "vitest";

const { spawnSyncMock } = vi.hoisted(() => ({ spawnSyncMock: vi.fn() }));

// Full replacement — ``importOriginal`` spread doesn't reliably re-export a Node
// builtin's named functions, and the test only exercises spawnSync. ``spawn`` /
// ``ChildProcess`` are referenced elsewhere in sidecar.ts but not on this path,
// so cheap stand-ins keep the module importable.
vi.mock("node:child_process", () => {
  const mod = {
    spawn: vi.fn(),
    spawnSync: (...args: unknown[]) => spawnSyncMock(...args),
    ChildProcess: class {},
  };
  return { ...mod, default: mod };
});

const { killWindowsProcessTree } = await import("./sidecar");

describe("killWindowsProcessTree", () => {
  beforeEach(() => spawnSyncMock.mockReset());

  it("kills the whole descendant tree with taskkill /T /F (forced, hidden)", () => {
    spawnSyncMock.mockReturnValue({ status: 0 });

    const ok = killWindowsProcessTree(1234);

    expect(ok).toBe(true);
    expect(spawnSyncMock).toHaveBeenCalledWith(
      "taskkill",
      ["/pid", "1234", "/T", "/F"],
      expect.objectContaining({ windowsHide: true }),
    );
  });

  it("reports success even if the process was already gone (taskkill launched)", () => {
    // taskkill returns a non-zero status when the PID is not found, but it DID
    // run — the tree is gone, so no fallback is needed.
    spawnSyncMock.mockReturnValue({ status: 128 });

    expect(killWindowsProcessTree(1234)).toBe(true);
  });

  it("returns false and logs when the taskkill binary itself can't run", () => {
    spawnSyncMock.mockReturnValue({ error: new Error("spawn taskkill ENOENT") });
    const logs: string[] = [];

    const ok = killWindowsProcessTree(1234, (line) => logs.push(line));

    expect(ok).toBe(false);
    expect(logs.join("\n")).toMatch(/taskkill tree-kill failed/);
  });
});
