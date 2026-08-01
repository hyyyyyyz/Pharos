import { useEffect, useState } from "react";
import {
  paperChat,
  paperChatAvailable,
  paperChatError,
  type ProviderStatus,
} from "../lib/paperChat";
import "./AiSettings.css";

/**
 * The 设置 → AI 对话 pane: which OpenAI-compatible model answers, and whose key
 * pays for it. The key itself never comes back from the server, so an existing
 * personal credential shows as a placeholder rather than a value.
 */
export function AiSettings(): JSX.Element {
  const available = paperChatAvailable();
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

  const personalCredential = status?.source === "personal" && Boolean(status.hasCredential);
  const canStoreCredential = status?.canStoreCredential !== false;
  const sourceLabel = status === null
    ? "读取中"
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
    if (!window.confirm("删除账户中的个人模型配置和 API Key？已有对话不会被删除。")) return;
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
            <span>论文理解和问答由 Pharos 后端安全转发，浏览器不直接接触密钥。</span>
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
          {canStoreCredential
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
          {status?.source === "personal" && (
            <button disabled={busy} onClick={() => void clear()}>
              清除个人配置
            </button>
          )}
        </div>
      </div>
      {message && <div className="ph-native-message is-ok">{message}</div>}
      {error && <div className="ph-native-message is-error">{error}</div>}
    </>
  );
}
