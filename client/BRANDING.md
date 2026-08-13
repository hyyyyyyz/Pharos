# Pharos 客户端 · 品牌与改造记录

本目录是 **Pharos 桌面客户端**，基于 Zotero 源码构建。

## 与上游的关系

按项目决定，本副本**手动复制自 Zotero 官方仓库并移除了 `.git`**，不保留 fork 关系。
下面记录切分时的上游版本，供将来需要人工比对上游修复时参考：

- 上游仓库：https://github.com/zotero/zotero
- 切分日期：见 `UPSTREAM.txt`
- 切分版本：见 `UPSTREAM.txt`

**许可证**：Zotero 采用 **AGPL-3.0**。移除 git 历史不影响许可证义务——
`COPYING` 与源码内的版权声明必须原样保留，衍生作品同样以 AGPL-3.0 发布。
Pharos 本身已是 AGPL-3.0，二者一致。

## 品牌改造点

| 位置 | 原值 | 改为 |
|---|---|---|
| `app/config.sh` → `APP_NAME` | `Zotero` | `Pharos` |
| `app/config.sh` → `APP_ID` | `zotero@zotero.org` | `pharos@pharos.selab.top` |
| `resource/config.mjs` → `GUID` | `zotero@zotero.org` | `pharos@pharos.selab.top` |
| `resource/config.mjs` → `ID` | `zotero` | `pharos` |
| `resource/config.mjs` → `CLIENT_NAME` | `Zotero` | `Pharos` |
| `resource/config.mjs` → `DOMAIN_NAME` | `zotero.org` | `pharos.selab.top` |
| `resource/config.mjs` → `PRODUCER` | `Digital Scholar` | `Pharos` |
| `app/assets/application.ini` → `Name`/`Vendor`/`ID` | Zotero 三项 | Pharos 三项 |
| `app/assets/application.ini` → `[AppUpdate]` | 指向 zotero.org | **整段停用** |
| `app/mac/Contents/Info.plist` → `CFBundleName` | `Zotero` | `Pharos` |
| `app/mac/Contents/Info.plist` → `CFBundleIdentifier` | `org.zotero.zotero` | `top.selab.pharos` |
| `app/mac/Contents/Info.plist` → URL scheme | `zotero://` | `pharos://` |
| `app/mac/Contents/Info.plist` → `CFBundleSignature` | `ZOTR` | `PHRS` |
| `chrome/.../zotero.js` → `_initDB` | `DBConnection('zotero')` | `DBConnection(ZOTERO_CONFIG.ID)` |
| 应用图标 | Zotero 图标 | Pharos 圆形 P 标（**未做**） |

**刻意保留未改**：`config.mjs` 里的 `API_URL` / `STREAMING_URL` / `SERVICES_URL` /
`BASE_URI` / `REPOSITORY_URL` 仍指向 zotero.org。这些是接 Zotero Web API 账号、
以及更新内置 translator 和引文样式所必需的，改掉会直接断掉这些能力。
`PREF_BRANCH`（`extensions.zotero.`）同样保留：`defaults/preferences/` 下的默认值
全部以该前缀写死，只改前缀会让所有默认设置静默失效。

`CFBundleExecutable` 保持 `zotero`，那是 `Contents/MacOS` 里启动器二进制的**文件名**，
不是用户可见字符串，改名需连带改 Gecko 构建产物名。

面向用户的文档链接（`SUPPORT_URL`、`FEEDBACK_URL`、`CHANGELOG_URL` 等）目前仍指向
Zotero 官方支持页与论坛。发布前需改掉——否则 Pharos 用户会被引导去 Zotero 论坛提
我们的 bug。列为阶段 3 收尾项。

## 应用隔离与共享文库（重要）

品牌身份、Gecko application profile、Zotero 文库、Pharos 原生数据是四个不同
层次。当前产品方向是：

```text
Application identity  = Pharos
Application profile   = Pharos 独立 profile
Reference library     = Zotero data directory / zotero.sqlite
Pharos extension data = 可选后端 + Daily Vault；本地 sidecar 仅预留路径
```

