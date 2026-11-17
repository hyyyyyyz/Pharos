import type { ReactNode } from "react";
import { Icons } from "../../design/icons";

type IconComponent = (typeof Icons)["library"];

interface Action {
  label: string;
  onClick: () => void;
}

interface EmptyStateProps {
  /** The module's own icon — never a spark or a star. */
  icon: IconComponent;
  title: ReactNode;
  description?: ReactNode;
  primary?: Action;
  secondary?: Action;
  /** An error is told in words (`--c-err` on the title), not with a red block. */
  tone?: "neutral" | "error";
  /** Fill the parent and centre (default), or sit inline at the top. */
  inline?: boolean;
}

/**
 * Nothing here, nothing selected, or it went wrong. One component for all of
 * them so the icon, the two lines and the at-most-two actions look the same
 * wherever the user lands on an empty screen.
 */
export function EmptyState({
  icon: Icon,
  title,
  description,
  primary,
  secondary,
  tone = "neutral",
  inline = false,
}: EmptyStateProps): JSX.Element {
  return (
    <div className={"ph-empty" + (inline ? " is-inline" : "")} role={tone === "error" ? "alert" : undefined}>
      <span className="ph-empty-icon" aria-hidden>
        <Icon size={24} />
      </span>
      <div className={"ph-empty-title" + (tone === "error" ? " is-error" : "")}>{title}</div>
      {description !== undefined && <div className="ph-empty-desc">{description}</div>}
      {(primary !== undefined || secondary !== undefined) && (
        <div className="ph-empty-actions">
          {primary !== undefined && (
            <button type="button" className="ph-btn is-primary" onClick={primary.onClick}>
              {primary.label}
            </button>
          )}
          {secondary !== undefined && (
            <button type="button" className="ph-btn" onClick={secondary.onClick}>
              {secondary.label}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

/** Loading is shown as the rows that are about to arrive, not as the word. */
export function SkeletonRows({ rows = 6 }: { rows?: number }): JSX.Element {
  return (
    <div className="ph-skel" aria-busy="true" aria-label="载入中">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="ph-skel-row">
          <span className="ph-skel-line" style={{ width: `${68 - ((i * 13) % 30)}%` }} />
          <span className="ph-skel-line is-short" />
        </div>
      ))}
    </div>
  );
}
