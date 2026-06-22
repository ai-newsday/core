# ADR 0004 — 把 data/state.db 移出 git，用 Actions cache 跨 run 持久化

- 日期:2026-06-23
- 状态:已采纳
- 关联:issue #25、[[project-status-2026-06]]

## Context

`data/state.db`(SQLite,二进制)被三个 cron 工作流当作可变状态 **force-commit 回 master**:`collect`(3×/天)、`finalize`(1×/天)、`publish`(手动)。`.gitignore` 里有 `!data/state.db` 白名单专门放行。

M1 让 `finalize` 成为 `state.db` 的**第二个写者**(原本只有 collect)。多个工作流提交并 push 同一个二进制文件会撞车:非快进 push 失败,而 git 无法对 `.db` 做三方合并,并发 run 会冲突或静默丢状态。当前把 `finalize` 挪到 01:00 UTC(避开 00:00 的 collect)只是止血,根因是"可变二进制状态进 git + 多写者"。

`state.db` 现状:156KB,5 张表——`runs`(历史/可观测)、`kv_state`(跨 run 滚动状态)、`feedback_events` + `quality_weights`(M1 反馈学到的编辑信号)、`pending_reviews`。真正怕丢的是 `quality_weights`(学习信号),但可重新积累;无 `items`/`sources` 表(在别处)。

## Decision

**停止把 `data/` 提交进 git;用 GitHub Actions cache 在 cron 之间滚动持久化 `state.db`。**

- 删掉 `.gitignore` 的 `!data/state.db` 白名单;`git rm --cached data/state.db`。保留 `data/.gitkeep` 让目录存在(`db.init()` 幂等建表,缺文件时 aiosqlite 自建)。
- 每个 tick 工作流在跑 tick 前加一步 `actions/cache`:
  ```yaml
  - uses: actions/cache@v4
    with:
      path: data/state.db
      key: state-db-${{ github.run_id }}
      restore-keys: state-db-
  ```
  `key` 含 `run_id` → 每次都是 miss → post-job 必存;`restore-keys: state-db-` 按前缀取**最近一次**已存缓存。这就是标准 rolling-cache 模式。
- 各工作流的 git 步骤里去掉 `data/`。`content/`(已发布产物,可见)仍留在 git,照常 commit+push。
- **写者**:三个 tick 都会改 state(collect 写 runs+kv,finalize 写 runs,反馈更新 quality_weights),都各自 restore+save。不再有单一写者,但 cache 取代 git push 消除了撞车。

## Ceiling & 升级路径

- **cache 驱逐**:GitHub 缓存 7 天不命中会被驱逐,repo cache 上限 10GB。156KB 可忽略容量;cron 每天跑保持常温,实际只有 >7 天停摆才丢。丢了 `quality_weights` 会重置学习,但可重新积累——MVP 可接受。
- **并发 save 竞争**:两个 run 重叠时会从同一基线存出分叉缓存,后存覆盖先存。当前 tick 时间错开(collect 00/05/12、finalize 01 UTC、publish 手动),低风险。
- **升级路径**:若学习信号需要强持久,改用 release asset(固定 tag 滚动 `gh release upload/download`)或外部存储,持久不被驱逐。届时另开 ADR。

## Alternatives considered

- **Release asset / 外部存储**:持久不被驱逐,但 YAML 更多、多一层 token/错误处理。对 156KB 的 MVP 过重,留作升级路径。
- **保留单一写者 + 继续 commit 进 git**:不解决二进制进 git 的根病(无法三方合并、binary diff 噪音),只是把多写者收敛成单写者。否决。
- **Artifact**:90 天保留但不易"取最新",跨 run restore 不如 cache 顺手。否决。
