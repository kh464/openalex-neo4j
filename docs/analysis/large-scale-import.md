# 大规模导入问题分析与优化方案

## 问题背景

当前项目设计面向百级到千级的数据量（WoS 1000 条、API 搜索默认 limit=100）。当关键词匹配量级达到**十万级**（如 "machine learning" 匹配 393 万条，取前 10 万条），现有架构会暴露出多个瓶颈。

本文从 API 调用、内存、磁盘、数据库、时间五个维度逐一分析问题并提出优化方案。

---

## 1. API 调用量

### 1.1 问题分析

| 阶段 | 调用类型 | 10 万条估算 | 耗时估算（polite pool 10/s） |
|---|---|---|---|
| 初始搜索 | `search_works` 分页 | 500 次（per_page=200） | 50s |
| 作者展开 | `fetch_authors_by_ids` | 2000 次（50 IDs/批） | 200s |
| 机构展开 | `fetch_institutions_by_ids` | 1000 次 | 100s |
| 来源展开 | `fetch_sources_by_ids` | 200 次 | 20s |
| 引用展开 | `fetch_works_by_ids` | 20000 次（50 IDs/批） | 2000s ≈ 33min |
| 主题/出版商/资助方 | 少量 | ~50 次 | 5s |
| **合计** | | **~23750 次 API 调用** | **~40 分钟** |

关键瓶颈：

- **引用展开是最大开销** — 10 万篇 × 平均 40 篇参考文献 = 400 万 ID。即使每批查 50 个也需要 8 万次调用，约 2.2 小时
- **polite pool 限频** — OpenAlex 对 polite pool（配置 email）限制约 10 req/s，这是硬性上限

### 1.2 优化方案

| 方案 | 效果 | 实现复杂度 |
|---|---|---|
| **A: 大导入自动限制 expand_depth=1** | 跳过引用展开，节省 80%+ 调用 | 低 |
| **B: 批量 DOI filter** | 合并多个 DOI 为一次查询 | 低（仅适用于 WoS 路径） |
| **C: 跳过关系展开（仅入库）** | 零额外调用 | 低 |
| **D: 延迟展开** | 先入库后再后台展开 | 中 |
| **E: 并行请求** | 提效 3-5x | 中（需处理限频） |

**推荐组合**：A + C。当 limit > 5000 时，自动限制 expand_depth=1 并跳过引用展开，只抓取直接作者/来源/主题。

---

## 2. 内存占用

### 2.1 问题分析

当前架构在导入阶段调用 `DataSerializer.read_all()` 一次性加载全部实体到内存：

| 类型 | 10 万篇估算 | 单条大小 | 总内存 |
|---|---|---|---|
| Work | 100,000 | ~2 KB | 200 MB |
| Author | 500,000 | ~0.5 KB | 250 MB |
| Institution | 200,000 | ~0.5 KB | 100 MB |
| Source | 50,000 | ~0.5 KB | 25 MB |
| Topic | 20,000 | ~0.5 KB | 10 MB |
| Publisher | 5,000 | ~0.3 KB | 1.5 MB |
| Funder | 5,000 | ~0.3 KB | 1.5 MB |
| **合计** | | | **~588 MB** |

加上 Python 解释器开销和关系构建时的临时对象，**总内存需求约 1-2 GB**。如果启用了 embedding（384 维 float 向量），每个 Work 额外增加 ~1.5 KB，总内存再增加 150 MB。

### 2.2 优化方案

| 方案 | 效果 | 实现复杂度 |
|---|---|---|
| **A: 按类型分批读取** | 峰值内存降至 200 MB | 低 |
| **B: 按 ID 范围分批写库** | 可控制在 50 MB/批 | 中 |
| **C: 流式读取 JSONL** | 固定内存 ~10 MB | 高（需改 DataSerializer） |
| **D: 不加载到内存，直接遍历文件** | 固定内存 | 高 |

**推荐方案 A 的实现思路**：

```python
def _import_nodes_batched(self, entities: dict) -> dict:
    """按类型分批创建节点，避免同时加载所有类型到内存。"""
    counts = {}
    for label, nodes in entities.items():
        if not nodes:
            continue
        # 每批 1000 条写入 Neo4j，写完后释放该批次内存
        for i in range(0, len(nodes), 1000):
            batch = nodes[i:i + 1000]
            # 清理 current_session 等辅助字段
            ...
        # 当前类型写入完成，该类型内存可 GC
    return counts
```

