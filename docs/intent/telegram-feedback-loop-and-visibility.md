# Intent — 打通 Telegram 人审闭环 + 让输出可见

- 日期:2026-06-20
- 来源:interview-me 会话(用户确认)
- 状态:意图已确认;下一步 `/brainstorm` → spec → plan → PR

## 背景 / 触发

用户当务之急不是"加更多源",而是**停止"在跑却没可见产出"的拖延态**。诊断发现产品其实已经在 Telegram + Hugo/Pages 上跑,但人审闭环是断的:

- **根因:轮询写死在 collect tick 里、同步阻塞 120s**(`run_collect_tick` → `poll_decisions_loop(timeout=120)`)。人工审阅本质异步:cron 自动跑那 120s 用户不在场(日志 `0/6 decided, 120s elapsed`);用户后来点按钮时**没有在线消费者** `answer()` 回调 → Telegram 永远 "Loading…"、按钮无反馈、信号不落库。
- **finalize 是 `workflow_dispatch`,以前从没触发过** → 终稿(`content/posts/<date>.md`)从未产出 → `pages.yml`(content 变更触发,带 `-D` 发草稿)没新内容可建 → 站旧/空。
- **发送 bug(并行):** `send_final_report` 里 `body = markdown[:3800]` 直接截断(="不全");review 卡片 `body` 不截断,超 Telegram 4096 上限报 "message too long"(="出错");HTML `parse_mode` 转义不全会 400。

## 产品最终形态(已确认)

- **(b) 对外产品**(奔读者去:RSS/公众号/网站),**但现阶段第一用户是用户自己**——先做到"能看、能审、能定稿"。
- Reddit 生产被 IP 封死(403 Blocked,见 KANBAN §2)记下,**不停工**。

## 要解决的(按优先级,先于 GitHub 源)

1. **Telegram 点击 → 决策反馈闭环** — 硬指标两条:①点按钮 **Telegram 上立刻可见反馈**(`answerCallbackQuery` toast + `editMessageText`);②决策信号**确实被采集落库**供 finalize 用。
2. **finalize 真能跑出终稿 + 站可见** — finalize 读累积决策 → review→publish → push `content/` → Pages 自动重建 → 打开 URL 能看到当天日报。
3. **日报排版 + 内容质量整顿(高优先,与"可见"一体)** — 草稿的排版和内容质量必须≈正式版,专业、易读(用户看 commit `1b9b38cc` 判定不合格)。缺陷:emoji 乱放、必读在速览重复、垃圾空条目漏入("The AirPods Effect"="原始信息缺失")、摘要截成病句、低信号当必读(个人 GGUF/firehose 噪声)、"锐评"太油。横跨 publish 渲染 / selfcheck / score / prompts 四层,需单独 brainstorm。
4. **Telegram 发送出错/截断** — 终稿 `[:3800]` 截断 + 卡片超长 4096 + HTML 转义。

## 机制(已确认):Telegram webhook + Cloudflare Worker + KV

- 选 **webhook** 而非轮询:唯一能做到秒级可见反馈;注意 **webhook 与 `getUpdates` 互斥**,设了 webhook 后 finalize 不能再用 `getUpdates`,决策必须走 webhook 的存储。
- 落地 **(a) Cloudflare Worker + KV**:Worker 免费常驻 HTTPS;收到 callback → `answerCallbackQuery`+`editMessageText`(秒回)→ 写 Cloudflare KV;finalize(Actions)跑前用 HTTP 把 KV 决策拉回 `state.db`。零常驻服务器、免费,契合 CLAUDE.md"未来可迁 serverless"。

## Success(验收)

- 点按钮 < 2s 内 Telegram 消息有可见变化;决策可在存储中查到。
- 触发 finalize → 产出 `content/posts/<date>.md` → Pages 部署 → URL 能打开看到当天日报。
- 终稿 Telegram 推送不截断关键内容、不报 4096/parse 错误。

## 约束

- 外科手术式修现有断链,不新建大东西;对外副作用 `--dry-run`;一次一个子项目、小 PR、issue-per-PR、从真实 `origin/master` 起分支;新逻辑走 TDD。

## Out of scope(暂缓)

GitHub 源(子项目 2,排在本闭环之后)、救 Reddit、博客 validation、跨轮看板。
