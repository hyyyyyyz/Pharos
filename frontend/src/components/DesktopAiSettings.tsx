import { useEffect, useState } from "react";
import {
  paperChat,
  paperChatAvailable,
  paperChatError,
  paperChatIsDesktop,
  type CodexCapabilities,
  type ProviderStatus,
  type WorkspaceStatus,
} from "../lib/paperChat";
import "./DesktopAiSettings.css";

export function AiSettings(): JSX.Element {
  const available = paperChatAvailable();
  const desktop = paperChatIsDesktop();
  const [status, setStatus] = useState<ProviderStatus | null>(null);
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [temperature, setTemperature] = useState("0.25");
  const [maxTokens, setMaxTokens] = useState("4096");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!available) return;
    let cancelled = false;
    void paperChat.providerStatus().then(
      (next) => {
        if (cancelled) return;
        setStatus(next);
        setBaseUrl(next.baseUrl);
        setModel(next.model);
        setTemperature(String(next.temperature));
        setMaxTokens(String(next.maxOutputTokens ?? 4096));
      },
      (cause) => { if (!cancelled) setError(paperChatError(cause)); },
    );
    return () => { cancelled = true; };
  }, [available]);

  if (!available) {
    return <div className="ph-native-empty">当前环境无法连接 AI 对话设置，请刷新后重试。</div>;
  }

  const personalCredential = desktop
    ? Boolean(status?.hasCredential)
    : status?.source === "personal" && Boolean(status.hasCredential);
  const canStoreCredential = desktop || status?.canStoreCredential !== false;
  const sourceLabel = status === null
    ? "读取中"
    : desktop
      ? status.configured ? "已连接" : "未配置"
      : status.source === "personal"
        ? "个人模型"
        : status.source === "server"
          ? "服务器模型"
          : "未配置";

  const save = async (): Promise<void> => {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const parsedTemperature = Number(temperature);
      const parsedTokens = Number(maxTokens);
      if (!Number.isFinite(parsedTemperature)) throw new Error("Temperature 不是有效数字。");
      if (!Number.isInteger(parsedTokens) || parsedTokens < 256 || parsedTokens > 128_000) {
        throw new Error("最大输出 Token 需要在 256 到 128000 之间。");
      }
      const next = await paperChat.saveProvider({
        baseUrl,
        model,
        temperature: parsedTemperature,
        maxOutputTokens: parsedTokens,
        apiKey: apiKey.trim() || null,
      });
      setStatus(next);
      setApiKey("");
      setMessage("模型配置已保存。新论文会在打开后自动建立理解档案。");
      window.dispatchEvent(new Event("pharos:model-provider-updated"));
    } catch (cause) {
      setError(paperChatError(cause));
    } finally {
      setBusy(false);
    }
  };

  const clear = async (): Promise<void> => {
    if (!window.confirm(`${desktop ? "删除本机保存的" : "删除账户中的个人"}模型配置和 API Key？已有对话不会被删除。`)) return;
    setBusy(true);
    try {
      await paperChat.clearProvider();
      const next = await paperChat.providerStatus();
      setStatus(next);
      setBaseUrl(next.baseUrl);
      setModel(next.model);
      setApiKey("");
      setTemperature(String(next.temperature));
      setMaxTokens(String(next.maxOutputTokens ?? 4096));
      setMessage(next.source === "server" ? "个人配置已清除，已恢复服务器模型。" : "模型配置已清除。");
      setError(null);
      window.dispatchEvent(new Event("pharos:model-provider-updated"));
    } catch (cause) {
      setError(paperChatError(cause));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className="ph-set-h">AI 对话</div>
      <div className="ph-native-card">
        <div className="ph-native-card-head">
          <div>
            <strong>OpenAI 兼容模型</strong>
            <span>{desktop
              ? "论文理解和问答直接由客户端请求你的接口。"
              : "论文理解和问答由 Pharos 后端安全转发，浏览器不直接接触密钥。"}</span>
          </div>
          <span className={`ph-native-state${status?.configured ? " is-on" : ""}`}>
            {sourceLabel}
          </span>
        </div>

        <label className="ph-native-field">
          <span>API Base URL</span>
          <input value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} placeholder="https://example.com/v1" autoCapitalize="none" spellCheck={false} />
        </label>
        <label className="ph-native-field">
          <span>模型</span>
          <input value={model} onChange={(event) => setModel(event.target.value)} placeholder="gpt-5.2 / deepseek-chat / 兼容模型名" autoCapitalize="none" spellCheck={false} />
        </label>
        <label className="ph-native-field">
          <span>API Key</span>
          <input
            type="password"
            value={apiKey}
            disabled={!canStoreCredential}
            onChange={(event) => setApiKey(event.target.value)}
            placeholder={personalCredential
              ? "已保存；留空表示不更换"
              : status?.source === "server"
                ? "输入 API Key 以建立个人配置"
                : "输入 API Key"}
            autoComplete="off"
          />
        </label>
        <div className="ph-native-grid">
          <label className="ph-native-field">
            <span>Temperature</span>
            <input inputMode="decimal" value={temperature} onChange={(event) => setTemperature(event.target.value)} />
          </label>
          <label className="ph-native-field">
            <span>最大输出 Token</span>
            <input inputMode="numeric" value={maxTokens} onChange={(event) => setMaxTokens(event.target.value)} />
          </label>
        </div>
        <div className="ph-native-security">
          {desktop
            ? "API Key 只进入系统凭据库，不会写入 Workspace、浏览器存储、日志或 Git。"
            : canStoreCredential
              ? "API Key 只提交给 Pharos 后端加密保存，不进入浏览器存储，接口也不会回传明文。"
              : "服务器尚未配置凭据加密密钥，目前只能使用管理员提供的服务器模型，不能保存个人 API Key。"}
        </div>
        <div className="ph-native-actions">
          <button
            className="is-primary"
            disabled={
              busy ||
              status === null ||
              !canStoreCredential ||
              !baseUrl.trim() ||
              !model.trim() ||
              (!personalCredential && !apiKey.trim())
            }
            onClick={() => void save()}
          >
            {busy ? "保存中…" : status?.source === "server" ? "保存为个人配置" : "保存配置"}
          </button>
          {(desktop ? status?.configured : status?.source === "personal") && (
            <button disabled={busy} onClick={() => void clear()}>
              {desktop ? "清除配置" : "清除个人配置"}
            </button>
          )}
        </div>
      </div>
      {message && <div className="ph-native-message is-ok">{message}</div>}
      {error && <div className="ph-native-message is-error">{error}</div>}
    </>
  );
}

