import { app } from "electron";
import { createServiceManager } from "../services/mod";
import { createDesktopRuntime } from "./services";

type DesktopRuntime = ReturnType<typeof createDesktopRuntime>;

let _desktopRuntime: DesktopRuntime | null = null;

export const getDesktopRuntime = () => {
  if (!_desktopRuntime) {
    _desktopRuntime = createDesktopRuntime(
      createServiceManager(app.getPath("userData"), {
        devMode: !app.isPackaged,
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
