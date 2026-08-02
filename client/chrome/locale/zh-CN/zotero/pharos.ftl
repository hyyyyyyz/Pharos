# Pharos 专属字符串。Zotero 自己的字符串留在 zotero.ftl，
# 这样将来手工合并上游改动时不会和我们的冲突。

pharos-error-signed-out = 尚未登录 Pharos
pharos-error-signed-out-detail = 请先在设置中登录 Pharos 账号。

## 保排版翻译

pharos-translate-title = Pharos 翻译
pharos-translate-column-attachment = 附件
pharos-translate-column-status = 状态

pharos-translate-menu = Pharos 保排版翻译
pharos-translate-menu-mono = 仅译文
pharos-translate-menu-dual = 中英对照

pharos-translate-status-uploading = 上传中…
pharos-translate-status-queued = 排队中…
# $stage (String) - 引擎给出的当前步骤名称
# $percent (Number) - 0–100
pharos-translate-status-running = { $stage } { $percent }%
pharos-translate-status-downloading = 下载中…

# 追加到生成附件的文件名后
pharos-translate-suffix-mono = 译文
pharos-translate-suffix-dual = 对照

pharos-translate-error-missing-file = 附件文件不存在
pharos-translate-error-failed = 翻译失败
pharos-translate-error-timeout = 翻译超时
pharos-translate-error-cancelled = 已取消
pharos-translate-error-no-output = 本次翻译没有生成该格式的文件
pharos-translate-error-disabled = 该账号已关闭整篇翻译功能

## 设置

pharos-prefs-pane = Pharos

pharos-prefs-account-header = Pharos 账号
pharos-prefs-account-intro = 登录后即可使用保排版翻译、论文 AI 对话与每日论文推送。
pharos-prefs-email = 邮箱
pharos-prefs-password = 密码
pharos-prefs-sign-in = 登录
pharos-prefs-signing-in = 正在登录…
pharos-prefs-error-incomplete = 请填写邮箱和密码。
pharos-prefs-error-sign-in = 登录失败。

pharos-prefs-signed-in-as = 已登录：
pharos-prefs-sign-out = 退出登录
pharos-prefs-sign-out-all = 退出所有设备
pharos-prefs-sign-out-all-confirm = 这会让所有已登录 Pharos 的设备一并退出，是否继续？
pharos-prefs-sign-out-all-help = 退出所有设备会吊销该账号签发过的全部令牌。

pharos-prefs-server-header = 服务器
pharos-prefs-server-help = Pharos 是开源的，可以自行部署。填入你自己的实例地址即可切换。令牌只在签发它的服务器上有效，因此更改地址会同时退出登录。
pharos-prefs-server-url = 地址

## AI 对话

section-pharos-chat =
    .label = 问 Pharos
pharos-chat-placeholder =
    .placeholder = 就这篇论文提问…
pharos-chat-send = 发送

pharos-chat-status-connecting = 连接中…
pharos-chat-status-preparing = 正在读取论文…
pharos-chat-status-thinking = 思考中…

pharos-chat-error-prepare = 无法读取这篇论文。
pharos-chat-error-prepare-timeout = 读取论文超时。
pharos-chat-error-failed = 未能生成回答。
pharos-chat-error-empty = 没有收到回答。

pharos-error-unreachable = 无法连接到 Pharos 服务器。

## 每日论文

pharos-daily-menu = 每日论文…
pharos-daily-window =
    .title = 每日论文
pharos-daily-refresh = 立即抓取
pharos-daily-refreshing = 正在扫描 arXiv…
pharos-daily-loading = 加载中…
pharos-daily-empty = 这一天还没有内容。
pharos-daily-error = 无法加载每日论文。
# $count (Number) - 当天匹配到的论文数
pharos-daily-count = { $count } 篇
pharos-daily-unread = 尚未阅读
pharos-daily-read-failed = 阅读失败
pharos-daily-save = 加入文库
pharos-daily-saving = 保存中…
pharos-daily-saved = 已加入
pharos-daily-save-failed = 无法保存这篇论文。
pharos-daily-open = 在 arXiv 打开
pharos-daily-matched = 命中方向

