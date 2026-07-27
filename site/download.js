const REPOSITORY = "hyyyyyyz/Pharos";
const RELEASES_URL = `https://github.com/${REPOSITORY}/releases`;
const RELEASE_API = `https://api.github.com/repos/${REPOSITORY}/releases/latest`;
const REPOSITORY_API = `https://api.github.com/repos/${REPOSITORY}`;
const CACHE_KEY = "pharos.public-release.v1";
const REPOSITORY_CACHE_KEY = "pharos.repository-summary.v1";
const CACHE_TTL = 15 * 60 * 1000;
const REQUEST_TIMEOUT = 6500;

const platformRules = {
  windows: {
    label: "Windows",
    test: (name) => /\.(?:msi|exe)$/i.test(name),
    score: (name) => scoreName(name, ["windows", "win", "x64", "setup", "msi"]),
  },
  macos: {
    label: "macOS",
    test: (name) => /\.(?:dmg|pkg)$/i.test(name),
    score: (name) => scoreName(name, ["universal", "aarch64", "arm64", "macos", "mac", "dmg"]),
  },
  linux: {
    label: "Linux",
    test: (name) => /\.(?:appimage|deb|rpm)$/i.test(name) || (/linux/i.test(name) && /\.(?:tar\.gz|tgz)$/i.test(name)),
    score: (name) => scoreName(name, ["appimage", "amd64", "x86_64", "linux", "deb", "rpm"]),
  },
  ios: {
    label: "iOS",
    test: (name) => /\.ipa$/i.test(name),
    score: (name) => scoreName(name, ["ios", "iphone", "ipad", "universal"]),
  },
  android: {
    label: "Android",
    test: (name) => /\.(?:apk|aab)$/i.test(name),
    score: (name) => scoreName(name, ["android", "universal", "arm64", "apk"]),
  },
};

const releaseVersion = document.querySelector("[data-release-version]");
const releaseSummary = document.querySelector("[data-release-summary]");
const releaseState = document.querySelector("[data-release-state]");
const releaseDate = document.querySelector("[data-release-date]");
const releaseEyebrow = document.querySelector("[data-release-eyebrow]");
const releaseSignal = document.querySelector("[data-release-signal]");
const releaseLink = document.querySelector("[data-release-link]");

markCurrentPlatform();
void Promise.allSettled([loadLatestRelease(), loadRepositorySummary()]);

async function loadLatestRelease() {
  try {
    const release = await fetchCachedJson(CACHE_KEY, RELEASE_API);
    if (!release || !Array.isArray(release.assets)) {
      throw new Error("GitHub 返回的 Release 数据不完整");
    }
    renderRelease(release);
  } catch (error) {
    const noPublishedRelease = error instanceof HttpError && error.status === 404;
    renderReleaseUnavailable(noPublishedRelease);
  }
}

async function loadRepositorySummary() {
  try {
    const repository = await fetchCachedJson(REPOSITORY_CACHE_KEY, REPOSITORY_API);
    const stars = Number(repository?.stargazers_count);
    if (!Number.isFinite(stars)) return;

    document.querySelectorAll("[data-github-stars]").forEach((node) => {
      node.textContent = formatCount(stars);
    });
    document.querySelectorAll("[data-github-stars-link]").forEach((node) => {
      node.setAttribute("aria-label", `查看 Pharos 的 GitHub Stars（当前 ${stars} 个）`);
      node.setAttribute("title", `Pharos 在 GitHub 上有 ${stars} 个 Star`);
    });
  } catch {
    document.querySelectorAll("[data-github-stars]").forEach((node) => {
      node.textContent = "—";
    });
  }
}

