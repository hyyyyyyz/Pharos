# 阶段记录：桌面客户端

> **历史阶段文档。** 本文记录的是早期“独立 `~/Pharos` 文库”实现及其形成原因，
> 不是当前产品架构。现行方向是：正式客户端在版本兼容验证后直接使用 Zotero 文库，
> Pharos 独有记录位于可选后端与 Daily Vault，本地 sidecar 目前只预留路径；
> 开发与测试仍必须隔离。见
> [`CLIENT_DATA_ARCHITECTURE.md`](CLIENT_DATA_ARCHITECTURE.md) 与
> [`DECISIONS.md`](DECISIONS.md) 第 11 项。下文保留旧判断，是为了保留事故背景和
> 当时的工程证据，而不是继续指导产品实现。

从 Zotero 源码构建 Pharos 桌面客户端的四个阶段，以及此后的功能与外观补齐。
路线与非目标见 [ROADMAP.md](ROADMAP.md)，关键决定的理由见 [DECISIONS.md](DECISIONS.md)，
改 Zotero 时会静默出错的坑见 [`../client/BRANDING.md`](../client/BRANDING.md)。

本文只记**做了什么、为什么这样做、以及哪些判断是有代价的**。逐条实现细节在 git
历史里，每个提交都写了理由。

---

## 阶段 1 — 从 Zotero 源码构建出可运行的 Pharos.app

手动复制 Zotero 官方仓库、删除 `.git`、永久切断 fork 关系。切分时的上游版本记在
`client/UPSTREAM.txt`。

改名涉及四层：`resource/config.mjs` 的身份字段、`application.ini`、
`Info.plist`、以及 Mozilla 的 branding locale。**只改身份字段**——
`API_URL`、`STREAMING_URL`、`REPOSITORY_URL` 保持指向 zotero.org，
因为接 Zotero Web API 账号、更新内置 translator 和引文样式都靠它们。

### 过程中发现的两个真实隐患

**数据目录没被隔离。** `-profile` 只隔离 Gecko profile，**不隔离 Zotero 的数据
目录**——后者由 `DataDirectory.defaultDir = homeDir/CLIENT_NAME` 决定。在
`CLIENT_NAME` 仍是 `Zotero` 时，开发构建解析到了 `~/Zotero` 并去开真实的
`zotero.sqlite`，只因另一进程持锁才失败。若当时真 Zotero 未运行，schema 迁移
有可能让正版客户端打不开自己的库。

当时的止损修法是改 `CLIENT_NAME`（默认目录变 `~/Pharos`）外加
`app/scripts/run_pharos_dev`——它强制传 `-datadir`，路径等于 `~/Zotero` 时直接
拒绝运行。这个隔离仍适用于开发期，但“正式版永久使用独立文库”的结论已被第 11 项
决策替代。

**自动更新指向 Zotero 官方服务器**，存在把官方 Zotero 当"更新"下发、静默替换掉
Pharos 的风险。`[AppUpdate]` 整段停用，恢复前需自建端点。

---

## 阶段 2 — 注入 Pharos 配色

Zotero 的主题是每个方案一个 SCSS map 喂给 `derive-colors`，所以配色替换主要是
重写两个 map。但不能机械替换：

- `derive-colors` 对 `fill-quarternary`、`fill-quinary`、`color-stripe`、
  `color-menu` 做 `100% * (1 - alpha)` 的合成，**这四个必须保持半透明**。
  给不透明值会让权重塌成 0%，合成结果变成满强度的填充而非淡色。
- 网页版用实心 hex 表述这些表面，所以我们**反解合成方程**，求出在纸色底上能渲染
  出目标实心色的叠加色。浅色主题解出来是暖棕而非深蓝：深蓝在米色上叠出的是冷灰，
  正是网页版曾被反馈"有些按钮还是蓝色"的那个问题。
- oklch 值全部解析成 hex。Sass 要把它们当颜色解析（`derive-colors` 会调
  `color.alpha`/`color.mix`），而 `oklch()` 字面量只在很新的 Dart Sass 里才是
  Sass 颜色。

### 有代价的判断

**macOS 上覆盖了系统强调色。** 上游用 `SelectedItem`，意味着最显眼的表面——选中
行——会是用户在系统设置里选的颜色，品牌深蓝永远不出现。我们显式覆盖了它。

**`fill-tertiary` 比网页版深。** 网页版的 `#8A93A0` 在纸色底上是 2.79:1，连
WCAG AA 大字号门槛都过不了。改成同色相的 `#677079`，4.52:1。这是修 bug，不是改
风格。

**标签配色一字未动。** 用户给标签指定的颜色以字面 hex 存在数据库里，改调色板会
悄悄改变别人已经标好的标签。

---

## 阶段 3 — Pharos 独有能力

后端客户端 `Zotero.Pharos.API`：token 存登录管理器、经 OSKeyStore 加密（与 Zotero
存自己 API key 的方式一致），但用**独立的 login host**——两个无关凭据放一个桶里，
清一个会连带清掉另一个。401 会清 token，否则过期令牌变成一堵永久的失败墙。

在此之上：保排版翻译、AI 对话、每日论文、文献探索、研究项目、管理后台、
方向配置、AI 供应商设置。

### 两个塑造了实现的约束

**翻译结果作为普通附件挂回同一条目**，而不是从远程 URL 展示。这是在文献管理器里
做这件事的全部意义：译文用同一个阅读器打开、能标注、能同步、离线可读。

**入库走 Zotero 而不是后端的 `/import`。** 后者把论文归到 Pharos 库（网页版的
库）；这里的目的是落进 Zotero，因为阅读器、标注和引文机制都已经在那儿。

### 一个诚实性约束

