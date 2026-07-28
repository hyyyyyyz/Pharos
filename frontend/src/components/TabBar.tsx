import { useQueries, useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { Icons } from "../design/icons";
import { isLocalZoteroPaperId, localZotero, localZoteroAvailable } from "../lib/localZotero";
import { parseZoteroItemId, zotero, zoteroAvailable } from "../lib/zotero";
import { useUI } from "../store";
import "./TabBar.css";

/** The browser-style tab strip above the 文库 module. */
export function TabBar(): JSX.Element {
  const tabs = useUI((s) => s.tabs);
  const activeTabId = useUI((s) => s.activeTabId);
  const setTab = useUI((s) => s.setTab);
  const closeTab = useUI((s) => s.closeTab);

  const { data: papers } = useQuery({ queryKey: ["papers"], queryFn: api.listPapers });
  const { data: localPapers } = useQuery({
    queryKey: ["zotero-local", "papers"],
    queryFn: localZotero.list,
    enabled:
      localZoteroAvailable() &&
      tabs.some((tab) => tab.kind === "paper" && isLocalZoteroPaperId(tab.paperId)),
  });
  const mirrorRefs = tabs.map((tab) =>
    tab.kind === "paper" ? parseZoteroItemId(tab.paperId) : null,
  );
  const mirrorDetails = useQueries({
    queries: mirrorRefs.map((reference) => ({
      queryKey: [
        "zotero-mirror",
        "item",
        reference?.sourceId,
        reference?.libraryId,
        reference?.itemKey,
      ],
      queryFn: () => zotero.item(reference!),
      enabled: zoteroAvailable() && reference !== null,
    })),
  });

  return (
    <div className="ph-tabbar">
      {tabs.map((tab, index) => {
        const active = tab.id === activeTabId;
        const isLibTab = tab.kind === "library";
        const local = !isLibTab && isLocalZoteroPaperId(tab.paperId);
        const mirror = mirrorRefs[index] !== null;
        const paper =
          isLibTab || local || mirror ? undefined : papers?.find((p) => p.id === tab.paperId);
        const localPaper =
          isLibTab || !local ? undefined : localPapers?.find((p) => p.id === tab.paperId);
        const localAttachment =
          isLibTab || !local || !tab.localAttachmentId
            ? undefined
            : localPaper?.pdfAttachments.find(
                (attachment) => attachment.id === tab.localAttachmentId,
              );
        const mirrorDetail = mirrorDetails[index]?.data;
        const mirrorAttachment =
          isLibTab || !tab.localAttachmentId
            ? undefined
            : mirrorDetail?.attachments.find(
                (attachment) => attachment.publicId === tab.localAttachmentId,
              );
        const title = isLibTab
          ? "文库"
          : mirrorAttachment?.filename ??
            mirrorDetail?.item.title ??
            localAttachment?.filename ??
            localPaper?.title ??
            paper?.title ??
            "论文";
        return (
          <div
            key={tab.id}
            className={active ? "ph-tabbar-tab is-active" : "ph-tabbar-tab"}
            onClick={() => setTab(tab.id)}
          >
            <span className="ph-tabbar-icon">
              {isLibTab ? <Icons.library /> : <Icons.file />}
            </span>
            <span className="ph-tabbar-title">{title}</span>
            {!isLibTab && (
              <span
                className="ph-tabbar-close"
                title="关闭"
                onClick={(e) => {
                  e.stopPropagation();
                  closeTab(tab.id);
                }}
              >
                <Icons.close />
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}