pharos-daily-highlight-contribution = 贡献
pharos-daily-highlight-innovation = 创新点
pharos-daily-highlight-method = 方法
pharos-daily-highlight-results = 结果

## 文献探索

pharos-discovery-menu = 文献探索…
pharos-discovery-window =
    .title = 文献探索
pharos-discovery-placeholder =
    .placeholder = 搜索 arXiv 与 OpenAlex…
pharos-discovery-search = 搜索
pharos-discovery-searching = 搜索中…
pharos-discovery-hint = 搜索 arXiv 与 OpenAlex，Pharos 会替你读一遍。
pharos-discovery-empty = 没有匹配结果。
pharos-discovery-error = 搜索失败。
# $count (Number) - 返回的结果数
pharos-discovery-count = { $count } 条结果
# $count (Number) - 被引次数
pharos-discovery-citations = 被引 { $count }
pharos-discovery-analyze = 精读
pharos-discovery-analyzing = 阅读中…
pharos-discovery-open = 打开
pharos-discovery-add-to-project = 归入项目
pharos-discovery-pick-project = 这篇论文归入哪个研究项目？
pharos-discovery-added-to-project = 已归入

pharos-discovery-section-contribution = 贡献
pharos-discovery-section-core-trick = 核心思路
pharos-discovery-section-method = 方法
pharos-discovery-section-results = 结果
pharos-discovery-section-limitations = 局限
pharos-discovery-rules-note = 该摘要由规则生成，未经模型阅读。可点「精读」获取模型解读。

## 研究项目

pharos-projects-menu = 研究项目…
pharos-projects-window =
    .title = 研究项目
pharos-projects-loading = 加载中…
pharos-projects-empty = 还没有项目。请先在 Pharos 网页端创建。
pharos-projects-error = 无法加载项目。
pharos-projects-none = 暂无内容。
pharos-projects-question = 研究问题
pharos-projects-advance = 推进阶段
pharos-projects-save-note = 存为笔记
# $count (Number) - 项目依据的论文数
pharos-projects-sources = 文献来源（{ $count }）
# $count (Number) - 项目已写下的记录数
pharos-projects-artifacts = 研究记录（{ $count }）

pharos-projects-stage-discovery = 文献调研
pharos-projects-stage-ideation = 构思
pharos-projects-stage-planning = 方案设计
pharos-projects-stage-experimentation = 实验
pharos-projects-stage-analysis = 分析
pharos-projects-stage-claims = 结论
pharos-projects-stage-drafting = 撰写
pharos-projects-stage-review = 评审
pharos-projects-stage-complete = 已完成

pharos-projects-type-hypothesis = 假设
pharos-projects-type-experiment-plan = 实验方案
pharos-projects-type-result = 实验结果
pharos-projects-type-claim = 论断
pharos-projects-type-draft = 草稿
pharos-projects-type-review = 评审意见

pharos-projects-status-draft = 草稿
pharos-projects-status-ready = 就绪
pharos-projects-status-verified = 已验证
pharos-projects-status-rejected = 已否决

## 模块栏

pharos-rail-library = 文库
pharos-rail-library-tooltip =
    .title = 文库
pharos-rail-daily = 每日论文
pharos-rail-daily-tooltip =
    .title = 每日论文
pharos-rail-discovery = 文献探索
pharos-rail-discovery-tooltip =
    .title = 文献探索
pharos-rail-projects = 研究项目
pharos-rail-projects-tooltip =
    .title = 研究项目
pharos-rail-collapse =
    .title = 收起
    .aria-label = 收起模块栏
pharos-rail-expand =
    .title = 展开
    .aria-label = 展开模块栏

## 管理后台

pharos-admin-menu = 管理后台…
pharos-admin-window =
    .title = 管理后台