但这仍有问题：`read_all()` 本身已经把所有类型加载到内存。真正的优化是**按类型读取，按类型写库，逐类型释放**：

```python
def _import_nodes_streaming(self) -> dict:
    """从 JSONL 逐类型读取 → 写库 → 释放。"""
    counts = {}
    for label in DataSerializer.LABELS:
        nodes = self.serializer.read(label)  # 只读当前类型
        if not nodes:
            continue
        # 写入 Neo4j
        counts[label.lower()] = self.neo4j.batch_create_nodes(...)
        # nodes 在此轮结束被 GC 回收
    return counts
```

---

## 3. JSONL 缓存磁盘占用

### 3.1 问题分析

| 文件 | 10 万篇估算大小 |
|---|---|
| `work.jsonl` | ~200 MB |
| `author.jsonl` | ~250 MB |
| `institution.jsonl` | ~100 MB |
| `source.jsonl` | ~25 MB |
| `topic.jsonl` | ~10 MB |
| **合计** | **~585 MB** |

磁盘通常不是瓶颈（NVMe 读写 2-3 GB/s），但以下情况需要注意：

- 临时目录所在分区空间不足
- 写入大量小文件时的 inode 耗尽
- JSONL 重写操作（skip_abstracts、generate_embeddings）需要完整读写文件

### 3.2 优化方案

| 方案 | 效果 | 复杂度 |
|---|---|---|
| **A: 设置磁盘空间预警** | 防止磁盘满 | 低 |
| **B: 文件级压缩（gzip）** | 减少 70-80% 空间 | 低 |
| **C: 跳过不必要的字段** | 减少 30-50% 体积 | 中 |

方案 B 改动最小：在 `DataSerializer` 中使用 `gzip.open` 替代 `open`，读写逻辑不变，文件名改为 `.jsonl.gz`。

```python
import gzip

def append(self, label, node_dict):
    file_path = self.data_dir / f"{label.lower()}.jsonl.gz"
    with gzip.open(file_path, "at", encoding="utf-8") as f:
        f.write(json.dumps(node_dict, ensure_ascii=False) + "\n")
```

---

## 4. Neo4j 写入性能

### 4.1 问题分析

当前每批 500 条，使用 UNWIND + MERGE：

| 操作 | 10 万篇估算 | 事务数 | 估算时间 |
|---|---|---|---|
| 创建节点 | 880,000 节点 | 1760 批 | ~30s（本地） |
| AUTHORED 关系 | 500,000 条 | 1000 批 | ~15s |
| CITES 关系 | 2,000,000 条 | 4000 批 | ~60s |
| 其他关系 | 300,000 条 | 600 批 | ~10s |
| **合计** | | | **~2min** |

Neo4j 本地写入性能通常不是瓶颈（1000+ 节点/批/秒），但以下问题需要考虑：

- MERGE 在 10 万级节点上需要唯一约束索引支持，否则性能骤降
- 大事务可能导致 Neo4j 内存溢出（heap 配置不足时）
- `ON MATCH` 中的 `CASE` 判断和列表操作在批量导入时有额外开销

### 4.2 优化方案

| 方案 | 效果 | 复杂度 |
|---|---|---|
| **A: 使用 PERIODIC COMMIT** | 控制事务大小 | 低（需改用 LOAD CSV） |
| **B: CALL { ... } IN TRANSACTIONS** | Neo4j 5.x 原生分批 | 低 |
| **C: 跳过非必要的 ON MATCH 逻辑** | 纯 CREATE 快 2-3x | 中 |
| **D: 预建索引再导入** | 避免 MERGE 扫描 | 已在做 |

当前已经使用 `CREATE CONSTRAINT ... IF NOT EXISTS` 预建索引，正确。主要优化点是控制单次事务大小——500 条一批是合理的，不需要调整。

---

## 5. 时间总览

### 5.1 10 万篇基准估算

| 阶段 | 当前方案 | 优化后 |
|---|---|---|
| API 搜索 | 50s | 50s |
| 关系展开（depth=1，跳过引用） | ~5min | 5min |
| JSONL 写入 | ~10s | 10s |
| skip_abstracts / embeddings | 5-30min（embedding 是瓶颈） | 5-30min |
| Neo4j 写库 | ~2min | 2min |
| **总时间（不含 embedding）** | **~8min** | **~8min** |
| **总时间（含 embedding）** | **~38min** | **~38min** |

