import { useEffect } from "react";
import { DESKTOP_EVENTS } from "../../preload/channels";
import { useUpdaterStore } from "@valuz/core";

type DesktopBridge = {
  invoke: <T>(ch: string, args?: unknown) => Promise<T>;
  on: (event: string, handler: (payload: unknown) => void) => void;
  off: (event: string, handler: (payload: unknown) => void) => void;
};

const getBridge = (): DesktopBridge | null =>
  (window as Window & { valuzDesktop?: DesktopBridge }).valuzDesktop ?? null;

interface AvailableInfo {
  version?: string;
}

interface ProgressInfo {
  percent?: number;
  bytesPerSecond?: number;
}

interface DownloadedInfo {
  version?: string;
}

interface ErrorInfo {
  message?: string;
}

/**
 * Mounted once at the renderer root. Listens to the main process's
 * autoUpdater lifecycle events and populates the updater Zustand store
 * so the UI (UpdateButton) can react.
 */
export const UpdaterListener = () => {
  const store = useUpdaterStore;

  useEffect(() => {
    const bridge = getBridge();
    if (!bridge) return;

    const onChecking = () => {
      store.getState().setChecking();
    };

    const onAvailable = (payload: unknown) => {
      const info = (payload ?? {}) as AvailableInfo;
      store.getState().setAvailable(info.version ?? "unknown");
    };

    const onNotAvailable = () => {
      store.getState().setNotAvailable();
    };

    const onProgress = (payload: unknown) => {
      const info = (payload ?? {}) as ProgressInfo;
      store.getState().setProgress(
        info.percent ?? 0,
        info.bytesPerSecond ?? 0,
      );
    };

    const onDownloaded = (payload: unknown) => {
      const info = (payload ?? {}) as DownloadedInfo;
      const s = store.getState();
      s.setDownloaded();
      if (info.version && !s.version) {
        store.setState({ version: info.version });
      }
    };

    const onError = (payload: unknown) => {
      const info = (payload ?? {}) as ErrorInfo;
      store.getState().setError(info.message ?? "Unknown error");
    };

    bridge.on(DESKTOP_EVENTS.updaterChecking, onChecking);
    bridge.on(DESKTOP_EVENTS.updaterAvailable, onAvailable);
    bridge.on(DESKTOP_EVENTS.updaterNotAvailable, onNotAvailable);
    bridge.on(DESKTOP_EVENTS.updaterProgress, onProgress);
    bridge.on(DESKTOP_EVENTS.updaterDownloaded, onDownloaded);
    bridge.on(DESKTOP_EVENTS.updaterError, onError);

    return () => {
      bridge.off(DESKTOP_EVENTS.updaterChecking, onChecking);
      bridge.off(DESKTOP_EVENTS.updaterAvailable, onAvailable);
      bridge.off(DESKTOP_EVENTS.updaterNotAvailable, onNotAvailable);
      bridge.off(DESKTOP_EVENTS.updaterProgress, onProgress);
      bridge.off(DESKTOP_EVENTS.updaterDownloaded, onDownloaded);
      bridge.off(DESKTOP_EVENTS.updaterError, onError);
    };
  }, [store]);

  return null;
};
