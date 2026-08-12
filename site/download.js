const REPOSITORY = "hyyyyyyz/Pharos";
const RELEASES_URL = `https://github.com/${REPOSITORY}/releases`;
const RELEASE_API = `https://api.github.com/repos/${REPOSITORY}/releases/latest`;
// Bump this whenever asset matching changes. A cached release is deliberately
// kept for offline-friendly rendering, but an old "unavailable" decision must
// not survive after a new package format is recognised.
const CACHE_KEY = "pharos.public-release.v3";
const CACHE_TTL = 15 * 60 * 1000;
const REQUEST_TIMEOUT = 6500;

const platformRules = {
  windows: {
    // The desktop workflow currently publishes a portable ZIP. Keep the
    // installer extensions for future releases, but do not make a platform
    // look unavailable merely because it has no NSIS installer yet.
    test: (name) => /\.(?:msi|exe|zip)$/i.test(name),
    score: (name) => scoreName(name, ["windows", "win", "x64", "portable", "setup", "msi", "zip"]),
  },
  macos: {
    test: (name) => /\.(?:dmg|pkg)$/i.test(name),
    score: (name) => scoreName(name, ["universal", "aarch64", "arm64", "macos", "mac", "dmg"]),
  },
  linux: {
    test: (name) =>
      /\.(?:appimage|deb|rpm)$/i.test(name) ||
      (/linux/i.test(name) && /\.(?:tar\.(?:gz|xz|bz2)|tgz)$/i.test(name)),
    score: (name) => scoreName(name, ["appimage", "amd64", "x86_64", "linux", "tar.xz", "deb", "rpm"]),
  },
  ios: {
    test: (name) => /\.ipa$/i.test(name),
    score: (name) => scoreName(name, ["ios", "iphone", "ipad", "universal"]),
  },
  android: {
    test: (name) => /\.apk$/i.test(name),
    score: (name) => scoreName(name, ["android", "universal", "arm64", "apk"]),
  },
};

const releaseTitle = document.querySelector("[data-release-title]");
const releaseSummary = document.querySelector("[data-release-summary]");
const releaseDate = document.querySelector("[data-release-date]");
const releaseIndicator = document.querySelector("[data-release-indicator]");
const releaseLink = document.querySelector("[data-release-link]");

markCurrentPlatform();
void loadLatestRelease();

async function loadLatestRelease() {
  try {
    const release = await fetchCachedRelease();
    if (!release || !Array.isArray(release.assets)) {
      throw new Error("GitHub returned incomplete release data");
    }
    renderRelease(release);
  } catch (error) {
    renderUnavailable(error instanceof HttpError && error.status === 404);
  }
}

function renderRelease(release) {
  const tag = textValue(release.tag_name) || textValue(release.name) || "最新版本";
  const releaseUrl = isSafeReleaseUrl(release.html_url) ? release.html_url : RELEASES_URL;
  const assets = release.assets.filter(isSafeAsset);
  const availablePlatforms = [];

  Object.entries(platformRules).forEach(([platform, rule]) => {
    const matches = assets
      .filter((asset) => rule.test(asset.name))
      .sort((a, b) => rule.score(b.name) - rule.score(a.name));
    if (matches.length) availablePlatforms.push(platform);
    renderPlatform(platform, matches[0], tag, releaseUrl);
  });

  releaseTitle.textContent = `${tag}${release.prerelease ? " · 预发布" : ""}`;
  releaseSummary.textContent = availablePlatforms.length
    ? `${availablePlatforms.length} 个平台已有公开安装包`
    : "此版本暂未附带可识别的安装包";
  releaseDate.textContent = formatDate(release.published_at);
  releaseLink.href = releaseUrl;
  releaseIndicator.classList.add("is-ready");
}

function renderUnavailable(noPublishedRelease) {
  releaseTitle.textContent = noPublishedRelease ? "暂无公开安装包" : "暂未发现公开安装包";
  releaseSummary.textContent = noPublishedRelease
    ? "首个公开版本发布后，这里会自动提供下载"
    : "可前往 GitHub Releases 查看当前状态";
  releaseDate.textContent = "—";
  releaseIndicator.classList.add("is-idle");

  Object.keys(platformRules).forEach((platform) => {
    renderPlatform(platform, null, "", RELEASES_URL);
  });
}

function renderPlatform(platform, asset, tag, releaseUrl) {
  const card = document.querySelector(`[data-platform-card][data-platform="${platform}"]`);
  if (!card) return;
  const status = card.querySelector("[data-platform-status]");

  if (!asset) {
    card.href = releaseUrl;
    card.target = "_blank";
    card.rel = "noopener noreferrer";
    card.classList.remove("is-available");
    status.textContent = "即将推出";
    return;
  }

  card.href = asset.browser_download_url;
  card.removeAttribute("target");
  card.removeAttribute("rel");
  card.classList.add("is-available");
  card.setAttribute("aria-label", `下载 ${asset.name}`);
  card.title = asset.name;
  status.textContent = `${tag} · ${formatBytes(asset.size)}`;
}

async function fetchCachedRelease() {
  const cached = readCache();
  if (cached) return cached;

  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT);
  try {
    const response = await fetch(RELEASE_API, {
      headers: { Accept: "application/vnd.github+json" },
      signal: controller.signal,
    });
    if (!response.ok) throw new HttpError(response.status);
    const release = await response.json();
    writeCache(release);
    return release;
  } finally {
    window.clearTimeout(timeout);
  }
}

function readCache() {
  try {
    const raw = localStorage.getItem(CACHE_KEY);
    if (!raw) return null;
    const cached = JSON.parse(raw);
    if (!cached || typeof cached.savedAt !== "number" || Date.now() - cached.savedAt > CACHE_TTL) {
      localStorage.removeItem(CACHE_KEY);
      return null;
    }
    return cached.value;
  } catch {
    return null;
  }
}

function writeCache(value) {
  try {
    localStorage.setItem(CACHE_KEY, JSON.stringify({ savedAt: Date.now(), value }));
  } catch {
    // Storage may be unavailable in private browsing; network loading still works.
  }
}

function markCurrentPlatform() {
  const platform = detectPlatform();
  if (!platform) return;
  document.querySelector(`[data-platform-card][data-platform="${platform}"]`)?.classList.add("is-current");
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
  return typeof value === "string" && value.startsWith(RELEASES_URL);
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
  if (Number.isNaN(date.valueOf())) return "—";
  return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit" }).format(date);
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
