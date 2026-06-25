import type { ReactNode } from "react";
import { cn } from "../../lib/cn";

export interface EmptyStateProps {
  /** Optional icon above the message */
  icon?: ReactNode;
  /** Primary empty-state title */
  title?: string;
  /** Optional supporting copy */
  message?: string;
  /** Optional call-to-action below the message */
  action?: ReactNode;
  /** Extra class on the outer container */
  className?: string;
  /** Extra class on the icon well */
  iconClassName?: string;
}

/**
 * Compact empty state following the design system's 09 Empty State pattern.
 * Page-level usage should provide an icon, title, optional description, and CTA.
 */
export const EmptyState = ({
  icon,
  title,
  message,
  action,
  className,
  iconClassName,
}: EmptyStateProps) => {
  const heading = title ?? message;
  const description = title ? message : undefined;

  return (
    <div
      className={cn(
        "mx-auto flex w-[300px] flex-col items-center px-5 py-8 text-center",
        className,
      )}
    >
      {icon && (
        <div
          className={cn(
            "mb-3 flex h-10 w-10 items-center justify-center rounded-[10px] bg-[color:var(--fg-3)] text-[color:var(--fg-50)] [&_svg]:h-5 [&_svg]:w-5",
            iconClassName,
          )}
        >
          {icon}
        </div>
      )}
      {heading && (
        <b className="block text-[13px] font-semibold text-ink-heading">
          {heading}
        </b>
      )}
      {description && (
        <p className="mt-1 mb-3 text-xs leading-[1.6] text-[color:var(--fg-60)]">
          {description}
        </p>
      )}
      {action && <div className={description ? "" : "mt-3"}>{action}</div>}
    </div>
  );
};