pharos-rail-admin = 管理
pharos-rail-admin-tooltip =
    .title = 管理后台

pharos-admin-tab-users = 用户
pharos-admin-tab-providers = API 配置
pharos-admin-search =
    .placeholder = 搜索邮箱或名称…
    .aria-label = 搜索用户

pharos-admin-loading = 加载中…
pharos-admin-error = 无法加载管理后台。
pharos-admin-forbidden = 当前账号不是管理员。
# 服务器未返回该项时的占位符
pharos-admin-none = —

pharos-admin-stat-users = 用户
# $count (Number) - 拥有管理员权限的账号数
pharos-admin-stat-admins = { $count } 位管理员
pharos-admin-stat-papers = 论文
# $count (Number) - 已完成翻译的论文数
pharos-admin-stat-translated = { $count } 篇已翻译
pharos-admin-stat-projects = 研究项目
pharos-admin-stat-daily = 每日论文
# $count (Number) - 本实例累计的文献检索次数
pharos-admin-stat-searches = { $count } 次检索
pharos-admin-registration-open = 注册开放中
pharos-admin-registration-closed = 注册已关闭
pharos-admin-registration-hint = · 由服务器的 .env 控制

pharos-admin-column-user = 用户
pharos-admin-column-papers = 论文
pharos-admin-column-projects = 项目
pharos-admin-column-highlights = 高亮
pharos-admin-column-created = 注册
pharos-admin-column-last-login = 最近登录
pharos-admin-column-role = 角色
pharos-admin-column-actions = 操作

pharos-admin-users-empty = 还没有用户。
pharos-admin-users-none-matched = 没有匹配的用户。
# $shown (Number) - 本页显示的账号数
# $total (Number) - 账号总数
pharos-admin-users-truncated = 共 { $total } 个账号，此处显示 { $shown } 个。可用搜索缩小范围。

pharos-admin-role-admin = 管理员
pharos-admin-role-user = 普通用户
pharos-admin-suspended = 已停用
pharos-admin-self = 你
pharos-admin-self-note = 当前账户
pharos-admin-promote = 设为管理员
pharos-admin-demote = 降为普通用户
pharos-admin-deactivate = 停用
pharos-admin-activate = 恢复
pharos-admin-delete = 删除
# $email (String) - 该按钮将删除的账号
pharos-admin-delete-tooltip = 删除 { $email }
pharos-admin-update-failed = 无法修改该账号。

pharos-admin-delete-title = 删除账号
# $email (String) - 将被删除的账号
pharos-admin-delete-body = 将永久删除 { $email } 及其全部数据。
# $papers, $projects, $highlights (Number) - 删除会一并销毁的内容
pharos-admin-delete-owns = 含 { $papers } 篇论文、{ $projects } 个项目、{ $highlights } 条高亮。
pharos-admin-delete-irreversible = 此操作无法撤销。
pharos-admin-delete-prompt = 请输入该账号的邮箱以确认：
pharos-admin-delete-confirm = 永久删除
pharos-admin-deleting = 删除中…
pharos-admin-cancel = 取消
pharos-admin-delete-failed = 删除失败。

pharos-admin-providers-note = API 密钥由服务器的 .env 统一配置，所有账号共享。本页只读——修改密钥需在服务器上编辑该文件并重启。密钥本身不会离开服务器。
# $configured (String) - 配置指定的翻译引擎
# $effective (String) - 实际生效的翻译引擎
pharos-admin-providers-degraded = 翻译已降级：配置的是 { $configured }，实际生效的是 { $effective }。通常是密钥缺失或无效。
pharos-admin-role-translate = 翻译
pharos-admin-role-chat = AI 对话
pharos-admin-providers-empty = 尚未配置任何服务商。
pharos-admin-provider-configured = 已配置
pharos-admin-provider-unconfigured = 未配置
pharos-admin-provider-model = 模型
pharos-admin-provider-url = 地址
pharos-admin-provider-key = 密钥
# $hint (String) - 密钥的末四位
pharos-admin-provider-key-set = 已设置 · 尾号 { $hint }
pharos-admin-provider-key-unset = 未设置
pharos-admin-provider-used-translate = 用于翻译
pharos-admin-provider-used-chat = 用于对话
pharos-admin-probe = 测试连通性
pharos-admin-probing = 测试中…
# $ms (Number) - 测试请求的往返耗时
pharos-admin-probe-ok = 正常 · { $ms } ms
pharos-admin-probe-failed = 测试失败。