正式客户端已经在 Zotero 核心版本兼容后共享 Zotero 文库；它不会为了共享数据而把
bundle ID、协议、凭据或用户可见品牌伪装成 Zotero。完整约束见
[`../docs/CLIENT_DATA_ARCHITECTURE.md`](../docs/CLIENT_DATA_ARCHITECTURE.md)。

### 早期隔离事故与仍然有效的开发规则

改造过程中发现两个会波及**本机真实 Zotero** 的隐患：

1. **数据目录**。`-profile` 只隔离 Gecko profile，**不隔离 Zotero 数据目录**。
   数据目录由 `DataDirectory.defaultDir = homeDir/CLIENT_NAME` 决定，在 `CLIENT_NAME`
   仍是 `Zotero` 时解析到了 `~/Zotero`，构建版直接去开真实的 `zotero.sqlite`——
   当时只因另一进程持有 SQLite 锁而失败（`NS_ERROR_STORAGE_BUSY`）。若那时真 Zotero
   未运行，schema 迁移有可能让正版客户端打不开自己的库。当时把默认目录改为
   `~/Pharos` 是正确止损，但它不是最终产品架构。随后 Zotero 8.0.5 兼容迁移和
   往返测试已经完成，正式版现已共享文库；开发构建继续强制隔离。

2. **自动更新**。`[AppUpdate]` 原本指向 Zotero 官方更新服务器，存在把官方 Zotero 当作
   "更新"下发、静默替换掉 Pharos 的风险，已整段停用。恢复前需先自建更新端点。

开发期一律用 `app/scripts/run_pharos_dev` 启动：它强制传 `-datadir`（默认
`~/PharosDev`），并在路径等于 `~/Zotero` 时直接拒绝运行。正式版共享文库与开发版
隔离并不矛盾；后者是在一次不可逆 schema 事故之前保住真实数据的安全线。

## 配色映射

Zotero 的样式层用标准 CSS 变量（`scss/abstracts/_variables.scss` 及各主题文件），
因此 Pharos 的品牌色可以直接映射，无需重写组件。

浅色主题（源自 `frontend/src/design/tokens.ts`，与网页版逐值一致）：

| Pharos 令牌 | 值 | 对应 Zotero 变量 |
|---|---|---|
| `--c-bg` 页面底 | `#F7F2E9` | `--color-background` |
| `--c-sf` 卡面 | `#FFFCF7` | `--material-background` / 面板底 |
| `--c-rail` 侧栏 | `#F1EADD` | `--material-sidepane` |
| `--c-bd` 描边 | `#E3DACA` | `--color-border` |
| `--c-tx` 正文 | `#0C2040` | `--fill-primary` |
| `--c-tx2` 次要文字 | `#4A5B72` | `--fill-secondary` |
| `--c-tx3` 弱化文字 | `#8A93A0` | `--fill-tertiary` |
| 强调色（灯塔蓝） | `oklch(0.30 0.066 259)` | `--color-accent` |
| 成功（青绿） | `oklch(0.594 0.097 195)` | `--color-success` |
| 错误 | `#C2412F` | `--color-critical` |

深色主题同样映射（Pharos 深色以品牌深蓝为底，而非中性炭黑）。

## 分阶段计划

