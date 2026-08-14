# Pharos 专属字符串。Zotero 自己的字符串留在 zotero.ftl，
# 这样将来手工合并上游改动时不会和我们的冲突。

pharos-error-signed-out = 尚未登录 Pharos
pharos-error-signed-out-detail = 请先在设置中登录 Pharos 账号。

## 页面证据

pharos-evidence-save = 保存为证据
pharos-evidence-saving = 正在保存证据…
pharos-evidence-saved = 证据已保存
pharos-evidence-error = 无法保存证据。
pharos-evidence-error-empty = 请先选中一段文字。
pharos-evidence-error-pdf-only = 只有 PDF 阅读器支持保存证据。
pharos-evidence-error-signed-out = 登录 Pharos 后才能保存证据。
pharos-evidence-error-not-in-paper = 这段文字未在论文的已提取页面中找到。

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
# 显示名。左栏底部的账号区优先显示它，地址退到提示框——地址是账号的标识，不能消失。
pharos-prefs-display-name = 显示名
pharos-prefs-display-name-input =
    .placeholder = 留空则显示邮箱
pharos-prefs-display-name-save = 保存
pharos-prefs-display-name-saved = 已保存。左栏现在显示这个名字。
pharos-prefs-display-name-cleared = 已清空。左栏恢复显示邮箱地址。
pharos-prefs-display-name-failed = 保存失败，请稍后再试。
# 账号级的整篇翻译开关，不是本机偏好——网页版改了这里也会变。
pharos-prefs-pdf-translation = 整篇 PDF 翻译
pharos-prefs-pdf-translation-help = 保留排版重建整篇论文，耗时且消耗接口额度。关闭后翻译入口、状态列与阅读模式一并隐藏。
pharos-prefs-pdf-translation-failed = 未能保存，请稍后再试。
pharos-prefs-sign-out = 退出登录
pharos-prefs-sign-out-all = 退出所有设备
pharos-prefs-sign-out-all-confirm = 这会让所有已登录 Pharos 的设备一并退出，是否继续？
pharos-prefs-sign-out-all-help = 退出所有设备会吊销该账号签发过的全部令牌。

# 「关于」区块：当前构建的版本号，以及是否存在更新。手动检查与左侧栏横幅共用
# 同一个模块与端点，两个表面不可能给出相互矛盾的回答。
pharos-prefs-about-header = 关于 Pharos
pharos-prefs-about-version = 当前版本
pharos-prefs-check-updates = 检查更新
pharos-prefs-checking-updates = 正在检查更新…
pharos-prefs-update-latest = 已是最新版本。
pharos-prefs-update-none = 服务器暂未发布新版本。
pharos-prefs-update-failed = 检查更新失败，请稍后重试。
pharos-prefs-update-found = 新版本已发布。下载安装包并替换当前版本即可。
pharos-prefs-update-ignored = 你已忽略该版本，仍可从下方下载。
pharos-prefs-update-download = 下载 v{ $version }

## AI 对话

# 网页版从头到尾叫「AI 对话」。同一个功能在两个客户端上叫两个名字，
# 是用户唯一无从自己解决的困惑，所以桌面端的「问 Pharos」向网页版看齐。
section-pharos-chat =
    .label = AI 对话
# 侧边导航按钮的提示，以及 collapsible-section 拿去拼 aria 名称的那一条。
# 两个 id 都由上游按 data-pane 拼出来（sidenav-${pane} / pane-${pane}），
# 缺了不报错：DOM 本地化只是 reject，按钮就一直没有提示。
pane-pharos-chat = AI 对话
sidenav-pharos-chat =
    .tooltiptext = AI 对话
pharos-chat-placeholder =
    .placeholder = 就这篇论文提问…
pharos-chat-hint = Enter 发送 · Shift+Enter 换行
pharos-chat-send = 发送
pharos-chat-stop = 停止
pharos-chat-dismiss = 关闭

## AI 对话 · 对话管理

section-button-pharos-chat-new =
    .tooltiptext = 新建对话
section-button-pharos-chat-more =
    .tooltiptext = 对话操作
pharos-chat-session-select =
    .aria-label = 选择对话
# 后端在第一次提问时把对话改名为那句问题；还没问过的保持这个默认名。
pharos-chat-untitled = 论文对话
pharos-chat-delete = 删除当前对话
# 这句承诺经过核对：delete_conversation 只删对话本身，论文的正文与模型理解
# 存在另一张按（用户，论文）建的表里（PaperAiContext），不受影响，所以下次
# 提问不必重新上传、也不必重新读一遍。对话里的消息则会一并删除——网页版那句
# 只说了什么会留下，这里把什么会没有也说清楚。
pharos-chat-delete-confirm = 删除当前 AI 对话？这次对话的消息会一并删除；论文索引会保留，下次提问不必重新上传。
pharos-chat-delete-go = 删除
pharos-chat-delete-cancel = 取消

## AI 对话 · 论文状态

# 标题下常驻的一行。它只汇报已知状态，从不为了有话可说而去准备什么——
# 解析一篇论文要上传整个文件，那笔钱只在用户真的提问时才花。
pharos-chat-phase-understanding = 正在建立论文理解
pharos-chat-phase-ready = 已理解这篇论文
pharos-chat-phase-indexed = 已读取论文正文
pharos-chat-phase-indexed-no-model = 已读取正文 · 等待配置模型
pharos-chat-phase-error = 论文理解失败
# 本次启动还没解析过这篇论文时的说法。网页版这里写「准备论文上下文」，
# 但桌面端此刻刻意什么都没在准备，照抄会把「不做」说成「在做」。
pharos-chat-phase-lazy = 首次提问时读取这篇论文
# $count (Number) - 后端抽取到的字符数
pharos-chat-phase-chars = 已读取 { $count } 字符

## AI 对话 · 空状态

pharos-chat-empty-ready = 我已经预先理解这篇论文
pharos-chat-empty-understanding = 正在为这篇论文建立上下文
pharos-chat-empty-idle = 围绕当前论文开始对话
# 四个起手问题，与网页版逐字一致。按钮上的字就是发出去的问题本身。
pharos-chat-starter-contribution = 核心贡献是什么？
pharos-chat-starter-trick = 真正关键的 trick 是什么？
pharos-chat-starter-evidence = 实验如何证明方法有效？
pharos-chat-starter-limitations = 这篇论文有哪些局限？

## AI 对话 · 无法提问时

pharos-chat-signed-out-title = 登录后可以就这篇论文提问
pharos-chat-signed-out-detail = 论文由 Pharos 后端读取并交给模型，因此需要一个账号。文库、阅读器与标注不受影响。
pharos-chat-signed-out-action = 前往登录