# =============================================================================

## 每日论文设置

pharos-prefs-daily-pane = 每日论文
pharos-prefs-daily-open =
    .label = 每日论文设置…

pharos-prefs-daily-signed-out = 登录 Pharos 账号后才能编辑研究方向。

pharos-prefs-daily-note = 方向是你自己的，抓取是大家共享的。改动方向立刻生效——下次打开每日论文就会按新方向重新匹配和排序，不需要重新抓取，也不会重新花一次阅读的钱。arXiv 分类则不同：它决定每天到底把哪些论文抓回来，而这一步全站共用一次请求。新加的分类要等下一次抓取才会带回论文，之前的日期不会补上。

pharos-prefs-daily-directions-header = 研究方向
# $count (Number) - 当前方向数
# $max (Number) - 上限
pharos-prefs-daily-count = { $count } / { $max }
pharos-prefs-daily-loading = 正在读取…
pharos-prefs-daily-load-failed = 无法读取研究方向。

pharos-prefs-daily-empty-title = 还没有任何研究方向
pharos-prefs-daily-empty-desc = 每日论文完全靠方向来筛选：一个方向都没有，每日论文就是空的，抓回来的论文一篇也不会显示。可以自己新建一个，也可以先把默认的七个方向恢复回来再慢慢改。
pharos-prefs-daily-restore = 恢复默认方向
pharos-prefs-daily-restoring = 恢复中…
pharos-prefs-daily-restore-none = 默认方向一个也没能恢复，请改用「新建方向」。

pharos-prefs-daily-add = 新建方向
pharos-prefs-daily-edit = 编辑
pharos-prefs-daily-delete = 删除
pharos-prefs-daily-delete-confirm = 确认删除
pharos-prefs-daily-deleting = 删除中…
pharos-prefs-daily-enabled = 启用中
pharos-prefs-daily-disabled = 已停用
pharos-prefs-daily-move-up = 上移
pharos-prefs-daily-move-down = 下移
pharos-prefs-daily-order-help = 一篇论文同时命中多个方向时，顺序就是决胜条件，它决定这篇论文最终归到哪个方向下。

pharos-prefs-daily-name = 名称
pharos-prefs-daily-name-input =
    .placeholder = 方向名称，例如 VLA
pharos-prefs-daily-keywords = 关键词
pharos-prefs-daily-keywords-input =
    .placeholder = 一行一个，或用逗号分隔
pharos-prefs-daily-save = 保存
pharos-prefs-daily-saving = 保存中…
pharos-prefs-daily-create = 新建
pharos-prefs-daily-cancel = 取消

pharos-prefs-daily-syntax-help = 关键词只要在标题或摘要中原样出现就算命中，空格和标点都算数。用英文双引号包起来——"wam"——则按整词匹配：能命中「WAM:」，但不会命中「swam」。你输入什么就原样提交什么。
pharos-prefs-daily-parsed-none = 还没有关键词。没有关键词的方向什么都匹配不到。
# $count (Number) - 实际参与匹配的词数
pharos-prefs-daily-parsed-count = 将按这 { $count } 个词匹配，出现任意一个即命中。
pharos-prefs-daily-chip-word = 整词匹配
pharos-prefs-daily-chip-substring = 出现即命中

