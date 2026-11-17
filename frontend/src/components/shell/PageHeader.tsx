import type { ReactNode } from "react";
import { Icons } from "../../design/icons";
import { useShell } from "./ModuleShell";

interface PageHeaderProps {
  title: ReactNode;
  /** One line of meta, beside the title — never a second row. */
  meta?: ReactNode;
  /** Between title and actions: the page's own search or filter. */
  lead?: ReactNode;
  /** Right side: at most one primary button, the rest secondary or icon-only. */
  children?: ReactNode;
}

/** 48px, identical in every module: title and meta left, actions right. */
export function PageHeader({ title, meta, lead, children }: PageHeaderProps): JSX.Element {
  const shell = useShell();
  return (
    <header className="ph-pageheader">
      <h1 className="ph-pageheader-title">{title}</h1>
      {meta !== undefined && meta !== null && <span className="ph-pageheader-meta">{meta}</span>}
      {lead !== undefined && <div className="ph-pageheader-lead">{lead}</div>}
      <div className="ph-pageheader-actions">
        {children}
        {shell.collapsible && (
          <button
            type="button"
            className="ph-iconbtn"
            onClick={shell.toggleDetail}
            title={shell.collapsed ? "展开详情" : "收起详情"}
            aria-label={shell.collapsed ? "展开详情" : "收起详情"}
            aria-pressed={!shell.collapsed}
          >
            <Icons.panelR />
          </button>
        )}
      </div>
    </header>
  );
}