研究项目**只记录、不执行**。后端在 `automation_notice` 里明说了这一点，客户端
**逐字照搬**而非转述。一条读起来像实验结果、却脱离了"没有任何东西执行过它"这句
说明的记录，正是那句话存在的理由。

---

## 阶段 4 — CI 出安装包

推 `v*` 标签构建 macOS 磁盘映像与 Windows/Linux 便携包。

**Windows 给便携 zip 而不是 NSIS 安装器。** 安装器分支要求 `WIN_NATIVE`（Cygwin），
而 GitHub 的 Windows runner 是 MSYS2，`build.sh` 在那里本来就走非原生路径。发一个
无法测试的安装器比发构建脚本本就产出的压缩包更糟。

动手前修了四件事：LFS 指针（分离副本没有 LFS 服务器，新克隆拿到的是指针）、
`DEVELOPER_ID` 里存着 Zotero 自己的证书指纹、公证无条件执行但凭据为空、
以及所有产物文件名仍是 `Zotero-*`。

---

## 功能与外观补齐

功能做齐之后，**账号这层外壳仍然缺失**——用户打开应用第一眼看到的东西。补上了
登录页、左栏常驻模块栏、底部账号区、外观设置。

### 左栏模块栏

网页版的结构是"选中的模块独占主区域"，不是"内容塞进条目表"。所以把
`#zotero-trees` 包进一个 box、用 `hidden` 切换即可，**Zotero 自己的视图代码一行
没动**。

另外两条路都调研过并否决：

- **collection tree 行**：`getSearchObject()` 对未知类型抛普通 Error，而这个异常
  会传到条目树**和标签选择器**，两处都只捕获 `SearchError`。中间区域唯一支持的
  DOM 注入接口 `setItemsPaneMessage()` 会把元素序列化成 `outerHTML` 再注入，
  事件监听器全部丢失——"加入文库"按钮点不了。
- **自定义标签页**：分类树本身就在库标签页内部，一选 Pharos 标签页左栏就消失。

`<deck>` 是最自然的包裹方式，也是错的：嵌套在 `#tabs-deck` 里会骗过"这个元素可见
吗"的判断（`closest('deck').selectedPanel` 会解析到内层），上游 `itemPaneTest`
正是这么判断的。

### 登录门

网页版能用 `AuthGate` 把整个应用挡在后面，因为它拥有自己的根节点；客户端不行，
Zotero 的主窗口就是应用本体。所以做成**覆盖在主窗口上的模态窗**。

**它可以跳过，这是刻意的。** 文库、阅读器、标注全是本地功能，不需要账号;
一个因为服务器挂了就打不开自己文库的客户端更糟。

注册可用性是**三态**而非布尔：未知保留注册入口，让提交时的 403 来定夺。老版自部署
后端没有 `/api/auth/status`，因为请求失败就藏起注册入口，会把人锁在一个其实开放的
实例外面。

### 强调色

10 个强调色，palette 逐值移植自 `tokens.ts`，并验证默认值与编译进去的主题**逐
字节一致**——所以在用户选之前，运行时路径是视觉无操作。

**浅色主题的强调色被压到 oklch L 0.48。** 网页版有两个强调色槽（填充一个、
前景一个更深的），Zotero 只有一个、两用。一个槽同时承担就必须满足更严的约束。
代价是灯塔金在浅色下渲染为深金而非光束的亮金；选色器画的就是实际会应用的颜色，
不存在货不对板。

**分发方式经外部评审后改过。** 最初逐窗口注入 CSS 变量，但这够不到 PDF 阅读器和
笔记编辑器——它们是 `type="content"` 文档，各自带着编译好的主题副本，而那正是用户
待得最久的地方。评审给出的方案是一张 `nsIStyleSheetService` USER_SHEET，**但用
`@-moz-document url-prefix()` 限定作用域**。

不限定的风险不是理论性的：`HiddenBrowser` 默认开启 JS 且会加载任意 URL（feed
翻译、附件导入），`basicViewer` 同样，而 `--color-accent` 这个名字足够通用会撞车。
限定到 `chrome://`、`resource://zotero/reader/`、`resource://zotero/note-editor/`
三个前缀，既覆盖阅读器，又不落到任何网页上。副产物是窗口枚举、
`chrome-document-global-created` 观察者、以及新窗口打开时那一帧的旧配色全部消失。

**「浅色/深色/自动」没有新建 pref。** Zotero 的常规设置已经绑在
`browser.theme.toolbar-theme` 上，那是 Gecko 自己的开关。外观面板绑同一个 pref，
而不是造一个会和它打架、且注定输给平台实际读取的那个。

---

## 现状

- Pharos 测试 **240 个全部通过**；后端 **743 passed**
- 上游 `itemPane` + `zoteroPane` 稳定在 **199/203**，四个 bibliography-entry
  失败在干净树上同样失败
- 有几个焦点相关的上游测试在这台机器上偶发失败（测试自己的注释说明了原因：
  遍历依赖窗口处于激活状态）。干净基线上连续复现过，与本项目改动无关

## 当时的已知缺口

- **构建未签名**。macOS 首次启动需 Control-点击、选"打开"。设置
  `DEVELOPER_ID` 与 `NOTARIZATION_*` 即可，构建在凭据缺失时会干净跳过并说明
- Windows 是便携包而非安装器
- 随旧 Tauri 客户端消失的 Workspace、Codex 互通、深链接**没有替代品**
- 页面级证据的**客户端界面当时还没有**。该缺口后来已补齐：阅读器现在提供
  “选中一段 → 保存为证据”的动作，见 [`PHASE-EVIDENCE.md`](PHASE-EVIDENCE.md)。
