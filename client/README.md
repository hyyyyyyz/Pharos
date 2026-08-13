Pharos 桌面客户端
================

Pharos 是一体化科研平台：发现 → 阅读 → 翻译 → 整理 → 写作。
本目录是它的**主桌面客户端**，基于 [Zotero](https://www.zotero.org/) 源码构建。

沿用 Zotero 是有意的：文献管理、PDF 阅读器、标注、引文样式、760+ 网页抓取器，
这些做了十几年才成熟的东西没有重造的必要。Pharos 加的是 Zotero 没有的部分。

## 客户端能做什么

- **保排版翻译** — PDF 阅读器顶栏在“在文件中查找”左侧提供翻译按钮，也可以
  在文库中右键任意 PDF 条目；两处都能选择「仅译文」或「中英对照」。译文由 BabelDOC 重建，
  图表、公式、分页全部保持原位，并作为普通附件挂回同一条目——用同一个阅读器
  打开，能标注、能同步、离线可读。
- **AI 对话** — 打开论文后，右侧默认直接显示占满高度的 AI 对话主面板，而不是
  条目信息下方的折叠区块；通过右侧导航仍可切回信息、笔记和其他阅读器面板。
  已登录且配置模型时，系统会在第一次提问前理解当前 PDF；后端保存可复用的论文
  档案，同一篇反复问不必重传。同一条目有多个 PDF 时，预理解绑定阅读器里实际
  打开的附件，链接到 Zotero 存储目录之外的 PDF 也支持。
- **每日论文** — 按你自己写的方向扫 arXiv，模型读过之后列出来，可连同 PDF
  和解读一起存进文库。
- **文献探索** — 搜 arXiv 与 OpenAlex，可对单条结果做模型精读。
- **研究项目** — 从文库这一侧看项目的阶段、依据的论文和已写下的记录。

这些功能需要一个 Pharos 后端。默认指向 `https://pharos.selab.top`，
在「设置 → Pharos」里可以改成你自己部署的实例。

## 安装

从 [GitHub Releases](https://github.com/hyyyyyyz/Pharos/releases) 下载。

- **macOS** — 打开 `.dmg`，把 Pharos 拖进「应用程序」。这些构建没有 Apple
  开发者证书签名，首次双击会被 Gatekeeper 拦下；随后到「系统设置 → 隐私与
  安全性」中选择「仍要打开」。macOS 15 已不再支持旧的 Control 点击 →「打开」
  绕过方式。
- **Windows** — 解压后运行 `zotero.exe`。这是便携版，不是安装程序。
- **Linux** — 解压 `.tar.xz` 后运行 `zotero`。

## 文库与应用数据

正式版不再建立第二套 `~/Pharos` 文库，而是像 Vibero 一样直接使用用户的 Zotero
数据目录：条目、分类、附件、PDF、笔记和标注都还是 Zotero 自己的数据。Zotero、
Vibero、Pharos 可以轮流打开这套文库，但不能同时运行。

Pharos 仍有独立的应用 profile、品牌、协议、凭据和设置；AI 对话与研究工作流等
Pharos 独有记录目前进入可选后端，每日论文另有用户选择的可迁移 Vault，所有这些都
不会给 `zotero.sqlite` 增加私有表或字段。源码仅预留文库旁的
`pharos-local.sqlite` 路径，目前没有功能创建或写入它。

**当前安全状态：正式版共享文库已经启用。** 客户端基线是 Zotero 8.0.5，
userdata schema 123，数据库名仍为 `zotero.sqlite`。文库副本已经完成 Zotero →
Pharos → Vibero 往返验证，279 个附件全部保持完整。Pharos 遇到不兼容 schema 时
不会迁移真实文库，也不会把 Pharos 独有数据写入 `zotero.sqlite`。详见
[`../docs/CLIENT_DATA_ARCHITECTURE.md`](../docs/CLIENT_DATA_ARCHITECTURE.md)。

开发、测试和 CI 仍必须使用隔离数据目录。正式版首次启动会先读取已安装 Zotero
profile 中的 `extensions.zotero.dataDir`，并且只有在该值是绝对路径、目录存在且包含
真正的 `zotero.sqlite` 时才采用它；因此外接磁盘或其他自定义位置的共享文库可以自动发现。
命令行 `-datadir` 和 Pharos 自身的明确数据目录设置始终优先。官方 profile 不可用或路径
校验失败时才回退到默认 `~/Zotero`，此时仍可显式传 `-datadir /path/to/Zotero`。

## 从源码构建

```bash
npm install
npm run build                    # 转译 JS/JSX、编译 SCSS
app/scripts/dir_build -p m       # 打出 app/staging/Pharos.app（m=Mac w=Win l=Linux）
app/scripts/run_pharos_dev       # 用隔离的数据目录启动
```

**开发期始终用 `run_pharos_dev` 启动。** 它强制传 `-datadir`，并在路径等于
`~/Zotero` 时拒绝运行——`-profile` 只隔离 Gecko profile，**不隔离 Zotero 的数据
目录**。正式版共享文库，不代表开发版可以拿真实数据做迁移和回归测试。

跑测试：

```bash
test/runtests.sh pharosAPI pharosTranslate pharosChat pharosReaderChat pharosDaily pharosDiscovery pharosProjects
```

出安装包（仓库根目录的 `.github/workflows/desktop-release.yml` 会在推送
`desktop-v*` 标签时自动构建三平台版本）：

```bash
npm run build
build_dir=$(mktemp -d)
app/scripts/prepare_build -s build -o "$build_dir" -c release
app/build.sh -d "$build_dir" -p m -c release
```

构建前应先让 `version` 包含准备发布的版本。`.SOURCE` 后缀是必需的：`prepare_build` 靠正则
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
