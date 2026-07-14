import { useUI } from "../store";
import { Settings } from "./Settings";
import "./Landing.css";

interface Module {
  glyph: string;
  title: string;
  desc: string;
  live: boolean;
}

const MODULES: Module[] = [
  { glyph: "读", title: "论文阅读 · 翻译", desc: "英文论文完全保留排版译成中文，双语对照精读。", live: true },
  { glyph: "航", title: "领航 · AI 精读问答", desc: "论文旁的丝滑 AI 对话，边读边问，直抵要义。", live: true },
  { glyph: "库", title: "文献管理 · Zotero 互通", desc: "连接你的本地 Zotero，一处管理全部文献。", live: false },
  { glyph: "检", title: "文献检索", desc: "跨库检索 arXiv 与期刊，一键入库开译。", live: false },
  { glyph: "识", title: "研究知识库", desc: "沉淀想法、笔记与文献关系的研究 wiki。", live: false },
  { glyph: "写", title: "写作助手", desc: "从综述到成稿，全流程科研写作辅助。", live: false },
];

export function Landing() {
  const setView = useUI((s) => s.setView);
  const enter = () => setView("read");

  return (
    <div className="landing">
      <header className="landing-top xz-container">
        <div className="brand">
          <span className="xz-seal brand-seal">P</span>
          <span className="brand-name xz-gild">Pharos</span>
        </div>
        <Settings />
      </header>

      <main className="landing-hero">
        <div className="xz-container">
          <p className="xz-eyebrow xz-ink-in">一体化科研平台 · Research Platform</p>
          <h1 className="hero-word xz-ink-in xz-delay-1">
            <span className="xz-gild">Pharos</span>
          </h1>
          <p className="hero-lede xz-ink-in xz-delay-2">
            照亮你的科研航路——检索、阅读、翻译、问答，尽在一处灯塔之下。
          </p>
          <div className="hero-cta xz-ink-in xz-delay-3">
            <button className="xz-btn xz-btn--primary" onClick={enter}>
              进入论文阅读 →
            </button>
            <a className="xz-btn xz-btn--gold" href="https://github.com/hyyyyyyz/Pharos" target="_blank" rel="noreferrer">
              GitHub · 开源
            </a>
          </div>
        </div>
      </main>

      <section className="landing-modules xz-container">
        <div className="xz-rule">
          <span>模 块</span>
        </div>
        <div className="module-grid">
          {MODULES.map((m, i) => (
            <button
              key={m.title}
              className={`module xz-card xz-card--gilt xz-ink-in xz-delay-${(i % 4) + 1}${
                m.live ? " xz-card--hover is-live" : " is-soon"
              }`}
              onClick={m.live ? enter : undefined}
              disabled={!m.live}
            >
              <div className="module-top">
                <span className="module-glyph">{m.glyph}</span>
                <span className={`xz-tag${m.live ? "" : " tag-soon"}`}>{m.live ? "可用" : "即将"}</span>
              </div>
              <h3 className="module-title">{m.title}</h3>
              <p className="module-desc xz-muted">{m.desc}</p>
            </button>
          ))}
        </div>
      </section>

      <footer className="landing-foot xz-container xz-faint">
        Pharos — AGPL-3.0 · 以 BabelDOC 为翻译引擎 · 灯塔照亮文献之海
      </footer>
    </div>
  );
}