pharos-chat-no-model-title = 连接你的模型
# 桌面端故意不提供填写框：API Key 只存在于后端，加密保存，从不经过这台电脑。
# 所以这里能做的是说清楚这件事，并指向设置里那个只读的模型视图。
pharos-chat-no-model-detail = 支持 OpenAI 兼容接口。API Key 只能在 Pharos 网页端填写，由服务端加密保存，不会写入这台电脑——因此桌面端没有填写框。设置里可以查看当前生效的模型。
pharos-chat-no-model-action = 查看模型设置

## AI 对话 · 进度与失败

pharos-chat-status-connecting = 连接中…
pharos-chat-status-preparing = 正在读取论文…
pharos-chat-status-thinking = 思考中…
# 不当作错误显示：按下停止的人知道为什么停了。
# 后端在中断时不保存半截回答（stream_chat_events 的 GeneratorExit 分支），
# 所以屏幕上那半截也一并撤掉，否则它就是一条模型并不记得的发言。
pharos-chat-stopped = 已停止生成，这次回答未保存。

pharos-chat-error-prepare = 无法读取这篇论文。
pharos-chat-error-prepare-timeout = 读取论文超时。
pharos-chat-error-failed = 未能生成回答。
pharos-chat-error-empty = 没有收到回答。
pharos-chat-error-history = 无法读取这次对话的记录。
pharos-chat-error-new = 无法新建对话。
pharos-chat-error-delete = 无法删除这次对话。

pharos-error-unreachable = 无法连接到 Pharos 服务器。

## 每日论文

# 用词：模型产出的那份东西一律叫「解读」，不叫「阅读」。
# 「阅读」是人做的事，两者混用会让「解读失败」读起来像用户读失败了。

pharos-daily-menu = 每日论文…
pharos-daily-window =
    .title = 每日论文
# pharos-daily-window 只有属性没有值，Zotero.getString() 读不到它，
# 在 en-US 下还会直接抛错。要在正文里写模块名，用这一条。
pharos-daily-heading = 每日论文
pharos-daily-error = 无法加载每日论文。
pharos-daily-loading = 载入中…
# $count (Number) - 当天匹配到的论文数
# 方向筛选把列表收窄时，给出当天的总数，免得筛后的数字被读成"这天没什么论文"。
pharos-daily-count-all = 全天 { $count } 篇
pharos-daily-count = { $count } 篇
pharos-daily-matched = 命中方向
# 写进 Zotero 笔记的溯源行。这条笔记会永久留在文库里，和用户自己写的读书笔记
# 长得一模一样，六个月后会被当作自己的理解引用出去。模型只看了摘要
# （backend/pharos/daily/reader.py：“The model reads this and nothing else”），
# 所以“方法/结果”两条是从摘要推断的，不是从正文读来的。
pharos-daily-note-provenance = 以上内容由模型 { $model } 依据英文摘要生成，模型未阅读正文。
pharos-daily-note-provenance-unknown = 以上内容由模型依据英文摘要生成，模型未阅读正文。


pharos-daily-highlight-contribution = 贡献
pharos-daily-highlight-innovation = 创新
pharos-daily-highlight-method = 方法
pharos-daily-highlight-results = 结果

## 每日论文 · 日期栏

pharos-daily-rail-head = 日期
pharos-daily-rail-unreachable = 无法连接
pharos-daily-rail-no-directions = 未配置方向
pharos-daily-rail-no-match = 无匹配日期
pharos-daily-rail-empty = 暂无记录
# $count (Number) - 该日尚未解读的论文数
pharos-daily-date-pending = { $count } 篇待解读

## 每日论文 · 工具栏

pharos-daily-refresh = 更新
pharos-daily-refreshing = 更新中
pharos-daily-refresh-tooltip = 抓取今日 arXiv 并解读
pharos-daily-filter-all = 全部
pharos-daily-sort-score = 推荐分
pharos-daily-sort-time = 时间

## 每日论文 · 更新进度与失败

# $total (Number) - 当前账号已匹配到的论文数
# $read (Number) - 其中已解读的篇数
# 数字来自 getStatus().today 或 getDates() 里对应的那一行。
# 不要用 last_run 的计数：那三列在 _finish_run 才写，整个抓取过程中都是 0。
pharos-daily-sweep-progress = 正在更新 · 已匹配 { $total } 篇 · 已解读 { $read }
# $failed (Number) - 解读失败的篇数
# 只在失败数大于 0 时追加在上一条后面。开头的 " · " 是这条的一部分——
# Fluent 会吃掉 "=" 后面的前导空格，所以必须写成字符串字面量。
pharos-daily-sweep-failed = { " " }· 解读失败 { $failed }
pharos-daily-last-run-failed = 上次更新失败
# $error (String) - 服务端记下的失败原因
pharos-daily-last-run-failed-detail = 上次更新失败：{ $error }
# $error (String)
pharos-daily-refresh-failed = 更新失败：{ $error }
# 409 时代替服务端那句英文原文显示
pharos-daily-refresh-busy = 已经有一次更新在进行中。

## 每日论文 · 未配置解读模型

# 说明而非报错：没有 Key 抓取照样能跑，只是不解读。
pharos-daily-no-llm = 尚未配置 LLM API Key —— 论文可以正常抓取并按你的方向筛选排序，但无法生成中文解读与评分。这些论文会保持「待解读」，配置后可随时重新解读。
pharos-daily-no-llm-tooltip = 尚未配置 LLM API Key
# 解读接口返回 503 时显示
pharos-daily-read-unavailable = 尚未配置解读模型，无法解读。
# $name (String) - 服务商名
# $model (String) - 模型名
pharos-daily-provider = 解读模型：{ $name } · { $model }
pharos-daily-provider-none = 未配置解读模型

## 每日论文 · 空状态

pharos-daily-no-directions-title = 尚未配置研究方向
pharos-daily-no-directions-desc = 每日论文按你自己定义的研究方向筛选 arXiv。当前没有任何启用的方向，因此不会有论文进入这里。在设置中添加一个方向并填入关键词即可开始。
pharos-daily-disabled-title = 每日论文已关闭
pharos-daily-disabled-desc = 这个账号关闭了每日论文。抓取仍在继续，但不会有论文进入这里。在「设置 → 每日论文」中重新开启即可。
pharos-daily-firstuse-title = 每日论文
pharos-daily-firstuse-desc = 每天自动扫描 arXiv，按你在设置里定义的研究方向筛出相关论文，逐篇生成中文解读、要点与评分。点「更新」抓取第一份日报。
pharos-daily-nomatch-title = 没有论文匹配你的方向
pharos-daily-nomatch-desc = 抓取已经运行过，但目前没有任何一篇论文命中你的方向关键词。可以在设置中放宽关键词、增加方向或分类；改动会立即重新筛选，无需重新抓取。
pharos-daily-directions-label = 当前方向
pharos-daily-open-settings = 前往设置
pharos-daily-edit-directions = 调整方向
pharos-daily-refetch = 重新抓取
pharos-daily-day-unswept = 该日尚未抓取
pharos-daily-day-unswept-hint = 周末与公告间隔期通常没有更新
pharos-daily-day-nomatch = 该日无匹配你方向的论文
# $fetched (Number) - Day.run.fetched，即那天全站抓回来的总数
pharos-daily-day-nomatch-hint = 当日共抓取 { $fetched } 篇，均未命中你的关键词
# 配 pharos-error-unreachable 一起显示
pharos-daily-unreachable-hint = 请确认 Pharos 服务已启动后重试。
pharos-daily-detail-empty = 选择一篇论文查看解读

