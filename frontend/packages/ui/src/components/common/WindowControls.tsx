import type { CSSProperties } from "react";
import { Minus, Square, Maximize2, X } from "lucide-react";

/**
 * Window control buttons (minimize, maximize/restore, close) for the
 * frameless Electron window on Windows and Linux.  macOS uses native
 * traffic-light buttons instead.
 *
 * Each button is marked ``WebkitAppRegion: "no-drag"`` so it remains
 * clickable even when rendered inside a drag region.
 */
export interface WindowControlsProps {
  onMinimize: () => void;
  onMaximize: () => void;
  onClose: () => void;
  isMaximized?: boolean;
}

const btnBase =
  "inline-flex h-full w-[36px] flex-shrink-0 items-center justify-center text-ink-body transition-colors hover:bg-surface-muted";

const closeBtnBase =
  "inline-flex h-full w-[36px] flex-shrink-0 items-center justify-center text-ink-body transition-colors hover:bg-destructive hover:text-destructive-foreground";

const noDragStyle = { WebkitAppRegion: "no-drag" } as CSSProperties;

export const WindowControls = ({
  onMinimize,
  onMaximize,
  onClose,
  isMaximized = false,
}: WindowControlsProps) => (
  <div
    className="flex h-full shrink-0 pr-2"
    style={{ WebkitAppRegion: "no-drag" } as CSSProperties}
  >
    {/* Minimize */}
    <button
      type="button"
      aria-label="Minimize"
      onClick={onMinimize}
      className={btnBase}
      style={noDragStyle}
    >
      <Minus className="h-[16px] w-[16px]" strokeWidth={2} />
    </button>

    {/* Maximize / Restore */}
    <button
      type="button"
      aria-label={isMaximized ? "Restore" : "Maximize"}
      onClick={onMaximize}
      className={btnBase}
      style={noDragStyle}
    >
      {isMaximized ? (
        <Maximize2 className="h-[14px] w-[14px]" strokeWidth={2} />
      ) : (
        <Square className="h-[14px] w-[14px]" strokeWidth={2} />
      )}
    </button>

    {/* Close — red background + white text on hover */}
    <button
      type="button"
      aria-label="Close"
      onClick={onClose}
      className={closeBtnBase}
      style={noDragStyle}
    >
      <X className="h-[16px] w-[16px]" strokeWidth={2} />
    </button>
  </div>
);
