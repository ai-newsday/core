# Spec — 去重聚类层 (Dedup / Clustering)

> 放置路径：`docs/specs/dedup.md`。这是七层流水线的第 2 层，MVP 第二个要实现的模块。
> 对应 PRD §3.2（NewsItem 增量）、§3.3（流程）、§4.1（去重聚类）、§2.1（验收 #3 去重覆盖率 100%）。
> 上游：第 1 层采集 (`docs/specs/collection.md`) 产出的 `list[RawItem]`。下游：第 3 层打分（消费本层的去重主条目）。

## 1. 目的

把上游"未去重"的 `RawItem` 列表，按**事件**聚类：同一事件的多源报道合并为一个 `cluster`，每个 cluster 内保留**信息最全 / 最一手**的一条为**主条目（primary）**，其余源的链接折叠进 `related_links`。

直接服务的痛点：同一事件多源重复刷屏 → 日报冗余、稀释信号。本层成败标准是 **PRD #3：去重覆盖率 100%**（golden 断言：无重复事件入选）。

## 2. 范围 / 非目标

- **做**：取 embedding、按相似度阈值贪心聚类、选主条目、折叠 related_links、产出 `DedupResult`、写 `runs` 事件、支持 `--dry-run`。
- **不做**：
  - 打分 / 类型配额 / explore 配额（第 3 层）。
  - 翻译 / 摘要 / 解读 / 锐评 / 证据链（第 4 层）。
  - **真正写入 Qdrant 持久化**：本圈只定义 `VectorStore` 适配器接口 + 内存实现；真实 Qdrant 归档（沉淀层）延后到后续圈（与第 1 圈延后 SQLite 同理）。
  - 读者相关度 / 画像向量（反馈闭环圈）。

## 3. 接口契约

```python
def dedup(items: list[RawItem], config: DedupConfig, ctx: RunContext) -> DedupResult: ...
```

- **输入**：
  - `items: list[RawItem]` —— 上游采集产物（去噪前、未去重）。
  - `config: DedupConfig`（见 §6，全部阈值/权重读 `config/dedup.yaml`，不写死）。
  - `ctx: RunContext` —— 复用第 1 层的 `run_id` / `now`（注入，确定性）/ `logger`。
- **依赖（构造时注入，便于测试）**：
  - `embedder: EmbeddingProvider` —— 取 embedding 的适配器（真实=ModelScope；测试=Fake）。
  - `store: VectorStore` —— 向量库适配器（真实=Qdrant；本圈=内存/no-op）。
- **输出 `DedupResult`**：见 §4。

> **IO 隔离（CLAUDE.md 架构约束）**：网络（embedding / 向量库）只在适配器里；`cluster()` 是**纯函数**（输入 items + 向量 → clusters），不碰网络，是本层的可测核心。

## 4. 数据契约

```python
class NewsItem(RawItem):          # RawItem 的下游演进；本圈只加去重相关字段
    cluster_id: str               # 所属事件聚类 ID, 形如 evt-2026-05-30-001
    related_links: list[str] = [] # 同事件其它源的链接(不含主条目自身)
    embedding_id: str | None = None  # 向量引用(后续映射 Qdrant point id)
    # 注: score/score_breakdown/takeaway/hot_take/evidence 等由后续圈逐步追加

class Cluster:
    cluster_id: str
    primary: NewsItem             # 主条目(信息最全/最一手)
    members: list[RawItem]        # 该 cluster 的全部成员(含主条目对应的原始 RawItem)
    related_links: list[str]      # = [m.link for m in members if m is not primary]
    size: int                     # == len(members)

class DedupResult:
    clusters: list[Cluster]       # 全部事件聚类(保留完整成员, 供未来 Qdrant 归档)
    deduped_items: list[NewsItem] # 下游消费: 每个 cluster 的 primary, 已带 cluster_id+related_links
    input_count: int              # 入参 items 数
    cluster_count: int            # == len(clusters)
    duplicate_count: int          # input_count - cluster_count (被合并掉的重复条目数)
```

## 5. 算法（确定性）

1. **构造 embed 文本**：`embed_text = title_en + "\n" + (raw_summary or "")`；无 summary 时退化为仅 `title_en`。
2. **取向量**：经 `embedder` 批量 embedding（`config.batch_size`），为每条赋 `embedding_id`（稳定值，如 `sha256(link)[:16]`）。
3. **排序**：先按**主条目优先级**升序排列 items —— `source_type_rank` → `source.priority`（小=高）→ `published_at`（早=先报道）。
4. **贪心聚类**：按上序遍历；当前条目与各已存在 cluster 的**种子（seed）向量**取 cosine 相似度，若 `max_sim > config.similarity_threshold` 则并入该 cluster，否则新建 cluster 并以自己为种子。
   - 因第 3 步排序，**种子恒为该 cluster 的主条目**（最优先者最先成簇）——天然确定性，无需二次选主。
