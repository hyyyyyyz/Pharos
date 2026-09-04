import { useEffect } from "react";
import { isDetailOverlay, useUI } from "./store";
import { themeStyle } from "./design/tokens";
import { Rail } from "./components/Rail";
import { TabBar } from "./components/TabBar";
import { CollectionTree } from "./components/CollectionTree";
import { ItemList } from "./components/ItemList";
import { DetailPanel } from "./components/DetailPanel";
import { ReadingView } from "./components/ReadingView";
import { DailyView } from "./components/DailyView";
import { DiscoveryView } from "./components/DiscoveryView";
import { ProjectsView } from "./components/ProjectsView";
import { HarnessRunCenter } from "./components/HarnessRunCenter";
import { AdminView } from "./components/AdminView";
import { ComingSoon } from "./components/ComingSoon";
import { SettingsModal } from "./components/SettingsModal";
import "./App.css";

/**
 * The single workbench shell: module rail | tabbed module pane.
 * There is no separate landing page — the app opens straight into 文库,
 * the way an IDE opens straight into a workspace.
 */
export default function App() {
  const theme = useUI((s) => s.theme);
  const accent = useUI((s) => s.accent);
  const activeModule = useUI((s) => s.activeModule);
  const tabs = useUI((s) => s.tabs);
  const activeTabId = useUI((s) => s.activeTabId);

  const setWinW = useUI((s) => s.setWinW);

  // Keep native chrome (scrollbars, form controls, caret) in step with the theme.
  useEffect(() => {
    document.documentElement.style.colorScheme = theme;
  }, [theme]);

  // The AI 对话 panel auto-shows above 1200px, so the width has to stay live —
  // reading it once at store-init would freeze the layout at the load-time size.
  useEffect(() => {
    const onResize = () => setWinW(window.innerWidth);
    onResize();
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [setWinW]);

  const activeTab = tabs.find((t) => t.id === activeTabId) ?? tabs[0];

  // On a touch device below the detail-overlay breakpoint the 280px third
  // column becomes a slide-over; on desktop the pane stays put and neither the
  // backdrop nor the close button exist.
  const detailOverlay = useUI(isDetailOverlay);
  const libDetailOpen = useUI((s) => s.libDetailOpen);
  const setLibDetail = useUI((s) => s.setLibDetail);

  return (
    <div className="ph-root" style={themeStyle(theme, accent)}>
      <Rail />
      <main className="ph-main">
        {activeModule === "library" ? (
          <>
            <TabBar />
            {activeTab && activeTab.kind === "paper" ? (
              <ReadingView key={activeTab.id} paperId={activeTab.paperId} />
            ) : (
              <div className="ph-libview">
                <CollectionTree />
                <ItemList />
                {detailOverlay ? (
                  libDetailOpen && (
                    <>
                      <div
                        className="ph-libview-backdrop"
                        onClick={() => setLibDetail(false)}
                      />
                      <DetailPanel />
                    </>
                  )
                ) : (
                  <DetailPanel />
                )}
              </div>
            )}
          </>
        ) : activeModule === "daily" ? (
          // 每日论文 is its own module, not a 文库 tab — it owns the whole main
          // area, so no <TabBar /> here.
          <DailyView />
        ) : activeModule === "search" ? (
          <DiscoveryView />
        ) : activeModule === "kb" ? (
          <ProjectsView />
        ) : activeModule === "runs" ? (
          <HarnessRunCenter />
        ) : activeModule === "admin" ? (
          <AdminView />
        ) : (
          <ComingSoon module={activeModule} />
        )}
      </main>
      <SettingsModal />
    </div>
  );
}