## 每日论文 · 卡片与解读

pharos-daily-pending = 待解读
pharos-daily-read = 解读
pharos-daily-reading = 解读中…
pharos-daily-retry = 重试
pharos-daily-retrying = 重试中…
# 论文自身的状态
pharos-daily-read-failed = 解读失败
# $error (String) - 上一次解读留下的 read_error。与上一条同时显示：
# 一条说这篇是什么状态，一条说为什么。不要合并。
pharos-daily-read-failed-detail = 解读失败：{ $error }
# $error (String) - 这一次重试自己的失败，显示在第三行
pharos-daily-retry-failed = 重试失败：{ $error }
pharos-daily-score-tooltip = 推荐分 · 含你的方向相关度
pharos-daily-open = arXiv 摘要页
pharos-daily-open-pdf = PDF
# 信息栏里没有值时的占位符
pharos-daily-none = —

## 每日论文 · 导入文库

# 桌面端的「导入文库」是存进本地 Zotero 文库，不是后端的 /import。
pharos-daily-import = 导入文库
pharos-daily-importing = 导入中…
pharos-daily-imported = 已在文库
# $error (String)
pharos-daily-import-failed = 导入失败：{ $error }

# 研究项目仍在用下面这四条：项目文献里每一行的「加入文库」。删掉会让那个窗口
# 在 en-US 下直接抛错、在中文下把 id 原样显示出来。等它也改用
# pharos-daily-import* 之后再删。
# 文献探索已经有自己的 pharos-discovery-save*，不再借这里。
pharos-daily-save = 加入文库
pharos-daily-saving = 保存中…
pharos-daily-saved = 已加入
pharos-daily-save-failed = 无法保存这篇论文。

## 每日论文 · 详情面板

pharos-daily-section-summary = 中文速览
pharos-daily-section-highlights = 要点
pharos-daily-section-scores = 评分
pharos-daily-section-info = 信息
pharos-daily-section-abstract = 英文摘要

# 顺序固定：相关、时效、热度、质量、推荐。推荐是加权结论，所以放最后。
pharos-daily-score-relevance = 相关
pharos-daily-score-recency = 时效
pharos-daily-score-popularity = 热度
pharos-daily-score-quality = 质量
pharos-daily-score-recommendation = 推荐
pharos-daily-score-relevance-hint = 与你的研究方向的匹配度，按你的关键词计算
pharos-daily-score-recency-hint = 论文本身的时效性
pharos-daily-score-popularity-hint = 论文本身的关注度
pharos-daily-score-quality-hint = 论文本身的质量
pharos-daily-score-recommendation-hint = 综合评分，含你的相关度，因此因人而异
pharos-daily-score-note = 相关与推荐按你的研究方向计算

pharos-daily-info-authors = 作者
pharos-daily-info-direction = 方向
pharos-daily-info-direction-hint = 命中的是你的哪个研究方向
pharos-daily-info-categories = 分类
pharos-daily-info-keywords = 命中
pharos-daily-info-keywords-hint = 你的哪些关键词命中了这篇论文

## 每日论文 · 数据目录

# 数据目录（Daily Vault）是「每日论文」的可携带副本：一个普通文件夹，里面是
# pharos-vault.json 加一批内容寻址的快照文件。格式见 docs/DAILY_VAULT_FORMAT.md，
# 与网页版逐字节一致，两边可以互相打开。
#
# 措辞底线：这里备份的是「日报」，不是「文库」。已导入的论文、它的 PDF 和那条
# 溯源笔记都在 Zotero 数据目录里，由数据目录自己的备份负责。任何一句让人以为
# 这是文库备份的文案，都会在硬盘坏掉那天变成最坏的一种误导——所以下面的
# scope 三条不要为了简洁而删。

pharos-daily-vault = 数据目录
pharos-daily-vault-saving = 保存中
pharos-daily-vault-attention = 待确认
pharos-daily-vault-eyebrow = PHAROS DAILY VAULT · V1
pharos-daily-vault-title = 每日论文数据目录
pharos-daily-vault-close = 关闭
pharos-daily-vault-none = 尚未连接本地目录
pharos-daily-vault-picker = 选择存放每日论文数据的文件夹

## 每日论文 · 数据目录 · 状态行

pharos-daily-vault-idle = 尚未选择数据目录
# 自动保存暂停时共用这一句；到底是哪一种，由下面的提示块说明。
pharos-daily-vault-paused = 自动保存已暂停，等待你确认
# $days (Number) - 目录里的天数
# $papers (Number) - 目录里的论文总数
pharos-daily-vault-connected = 已连接 · { $days } 天 · { $papers } 篇
pharos-daily-vault-created = 目录已初始化 · { $days } 天 · { $papers } 篇
pharos-daily-vault-saved = 已保存 · { $days } 天 · { $papers } 篇
pharos-daily-vault-unsaved = 最近一次保存没有成功
# $added, $updated, $unchanged (Number) - 后端返回的合并结果
pharos-daily-vault-restored = 恢复完成 · 新增 { $added } 篇 · 更新 { $updated } 篇 · 保留 { $unchanged } 篇；研究方向与筛选设置已被目录里的版本替换
pharos-daily-vault-disconnected = 已解除连接；目录里的文件没有被删除
# $error (String)
pharos-daily-vault-failed = 数据目录操作失败：{ $error }

## 每日论文 · 数据目录 · 需要确认

pharos-daily-vault-warn-head = 需要你确认
# $path (String) - 记住的那个目录
pharos-daily-vault-warn-missing = 找不到这个目录：{ $path }。多半是外置磁盘没挂载，或者文件夹被移走、改名了。目录回来之前不会写入，也不会在原位置新建一个空的——那会让下次挂载时真正的备份被盖住。
pharos-daily-vault-warn-empty = 目录还在，但里面没有 pharos-vault.json。点「立即保存」可以重新写一份；也可能是你选错了文件夹。
pharos-daily-vault-warn-changed = 目录里的这份数据不是 Pharos 上次写进去的那一份——可能被另一台机器同步过，或者路径被别的文件夹顶替了。先选一个方向：把它恢复到这个账号，或者用这个账号的数据覆盖它。
# $error (String)
pharos-daily-vault-warn-broken = 读不出目录里的清单：{ $error }。在弄清原因之前不会覆盖它。
# $days (Number), $papers (Number)
pharos-daily-vault-warn-existing = 这个目录里已经有一份数据（{ $days } 天、{ $papers } 篇）。换新机器时，账号往往是空的而目录不是，所以不会直接写入：先选择恢复还是覆盖。