5. **产出**：每个 cluster 的 `related_links = 非主成员的 link`；为 primary 构造 `NewsItem`（带 `cluster_id`、`related_links`、`embedding_id`）。`cluster_id = f"evt-{ctx.now:%Y-%m-%d}-{NNN}"`，`NNN` 按成簇顺序三位补零。
6. cosine 相似度用 `numpy`（小而标准的依赖）。

## 6. 配置：`config/dedup.yaml`

```yaml
similarity_threshold: 0.83        # cosine 阈值; 调高=更少合并(更保守), 调低=更多合并
embedding_model: "Qwen/Qwen3-Embedding-8B"
batch_size: 32
# 主条目优先级: 越靠前=越一手/权威; dedup 选主与排序据此
source_type_rank: [official, paper, model, tool, news, community, blog]
```

## 7. 错误与回退（非致命，继承 CLAUDE.md/PRD §3.4）

| 情况 | 处理 |
| --- | --- |
| 入参 `items == []`（上游 `is_silent`） | 返回空 `DedupResult`（clusters/deduped_items 皆空），不抛异常 |
| **embedding 整体失败**（provider 不可用） | **降级**：每条各自成 singleton cluster（不去重），emit 告警；链路继续，不崩 |
| 单条 embedding 失败 | 该条作为 singleton（不参与相似度比较），记录告警 |
| `--dry-run` | `cluster()` 在内存完成；`store` 写入为 no-op；产出 `DedupResult` JSON |

## 8. 不变量（golden 测试必须断言）

1. **去重覆盖率 100%**：跨源同一事件被合并到**同一个** cluster；`deduped_items` 中**不存在两条代表同一事件**（PRD #3）。
2. 每个输入条目恰好属于**一个** cluster：`sum(c.size for c in clusters) == input_count`。
3. 每个 cluster 的 `primary` 满足主条目优先级（`source_type_rank` → priority → 最早），且 `related_links` 恰为其余成员的 link、不含 primary 自身。
4. `deduped_items` 长度 `== cluster_count`；`duplicate_count == input_count - cluster_count >= 0`。
5. 每个 `NewsItem` 继承 RawItem 的全部不变量（标题/链接/源/类型/时区非空），并新增非空 `cluster_id`。
6. embedding 整体失败时：`cluster_count == input_count`（全 singleton），不抛异常。
7. 确定性：同一输入 + 同一注入向量 ⇒ 同一 clusters / cluster_id 序列。

## 9. golden 用例（fixtures 驱动，≥4）

> 测试用 `FakeEmbeddingProvider` 注入**冻结向量**（每条 fixture 指定其向量），使聚类结果确定、可断言，且**不依赖网络**。

1. **跨源重复合并**：3 条来自不同源、向量近似（>阈值）的"同一事件" → 合并为 1 个 cluster；primary 为最一手源；其余 2 条 link 进 `related_links`；`duplicate_count == 2`。
2. **无重复**：N 条互不相似条目 → N 个 singleton cluster；`duplicate_count == 0`。
3. **选主规则**：同一 cluster 内含 `official` 与 `blog`，official 必为 primary；若同 type 则 priority 小者；再同则 `published_at` 早者。
4. **边界阈值**：相似度恰在阈值附近的两条（一组 > 阈值、一组 < 阈值），断言合并/不合并行为正确。
5. **空输入**：`items == []` → 空 `DedupResult`，不抛异常。
6. **降级**：`embedder` 抛错 → 全 singleton（`cluster_count == input_count`），emit 告警，不崩。

## 10. 测试要求

- **contract**：`EmbeddingProvider` / `VectorStore` 适配器在 mock 下行为合法；`NewsItem` schema 校验。
- **golden**：用冻结向量驱动 §9 的 6 个用例，断言 §8 不变量。
- 时间相关一律用注入的 `ctx.now`（`cluster_id` 日期据此），**不依赖真实当前时间**。
- 纯函数 `cluster()` 全程离线可测，无网络。

## 11. 可观察

- 每成簇 emit `dedup_cluster_created{cluster_id, size}`。
- 降级 emit `dedup_embedding_degraded{reason}`。
- `dedup()` 结束 emit `dedup_done{input_count, cluster_count, duplicate_count, silent}`，写入 `runs`。

## 12. 验收（对齐 PRD §2.1）

- **#3 去重正确**：同事件多源合并；**去重覆盖率 100%**（golden 断言，无重复事件入选）。
- **#1 端到端**：`collect() → dedup()` 可串联，`--dry-run` 产出 `DedupResult` JSON，无人工干预。
- **#8 静默正确**：上游静默时本层返回空结果，不产空数据、不抛异常。
