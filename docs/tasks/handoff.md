# 交接(2026-09-26)

> 交给 Codex 时用 `docs/tasks/codex-prompt.md` 作为启动 prompt(Codex 读不到 Claude 的技能)。

**锚点**:基于 master `ba8bf96`,加上本文件所在的 PR #214(评估文档 + 本交接)。

## 目标
按外部架构评审修"把各步骤串起来的那一层"。**停止加新功能。** 一次只做一件,每件是一个 issue + 一个小 PR,
做完用 `manager-harness` 四段格式汇报(能做什么 / 怎么验 / 还缺什么 / 唯一下一步),**等 Boss 批准再开下一件**。

## 必读(按顺序)
1. `CLAUDE.md`(注意:评审发现它描述的架构一半不存在,以代码为准)
2. `docs/review/2026-09-26-evaluation.md` — 评估 + 评审全文,三件事的定义都在这里
3. `docs/MAINLINE.md` 顶部两节
4. `.claude/skills/newsday-evidence/SKILL.md` — 先量信号、"测试全绿"≠"生效了"

## 这段对话里得出、文档之外的增量
- [已验证事实] 最近 12 晚只有 3 晚出刊;瓶颈是审阅,不是解读数量。**不要再优化解读产量。**
- [已验证事实] agnes 429 原文是免费用户限流,无限额响应头,实测约每分钟 1 次成功;ModelScope 429 是 `insufficient balance`。
- [Boss 决定] 只用免费额度;agnes 只用 `agnes-2.5-flash`;一期一模型、agnes 优先。
- [Boss 决定] 砍来源名单已生效(#204);保留"只发点了保留的条目"这道确认门。
- [未决] "没审就发缩减版"是产品取舍,Boss 未定,不要自行实现。

## 已放弃
- agnes 多模型轮换(Boss:只有 2.5-flash 能用)。修故事线合并(改用公司封顶 #200;评审建议直接删 storylink)。

## 禁止 / 必须停下问 Boss
- 不碰主仓库脏工作区 `feat/draft-preview-publish`(不提交、不清理、不 reset);在 worktree 里干活。**2026-09-27 起这块脏工作区已移进 `stash@{0}`,见文末「主仓库脏工作区 → stash」**。
- 凭证只从 `~/.zshrc` 读,绝不打印;绝不让 Boss 在对话里贴 key。
- 探测真实 LLM 会消耗免费额度,能不探就不探。
- 删除代码(评审列的 ~1,200–1,500 行)属于第 3 件,先列清单给 Boss 批。

## 唯一下一步
**第 1 件**:三个工作流(collect/finalize/publish)加同一个 `concurrency` 组;运行结束健康检查
(解读成功率过低、决策拉取失败、0 条可发 → Telegram 告警 + 任务失败退出);修好或删掉 `publish.yml:36`
那条用不存在参数 `--publish-only` 并吞报错的命令(先问 Boss 该修还是该删)。

## 返回格式
中文;第一行就是可执行动作;一次只问一个决策点;每个决策附上足够判断的数据。

## 主仓库脏工作区 → stash(2026-09-28 增补,project-manager 会话)
- [已验证事实] 2026-09-27 经 Boss 批准,主仓库 `~/workspace/ai-newsday` 从 `feat/draft-preview-publish`(`2ba48ff`,已 squash 进 master #5)切到 master,方便看到最新进度。**当时没先读本交接,违反了上面「不碰主仓库脏工作区」**;脏工作区没丢,完整在 `stash@{0}`(`137a8a5`,消息 `pre-master 2026-09-27`)。
- 原样恢复(回到 09-27 之前的状态):`git -C ~/workspace/ai-newsday switch feat/draft-preview-publish && git -C ~/workspace/ai-newsday stash pop`

| 内容 | stash 里的状态 | 和 master 比 | [Agent 提案] |
|---|---|---|---|
| `docs/superpowers/plans/2026-08-28-{content-certainty-and-title-hooks,entity-factcheck,story-merge}.md` | 未跟踪 | **与 master 完全相同** | 丢 |
| `data/state.db` | 被删(原 53 KB) | master 已 `.gitignore`(`data/*`) | 丢 |
| `uv.lock` | 改动(+57 行) | 与 master 不同 | 丢(以 master 为准,需要时 `uv lock` 重生) |
| `docs/PROGRESS.md` | 未跟踪 | master 没有 | Boss 看一眼再定 |
| `docs/daily/2026-05-30.md`、`06-18.md`、`06-20.md` | 未跟踪 | master 没有 | Boss 看一眼再定 |
| `content/posts/2026-05-30.md` | 未跟踪 | master 有同名文件,**内容不同** | Boss 看一眼再定(已发布的以 master 为准) |
| `x-extension/`(含 `node_modules`) | **不在 stash 里**,仍在主目录未跟踪 | 是 `ai-newsday/x-extension` 仓库的副本 | 不动 |

- 看内容:`git -C ~/workspace/ai-newsday stash show -p --include-untracked stash@{0}`;取单个文件:`git -C ~/workspace/ai-newsday show 'stash@{0}^3:docs/PROGRESS.md'`
- **必须停下问 Boss**:`git stash drop` / `git stash clear`。Boss 定完上表「看一眼」三行后再 drop。