## 每日论文 · 数据目录 · 操作

pharos-daily-vault-choose = 选择目录
pharos-daily-vault-change = 更换目录
pharos-daily-vault-choose-hint = 任意普通文件夹。放进 iCloud、OneDrive、Dropbox、Syncthing 或移动硬盘即可在多台机器之间使用。
pharos-daily-vault-save-now = 立即保存
pharos-daily-vault-save-now-hint = 把当前账号完整的每日论文快照写进这个目录。
pharos-daily-vault-restore = 从此目录恢复
pharos-daily-vault-restore-hint = 逐个文件校验后合并论文，并用目录里的版本替换研究方向与筛选设置。
pharos-daily-vault-overwrite = 用当前账号覆盖目录
pharos-daily-vault-overwrite-hint = 保留目录本身的身份，用当前账号的数据写入新的快照。
pharos-daily-vault-disconnect = 解除连接（不删除目录里的文件）

## 每日论文 · 数据目录 · 二次确认

pharos-daily-vault-restore-title = 从数据目录恢复
# $days (Number), $papers (Number)
# 两句话，分工固定：第一句说会得到什么，第二句说会失去什么。合并成一句时，
# 「替换设置」这半句总是被读漏。
pharos-daily-vault-restore-body =
    将恢复 { $days } 天、{ $papers } 篇论文。论文是合并，不会删掉服务器上已有的解读。
    同时会用目录里的版本替换这个账号的研究方向与筛选设置：现有方向、关键词、分类与每日上限都会被覆盖，且无法撤销。
pharos-daily-vault-restore-ok = 恢复并替换设置
pharos-daily-vault-overwrite-title = 用当前账号覆盖目录
# $days (Number), $papers (Number)
pharos-daily-vault-overwrite-body =
    目录里现有 { $days } 天、{ $papers } 篇。覆盖后 Pharos 会以当前账号的数据继续写入这个目录。
    旧的快照文件不会被删除，但清单不再指向它们；如果那份数据是另一台机器或另一个账号的唯一副本，请先恢复或另存一份。
pharos-daily-vault-overwrite-ok = 覆盖目录

## 每日论文 · 数据目录 · 边界说明

# 整个面板里最重要的三句。桌面端和网页版在这里的答案不一样：桌面端**有**真正的
# 本地文库，所以必须说清楚这个目录不管文库。
pharos-daily-vault-scope-head = 备份的是「每日论文」，不是你的文库
pharos-daily-vault-scope-in = 会写进目录：研究方向与筛选设置（分类、每日上限、开关，以及每个方向的关键词与排序）——它们只存在于服务器上，本机没有任何副本，换服务器或丢账号就得一条条重打；还有每天的论文快照、中文解读、要点、评分与命中方向，包括你没有导入文库的那些，也就是日报里的绝大多数。
pharos-daily-vault-scope-out = 不会写进目录：你的 Zotero 文库。已导入的论文条目、它的 PDF 和那条溯源笔记本来就在本地数据目录里，由数据目录自己的备份负责，这里既不重复保存，硬盘坏了也救不回它们。登录密码、JWT、LLM Key、Zotero Token 和账号 id 同样不会写入。
pharos-daily-vault-scope-when = 每次抓取结束后自动保存一次；在设置里改完研究方向，可以回到这里点「立即保存」。目录里的旧快照永远不会被删除。

## 每日论文 · 数据目录 · 读写失败

# 下面这些是 Zotero.Pharos.Daily.Vault 抛出的 Error 的文案。目录是用户随手指定
# 的路径，内容可能被任何东西改过，所以每一种拒绝都要说清楚拒绝的是什么。
# $path (String)
pharos-daily-vault-unsafe-path = 目录清单里有不安全的路径，已拒绝：{ $path }
pharos-daily-vault-missing-file = 清单指向的文件不存在：{ $path }
pharos-daily-vault-too-large = 文件过大，已拒绝读取：{ $path }
pharos-daily-vault-root-missing = 目录不存在，未写入任何文件：{ $path }
# $label (String) - 出问题的是清单里的哪一条
pharos-daily-vault-entry-missing = 清单缺少 { $label } 条目
pharos-daily-vault-entry-invalid = 清单里的 { $label } 条目格式错误
pharos-daily-vault-entry-digest = 清单里的 { $label } 校验值格式错误
pharos-daily-vault-checksum = { $label } 校验失败，文件可能已损坏或被改动
pharos-daily-vault-bad-file = { $label } 不是有效 JSON
pharos-daily-vault-label-profile = 研究方向配置
pharos-daily-vault-label-day = 每日快照
pharos-daily-vault-bad-json = pharos-vault.json 不是有效 JSON
pharos-daily-vault-bad-manifest = pharos-vault.json 缺少必要字段
pharos-daily-vault-bad-version = 这个目录不是受支持的 Pharos 数据目录 v1
pharos-daily-vault-bad-index = 数据目录的日期索引无效
pharos-daily-vault-bad-archive = 服务器返回的每日论文快照不完整
pharos-daily-vault-no-manifest = 所选目录里没有 pharos-vault.json
pharos-daily-vault-bad-profile = 目录里的研究方向配置版本不受支持
# $date (String) - YYYY-MM-DD
pharos-daily-vault-bad-date = 日期无效：{ $date }
pharos-daily-vault-bad-day = { $date } 的每日快照格式错误
pharos-daily-vault-bad-count = { $date } 的论文数量与清单不符

## 文献探索

pharos-discovery-menu = 文献探索…
pharos-discovery-window =
    .title = 文献探索
# pharos-discovery-window 只有属性没有值，Zotero.getString() 读不到它。
# 要在正文里写模块名，用下面这一条。
pharos-discovery-heading = 文献探索
pharos-discovery-subheading = 从研究问题出发；每次检索都保留它用了哪些来源、哪些失败了，以及分析到什么程度。
pharos-discovery-current-project = 当前项目

## 文献探索 · 检索条件

pharos-discovery-query-label = 研究问题或 Idea
pharos-discovery-placeholder =
    .placeholder = 例如：KV cache compression for long-context video generation