function renderRelease(release) {
  const tag = textValue(release.tag_name) || textValue(release.name) || "最新版本";
  const publishedAt = textValue(release.published_at);
  const releaseUrl = isSafeReleaseUrl(release.html_url) ? release.html_url : RELEASES_URL;
  const safeAssets = release.assets.filter(isSafeAsset);

  releaseVersion.textContent = tag;
  releaseSummary.textContent = safeAssets.length
    ? `已找到 ${safeAssets.length} 个公开构建文件；下面仅显示可识别的平台安装包。`
    : "最新公开版本暂未附带可下载构建，请查看发行说明。";
  releaseState.textContent = release.prerelease ? "预发布" : "已发布";
  releaseDate.textContent = publishedAt ? formatDate(publishedAt) : "未提供";
  releaseEyebrow.textContent = "GitHub Releases 已同步";
  releaseSignal.classList.add("is-ready");
  releaseLink.href = releaseUrl;
  releaseLink.firstChild.textContent = "查看本次 Release ";

  Object.entries(platformRules).forEach(([platform, rule]) => {
    const matches = safeAssets
      .filter((asset) => rule.test(asset.name))
      .sort((a, b) => rule.score(b.name) - rule.score(a.name));
    renderPlatform(platform, matches, tag, releaseUrl);
  });
}

function renderReleaseUnavailable(noPublishedRelease) {
  releaseVersion.textContent = noPublishedRelease ? "尚未公开" : "暂时离线";
  releaseSummary.textContent = noPublishedRelease
    ? "仓库目前没有 GitHub API 可见的公开 Release；开发中的草稿构建不会在此展示。"
    : "暂时无法连接 GitHub API。你仍可前往 Releases 页面检查最新发布。";
  releaseState.textContent = noPublishedRelease ? "等待首个版本" : "无法核对";
  releaseDate.textContent = "—";
  releaseEyebrow.textContent = noPublishedRelease ? "暂无公开安装包" : "GitHub API 暂不可用";
  releaseSignal.classList.add("is-idle");

  Object.keys(platformRules).forEach((platform) => {
    renderPlatformUnavailable(platform, noPublishedRelease);
  });
}

function renderPlatform(platform, assets, tag, releaseUrl) {
  const card = document.querySelector(`[data-platform-card][data-platform="${platform}"]`);
  if (!card) return;
  const status = card.querySelector("[data-platform-status]");
  const container = card.querySelector("[data-platform-assets]");

  if (!assets.length) {
    setAvailability(status, "即将推出", "soon");
    renderEmpty(container, `最新版本 ${tag} 暂无此平台安装包。`, releaseUrl, "查看发行说明");
    return;
  }

  setAvailability(status, assets.length > 1 ? `${assets.length} 个构建` : "可下载", "ready");
  container.replaceChildren(...assets.slice(0, 4).map((asset) => createArtifactLink(asset, tag)));
}

function renderPlatformUnavailable(platform, noPublishedRelease) {
  const card = document.querySelector(`[data-platform-card][data-platform="${platform}"]`);
  if (!card) return;
  const status = card.querySelector("[data-platform-status]");
  const container = card.querySelector("[data-platform-assets]");

  setAvailability(status, noPublishedRelease ? "即将推出" : "待核对", "soon");
  renderEmpty(
    container,
    noPublishedRelease ? "目前没有公开安装包。" : "暂时无法自动核对安装包。",
    RELEASES_URL,
    "前往 Releases",
  );
}

function createArtifactLink(asset, tag) {
  const link = document.createElement("a");
  link.className = "artifact-link";
  link.href = asset.browser_download_url;
  link.setAttribute("aria-label", `下载 ${asset.name}`);

  const copy = document.createElement("span");
  const title = document.createElement("b");
  const meta = document.createElement("small");
  const arrow = document.createElement("i");

  title.textContent = asset.name;
  meta.textContent = `${tag} · ${formatBytes(asset.size)}`;
  arrow.textContent = "↓";
  arrow.setAttribute("aria-hidden", "true");
  copy.append(title, meta);
  link.append(copy, arrow);
  return link;
}

