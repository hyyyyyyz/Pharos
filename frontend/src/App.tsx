import { useEffect, useState } from "react";
import "./App.css";

type Theme = "light" | "dark";

const SAMPLE_PAPERS = [
  { en: "Attention Is All You Need", zh: "注意力就是一切", meta: "NeurIPS · 2017 · 15 页", tag: "已译", seal: "译" },
  { en: "Deep Residual Learning for Image Recognition", zh: "面向图像识别的深度残差学习", meta: "CVPR · 2016 · 12 页", tag: "翻译中", seal: "作" },
  { en: "Denoising Diffusion Probabilistic Models", zh: "去噪扩散概率模型", meta: "NeurIPS · 2020 · 25 页", tag: "待译", seal: "待" },
];

const SWATCHES = [
  { name: "朱砂", css: "var(--tang-cinnabar)" },
  { name: "鎏金", css: "var(--tang-gold)" },
  { name: "青绿", css: "var(--tang-jade)" },
  { name: "赭石", css: "var(--tang-ochre)" },
  { name: "青金", css: "var(--tang-lapis)" },
  { name: "墨", css: "var(--tang-ink)" },
];

export default function App() {
  const [theme, setTheme] = useState<Theme>(() =>
    window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light",
  );
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  return (
    <div className="app">
      <header className="topbar xz-container">
        <div className="brand">
          <span className="xz-seal brand-seal">玄奘</span>
          <span className="brand-name xz-gild">Xuanzang</span>
        </div>
        <button
          className="xz-btn theme-toggle"
          onClick={() => setTheme((t) => (t === "dark" ? "light" : "dark"))}
          aria-label="切换昼夜主题"
        >
          {theme === "dark" ? "☾ 夜" : "☀ 昼"}
        </button>
      </header>

      <main>
        <section className="hero">
          <div className="xz-container">
            <p className="xz-eyebrow xz-ink-in">大唐 · 译经 · 求真</p>
            <h1 className="wordmark xz-ink-in xz-delay-1">
              <span className="xz-gild">玄奘</span>
            </h1>
            <p className="lede xz-ink-in xz-delay-2">
              拿一篇英文论文，<b>完全保留排版</b>地译成中文——分栏、公式、图表原位不动。
              以译经之心，助你读遍寰宇文献。
            </p>
            <div className="hero-actions xz-ink-in xz-delay-3">
              <button className="xz-btn xz-btn--primary">＋ 译一篇论文</button>
              <button className="xz-btn xz-btn--gold">进入译场</button>
            </div>
          </div>
        </section>

        <section className="xz-container section">
          <div className="xz-rule">
            <span>译 场</span>
          </div>
          <div className="grid">
            {SAMPLE_PAPERS.map((p, i) => (
              <article
                key={p.en}
                className={`xz-card xz-card--hover xz-card--gilt paper xz-ink-in xz-delay-${i + 1}`}
              >
                <div className="paper-top">
                  <span className="xz-tag">{p.tag}</span>
                  <span className="xz-seal paper-seal">{p.seal}</span>
                </div>
                <h3 className="paper-zh">{p.zh}</h3>
                <p className="paper-en xz-muted">{p.en}</p>
                <p className="paper-meta xz-faint">{p.meta}</p>
              </article>
            ))}
          </div>
        </section>

        <section className="xz-container section">
          <div className="xz-rule">
            <span>唐 三 彩</span>
          </div>
          <div className="swatches">
            {SWATCHES.map((s) => (
              <div className="swatch" key={s.name}>
                <span className="swatch-chip" style={{ background: s.css }} />
                <span className="swatch-name">{s.name}</span>
              </div>
            ))}
          </div>
          <div className="specimen xz-card">
            <p className="spec-display">春江潮水连海平，海上明月共潮生</p>
            <p className="spec-serif">
              主流的序列转换模型基于复杂的循环或卷积神经网络。我们提出一种全新的简单网络架构
              <b> Transformer</b>，它完全建立在注意力机制之上，彻底摒弃了循环与卷积。
            </p>
            <p className="spec-sans xz-faint">
              Songti / Kaiti / PingFang · 昼夜双主题 · 鎏金流光 · 水墨晕染 · 朱印
            </p>
          </div>
        </section>
      </main>

      <footer className="foot xz-container xz-faint">
        玄奘 · Xuanzang — AGPL-3.0 · 以 BabelDOC 为译经之引擎
      </footer>
    </div>
  );
}