- [x] **阶段 1**：拉取源码 → 移除 `.git` → 改品牌标识 → 构建出可运行的 `Pharos.app`
- [x] **阶段 2**：注入 Pharos 配色（浅色 + 深色）
- [x] **阶段 3**：接入 Pharos 独有能力
  - [x] 后端客户端 `Zotero.Pharos.API`（token 经 OSKeyStore 加密存入登录管理器，
        用独立 login host；401 即清 token，把死墙变回登录提示）
  - [x] 账号设置面板（服务器不可达 ≠ 已退出；改服务器地址强制退出）
  - [x] 保排版翻译（右键 PDF → 仅译文 / 中英对照，译文作为普通附件挂回同一条目）
  - [x] AI 对话（PDF 阅读器顶栏入口 + 右侧上下文面板区块，`API.stream()` 读 NDJSON 流）
  - [x] 每日论文（工具菜单 → 独立窗口，可把论文连同 PDF 与模型解读存入本地文库）
  - [x] 文献探索（工具菜单 → 搜 arXiv/OpenAlex，可精读、可入库、可归入项目）
  - [x] 研究项目（工具菜单 → 看阶段/来源/记录，推进阶段，记录可存为 Zotero 笔记）
- [x] **阶段 4**：GitHub Actions 出安装包（macOS `.dmg`、Windows/Linux 便携包）

### 改动 Zotero 时踩过的坑（都不报错，只是行为错）

- `buildItemContextMenu` **按索引**寻址菜单子元素，`options` 数组必须和
  `zoteroPane.xhtml` 的 menupopup 顺序严格一致。
- `data-l10n-id` 解析的是**该文档自己的** `<link rel="localization">` 列表，
  与 `Zotero.ftl`（`getString()` 用的）是两套。id 缺失会让 DOM 本地化 reject，
  表现为调用方抛出裸的 `undefined`，无堆栈。
- **XML 注释里不能出现 `--`**，否则整个文档解析失败、窗口打不开。
  （`CLAUDE.md` 要求注释用 `--`，那是给 JS/SCSS 的，XML 是例外。）
- 侧边导航的区块排序把「移动一个**可见**位置」的结果写回 `_builtInPanes`，
  所以「对部分常规条目隐藏、邻居却可见」的区块不能插在列表中间，否则会打乱
  上游的排序行为。Pharos 对话区块因此排在最后。
- 窗口的 `onload="X.init()"` 里做的赋值，在 `loadWindow` 返回时**尚未执行**。
  需要等待初始化的句柄必须在脚本加载时就创建，否则 `await undefined` 会静默通过。
- `git stash -u` 会把**尚未提交的新文件**一并藏起来。期间任何一次构建都会把它们
  从 `build/` 移除，而 `stash pop` 只恢复源码、不重建——`dir_build` 于是打包了一个
  缺文件的 build，窗口报 “Missing chrome or resource URL”。排查未提交的改动时，
  pop 之后要重新 `npm run build`。
- `version` 文件里的 `.SOURCE` 后缀是**必需的**：`prepare_build` 用正则
  `([0-9].+)\.SOURCE` 找版本号，再按通道替换该后缀（`release` 替换为空串）。
  直接写 `0.1.0` 会报 “Version number not found”。
- 那 4 个 updater/launcher 归档原本是 **Git LFS 指针**，而分离副本没有 LFS 服务器，
  新克隆拿到的会是指针、构建停在 `check_lfs_file`。共 1.1M，已改为普通 blob。

### 阶段 1 历史验收结果

以下结果只证明早期构建没有再误开真实文库，不再代表最终数据架构：

已实测通过：

- macOS 识别为 `Pharos`，`bundleID = top.selab.pharos-source`
- 窗口标题「我的文库 - Pharos」，欢迎页「欢迎来到 Pharos!」
- 数据目录 `~/PharosDev`，数据库 `pharos.sqlite`
- 启动前后 `~/Zotero/zotero.sqlite` 的 sha256 完全一致，日志 0 次提及该路径

阶段 1 遗留：

- ~~应用图标仍是 Zotero 的~~ → 阶段 2 已换（见下）
- 调试日志前缀仍是 `zotero(N)`，仅开发者控制台可见
- `SUPPORT_URL` 等面向用户的链接仍指向 zotero.org，见上文
- `.ftl` / `.dtd` 中仍有少量直接写死 `Zotero` 的文案（未走 `brandShortName`），
  需在阶段 3 逐条排查——注意其中一部分是**正当引用**（如 Zotero 同步、
  Zotero Connector），不能无差别替换

