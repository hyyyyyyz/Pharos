import { useTheme } from "./hooks/useTheme";
import { Library } from "./components/Library";
import "./App.css";

export default function App() {
  const { theme, toggle } = useTheme();

  return (
    <div className="app">
      <header className="topbar xz-container">
        <div className="brand">
          <span className="xz-seal brand-seal">玄奘</span>
          <span className="brand-name xz-gild">Xuanzang</span>
        </div>
        <button className="xz-btn theme-toggle" onClick={toggle} aria-label="切换昼夜主题">
          {theme === "dark" ? "☾ 夜" : "☀ 昼"}
        </button>
      </header>

      <main className="xz-container app-main">
        <section className="intro">
          <p className="xz-eyebrow">大唐 · 译经 · 求真</p>
          <h1 className="intro-title">
            <span className="xz-gild">译 场</span>
          </h1>
          <p className="intro-lede xz-muted">拿一篇英文论文，完全保留排版译成中文。</p>
        </section>

        <Library />
      </main>

      <footer className="foot xz-container xz-faint">
        玄奘 · Xuanzang — AGPL-3.0 · 以 BabelDOC 为译经之引擎
      </footer>
    </div>
  );
}