pharos-discovery-search = 运行检索
pharos-discovery-searching = 正在检索…
# 这一条必须说清楚：按下「运行检索」时没有任何一篇被读过。
# 两个来源只返回题录与摘要，模型要逐篇点「生成核心思路」才会被调用。
pharos-discovery-hint = 输入研究问题后点「运行检索」。arXiv 与 OpenAlex 只返回题录与摘要；中文核心思路要逐篇点「生成核心思路」才会调用模型。
pharos-discovery-language-hint = arXiv 建议用英文关键词；中文会原样发送，当前不会自动翻译。

pharos-discovery-sources-label = 检索来源
pharos-discovery-source-arxiv = arXiv
pharos-discovery-source-arxiv-note = 预印本与最新工作
pharos-discovery-source-openalex = OpenAlex
pharos-discovery-source-openalex-note = 跨出版源与引用数
pharos-discovery-source-unknown = 来源未知
# 拼接多个来源名时用的分隔符。中文是顿号；en-US 是逗号加空格，
# 而 Fluent 会吃掉 "=" 之后的空白，所以那一边必须写成字符串字面量。
pharos-discovery-source-separator = 、

pharos-discovery-project-label = 关联项目
pharos-discovery-project-none = 暂不关联
# $name (String) - 项目名称
pharos-discovery-project-archived = { $name }（已归档）
pharos-discovery-limit-label = 数量
pharos-discovery-projects-failed = 项目列表加载失败；仍然可以检索，但暂时无法把结果加入项目。

## 文献探索 · 表单校验

# $min (Number) - 检索词的最少字符数
pharos-discovery-need-query = 检索词至少需要 { $min } 个字符。
# $max (Number) - 检索词的最多字符数
pharos-discovery-query-too-long = 检索词最多 { $max } 个字符。
pharos-discovery-need-source = 至少选择一个检索来源。
# $min (Number)
# $max (Number)
pharos-discovery-limit-range = 数量需要是 { $min } 到 { $max } 之间的整数。
# $error (String) - 传输层或服务端给出的原因
pharos-discovery-search-failed = 检索请求失败：{ $error }

## 文献探索 · 历史栏

pharos-discovery-history-head = 探索记录
pharos-discovery-history-loading = 正在读取历史…
pharos-discovery-history-empty = 完成一次检索后，随时可以重新打开它的结果。
pharos-discovery-history-failed = 无法读取检索历史。
# $time (String) - 窗口用 Intl.DateTimeFormat 预先格式化好的时间。
# 是普通字符串，不是 Fluent 的 DATETIME()，不要改成日期参数。
# $count (Number) - 该次检索留下的结果数
pharos-discovery-history-meta = { $time } · { $count } 篇
pharos-discovery-retry = 重试
# 只有历史行没带 results、需要单独取详情时才会出现。请求在飞的时候屏幕上
# 原本什么都不变，上一次运行还留在那里，读者无从知道点击有没有生效。
pharos-discovery-opening = 正在打开检索结果…
# $error (String)
pharos-discovery-open-failed = 无法打开这次检索：{ $error }

## 文献探索 · 运行状态

pharos-discovery-status-complete = 已完成
pharos-discovery-status-partial = 部分完成
pharos-discovery-status-error = 失败
# 刻意写「未完成」而不是「检索中」：POST /api/discovery/search 是同步的，
# 只在成功时落库，所以库里留下的 running 行代表请求中途死了，不是还在跑。
pharos-discovery-status-running = 未完成
pharos-discovery-status-running-hint = 这次运行没有正常结束，结果可能不完整。重新运行会新建一条记录，不会覆盖它。
# $count (Number) - 本次留下的结果数
# $sources (String) - 来源名，已由窗口用 pharos-discovery-source-separator 拼好
# $time (String) - 预先格式化好的时间，同 pharos-discovery-history-meta
pharos-discovery-run-meta = { $count } 篇 · { $sources } · { $time }
pharos-discovery-reuse = 复用条件
pharos-discovery-reused = 已把历史条件放回检索框，可调整后重新运行。
pharos-discovery-errors-partial = 部分来源未返回
pharos-discovery-errors-all = 来源错误

## 文献探索 · 结果提示

# $count (Number) - 找到的候选文献数
pharos-discovery-notice-complete = 检索完成，找到 { $count } 篇候选文献。
# $count (Number) - 仍然可用的结果数
pharos-discovery-notice-partial = 部分来源完成，已保留 { $count } 篇可用结果。
pharos-discovery-notice-error = 所有来源都失败了。这次运行已经保存，可以从左侧重新打开。

## 文献探索 · 批量加入项目

# 这两条按网页版逐字对齐：同一个操作区在两个客户端上不该有两种说法。
pharos-discovery-select-all = 选择全部
# $count (Number) - 已勾选的论文数
pharos-discovery-selected = { $count } 篇已选择
pharos-discovery-add-to-project = 加入项目
pharos-discovery-adding = 正在加入…
# 项目下拉框的标签与提示，不再是弹窗提问。
pharos-discovery-pick-project = 选择项目
pharos-discovery-new-project = 新建项目
pharos-discovery-new-project-head = 新建项目并归档所选文献
pharos-discovery-new-project-desc = 项目建立后会成为当前项目。
pharos-discovery-new-project-name =
    .placeholder = 项目名称
pharos-discovery-new-project-question =
    .placeholder = 研究问题（可选）
# $count (Number) - 将一并加入的论文数
pharos-discovery-new-project-create = 创建并加入 { $count } 篇
pharos-discovery-new-project-creating = 正在创建…
pharos-discovery-need-project = 请先选择一个项目。
pharos-discovery-need-selection = 请先勾选要加入的论文。
pharos-discovery-filed = 已在当前项目
# $name (String) - 目标项目名
# $added (Number) - 本次新增的篇数
pharos-discovery-file-result = 已处理「{ $name }」：新增 { $added } 篇
# $count (Number) - 已经在项目里、这次跳过的篇数
# 只在数量大于 0 时接在上一条后面，句号由窗口补，和 pharos-daily-sweep-failed 同一套写法。
pharos-discovery-file-skipped = ，{ $count } 篇已存在
# $count (Number) - 加入失败的篇数，同样只在大于 0 时追加
pharos-discovery-file-failed = ，{ $count } 篇加入失败
# $error (String)
pharos-discovery-file-error = 加入项目失败：{ $error }

## 文献探索 · 结果卡片

