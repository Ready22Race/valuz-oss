import { app, session } from "electron";
import {
  EgressManager,
  resolveInitialEgressMode,
} from "../network/egress-manager";
import {
  readPersistedEgressMode,
  writePersistedEgressMode,
} from "../network/mode-store";
import { publishDevEgressBootstrap } from "../network/bootstrap-file";
import { createServiceManager } from "../services/mod";
import { createDesktopRuntime } from "./services";

type DesktopRuntime = ReturnType<typeof createDesktopRuntime>;

let _desktopRuntime: DesktopRuntime | null = null;

export const getDesktopRuntime = () => {
  if (!_desktopRuntime) {
    const userDataDir = app.getPath("userData");
    const emergencyOverride =
      process.env.VALUZ_EGRESS_MODE?.trim().toLowerCase() === "off";
    const egressManager = new EgressManager({
      mode: resolveInitialEgressMode(
        process.env,
        readPersistedEgressMode(userDataDir),
      ),
      env: process.env,
      resolveSystemProxy: (targetUrl) =>
        session.defaultSession.resolveProxy(targetUrl),
      frontendsEnabled: app.commandLine.hasSwitch(
        "enable-valuz-egress-frontends",
      ) || process.env.VALUZ_EGRESS_FRONTENDS === "1",
      emergencyOverride,
    });
    _desktopRuntime = createDesktopRuntime(
      createServiceManager(app.getPath("userData"), {
        devMode: !app.isPackaged,
        egressManager,
        onEgressModeChanged: (mode) =>
          writePersistedEgressMode(userDataDir, mode),
        publishDevEgressBootstrap:
          !app.isPackaged && process.env.VALUZ_EGRESS_BOOTSTRAP_FILE
            ? (bootstrap) =>
                publishDevEgressBootstrap(
                  process.env.VALUZ_EGRESS_BOOTSTRAP_FILE as string,
                  bootstrap,
                )
            : undefined,
      }),
    );
  }
  return _desktopRuntime;
};

/** Convenience alias — safe after app.whenReady(). */
export const desktopRuntime = new Proxy(
  {} as DesktopRuntime,
  {
    get(_target, prop) {
      return Reflect.get(getDesktopRuntime(), prop);
    },
  },
);
