import { Suspense, lazy, useEffect } from "react";
import { isDetailOverlay, useUI } from "./store";
import { themeStyle } from "./design/tokens";
import { Rail } from "./components/Rail";
import { TabBar } from "./components/TabBar";
import { CollectionTree } from "./components/CollectionTree";
import { ItemList } from "./components/ItemList";
import { DetailPanel } from "./components/DetailPanel";
import { ModuleShell } from "./components/shell";
import { ComingSoon } from "./components/ComingSoon";
import { SettingsModal } from "./components/SettingsModal";
import "./App.css";

// Everything below the library shell loads on demand. ReadingView drags in
// pdf.js, the four annotation layers and their canvas code, which is by far the
// heaviest part of the bundle and is not needed to show the library list.
const lazyNamed = <K extends string>(load: () => Promise<Record<K, React.ComponentType<any>>>, name: K) =>
  lazy(() => load().then((m) => ({ default: m[name] })));
const ReadingView = lazyNamed(() => import("./components/ReadingView"), "ReadingView") as React.ComponentType<{
  paperId: string;
}>;
const DailyView = lazyNamed(() => import("./components/DailyView"), "DailyView");
const DiscoveryView = lazyNamed(() => import("./components/DiscoveryView"), "DiscoveryView");
const ProjectsView = lazyNamed(() => import("./components/ProjectsView"), "ProjectsView");
const HarnessRunCenter = lazyNamed(() => import("./components/HarnessRunCenter"), "HarnessRunCenter");
const AdminView = lazyNamed(() => import("./components/AdminView"), "AdminView");

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
        <Suspense fallback={<div className="ph-lazy-fallback" aria-busy="true" />}>
        {activeModule === "library" ? (
          <>
            <TabBar />
            {activeTab && activeTab.kind === "paper" ? (
              <ReadingView key={activeTab.id} paperId={activeTab.paperId} />
            ) : (
              <ModuleShell
                side={<CollectionTree />}
                detail={<DetailPanel />}
                detailKey="library"
                detailOverlay={detailOverlay}
                detailOpen={libDetailOpen}
                onDetailClose={() => setLibDetail(false)}
              >
                <ItemList />
              </ModuleShell>
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
        </Suspense>
      </main>
      <SettingsModal />
    </div>
  );
}