pharos-discovery-rank-tooltip = 本次检索中的排序位次
# 卡片信息行的四段。全部带标签，和网页版一致：光秃秃的值用「·」串起来时，
# 读者分不出哪一段是刊物名、哪一段是来源列表——而「arXiv」两边都可能出现。
# $year (String) - 出版年，窗口已转成字符串
pharos-discovery-meta-year = { $year } 年
# $venue (String) - 刊物或会议名
pharos-discovery-meta-venue = 刊载：{ $venue }
# $sources (String) - 佐证这篇论文的来源，已由窗口用
#   pharos-discovery-source-separator 拼好。不是本次检索请求的来源列表。
pharos-discovery-meta-sources = 来源：{ $sources }
# $count (Number) - 被引次数
pharos-discovery-citations = 引用 { $count } 次
# 标题链接的提示。链接由 Zotero.launchURL 交给系统浏览器打开，所以这里说明白。
pharos-discovery-open = 在浏览器中打开来源页
pharos-discovery-pdf = 查看 PDF

pharos-discovery-trick-label = 核心思路
pharos-discovery-trick-pending = 尚未生成中文核心思路
pharos-discovery-trick-empty = AI 未返回中文核心思路
pharos-discovery-trick-extracted-tooltip = 摘要里的原句，未经模型阅读
pharos-discovery-abstract-label = 英文摘要

pharos-discovery-section-contribution = 贡献
pharos-discovery-section-core-trick = 核心思路
pharos-discovery-section-method = 方法
pharos-discovery-section-results = 结果
pharos-discovery-section-limitations = 局限
# pharosDiscoveryTest.js 按值相等断言这一条，改一个字测试就红。
# 里面的「精读」已经和按钮文案（生成核心思路）对不上了，但这是笔记正文而不是界面文案，
# 措辞漂移单独处理，不在这次改动里破坏一条钉死的断言。
pharos-discovery-rules-note = 该摘要由规则生成，未经模型阅读。可点「精读」获取模型解读。
# 精读后，后端会覆盖 summary_zh / 贡献 / 核心思路 / 方法 / 结果，但**故意保留**
# 规则版的「局限」（backend/pharos/services/projects.py:393-403）。所以那一行仍是
# 从英文摘要按线索匹配抄出来的原句，模型从未看过它。卡片此时挂着「AI 中文解读」
# 的标记，若不单独标注，读者会把它当成模型对论文弱点的判断。
pharos-discovery-limitations-rules = 规则摘录
pharos-discovery-limitations-rules-hint = 「精读」不覆盖这一项：以下句子由规则从英文摘要中摘出，模型未阅读或评估它。
# 写进 Zotero 笔记的溯源行。笔记会永久留在文库里，和用户自己的读书笔记
# 无法区分，而知道来源的那个窗口早就关了。
pharos-discovery-note-llm = 以上内容由模型 { $model } 依据标题与摘要生成，模型未阅读全文。
pharos-discovery-note-llm-unknown = 以上内容由模型依据标题与摘要生成，模型未阅读全文。
pharos-discovery-note-limitations = 其中「局限」一项为规则摘录，非模型判断。

pharos-discovery-mode-rules = 仅摘要规则
pharos-discovery-mode-llm = AI 中文解读
# analysis_warning 的本地化替身，直接渲染在卡片里。服务端那句英文原文
# 放在同一块的 title 上，保证线上返回值仍然可查，但不再是唯一的呈现面。
pharos-discovery-mode-rules-detail = 只从标题和摘要里抽取原句，没有调用模型，也没有下载或阅读全文。空字段表示摘要里没有明确写。
# 接在上一句之后。上一句说明了这份摘录是怎么来的，这一句说去哪里换成模型解读——
# 「精读」那个控件只存在于文献探索窗口，在这里说「点精读」就是指向一个不存在的按钮。
pharos-projects-source-rules-where = 可在「文献探索」中打开这篇论文，生成模型解读。
# $model (String) - 服务端记下的解读模型名
pharos-discovery-model = 解读模型：{ $model }
pharos-discovery-model-unknown = 解读模型未记录

pharos-discovery-analyze = 生成核心思路
pharos-discovery-analyzing = 生成中…
pharos-discovery-reanalyze = 重新生成

## 文献探索 · 保存到文库

# 桌面端独有：把这篇论文写成一条真实的 Zotero 条目，网页版没有对应能力。
# 用自己的 id 而不是继续借 pharos-daily-save*——跨模块借字符串正是这次重建要拆掉的耦合。
pharos-discovery-save = 保存到文库
pharos-discovery-saving = 保存中…
pharos-discovery-saved = 已在文库
# $error (String)
pharos-discovery-save-failed = 无法保存这篇论文：{ $error }

## 文献探索 · 生成失败

# 409 和 503 都必须写明规则提取的结果还在。直接抛服务端英文原文时，
# 用户看不出旧结果有没有被覆盖，那正是这两条要解决的问题。
pharos-discovery-analyze-no-provider = 服务端没有配置解读模型；规则提取的结果已经保留，没有被覆盖。配置模型后可以随时重新生成。
pharos-discovery-analyze-provider-failed = 中文核心思路这次没有生成成功；规则提取的结果未被覆盖，稍后可以重试。
pharos-discovery-analyze-no-abstract = 这条结果没有摘要，没有可读的内容。
# $error (String)
pharos-discovery-analyze-failed = 生成失败：{ $error }

## 文献探索 · 空状态

pharos-discovery-first-title = 从一个可以讨论的问题开始
pharos-discovery-first-desc = 每次检索都会记下它用了哪些来源、抽取了哪些字段，以及这些字段是模型读出来的还是规则抽出来的。没有配置模型时会明确回退为摘要规则提取，并说明这一点。
# 零结果时的标题
pharos-discovery-empty = 没有找到可用结果。
# search.status == 'error' 时代替上一条做标题
pharos-discovery-error = 搜索失败。
pharos-discovery-empty-hint = 看看上面的来源错误，或者复用条件后放宽关键词、增加来源。

## 文献探索 · 已停用

# 下面两条的唯一读者是重建前的 pharosDiscovery.js（第 116、250 行）：
# pharos-discovery-count 由 pharos-discovery-run-meta / -history-meta 接手，
# pharos-discovery-added-to-project 由批量加入的结果提示接手。
# 重建还没落地，此刻删掉会让这个窗口在 en-US 下当场抛错，所以先留着——
# 没人读的字符串是惰性的，而只删一个语言文件才是真的会出事。
# $count (Number) - 返回的结果数
pharos-discovery-count = { $count } 条结果
pharos-discovery-added-to-project = 已归入

## 分析来源

# 两个窗口共用：探索结果和项目文献都带着 analysis_mode，
# 不渲染它就等于让模型写的和规则抽的看起来一模一样。
pharos-analysis-mode-llm = AI 深读
pharos-analysis-mode-rules = 摘要提取

## 研究项目

pharos-projects-menu = 研究项目…
pharos-projects-window =
    .title = 研究项目

## 研究项目 · 阶段

