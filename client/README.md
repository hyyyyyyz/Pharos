Pharos 桌面客户端
================

Pharos 是一体化科研平台：发现 → 阅读 → 翻译 → 整理 → 写作。
本目录是它的**主桌面客户端**，基于 [Zotero](https://www.zotero.org/) 源码构建。

沿用 Zotero 是有意的：文献管理、PDF 阅读器、标注、引文样式、760+ 网页抓取器，
这些做了十几年才成熟的东西没有重造的必要。Pharos 加的是 Zotero 没有的部分。

## 客户端能做什么

- **保排版翻译** — 右键任意 PDF，选「仅译文」或「中英对照」。译文由 BabelDOC
  重建，图表、公式、分页全部保持原位，并作为普通附件挂回同一条目——
  用同一个阅读器打开，能标注、能同步、离线可读。
- **AI 对话** — 在条目面板里就着正在读的论文提问。后端抽一次正文存成可复用的
  上下文，同一篇反复问不必重传。
- **每日论文** — 按你自己写的方向扫 arXiv，模型读过之后列出来，可连同 PDF
  和解读一起存进文库。
- **文献探索** — 搜 arXiv 与 OpenAlex，可对单条结果做模型精读。
- **研究项目** — 从文库这一侧看项目的阶段、依据的论文和已写下的记录。

这些功能需要一个 Pharos 后端。默认指向 `https://pharos.selab.top`，
在「设置 → Pharos」里可以改成你自己部署的实例。

## 安装

从 [Releases](../../releases) 下载。

- **macOS** — 打开 `.dmg`，把 Pharos 拖进「应用程序」。**首次启动需要按住
  Control 点击图标、选「打开」**：这些构建没有 Apple 开发者证书签名，
  双击会被 Gatekeeper 拦下。
- **Windows** — 解压后运行 `zotero.exe`。这是便携版，不是安装程序。
- **Linux** — 解压 `.tar.xz` 后运行 `zotero`。

## 文库与应用数据

正式产品的目标不是再建一个 `~/Pharos` 文库，而是像 Vibero 一样直接使用用户的
Zotero 数据目录：条目、分类、附件、PDF、笔记和标注都还是 Zotero 自己的数据。
Zotero、Vibero、Pharos 可以轮流打开这套文库，但不能同时运行。

Pharos 仍有独立的应用 profile、品牌、协议、凭据和设置；AI 对话、每日论文状态、
研究工作流等 Pharos 独有数据进入单独的 sidecar，不给 `zotero.sqlite` 增加私有
表或字段。

**当前安全状态：共享文库尚未启用。** 这份源码的 Zotero 基线是
`10.0.SOURCE`/schema 129，而本机 Zotero 8.0.5 与 Vibero 8.0 是 schema 123。
直接打开真实文库会有升级后旧客户端无法再打开的风险。客户端正在回到兼容的
Zotero 8.0.5 基线；完成前，所有构建仍使用隔离文库。详见
[`../docs/CLIENT_DATA_ARCHITECTURE.md`](../docs/CLIENT_DATA_ARCHITECTURE.md)。

## 从源码构建

```bash
npm install
npm run build                    # 转译 JS/JSX、编译 SCSS
app/scripts/dir_build -p m       # 打出 app/staging/Pharos.app（m=Mac w=Win l=Linux）
app/scripts/run_pharos_dev       # 用隔离的数据目录启动
```

**开发期始终用 `run_pharos_dev` 启动。** 它强制传 `-datadir`，并在路径等于
`~/Zotero` 时拒绝运行——`-profile` 只隔离 Gecko profile，**不隔离 Zotero 的数据
目录**。正式版未来共享文库，不代表开发版可以拿真实数据做迁移和回归测试。

跑测试：

```bash
test/runtests.sh -f pharosAPI pharosTranslate pharosChat pharosDaily pharosDiscovery pharosProjects
```

出安装包（`.github/workflows/release.yml` 会在推 `v*` 标签时自动做同样的事）：

```bash
echo "0.1.0.SOURCE" > version && npm run build && b=$(mktemp -d) && app/scripts/prepare_build -s build -o "$b" -c release && app/build.sh -d "$b" -p m -c release
```

`version` 里的 `.SOURCE` 后缀是必需的：`prepare_build` 靠正则
`([0-9].+)\.SOURCE` 找版本号，再按通道替换该后缀（`release` 替换为空）。

## 与上游的关系

本仓库是 Zotero 官方仓库的**手动副本**，已移除 `.git`，不保留 fork 关系。
切分时的上游版本记录在 `UPSTREAM.txt`；改造细节、配色映射，以及一批
「改 Zotero 时不报错但行为会错」的坑，都记在 `BRANDING.md`。

## 许可证

Zotero 采用 **AGPL-3.0**，本衍生作品同样以 AGPL-3.0 发布，`COPYING` 与源码内的
版权声明原样保留。

Zotero 是 Corporation for Digital Scholarship 的商标。本项目不使用该商标，
也与 Zotero 项目没有从属关系。
