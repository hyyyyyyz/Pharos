import { useEffect } from "react";
import { useUI } from "./store";
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
import { ComingSoon } from "./components/ComingSoon";
import { SettingsModal } from "./components/SettingsModal";
import { DesktopOAuthBridge } from "./components/DesktopOAuthBridge";
import { DesktopExternalLinks } from "./components/DesktopExternalLinks";
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

  // The 领航 panel auto-shows above 1200px, so the width has to stay live —
  // reading it once at store-init would freeze the layout at the load-time size.
  useEffect(() => {
    const onResize = () => setWinW(window.innerWidth);
    onResize();
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [setWinW]);

  const activeTab = tabs.find((t) => t.id === activeTabId) ?? tabs[0];

  return (
    <div className="ph-root" style={themeStyle(theme, accent)}>
      <Rail />
      <main className="ph-main">
        {activeModule === "library" ? (
          <>
            <TabBar />
            {activeTab && activeTab.kind === "paper" ? (
              <ReadingView
                key={activeTab.id}
                paperId={activeTab.paperId}
                initialLocalAttachmentId={activeTab.localAttachmentId}
              />
            ) : (
              <div className="ph-libview">
                <CollectionTree />
                <ItemList />
                <DetailPanel />
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
        ) : (
          <ComingSoon module={activeModule} />
        )}
      </main>
      <DesktopExternalLinks />
      <DesktopOAuthBridge />
      <SettingsModal />
    </div>
  );
}