# $max (Number)
# $count (Number)
pharos-prefs-daily-warn-keyword-count = 关键词太多了（最多 { $max } 个，现在 { $count } 个）。
# $max (Number)
# $count (Number)
pharos-prefs-daily-warn-keyword-total = 关键词总长度超出上限（最多 { $max } 字，现在 { $count } 字）。
# $count (Number) - 超长的关键词数量
# $max (Number)
pharos-prefs-daily-warn-keyword-long = 有 { $count } 个关键词超过 { $max } 字。整句话几乎不可能原样出现。

pharos-prefs-daily-sweep-header = 抓取范围
pharos-prefs-daily-categories = arXiv 分类
# $max (Number)
pharos-prefs-daily-categories-help = 这些分类决定每天有哪些论文被抓回来。方向只能在抓回来的论文里筛选——分类之外的论文，写再多关键词也不会出现。逗号或空格分隔，最多 { $max } 个。
# $list (String) - 无法识别的词
pharos-prefs-daily-categories-invalid = 看起来不像 arXiv 分类：{ $list }
# $max (Number)
# $count (Number)
pharos-prefs-daily-categories-too-many = 分类太多了（最多 { $max } 个，现在 { $count } 个）。
pharos-prefs-daily-max = 每日上限
# $min (Number)
# $max (Number)
pharos-prefs-daily-max-help = 一天最多给你留多少篇（{ $min }–{ $max }）。命中的论文多于这个数时，保留推荐分最高的那些。
# $min (Number)
# $max (Number)
pharos-prefs-daily-max-range = 每日上限需要是 { $min } 到 { $max } 之间的整数。
pharos-prefs-daily-max-blank = 留空表示不改动每日上限。
pharos-prefs-daily-config-save = 保存抓取设置
pharos-prefs-daily-config-revert = 还原
pharos-prefs-daily-config-saved = 已保存。
pharos-prefs-daily-config-failed = 无法读取抓取设置。

## AI 模型

pharos-prefs-provider-header = AI 模型
pharos-prefs-provider-loading = 正在读取…
pharos-prefs-provider-failed = 无法读取模型配置。
pharos-prefs-provider-source-personal = 个人模型
pharos-prefs-provider-source-server = 服务器模型
pharos-prefs-provider-source-none = 未配置
pharos-prefs-provider-address = 接口地址
pharos-prefs-provider-model = 模型
pharos-prefs-provider-temperature = Temperature
pharos-prefs-provider-max-tokens = 最大输出 Token
pharos-prefs-provider-key-stored = 该账号已保存 API Key。
pharos-prefs-provider-key-none = 尚未保存你自己的 API Key。
pharos-prefs-provider-key-unsupported = 该服务器没有配置凭据加密密钥，无法保存个人 API Key，目前只能使用管理员提供的服务器模型。
pharos-prefs-provider-security = Pharos 不会把 API Key 留在这台电脑上——不写入设置，不写入日志，哪里都不写。Key 在 Pharos 网页端填写，由服务端加密保存。本页只能显示当前使用的模型并清除它，读不回明文。
pharos-prefs-provider-clear = 清除个人模型
pharos-prefs-provider-clearing = 清除中…
pharos-prefs-provider-clear-confirm = 删除该账号保存的个人模型配置和 API Key？已有的对话不会被删除。
pharos-prefs-provider-cleared = 个人模型配置已清除。
pharos-prefs-provider-cleared-server = 个人模型配置已清除，已恢复使用服务器模型。
pharos-prefs-provider-clear-failed = 无法清除模型配置。

## 外观

pharos-prefs-appearance-pane = 外观

pharos-prefs-appearance-scheme-header = 配色方案
pharos-prefs-appearance-scheme = 配色方案：
pharos-prefs-appearance-scheme-auto =
    .label = 跟随系统
pharos-prefs-appearance-scheme-light =
    .label = 浅色
pharos-prefs-appearance-scheme-dark =
    .label = 深色
pharos-prefs-appearance-scheme-help = 「跟随系统」随操作系统切换。此项与「常规」中的同名设置是同一个。

pharos-prefs-appearance-accent-header = 强调色
pharos-prefs-appearance-accent-help = 仅用于关键处：选中行、激活图标、焦点框与链接。
pharos-prefs-appearance-accent-group =
    .aria-label = 强调色
