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

  setChecking: () => void;
  setAvailable: (version: string) => void;
  setNotAvailable: () => void;
  setProgress: (progress: number, bytesPerSecond: number) => void;
  setDownloaded: () => void;
  setError: (message: string) => void;
  reset: () => void;
}

const initial = {
  status: "idle" as UpdaterStatus,
  version: null as string | null,
  progress: 0,
  bytesPerSecond: 0,
  errorMessage: null as string | null,
};

export const useUpdaterStore = create<UpdaterState>((set) => ({
  ...initial,

  setChecking: () => set({ status: "checking", errorMessage: null }),
  setAvailable: (version: string) =>
    set({ status: "available", version, errorMessage: null }),
  setNotAvailable: () => set({ status: "idle" }),
  setProgress: (progress: number, bytesPerSecond: number) =>
    set({ status: "downloading", progress, bytesPerSecond }),
  setDownloaded: () => set({ status: "downloaded", progress: 100 }),
  setError: (message: string) =>
    set({ status: "error", errorMessage: message }),
  reset: () => set(initial),
}));