# 九个阶段的名称与网页版逐条对齐：同一个项目在两个客户端上必须叫同一个名字。
# 注意 discovery 阶段叫「文献探索」，和探索模块本身重名（pharos-rail-discovery）。
# 网页版也有这个重名并接受了它，这里跟随以保持一致；时间轴上显示的是短标签「探索」，
# 只有阶段下拉和面板标题会出现完整的「文献探索」。
pharos-projects-stage-discovery = 文献探索
pharos-projects-stage-ideation = Idea 构思
pharos-projects-stage-planning = 实验规划
pharos-projects-stage-experimentation = 实验执行
pharos-projects-stage-analysis = 结果分析
pharos-projects-stage-claims = 主张整理
pharos-projects-stage-drafting = 论文草稿
pharos-projects-stage-review = 反方审阅
pharos-projects-stage-complete = 项目完成

# 时间轴节点上的短标签。一律用名词：动词（「运行」「分析」）挨着自动化说明，
# 会被读成一个可以点的按钮。
pharos-projects-stage-discovery-short = 探索
pharos-projects-stage-ideation-short = 构思
pharos-projects-stage-planning-short = 规划
pharos-projects-stage-experimentation-short = 实验
pharos-projects-stage-analysis-short = 分析
pharos-projects-stage-claims-short = 主张
pharos-projects-stage-drafting-short = 草稿
pharos-projects-stage-review-short = 审阅
pharos-projects-stage-complete-short = 完成

# 「研究路径」里每个阶段下面的一行说明
pharos-projects-stage-discovery-note = 建立问题边界和证据池
pharos-projects-stage-ideation-note = 形成候选假设与机制
pharos-projects-stage-planning-note = 冻结指标、基线和停止条件
pharos-projects-stage-experimentation-note = 记录真实运行与产物
pharos-projects-stage-analysis-note = 解释结果和替代原因
pharos-projects-stage-claims-note = 把结果约束成可追溯主张
pharos-projects-stage-drafting-note = 组织叙事、引用和图表
pharos-projects-stage-review-note = 找出证据缺口与过度声称
pharos-projects-stage-complete-note = 冻结当前研究版本

## 研究项目 · 记录类型与状态

# review 既是阶段也是类型。阶段是「反方审阅」，类型是「审阅记录」，
# 两者不能都渲染成光秃秃的「审阅」——记录卡片会把类型和阶段并排显示。
pharos-projects-type-hypothesis = 研究假设
pharos-projects-type-experiment-plan = 实验计划
pharos-projects-type-result = 实验结果
pharos-projects-type-claim = 论文主张
pharos-projects-type-draft = 写作草稿
pharos-projects-type-review = 审阅记录

# verified 是用户自己下的判断，不是系统验过。「已验证」丢掉了动作的主语，
# 读起来像平台替他核验过；「人工核验」把人放回句子里。见 docs/DECISIONS.md §9。
pharos-projects-status-draft = 草稿
pharos-projects-status-ready = 可使用
pharos-projects-status-verified = 人工核验
pharos-projects-status-rejected = 已否决

# 项目自身的状态。刻意不叫 pharos-projects-status-*：那个前缀是记录状态，
# 撞在一起会让按前缀拼 id 的查表函数解析到另一个命名空间里去。
pharos-projects-state-active = 进行中
pharos-projects-state-archived = 已归档

## 研究项目 · 项目列表

pharos-projects-list-head = 研究项目
pharos-projects-new = 新建项目
pharos-projects-show-archived = 显示已归档
# $sources (Number) - 该项目的文献数
# $records (Number) - 该项目的研究记录数
pharos-projects-item-meta = { $sources } 篇文献 · { $records } 条记录
pharos-projects-loading = 正在读取项目…
# 旧值「还没有项目。请先在 Pharos 网页端创建。」是 docs/DECISIONS.md §4 点名的失败：
# 桌面端自己就能建项目，把用户支去网页端是在说自己做不到。
pharos-projects-empty = 还没有研究项目
pharos-projects-none-matched = 没有符合筛选的项目
pharos-projects-error = 无法加载项目。
pharos-projects-retry = 重试

## 研究项目 · 空状态与加载失败

pharos-projects-welcome-title = 建立一个可持续推进的研究项目
pharos-projects-welcome-desc = 项目把探索结果、证据备注、实验计划、真实结果和论文主张放在同一条可追溯路径上。
pharos-projects-load-failed-title = 项目加载失败

## 研究项目 · 新建

pharos-projects-create-title = 新建研究项目
pharos-projects-name = 项目名称
pharos-projects-name-input =
    .placeholder = 项目名称
pharos-projects-question-input =
    .placeholder = 核心研究问题（可选）
pharos-projects-description-input =
    .placeholder = 项目说明（可选）
pharos-projects-create-submit = 创建项目
pharos-projects-creating = 创建中…
pharos-projects-cancel = 取消
# $name (String) - 新建的项目名
pharos-projects-created = 项目「{ $name }」已创建

## 研究项目 · 头部与生命周期

pharos-projects-question = 核心研究问题
pharos-projects-description = 项目说明
pharos-projects-edit = 编辑
pharos-projects-save = 保存项目
pharos-projects-saving = 保存中…
pharos-projects-updated = 项目已更新
pharos-projects-archive = 归档
pharos-projects-restore = 恢复
pharos-projects-delete = 删除
pharos-projects-delete-confirm = 删除整个项目？
pharos-projects-delete-submit = 确认删除
pharos-projects-deleted = 项目已删除
# $date (String) - 窗口预先格式化好的日期
pharos-projects-meta-created = 创建于 { $date }
# $date (String)
pharos-projects-meta-updated = 更新于 { $date }

## 研究项目 · 研究路径

pharos-projects-path = 研究路径
pharos-projects-stage-select =
    .aria-label = 修改项目阶段
pharos-projects-stage-save = 保存阶段
pharos-projects-advance = 进入下一阶段
pharos-projects-advancing = 推进中…
# $stage (String) - 推进到的阶段名
pharos-projects-advanced = 已推进到「{ $stage }」
# $count (Number) - 该阶段下的记录数
pharos-projects-stage-count = { $count } 条
pharos-projects-stage-count-none = 无记录
# 整个窗口里唯一可能被读成「运行这个阶段」的控件，所以把 §9 写在它旁边。
pharos-projects-stage-help = 点击阶段查看对应记录；阶段下拉可显式回退，所有调整只改变项目状态，不会触发自动实验。

## 研究项目 · 项目文献

pharos-projects-sources-head = 项目文献
# $count (Number) - 项目依据的论文数
pharos-projects-sources = { $count } 篇证据来源
pharos-projects-sources-empty-title = 还没有项目文献
pharos-projects-sources-empty-desc = 前往「文献探索」检索并选择论文加入这个项目。
pharos-projects-source-note = 证据备注
pharos-projects-source-note-empty = 添加纳入理由或证据关系
pharos-projects-source-note-input =
    .placeholder = 为什么把这篇论文纳入项目？它支持或反驳了什么？
