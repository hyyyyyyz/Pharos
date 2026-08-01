Pharos 桌面客户端
================

Pharos 是一体化科研平台：发现 → 阅读 → 翻译 → 整理 → 写作。
本仓库是它的**桌面客户端**，基于 [Zotero](https://www.zotero.org/) 源码构建。

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

Pharos 的文库放在 `~/Pharos`，和 Zotero 自己的 `~/Zotero` 完全分开。
安装 Pharos 不会动你已有的 Zotero 或它的数据。

## 从源码构建

```bash
npm install
npm run build                    # 转译 JS/JSX、编译 SCSS
app/scripts/dir_build -p m       # 打出 app/staging/Pharos.app（m=Mac w=Win l=Linux）
app/scripts/run_pharos_dev       # 用隔离的数据目录启动
```

**开发期请始终用 `run_pharos_dev` 启动。** 它强制传 `-datadir`，并在路径等于
`~/Zotero` 时拒绝运行——`-profile` 只隔离 Gecko profile，**不隔离 Zotero 的数据
目录**，这一点曾经让开发构建去打开了本机真实的 Zotero 库。

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