pharos-prefs-appearance-accent-note = 浅色主题下每个强调色都会加深，以保证作为文字落在纸色底上仍然可读。

pharos-prefs-appearance-accent-pharos = 灯塔蓝
pharos-prefs-appearance-accent-beacon = 灯塔金
pharos-prefs-appearance-accent-mint = 薄荷
pharos-prefs-appearance-accent-sky = 天蓝
pharos-prefs-appearance-accent-pine = 松绿
pharos-prefs-appearance-accent-indigo = 靛蓝
pharos-prefs-appearance-accent-lilac = 丁香
pharos-prefs-appearance-accent-coral = 珊瑚
pharos-prefs-appearance-accent-amber = 琥珀
pharos-prefs-appearance-accent-stone = 石青

pharos-auth-window =
    .title = 登录 Pharos

pharos-auth-tagline = 从文献发现到研究推进的一体化科研工作台

# 海报图缺失时显示在品牌面板上
pharos-auth-poster-sub = 照亮文献之海

pharos-auth-mode-login = 登录
pharos-auth-mode-register = 注册

pharos-auth-email = 邮箱
pharos-auth-email-placeholder =
    .placeholder = you@example.com
pharos-auth-email-required = 请输入邮箱
pharos-auth-email-invalid = 邮箱格式不正确

pharos-auth-password = 密码
pharos-auth-password-placeholder =
    .placeholder = ••••••••
# $min (Number) - 后端要求的最短密码长度
pharos-auth-password-placeholder-register =
    .placeholder = 至少 { $min } 个字符
pharos-auth-password-required = 请输入密码
# $min (Number) - 后端要求的最短密码长度
pharos-auth-password-short = 密码至少 { $min } 个字符

pharos-auth-display-name = 显示名称
pharos-auth-display-name-optional = · 可选
pharos-auth-display-name-placeholder =
    .placeholder = 留空则使用邮箱

pharos-auth-submit-sign-in = 登录
pharos-auth-submit-register = 创建账户
pharos-auth-submitting-sign-in = 登录中…
pharos-auth-submitting-register = 创建中…

pharos-auth-register-note = 注册即拥有独立文库 · 你的论文与翻译只有你能看到
pharos-auth-registration-closed = 本实例已关闭注册 · 请使用已有账户登录

# 越过登录页的出口。文库、阅读器与标注全部在本地，没有账号也能用。
pharos-auth-skip = 暂不登录
pharos-auth-skip-note = 文库、阅读器与标注无需账号即可使用。之后可在「设置 → Pharos」中登录。

# 按本地日期选一句。写给在长长的读文献的一天开始时打开它的人，
# 语气是安静的鼓励而非口号：不为「你来了」道贺，也不许诺产品能带来什么。
pharos-auth-greeting-0 = 愿今天有一篇，正好照亮你卡住的地方
pharos-auth-greeting-1 = 读不完的文献，一篇一篇来
pharos-auth-greeting-2 = 慢一点也没关系，读懂比读完重要
pharos-auth-greeting-3 = 好问题比好答案更难得
pharos-auth-greeting-4 = 今天也在往前，哪怕只挪了一点
pharos-auth-greeting-5 = 灯亮着，海就不算太黑
pharos-auth-greeting-6 = 你正在做的事，值得慢慢做
pharos-auth-greeting-7 = 先读一篇，再说别的

# Shown in place of an address when no one is signed in.
pharos-rail-account-none = 未登录
pharos-rail-account-settings = 设置与账号
# Sub-label of the signed-out footer. It says what the button does, because
# signing in is the only thing worth doing from that state.
pharos-rail-account-sign-in = 点击登录
# Names the button when the rail is collapsed and the labels are hidden.
pharos-rail-account-tooltip =
    .title = 设置与账号
pharos-rail-account-sign-in-tooltip =
    .title = 登录 Pharos
