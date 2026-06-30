import { ChildProcess, spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import { homedir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

// Vite bundles the Electron main process as ESM (vite.main.config.ts
// formats:['es']), so the CommonJS ``__dirname`` global is undefined at
// runtime. Recreate it from import.meta.url for the dev-mode lookups
// below. Production paths use ``process.resourcesPath`` and never hit this.
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

export interface SidecarOptions {
  appDataDir: string;
  name: string;
  port: number;
  onLog?: (line: string) => void;
  onExit?: (code: number | null, signal: string | null) => void;
}

export interface DesktopSidecarResult {
  name: string;
  pid: number | null;
  port: number;
  /** Tear the sidecar (and its whole process tree) down. Resolves once the
   *  process has actually exited (or after the forced-kill grace window), so
   *  callers like ``before-quit`` can await a clean teardown. */
  stop: () => Promise<void>;
}

/** Grace period after SIGTERM before escalating to SIGKILL on POSIX. */
const GRACEFUL_SHUTDOWN_MS = 5000;

/**
 * Kill a process AND its whole descendant tree on Windows.
 *
 * ``child.kill()`` maps to ``TerminateProcess``, which terminates ONLY the
 * target PID. The backend spawns grandchildren (codex CLI, the node
 * chrome-devtools engine, MCP servers, a sub-process kernel); under
 * ``TerminateProcess`` those survive as ORPHANS and keep file handles open
 * (SQLite WAL, the magika ONNX model, skill dirs). On Windows an open file
 * can't be deleted/overwritten, so the next launch then trips ``[WinError 32]``.
 *
 * ``taskkill /T`` walks the descendant tree; ``/F`` forces it (a window-less
 * console child has no graceful signal we can deliver per-PID from Node — and
 * WAL + next-boot session recovery already cover abrupt termination). Run
 * synchronously so the tree is dead before ``before-quit`` returns and the
 * updater's installer touches those files.
 *
 * Returns ``true`` if ``taskkill`` launched; ``false`` (caller should fall back
 * to ``child.kill()``) if the binary itself couldn't run.
 */
export const killWindowsProcessTree = (
  pid: number,
  onLog?: (line: string) => void,
): boolean => {
  const result = spawnSync("taskkill", ["/pid", String(pid), "/T", "/F"], {
    windowsHide: true,
  });
  if (result.error) {
    onLog?.(
      `[warn] taskkill tree-kill failed for pid ${pid}: ${result.error.message}` +
        " — falling back to single-process kill",
    );
    return false;
  }
  return true;
};

/**
 * Locate the backend server binary.
 *
 * Layout: PyInstaller's onedir contents (binary + _internal/) are staged
 * directly under libexec/, with no extra ``valuz-server/`` wrapper dir:
 *   Valuz.app/Contents/Resources/libexec/
 *     ├── valuz-server     (executable)
 *     ├── _internal/       (PyInstaller runtime, sibling of the binary)
 *     └── rg               (helper)
 *
 * Lookup order:
 * 1. Production: ``process.resourcesPath/libexec/valuz-server``
 * 2. Dev (built binary): project ``resources/libexec/valuz-server``
 * 3. Returns null when no bundled binary is found (caller falls back to ``uv run``).
 */
function resolveServerBinary(): string | null {
  const serverName =
    process.platform === "win32" ? "valuz-server.exe" : "valuz-server";

  // Production: packaged Electron app
  const bundled = path.join(process.resourcesPath, "libexec", serverName);
  if (fs.existsSync(bundled)) return bundled;

  // Dev: check if the build script has placed a binary in the project tree.
  // From dist-electron/ we walk up to desktop/, then into resources/.
  const devBinary = path.join(
    __dirname,
    "..",
    "..",
    "resources",
    "libexec",
    serverName,
  );
  if (fs.existsSync(devBinary)) return devBinary;

  return null;
}

/**
 * Locate the bundled ripgrep helper.
 *
 * Layout per docs/STRUCTURE.md §"Desktop Distribution":
 *   Valuz.app/Contents/Resources/libexec/rg
 *
 * Returns null when no bundled rg is found; the backend's
 * docs_embedded._detect_rg() then falls back to shutil.which("rg").
 */
function resolveRgBinary(): string | null {
  const bundledExt = process.platform === "win32" ? "rg.exe" : "rg";

  const bundled = path.join(process.resourcesPath, "libexec", bundledExt);
  if (fs.existsSync(bundled)) return bundled;

  const devRg = path.join(
    __dirname,
    "..",
    "..",
    "resources",
    "libexec",
    bundledExt,
  );
  if (fs.existsSync(devRg)) return devRg;

  return null;
}

/**
 * Locate the bundled Node binary that runs the browser engine.
 *
 * A GUI-launched app gets a stripped PATH that hides the user's Node, so the
 * browser feature ships its own (downloaded + verified at build time by
 * scripts/download-node.sh, staged at libexec/node). See
 * docs/design/browser-feature.md §8.
 *
 * Returns null when not bundled (dev), so the backend falls back to npx.
 */
function resolveNodeBinary(): string | null {
  const nodeName = process.platform === "win32" ? "node.exe" : "node";

  const bundled = path.join(process.resourcesPath, "libexec", nodeName);
  if (fs.existsSync(bundled)) return bundled;

  const devNode = path.join(
    __dirname,
    "..",
    "..",
    "resources",
    "libexec",
    nodeName,
  );
  if (fs.existsSync(devNode)) return devNode;

  return null;
}

/**
 * Locate the bundled chrome-devtools-mcp CLI entry (committed JS tree staged at
 * libexec/chrome-devtools-mcp/node_modules/...). Run as ``node <entry>``.
 *
 * Returns null when not bundled (dev), so the backend falls back to npx.
 */
function resolveCdtEntry(): string | null {
  const rel = path.join(
    "chrome-devtools-mcp",
    "node_modules",
    "chrome-devtools-mcp",
    "build",
    "src",
    "bin",
    "chrome-devtools.js",
  );

  const bundled = path.join(process.resourcesPath, "libexec", rel);
  if (fs.existsSync(bundled)) return bundled;

  const devEntry = path.join(
    __dirname,
    "..",
    "..",
    "resources",
    "libexec",
    rel,
  );
  if (fs.existsSync(devEntry)) return devEntry;

  return null;
}

/**
 * Build spawn arguments for dev-mode fallback (uv run python -m valuz_agent).
 */
function buildDevSpawnArgs(port: number): {
  cmd: string;
  args: string[];
  cwd: string;
} {
  const backendDir = path.resolve(
    __dirname,
    "..",
    "..",
    "..",
    "..",
    "..",
    "backend",
  );
  return {
    cmd: "uv",
    args: [
      "run",
      "python",
      "-m",
      "valuz_agent",
      "--host",
      "127.0.0.1",
      "--port",
      String(port),
    ],
    cwd: backendDir,
  };
}

/**
 * Start the backend server as a managed sidecar process.
 *
 * Spawns the bundled PyInstaller binary if available, otherwise falls back
 * to launching via `uv run` for development.
 */
export const startSidecar = async (
  options: SidecarOptions,
): Promise<DesktopSidecarResult> => {
  // ``appDataDir`` is kept in SidecarOptions for callers, but the env
  // override below uses a literal path; destructure only what we read.
  const { name, port, onLog, onExit } = options;

  const serverBinary = resolveServerBinary();

  let cmd: string;
  let args: string[];
  let cwd: string | undefined;
  // ``VALUZ_DATA_DIR`` MUST be an absolute path. A literal ``"~/..."``
  // gets passed verbatim to pydantic-settings, which does not expand
  // ``~`` — the backend then tries to mkdir under cwd, fails on root
  // (Electron's default cwd) with "Read-only file system: '~'", and
  // the PyInstaller bootloader prints "Failed to execute script
  // _pyinstaller_entry" before the sidecar can even log a line.
  const env: Record<string, string> = {
    ...(process.env as Record<string, string>),
    VALUZ_DATA_DIR: path.join(homedir(), ".valuz-oss"),
  };

  // The backend builds its public callback URLs (notably the connector OAuth
  // redirect_uri) from VALUZ_BACKEND_BASE_URL, which defaults to :8000. The
  // sidecar listens on ``port`` (19100 by default), so without this the OAuth
  // redirect points at a dead :8000 and the browser callback is refused. Pin
  // the base URL to the actual port the sidecar is bound to.
  env.VALUZ_BACKEND_BASE_URL = `http://127.0.0.1:${port}`;

  // Point the backend's docs_embedded._detect_rg() at the bundled binary.
  // It already honours VALUZ_RG_PATH ahead of bundled / PATH lookup.
  const rgBinary = resolveRgBinary();
  if (rgBinary) {
    env.VALUZ_RG_PATH = rgBinary;
  }

  // Point the backend's browser service at the vendored Node + chrome-devtools-mcp
  // entry (modules/browser/service.py::_cli_argv). Both must be present; with
  // them set the backend invokes ``node <entry>`` by absolute path, bypassing
  // the GUI app's stripped PATH. Absent (dev), it falls back to npx.
  const nodeBinary = resolveNodeBinary();
  const cdtEntry = resolveCdtEntry();
  if (nodeBinary && cdtEntry) {
    env.VALUZ_NODE_PATH = nodeBinary;
    env.VALUZ_CDT_ENTRY = cdtEntry;
  }

  if (serverBinary) {
    cmd = serverBinary;
    args = ["--host", "127.0.0.1", "--port", String(port)];
  } else {
    const dev = buildDevSpawnArgs(port);
    cmd = dev.cmd;
    args = dev.args;
    cwd = dev.cwd;
  }

  const child: ChildProcess = spawn(cmd, args, {
    cwd,
    env,
    stdio: ["ignore", "pipe", "pipe"],
    // POSIX: give the child its own process group (setsid) so stop() can signal
    // the WHOLE tree via process.kill(-pid, …) — the analog of taskkill /T on
    // Windows. Not on Windows (no POSIX groups; the tree is killed via taskkill
    // /T /F there). stdio stays piped, so this does not detach the log streams.
    detached: process.platform !== "win32",
  });

  // Capture stdout/stderr for logs
  const captureStream = (
    stream: NodeJS.ReadableStream | null,
    prefix: string,
  ) => {
    if (!stream) return;
    stream.setEncoding("utf8");
    stream.on("data", (chunk: string) => {
      for (const line of chunk.split("\n")) {
        const trimmed = line.trim();
        if (trimmed) {
          onLog?.(`${prefix}${trimmed}`);
        }
      }
    });
  };

  captureStream(child.stdout, "");
  captureStream(child.stderr, "[stderr] ");

  // Track lifecycle
  let stopped = false;

  child.on("exit", (code, signal) => {
    if (!stopped) {
      onExit?.(code, signal);
    }
  });

  child.on("error", (err) => {
    onLog?.(`[error] Failed to start sidecar: ${err.message}`);
  });

  const stop = (): Promise<void> => {
    if (stopped || !child.pid) return Promise.resolve();
    stopped = true;
    const pid = child.pid;

    if (process.platform === "win32") {
      // Kill the WHOLE descendant tree, not just the top PID — child.kill()
      // (TerminateProcess) would orphan the backend's grandchildren (codex /
      // node chrome-devtools / MCP / sub-process kernel), which then hold file
      // handles and trip [WinError 32] on the next launch. taskkill /T /F runs
      // synchronously, so the tree is gone before this returns. WAL + next-boot
      // session recovery cover the abrupt (forced) termination.
      if (!killWindowsProcessTree(pid, onLog)) {
        try {
          child.kill();
        } catch {
          // Process may have already exited
        }
      }
      return Promise.resolve();
    }

    // POSIX: signal the whole process GROUP (the child leads its own group via
    // detached spawn), SIGTERM first for a graceful shutdown, then SIGKILL after
    // a grace period. ``-pid`` targets the group so the backend's children go
    // down with it. Resolve once it has actually exited so callers can await a
    // clean teardown instead of racing app exit.
    const killGroup = (signal: NodeJS.Signals) => {
      try {
        process.kill(-pid, signal); // negative pid → the whole group
      } catch {
        // Group already gone (ESRCH) — fall back to the bare child, ignore if dead.
        try {
          child.kill(signal);
        } catch {
          // Already exited
        }
      }
    };

    return new Promise<void>((resolve) => {
      let settled = false;
      const done = () => {
        if (settled) return;
        settled = true;
        resolve();
      };
      child.once("exit", done);
      killGroup("SIGTERM");
      const escalate = setTimeout(() => {
        if (settled) return; // exited within the grace window — nothing to force
        killGroup("SIGKILL");
        // Resolve shortly after the force-kill even if 'exit' is slow to fire,
        // so an awaiting caller is never blocked indefinitely.
        setTimeout(done, 200);
      }, GRACEFUL_SHUTDOWN_MS);
      // Don't let the escalation timer keep the event loop alive on its own.
      escalate.unref?.();
    });
  };

  return {
    name,
    pid: child.pid ?? null,
    port,
    stop,
  };
};

/**
 * @deprecated Use startSidecar instead.
 */
export const startSidecarSkeleton = startSidecar;