**不展开引用关系时，10 万篇的瓶颈不在代码，在 OpenAlex API 限频（~5 分钟）和可选的 embedding 生成（~30 分钟）**。

### 5.2 如果展开引用引用（expand_depth=2）

```
初始 10 万篇 → 每篇 ~40 个引用 = 400 万引用 ID
  → API 回查 400 万引用（每批 50）= 80,000 次调用
  → 引用又各含 ~40 个引用 = 1.6 亿 ID
    → 完全不可控
```

结论：** expand_depth >= 2 在万级以上不可用**，必须在代码层面做硬性限制。

---

## 6. 其他问题

### 6.1 embedding 生成

sentence-transformers 在 CPU 上每秒约处理 5-10 篇，10 万篇约 3-6 小时。如果有 GPU（CUDA），可提升至 1000+ 篇/秒。

建议：**大导入时不生成 embedding**，后续用 `enrich` 命令或独立脚本补充。

### 6.2 import_sessions 数组膨胀

一个节点被多次导入后，`import_sessions` 数组持续增长。10 次导入后约为 `["S001", "S002", ..., "S010"]`，长度 10 不影响性能。但如果设计为每天导入累积，几年后可能达到数千条。

建议：设置上限保留最近 20 个 session，超出时裁剪。

### 6.3 中途失败恢复

10 万篇导入耗时数分钟，网络波动或 Neo4j 重启可能导致中途失败。

当前已有 `--resume` 机制：如果缓存未清理，直接重跑 `import --resume` 即可跳过 API 阶段。问题是：

- 关系展开的结果也在缓存中，恢复时直接使用
- 如果失败发生在导入阶段（Neo4j 写库中途），恢复时会重新写所有节点（幂等，不会重复）

现状：**恢复机制已就绪，不需要额外改动。**

### 6.4 幂等写入的 ON MATCH 开销

```cypher
ON MATCH SET
  n.import_sessions =
    CASE WHEN item.current_session IN n.import_sessions
    THEN n.import_sessions
    ELSE n.import_sessions + item.current_session
    END
```

当前 CASE 判断是 O(n) 的列表扫描。如果 `import_sessions` 数组很长（数十个），每次 MERGE 都有额外开销。

建议：将 `import_sessions` 改为 `COALESCE(n.import_sessions, []) + item.current_session` 不做去重，用 `apoc.coll.toSet()` 或 Cypher 的列表去重替代。但去重不是频繁操作（只在导入时做），当前实现足够。

---

## 7. 优化优先级汇总

| 优先级 | 优化项 | 预期效果 | 工作量 |
|---|---|---|---|
| P0 | limit > 5000 时自动限制 expand_depth=1 | 防止引用爆炸 | 0.5d |
| P0 | 大导入跳过引用展开（referenced_works） | 节省 80%+ API 调用 | 0.5d |
| P1 | 按类型分批读 JSONL 替代 `read_all()` | 峰值内存降低 50-80% | 1d |
| P2 | DataSerializer 支持 gzip 压缩 | 磁盘占用降 70% | 0.5d |
| P2 | embedding 生成委托给独立命令 | 核心导入不受影响 | 1d |
| P3 | 并发 API 请求（带限频控制） | 提效 3-5x | 2d |

---

## 8. 结论

| 量级 | expand_depth=1（当前默认） | expand_depth>=2 |
|---|---|---|
| 1000 篇 | **完全可行**，~2 分钟 | 可行，~5 分钟 |
| 10,000 篇 | **可行**，~8 分钟 | 不可行，引用展开耗时长 |
| 100,000 篇 | **可行但需优化内存**，~8 分钟+ | 不可行 |
| 1,000,000 篇 | 需要 P1-P2 优化 | 不可行 |

对于十万级导入，**核心瓶颈不在代码逻辑，而在于 OpenAlex API 限频和大规模关系展开。** 当前架构在做了以下优化后可以支撑：

1. 自动限制 expand_depth=1（以及跳过引用展开）
2. 按类型分批读写 JSONL 降低内存
3. 给用户清晰的进度反馈

无需架构重构，属于增量优化。
