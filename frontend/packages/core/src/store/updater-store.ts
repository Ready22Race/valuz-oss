import { create } from "zustand";

export type UpdaterStatus =
  | "idle"
  | "checking"
  | "available"
  | "downloading"
  | "downloaded"
  | "error";

export interface UpdaterState {
  status: UpdaterStatus;
  version: string | null;
  progress: number;
  bytesPerSecond: number;
  errorMessage: string | null;
  /** Whether the current error should appear in the floating toast. False for
   *  the About-page check (it shows its own inline error); true for menu/tray
   *  checks and downloads, where the toast is the only feedback surface. Only
   *  meaningful while ``status === "error"``. */
  errorInToast: boolean;
  /** User hid the in-app update toast. Re-shown when a new lifecycle event
   *  arrives (available / downloaded) or via show(). */
  dismissed: boolean;

  setChecking: () => void;
  setAvailable: (version: string) => void;
  setNotAvailable: () => void;
  /** Optimistically flip to "downloading" at 0% the instant the user clicks
   *  download, so the progress bar appears immediately instead of waiting for
   *  the first ``download-progress`` event (which can lag a beat). */
  setDownloading: () => void;
  setProgress: (progress: number, bytesPerSecond: number) => void;
  setDownloaded: () => void;
  /** ``toast`` controls whether the error also pops the floating toast. */
  setError: (message: string, toast?: boolean) => void;
  dismiss: () => void;
  show: () => void;
  reset: () => void;
}

const initial = {
  status: "idle" as UpdaterStatus,
  version: null as string | null,
  progress: 0,
  bytesPerSecond: 0,
  errorMessage: null as string | null,
  errorInToast: false,
  dismissed: false,
};

export const useUpdaterStore = create<UpdaterState>((set) => ({
  ...initial,

  setChecking: () =>
    set({ status: "checking", errorMessage: null, errorInToast: false }),
  setAvailable: (version: string) =>
    set({ status: "available", version, errorMessage: null, dismissed: false }),
  setNotAvailable: () => set({ status: "idle" }),
  setDownloading: () =>
    set({ status: "downloading", progress: 0, errorMessage: null }),
  setProgress: (progress: number, bytesPerSecond: number) =>
    set({ status: "downloading", progress, bytesPerSecond }),
  setDownloaded: () =>
    set({ status: "downloaded", progress: 100, dismissed: false }),
  setError: (message: string, toast = false) =>
    set(
      toast
        ? {
            status: "error",
            errorMessage: message,
            errorInToast: true,
            dismissed: false,
          }
        : { status: "error", errorMessage: message, errorInToast: false },
    ),
  dismiss: () => set({ dismissed: true }),
  show: () => set({ dismissed: false }),
  reset: () => set(initial),
}));
