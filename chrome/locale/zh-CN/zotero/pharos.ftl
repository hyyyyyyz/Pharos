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

pharos-discovery-section-contribution = 贡献
pharos-discovery-section-core-trick = 核心思路
pharos-discovery-section-method = 方法
pharos-discovery-section-results = 结果
pharos-discovery-section-limitations = 局限
pharos-discovery-rules-note = 该摘要由规则生成，未经模型阅读。可点「精读」获取模型解读。