### 阶段 2：配色注入

Zotero 的主题层很干净：每个主题就是一个 SCSS map 喂给 `derive-colors`
（`scss/abstracts/_mixins.scss:325`）。改动落在四处 map 加若干补丁：

| 文件 | 说明 |
|---|---|
| `scss/themes/_{light,dark}.scss` | 主 map，全部键 |
| `note-editor/src/stylesheets/themes/_{light,dark}.scss` | 独立副本，键为子集 |
| `reader/src/common/stylesheets/themes/_{light,dark}.scss` | 独立副本，多 `color-button50` |
| `scss/base/_base.scss` | 覆盖 macOS/Linux 的 `SelectedItem` 系统色 |
| `scss/components/_banners.scss` | 横幅改暖色，去掉硬编码色 |
| `chrome/content/zotero/xpcom/reader.js` | PDF 页面周围底色（注入式 CSS） |
| 306 个 SVG + `z.svg` | 图标改色，Z 标志换成 P 标记 |
| `app/mac/Contents/Info.plist` + `Pharos.icns` | 应用图标 |

**两个必须遵守的约束**（否则会静默出错）：

1. `fill-quarternary` / `fill-quinary` / `color-stripe` / `color-menu` /
   `color-border` **必须保持半透明**。`derive-colors` 用 `100% * (1 - alpha)`
   作为混合权重，给不透明值会让权重塌成 0%，合成色直接等于原色而不是淡淡的一层。
   网页版调色板给的是实心色，所以这些键是把合成方程**反解**出来的叠加色：
   浅色解出暖棕（`#68470c24` 等），深色解出冷蓝（`#7bb4fe2b` 等），
   在各自底色上正好还原网页版的目标色。
2. `color-sidepane` / `color-toolbar` / `color-tabbar` **必须不透明**。
   前者还被赋给 Windows 的 `--color-form-element-base-background`
   （`scss/win/abstracts/_mixins.scss:19` 原注释就写着 "Must be non-transparent color"），
   后者是所有 XUL `<panel>` 弹层的背景。

**oklch 值全部解析成 hex 写入**：`derive-colors` 会对色值调用 Sass 的
`color.alpha()` / `color.change()` / `color.mix()`，需要 Sass 能把它解析为颜色，
而 `oklch()` 字面量要很新的 Dart Sass 才算颜色。

**刻意偏离网页版的三处**，都有理由：

- `fill-tertiary` 从 `#8A93A0` 改为 `#677079`。前者在纸色底上只有 2.79:1，
  连 WCAG AA 大字号的 3.0 都不到；后者 4.52:1，过正文门槛。
- `accent-green` 保持真正的绿（`#418e47`），**没有**统一成品牌青绿。
  `$item-pane-sections` 把 `accent-green`（附件）和 `accent-teal`（文库与分类）
  当作两个**分类**，统一会让两个侧栏图标撞色。成功语义用 `accent-teal`。
- 选中行保持实心强调色填充，而非网页版的淡沙色。
  `_virtualized-table.scss:517` 用 `#ffffff1a` 给选中行内的按钮做悬停叠加，
  这假设了选中行是深色；改成淡色会让那些叠加失效。

新增 `accent-text` 键（网页版的 `--c-acc`）：浅色 `#fffcf7`、深色 `#08111d`。
两个主题必须分别给值——深色主题的强调色是**亮**的（`#82a6dd`），
沿用浅色的米白会得到 2.02:1，直接不可读。上游在 macOS/Linux 没这个问题，
因为 `SelectedItem` / `SelectedItemText` 是系统成对给的。

标签色（`tag-*`）**原样保留**：标签颜色以字面 hex 存在数据库里，
调色板是 JS 里写死的固定 9 色，改了会让用户已标注的标签静默变色。

## 服务端依赖

翻译引擎（BabelDOC，Python + Rosetta）无法进入客户端进程，
仍由 `https://pharos.selab.top` 提供；账号体系与云端记录同样保留。
