import type { ReactNode } from "react";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "../../lib/cn";
import { IconBox } from "./IconBox";

const emptyStateVariants = cva("", {
  variants: {
    variant: {
      dashed:
        "rounded-[10px] border border-dashed border-surface-border-hover bg-surface-soft px-5 py-6 text-center",
      plain:
        "flex flex-col items-center text-center",
    },
  },
  defaultVariants: {
    variant: "dashed",
  },
});

export interface EmptyStateProps extends VariantProps<typeof emptyStateVariants> {
  /** Optional icon or illustration above the message */
  icon?: ReactNode;
  /**
   * Primary empty-state message. Alias for `title` — either can be used.
   * When `title` is also provided, `title` takes precedence.
   */
  message?: string;
  /** Primary title (preferred over `message`). */
  title?: string;
  /** Secondary description shown below the title. Only rendered in `plain` variant. */
  description?: string;
  /** Optional call-to-action below the message */
  action?: ReactNode;
  /** Extra class on the outer container */
  className?: string;
}

/**
 * Empty state component with two variants:
 * - `dashed` (default): dashed-border card for inline empty lists
 * - `plain`: centered layout for full-page empty states
 */
export const EmptyState = ({
  icon,
  message,
  title,
  description,
  action,
  variant = "dashed",
  className,
}: EmptyStateProps) => {
  const text = title ?? message ?? "";

  if (variant === "plain") {
    return (
      <div className={cn(emptyStateVariants({ variant }), className)}>
        {icon && (
          <IconBox size="xl" variant="default">
            {icon}
          </IconBox>
        )}
        <p className="mt-3 text-sm font-medium text-ink-heading">{text}</p>
        {description && (
          <p className="mt-1 max-w-[460px] text-xs leading-5 text-ink-body">
            {description}
          </p>
        )}
        {action && <div className="mt-4">{action}</div>}
      </div>
    );
  }

  return (
    <div className={cn(emptyStateVariants({ variant }), className)}>
      {icon && (
        <div className="mb-2 flex justify-center text-ink-meta">{icon}</div>
      )}
      <p className="text-sm text-ink-body">{text}</p>
      {action && <div className="mt-3">{action}</div>}
    </div>
  );
};