function renderEmpty(container, message, href, label) {
  const empty = document.createElement("div");
  empty.className = "artifact-empty";

  const wrapper = document.createElement("span");
  const text = document.createTextNode(message);
  const link = document.createElement("a");
  link.href = isSafeReleaseUrl(href) ? href : RELEASES_URL;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.textContent = `${label} ↗`;
  wrapper.append(text, document.createElement("br"), link);
  empty.append(wrapper);
  container.replaceChildren(empty);
}

function setAvailability(node, label, variant) {
  node.textContent = label;
  node.className = `availability availability--${variant}`;
}

async function fetchCachedJson(cacheKey, url) {
  const cached = readCache(cacheKey);
  if (cached) return cached;

  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT);

  try {
    const response = await fetch(url, {
      headers: { Accept: "application/vnd.github+json" },
      signal: controller.signal,
    });
    if (!response.ok) throw new HttpError(response.status);
    const value = await response.json();
    writeCache(cacheKey, value);
    return value;
  } finally {
    window.clearTimeout(timeout);
  }
}

function readCache(key) {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return null;
    const cached = JSON.parse(raw);
    if (!cached || typeof cached.savedAt !== "number" || Date.now() - cached.savedAt > CACHE_TTL) {
      localStorage.removeItem(key);
      return null;
    }
    return cached.value;
  } catch {
    return null;
  }
}

function writeCache(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify({ savedAt: Date.now(), value }));
  } catch {
    // Private browsing or storage policies can disable localStorage; the page still works.
  }
}

function markCurrentPlatform() {
  const platform = detectPlatform();
  if (!platform) return;
  const card = document.querySelector(`[data-platform-card][data-platform="${platform}"]`);
  card?.classList.add("is-current");
}

function detectPlatform() {
  const platform = String(navigator.userAgentData?.platform || navigator.platform || navigator.userAgent).toLowerCase();
  if (/iphone|ipad|ipod/.test(platform)) return "ios";
  if (/android/.test(platform)) return "android";
  if (/mac/.test(platform)) return "macos";
  if (/win/.test(platform)) return "windows";
  if (/linux|x11/.test(platform)) return "linux";
  return null;
}

function isSafeAsset(asset) {
  return Boolean(
    asset &&
      typeof asset.name === "string" &&
      Number.isFinite(Number(asset.size)) &&
      typeof asset.browser_download_url === "string" &&
      asset.browser_download_url.startsWith(`https://github.com/${REPOSITORY}/releases/download/`) &&
      !/\.(?:sig|asc|sha256|sha512|blockmap)$/i.test(asset.name),
  );
}

function isSafeReleaseUrl(value) {
  return typeof value === "string" && value.startsWith(`https://github.com/${REPOSITORY}/releases`);
}

function scoreName(name, preferences) {
  const normalized = name.toLowerCase();
  return preferences.reduce((score, token, index) => score + (normalized.includes(token) ? preferences.length - index : 0), 0);
}

function formatBytes(value) {
  const bytes = Number(value);
  if (!Number.isFinite(bytes) || bytes <= 0) return "未知大小";
  const units = ["B", "KB", "MB", "GB"];
  const unitIndex = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const amount = bytes / 1024 ** unitIndex;
  return `${amount >= 10 || unitIndex === 0 ? amount.toFixed(0) : amount.toFixed(1)} ${units[unitIndex]}`;
}

function formatDate(value) {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return "未提供";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(date);
}

function formatCount(value) {
  if (value < 1000) return String(value);
  if (value < 1_000_000) return `${(value / 1000).toFixed(value < 10_000 ? 1 : 0)}k`;
  return `${(value / 1_000_000).toFixed(1)}m`;
}

function textValue(value) {
  return typeof value === "string" ? value.trim() : "";
}

class HttpError extends Error {
  constructor(status) {
    super(`GitHub API returned HTTP ${status}`);
    this.status = status;
  }
}