export function DataSettings(): JSX.Element {
  const desktop = paperChatIsDesktop();
  const [workspace, setWorkspace] = useState<WorkspaceStatus | null>(null);
  const [codex, setCodex] = useState<CodexCapabilities | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!desktop) return;
    let cancelled = false;
    void Promise.all([paperChat.workspaceStatus(), paperChat.codexCapabilities()]).then(
      ([nextWorkspace, nextCodex]) => {
        if (cancelled) return;
        setWorkspace(nextWorkspace);
        setCodex(nextCodex);
      },
      (cause) => { if (!cancelled) setError(paperChatError(cause)); },
    );
    return () => { cancelled = true; };
  }, [desktop]);

  if (!desktop) {
    return (
      <>
        <div className="ph-set-h">数据与互通</div>
        <div className="ph-native-card">
          <div className="ph-native-card-head">
            <div>
              <strong>账户研究数据</strong>
              <span>网页端数据按账户隔离保存，并在你登录的浏览器设备间同步。</span>
            </div>
            <span className="ph-native-state is-on">云端同步</span>
          </div>
          <div className="ph-native-kv"><span>论文与文库</span><strong>上传文件、元数据与阅读状态</strong></div>
          <div className="ph-native-kv"><span>AI 对话</span><strong>论文理解档案与独立会话历史</strong></div>
          <div className="ph-native-kv"><span>研究数据</span><strong>每日论文、标注、笔记与项目记录</strong></div>
          <p className="ph-native-copy">
            网页端不会自动读取你电脑上的任意目录。需要使用本机 Zotero PDF 或可迁移的标准 Workspace 时，请使用 Pharos 客户端。
          </p>
        </div>

        <div className="ph-native-card">
          <div className="ph-native-card-head">
            <div>
              <strong>客户端本地桥接</strong>
              <span>这些能力必须由安装在电脑上的 Pharos 获得明确的本地权限。</span>
            </div>
            <span className="ph-native-state">客户端专属</span>
          </div>
          <div className="ph-native-kv"><span>本地 Zotero</span><strong>分类、条目、附件与未上传云端的 PDF</strong></div>
          <div className="ph-native-kv"><span>Workspace</span><strong>完整目录迁移、备份和离线数据</strong></div>
          <div className="ph-native-kv"><span>Codex</span><strong>扫描本机历史并创建真实 Codex 任务</strong></div>
          <div className="ph-native-actions ph-native-actions--spaced">
            <a
              className="ph-native-action-link is-primary"
              href="https://hyyyyyyz.github.io/Pharos/download.html"
              target="_blank"
              rel="noreferrer"
            >
              下载 Pharos 客户端
            </a>
          </div>
        </div>
      </>
    );
  }

  const relocate = async (): Promise<void> => {
    const { open } = await import("@tauri-apps/plugin-dialog");
    const destination = await open({ directory: true, multiple: false, title: "选择新的 Pharos Workspace" });
    if (typeof destination !== "string") return;
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const result = await paperChat.relocateWorkspace(destination);
      setMessage(`${result.copied ? "Workspace 已完整复制" : "已切换到现有 Workspace"}：${result.root}。退出并重新打开 Pharos 后生效。`);
      setWorkspace((current) => current ? { ...current, configuredRoot: result.root, requiresRestart: true } : current);
    } catch (cause) {
      setError(paperChatError(cause));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className="ph-set-h">数据与互通</div>
      <div className="ph-native-card">
        <div className="ph-native-card-head">
          <div>
            <strong>Pharos Workspace</strong>
            <span>所有可迁移的本地研究数据统一存放在这里。</span>
          </div>
          <span className={`ph-native-state${workspace && !workspace.requiresRestart ? " is-on" : ""}`}>
            {workspace?.requiresRestart ? "等待重启" : "本地优先"}
          </span>
        </div>
        <div className="ph-native-path" title={workspace?.root}>{workspace?.root ?? "正在读取…"}</div>
        <pre className="ph-native-tree">{`Pharos Workspace/
├── database/       索引与 Zotero 镜像
├── library/        论文对象、元数据与论文理解
├── daily/          每日论文
├── conversations/  AI 对话 JSONL
├── annotations/    标注与笔记
├── interchange/    Codex 导入与导出
├── backups/        一致性备份
└── cache · logs · tmp`}</pre>
        <div className="ph-native-actions">
          <button className="is-primary" disabled={busy} onClick={() => void relocate()}>{busy ? "迁移中…" : "迁移 Workspace"}</button>
          <button
            disabled={!workspace}
            onClick={() => {
              if (!workspace) return;
              void import("@tauri-apps/plugin-opener").then(({ revealItemInDir }) => revealItemInDir(workspace.root));
            }}
          >
            在 Finder 中显示
          </button>
        </div>
      </div>

      <div className="ph-native-card">
        <div className="ph-native-card-head">
          <div>
            <strong>Codex 对话互通</strong>
            <span>Pharos 只读取可见的用户/助手消息，不读取工具输出、推理或认证文件。</span>
          </div>
          <span className={`ph-native-state${codex?.available ? " is-on" : ""}`}>{codex?.available ? "已检测" : "未检测"}</span>
        </div>
        <div className="ph-native-kv"><span>Codex CLI</span><strong>{codex?.version ?? "—"}</strong></div>
        <div className="ph-native-kv"><span>CODEX_HOME</span><strong title={codex?.codexHome ?? ""}>{codex?.codexHome ?? "—"}</strong></div>
        <p className="ph-native-copy">在论文的「AI 对话」面板中，可以把终端版或客户端版 Codex 历史导入当前论文，也可以把当前 Pharos 对话创建为一个真实的 Codex 任务。Pharos 不会直接改写 Codex 的内部数据库或 JSONL。</p>
      </div>
      {message && <div className="ph-native-message is-ok">{message}</div>}
      {error && <div className="ph-native-message is-error">{error}</div>}
    </>
  );
}