pharos-projects-source-note-save = 保存备注
pharos-projects-source-note-saved = 证据备注已保存
# $date (String) - 窗口预先格式化好的日期
pharos-projects-source-added = 加入于 { $date }
pharos-projects-source-remove = 移除
pharos-projects-source-remove-confirm = 从项目移除？
pharos-projects-source-removed = 文献已从项目移除，探索历史仍保留
# 把某条记录写成一条真正的 Zotero 笔记。这是本模块存在的理由，网页版没有对应能力。
pharos-projects-save-note = 存为笔记

## 研究项目 · 研究记录

# $count (Number) - 项目已写下的记录数
pharos-projects-artifacts = { $count } 条研究记录
pharos-projects-artifact-new = 新建记录
pharos-projects-artifact-new-title = 新建研究记录
pharos-projects-artifact-edit-title = 编辑研究记录
pharos-projects-artifact-stage = 阶段
pharos-projects-artifact-type = 类型
pharos-projects-artifact-status = 状态
pharos-projects-artifact-title = 标题
pharos-projects-artifact-title-input =
    .placeholder = 一句话说明这条记录
pharos-projects-artifact-body = 正文
pharos-projects-artifact-body-input =
    .placeholder = 记录假设、实验约束、真实结果、主张或审阅意见。不要把计划写成已经执行。
pharos-projects-artifact-save = 保存记录
pharos-projects-artifact-saved = 研究记录已保存
# status == 'verified' 时代替上一条：在下判断的那一刻再说一次，判断是用户做的。
pharos-projects-artifact-saved-verified = 记录已保存并标记为人工核验
pharos-projects-artifact-delete = 删除
pharos-projects-artifact-delete-confirm = 确认删除？
pharos-projects-artifact-deleted = 研究记录已删除
# $date (String)
pharos-projects-artifact-updated = 更新于 { $date }
pharos-projects-artifacts-empty-title = 这个阶段还没有记录
pharos-projects-artifacts-empty-desc = 新建一条真实的科研记录；系统不会替你声称实验已经执行。

## 研究项目 · 已停用

# pharos-projects-none 的唯一读者是重建前的 pharosProjects.js（第 173 行）。
# 每个空状态现在都有自己的文案，这一条不该再被复用：正是它让「阶段不会触发自动实验」
# 那块说明看起来像「这里什么都没有」。重建落地前先留着，删掉会让窗口在 en-US 下抛错。
pharos-projects-none = 暂无内容。

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
pharos-rail-resize =
    .aria-label = 调整模块栏宽度

## 管理员后台

pharos-admin-menu = 管理员后台…
pharos-admin-window =
    .title = 管理员后台

pharos-rail-admin = 管理员后台
pharos-rail-admin-tooltip =
    .title = 管理员后台

pharos-admin-tab-users = 账号管理
pharos-admin-tab-providers = API 配置
pharos-admin-search =
    .placeholder = 搜索邮箱或名称…
    .aria-label = 搜索用户

pharos-admin-loading = 加载中…
pharos-admin-error = 无法加载管理员后台。
pharos-admin-forbidden = 当前账号不是管理员。
# 服务器未返回该项时的占位符
pharos-admin-none = —

pharos-admin-stat-users = 账号
pharos-admin-stat-admins = 管理员
pharos-admin-stat-inactive = 已停用
pharos-admin-registration-open = 注册开放中
pharos-admin-registration-closed = 注册已关闭
pharos-admin-registration-hint = · 由服务器的 .env 控制

pharos-admin-column-user = 账号
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
pharos-admin-delete-body = 将永久删除 { $email } 的 Pharos 服务器端账号及账号数据。
pharos-admin-delete-local = 不会读取或删除该用户设备上的本地 Zotero 或 Pharos 文库。
pharos-admin-delete-irreversible = 服务器端账号删除后无法撤销。
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
# 模块总开关。每日文摘的「已关闭」空状态会把用户送到这个面板并让他在这里重新打开——
# 没有这个控件，那句指引就是错的，而那个状态在桌面端无法恢复。
#
# 不叫 -enabled：那个 id 已经存在，是单个研究方向的状态标签（「启用中」）。
pharos-prefs-daily-module-on = 启用每日论文
pharos-prefs-daily-module-on-help = 关闭后不再抓取，也不再解读。已有的文摘和已导入的论文都保留。
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

# 位于 spacer 与账号按钮之间的更新横幅。只有检测到用户尚未忽略的新版本时才会
# 出现，所以这些文案每个版本用户最多看到一次。
pharos-rail-update-title = 新版本可用：{ $version }
pharos-rail-update-download = 立即更新
pharos-rail-update-ignore = 忽略
pharos-rail-update-tooltip =
    .title = Pharos { $version } 已发布——点击前往下载

## 条目面板的翻译栏。
##
## 前两个是 attribute-only（.label / .tooltiptext），Zotero 从 data-pane 拼出这两个
## id；其余全部是 value message——栏内的代码用 formatValueSync 读它们，而那个函数
## 对只有属性的消息返回 null。

section-pharos-translate =
    .label = 翻译
sidenav-pharos-translate =
    .tooltiptext = 翻译

# 「本地无译文」而不是「未译」。这一栏只看得见本机文库，网页版或另一台机器上翻译过
# 的论文它无从知晓，说「未译」就是拿一个文库的证据去断言整个账号的状态。
# 条目树里那一列的表头。列宽有限，所以只在真的发生过什么时才有值——
# 见 Translate.stateLabel()。
pharos-translate-column-state = 翻译
pharos-translate-state-unknown = 本地无译文
pharos-translate-state-unknown-detail = 这里只反映本机文库。在网页版或其他设备上翻译过的论文，这里看不到。
pharos-translate-state-is-translation = 本篇是译文
pharos-translate-state-translating = 翻译中
pharos-translate-state-translating-percent = 翻译中 · { $percent }%
pharos-translate-state-translated = 已译
pharos-translate-state-failed = 失败

# 引擎自己的阶段标签是自由文本，有些很长且几分钟不变，读起来像卡死。这三步是网页版
# 的同一套映射，逐字对应——同一个任务不能在两个界面里被描述成处在不同阶段。
pharos-translate-stage-parse = 解析版面
pharos-translate-stage-translate = 翻译正文
pharos-translate-stage-typeset = 重排版面
pharos-translate-stage-tooltip = 引擎阶段：{ $stage }

pharos-translate-action-open = 打开译文
pharos-translate-action-open-named = 打开{ $name }
pharos-translate-action-open-original = 查看原文
pharos-translate-action-retry = 重试
# 进度对话框是唯一能取消运行中任务的地方，所以这不只是「看进度」。
pharos-translate-action-queue = 查看进度
