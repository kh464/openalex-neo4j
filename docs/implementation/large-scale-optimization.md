# 大规模导入优化执行方案

## 概述

基于 `docs/analysis/large-scale-import.md` 的分析结果，实现三个优化维度的增量改造：API 调用量优化（自动限 depth + 并行请求）、内存优化（按类型分批读取）、Neo4j 写入优化（不做改动）。

> **整体状态：✅ 已完成**

---

## 修改文件清单

| 文件 | 操作 | 说明 | 状态 |
|---|---|---|---|
| `src/openalex_neo4j/importer.py` | 修改 | 三项优化入口 | ✅ 已完成 |
| `src/openalex_neo4j/openalex_client.py` | 修改 | 新增令牌桶限频，可选并行批量 | ✅ 已完成 |
| `src/openalex_neo4j/serializer.py` | 未修改 | 已有 `read(label)` 方法，无需改动 | — |
| `src/openalex_neo4j/rate_limiter.py` | **新增** | 令牌桶限频器 | ✅ 已完成 |
| `tests/test_importer.py` | 修改 | 新增优化相关测试 | ✅ 已完成 |
| `tests/test_rate_limiter.py` | **新增** | 令牌桶测试 | ✅ 已完成 |

---

## Step 1：API 调用量 — 自动限制展开深度 — ✅ 已完成

### 1.1 修改点

在 `OpenAlexImporter` 中添加阈值常量，并在 `import_from_query()` 中引入保护逻辑。

### 1.2 具体实现

```python
# importer.py — OpenAlexImporter 类属性

LARGE_IMPORT_THRESHOLD = 5000
```

在 `import_from_query()` 中，在 expand_depth 循环之前插入保护逻辑：

```python
# 大导入保护：limit > 阈值时自动限制展开行为
if limit > self.LARGE_IMPORT_THRESHOLD:
    original_depth = expand_depth
    if expand_depth > 1:
        logger.warning(
            f"Large import (limit={limit}): forcing expand_depth=1 "
            f"(was {expand_depth}) to avoid excessive API calls"
        )
        expand_depth = 1
    logger.info(
        f"Large import (limit={limit}): skipping referenced_works expansion"
    )
```

### 1.3 跳过引用展开的逻辑

当前 `_expand_and_save_relationships()` 会提取 `referenced_work_ids` 并回查 API 获取这些引用 Work。跳过方式有两种：

**方案 A（推荐 — 改动最小）**：在 `_expand_and_save_relationships()` 中添加参数或实例标记，控制是否跳过引用：

```python
def _expand_and_save_relationships(self, skip_referenced: bool = False) -> None:
    ...
    # 仅在 not skip_referenced 时执行引用展开
    if not skip_referenced and referenced_work_ids:
        works = self.openalex.fetch_works_by_ids(list(referenced_work_ids))
        self._save_works_batch(works)
```

导入循环改为：

```python
for depth in range(1, expand_depth + 1):
    logger.info(f"Expanding relationships at depth {depth}")
    skip_ref = (depth == 1 and limit > self.LARGE_IMPORT_THRESHOLD)
    self._expand_and_save_relationships(skip_referenced=skip_ref)
```

注意：`skip_referenced` 仅在 depth=1 时有效（引用展开只在 depth=1 做，depth>=2 是展开引用的引用）。

**效果**：跳过引用展开节省约 80% API 调用（20000 次回查），从 ~40 分钟降至 ~5 分钟。

---

## Step 2：API 调用量 — 并行请求 + 令牌桶限频 — ✅ 已完成

### 2.1 新增 rate_limiter.py

令牌桶算法，线程安全，供 OpenAlexClient 的所有 API 调用共享使用。

```python
"""Token bucket rate limiter for OpenAlex API."""

import time
import threading


class TokenBucket:
    """Thread-safe token bucket rate limiter.

    10 requests/second → TokenBucket(rate=10, burst=10)
    """

    def __init__(self, rate: float = 10, burst: int = 10):
        """Initialize token bucket.

        Args:
            rate: Tokens added per second (i.e., max request rate)
            burst: Maximum accumulated tokens (i.e., max burst size)
        """
        self.rate = rate
        self.burst = burst
        self._tokens = burst
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, tokens: int = 1) -> None:
        """Wait until the requested number of tokens is available.

        Args:
            tokens: Number of tokens to consume (default 1 per request)
        """
        while True:
            with self._lock:
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                # Calculate wait time until enough tokens are available
                needed = tokens - self._tokens
                wait = needed / self.rate
            time.sleep(max(wait, 0.01))

    def _refill(self) -> None:
        """Refill tokens based on elapsed time."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self.burst, self._tokens + elapsed * self.rate)
        self._last_refill = now
```

### 2.2 修改 OpenAlexClient

注入 `TokenBucket` 实例，在每个实际 API 请求调用前 `acquire()`。

```python
class OpenAlexClient:
    def __init__(self, email: str | None = None, rate_limiter: TokenBucket | None = None):
        self.email = email
        self.rate_limiter = rate_limiter or TokenBucket(rate=10, burst=10)

    def _rate_limited_request(self, func, *args, **kwargs):
        """Execute a request with rate limiting."""
        self.rate_limiter.acquire()
        return func(*args, **kwargs)
```

在 `cli.py` 中创建 `OpenAlexClient` 时传入共享限频器，使其对所有命令（search_works、count_works 及所有批量 fetch）生效。

### 2.3 并行展开关系

改造 `_expand_and_save_relationships()`，对不同实体类型的 API 调用使用 `ThreadPoolExecutor` 并行执行。由于底层 `OpenAlexClient` 已有令牌桶限频，并发请求会被限频器自动节流，不会触发 429。

```python
def _expand_and_save_relationships(self, skip_referenced: bool = False) -> None:
    cached_works = self.serializer.read("Work")

    # 收集所有 ID（与当前逻辑相同）
    author_ids, institution_ids, source_ids = set(), set(), set()
    topic_ids, funder_ids, referenced_work_ids = set(), set(), set()
    for w in cached_works:
        ...  # 同现有代码

    # 排除已缓存的 ID（与当前逻辑相同）
    ...

    ts = datetime.now().isoformat()

    def fetch_type(fetch_fn, ids, label, extra_fn=None):
        """Fetch entities and save to cache."""
        if not ids:
            return label, 0
        entities = fetch_fn(list(ids))
        nodes = []
        for entity in entities:
            node = entity.to_node_dict(current_session=self.current_session)
            node["current_session"] = self.current_session
            node["current_timestamp"] = ts
            nodes.append(node)
        if extra_fn:
            extra_fn(entities, nodes, ts)
        self.serializer.append_batch(label, nodes)
        return label, len(nodes)

    futures = {}
    with ThreadPoolExecutor(max_workers=5) as executor:
        if author_ids:
            futures[executor.submit(fetch_type, self.openalex.fetch_authors_by_ids, author_ids, "Author")] = "Author"
        if institution_ids:
            futures[executor.submit(fetch_type, self.openalex.fetch_institutions_by_ids, institution_ids, "Institution")] = "Institution"
        if source_ids:
            futures[executor.submit(fetch_type_with_publisher, ...)] = "Source"
        if topic_ids:
            futures[executor.submit(fetch_type, self.openalex.fetch_topics_by_ids, topic_ids, "Topic")] = "Topic"
        if funder_ids:
            futures[executor.submit(fetch_type, self.openalex.fetch_funders_by_ids, funder_ids, "Funder")] = "Funder"
        if referenced_work_ids and not skip_referenced:
            futures[executor.submit(fetch_type, self.openalex.fetch_works_by_ids, referenced_work_ids, "Work")] = "Work"

        for future in as_completed(futures):
            label, count = future.result()
            logger.info(f"Fetched {count} {label} entities")
```

注意：Source 的 fetch 需要额外处理 Publisher（当前代码在 fetch_source 后根据 `publisher_id` 再 fetch_publisher），这部分需要特殊处理，例如在 `fetch_type` 的 `extra_fn` 回调中完成。

### 2.4 效果

并行 + 令牌桶配合下，各实体类型的 API 调用可以同时进行。令牌桶确保总请求速率不超过 10 req/s。

- 当前：顺序执行，5 种实体类型各串行 → 总时间 = 各阶段之和（~5 分钟）
- 优化后：并行执行，总时间 ≈ 最慢的实体类型（~1-2 分钟）
- 实际提升约 2-3x（受限于 429 重试和网络延迟）

---

## Step 3：内存占用 — 按类型分批读取 JSONL — ✅ 已完成

### 3.1 问题所在

当前流程：

```python
all_entities = self.serializer.read_all()            # 全部加载到内存
node_counts = self._import_nodes_from_dict(all_entities)    # 逐类型写库
rel_counts = self._import_relationships_from_dict(all_entities)  # 关系写库
```

`read_all()` 一次性读取全部 7 种实体，峰值 ~588 MB（10 万篇 Work 场景）。

### 3.2 优化方案

改为两阶段流式处理：

**阶段 I — 节点创建流式**：按类型逐一读 JSONL → 写 Neo4j 节点 → 释放内存，同时收集各类型的 ID 集合供后续关系创建用。

```python
def _import_nodes_streaming(self) -> dict[str, int]:
    """按类型逐一读取 JSONL → 创建节点 → 释放内存。

    同时收集各类型的 ID 集合，返回供关系创建阶段使用。
    """
    counts = {}
    # 存储各实体类型的 ID 集合，供关系创建阶段做成员检查
    entity_ids: dict[str, set[str]] = {}

    label_map = [
        ("Work", "works", True),
        ("Author", "authors", False),
        ("Institution", "institutions", False),
        ("Source", "sources", False),
        ("Topic", "topics", False),
        ("Publisher", "publishers", False),
        ("Funder", "funders", False),
    ]

    ts = datetime.now().isoformat()

    for label, count_key, dynamic in label_map:
        nodes = self.serializer.read(label)
        if not nodes:
            entity_ids[label] = set()
            continue

        # 剥离关系字段，注入 session 字段
        clean_nodes = []
        ids_for_relations = set()
        for node in nodes:
            ids_for_relations.add(node["id"])
            clean = {k: v for k, v in node.items()
                     if k not in self._REL_FIELDS}
            if "current_session" not in clean:
                clean["current_session"] = self.current_session
                clean["current_timestamp"] = ts
            clean_nodes.append(clean)

        # 写库
        counts[count_key] = self.neo4j.batch_create_nodes(
            label, clean_nodes, dynamic_label=dynamic,
            current_session=self.current_session,
        )
        # clean_nodes 和 nodes 在此轮迭代结束后被 GC 回收
        entity_ids[label] = ids_for_relations

    return counts, entity_ids
```

**阶段 II — 关系创建流式**：仅读取 Work JSONL（因为关系字段都在 Work 中），配合阶段 I 收集的 ID 集合构建关系。

```python
def _import_relationships_streaming(self, entity_ids: dict[str, set[str]]) -> dict[str, int]:
    """从 JSONL 读取 Work dicts，结合预收集的 ID 集合构建关系。

    只有 Work 的 JSONL 被再次读取，其他类型的 ID 仅通过 entity_ids 集合做成员检查。
    """
    works_list = self.serializer.read("Work")
    works = {w["id"]: w for w in works_list}
    # works_list 在此释放
    # works (~200 MB) 保持在内存中直到关系创建完成

    authors = entity_ids.get("Author", set())
    institutions = entity_ids.get("Institution", set())
    sources = entity_ids.get("Source", set())
    topics = entity_ids.get("Topic", set())
    funders = entity_ids.get("Funder", set())
    publishers = entity_ids.get("Publisher", set())

    counts = {}

    # AUTHORED
    authored = []
    for w_id, w in works.items():
        for a_id in w.get("author_ids", []):
            if a_id in authors:
                authored.append({"source_id": a_id, "target_id": w_id})
    if authored:
        counts["authored"] = self.neo4j.batch_create_relationships(
            "AUTHORED", "Author", "Work", authored,
        )

    # 其余关系类型同样处理：AFFILIATED_WITH, PUBLISHED_IN, CITES,
    # HAS_TOPIC, FUNDED_BY, PUBLISHED_BY
    # ... 代码同现有 _import_relationships_from_dict，
    #     但 authors 等是 set[str] 而非 dict

    return counts
```

**峰值内存变化**：

| 阶段 | 当前方案 | 优化后 |
|---|---|---|
| 节点创建 | 588 MB（全部类型） | 250 MB（单类型最大 Author） |
| 关系创建 | 588 MB（复用同一份） | 200 MB（仅 Work dicts）+ 10 MB（ID 集合） |
| **峰值** | **~588 MB** | **~250 MB** |

### 3.3 改造 `import_from_query()`

```python
# 替换原有的 read_all → _import_nodes_from_dict → _import_relationships_from_dict
logger.info("Importing nodes to Neo4j (streaming mode)")
node_counts, entity_ids = self._import_nodes_streaming()

logger.info("Importing relationships to Neo4j")
rel_counts = self._import_relationships_streaming(entity_ids)

counts = {**node_counts, **rel_counts}
```

### 3.4 改造 `import_from_cache()`

同样替换为 `_import_nodes_streaming()` + `_import_relationships_streaming()`。

---

## Step 4：Neo4j 写入性能 — 不做改动 — ✅ 已确认

十万级导入中 Neo4j 写入仅占 ~2 分钟（节点 ~30s + 关系 ~90s），在优化后的总耗时中占比很低，不做专门优化。

---

## 依赖引入

| 依赖 | 类型 | 用途 | 操作 |
|---|---|---|---|
| `threading` | 内置 | 令牌桶线程锁 | 新增 import |
| `time` | 内置 | 令牌桶计时 | 新增 import |
| `concurrent.futures` | 内置 | 并行 API 请求 | 新增 import |

**无新增外部依赖**。

---

## 测试计划

### 新增测试：test_rate_limiter.py

| 测试 | 说明 |
|---|---|
| `test_acquire_basic` | 单次获取 token 后计数减少 |
| `test_acquire_wait` | token 不足时等待 |
| `test_burst_limit` | 突发请求不超过 burst 上限 |
| `test_thread_safety` | 多线程并发不超限 |

### 修改测试：test_importer.py

| 测试 | 说明 |
|---|---|
| `test_large_import_auto_limit_depth` | limit > 5000 时强制 depth=1 |
| `test_large_import_skip_referenced` | 大导入跳过引用展开 |
| `test_small_import_no_limit` | limit ≤ 5000 不做限制 |
| `test_import_nodes_streaming` | 流式节点创建结果与原有方法一致 |
| `test_import_relationships_streaming` | 流式关系创建结果与原有方法一致 |

### 回归测试

所有现有测试（188+）不出现回归。旧方法（`_import_nodes_from_dict`、`_import_relationships_from_dict`）保留供其他调用方使用。

---

## 回滚方案

### 情况 A：代码已修改但未提交

```bash
git checkout -- src/openalex_neo4j/importer.py
git checkout -- src/openalex_neo4j/openalex_client.py
git checkout -- src/openalex_neo4j/rate_limiter.py       # 删除新文件
git rm --cached src/openalex_neo4j/rate_limiter.py       # 如果有
```

### 情况 B：代码已提交

```bash
git revert HEAD --no-edit
```

### 兼容性策略

- `_import_nodes_streaming()` 和 `_import_relationships_streaming()` 是新增方法，不修改现有方法
- 旧的 `_import_nodes_from_dict()`、`_import_relationships_from_dict()`、`_import_nodes()`、`_import_relationships()` 及其调用者不受影响
- 令牌桶是 `OpenAlexClient` 的可选构造参数，不传时使用默认 10 req/s 限频

---

## 执行顺序

| 步骤 | 文件 | 依赖 |
|---|---|---|
| 1 | `rate_limiter.py` 新增 | 无 |
| 2 | `openalex_client.py` 注入令牌桶 | Step 1 |
| 3 | `importer.py` — 大导入限制 depth + 跳过引用 | 无 |
| 4 | `importer.py` — 并行展开关系 | Step 1, 2 |
| 5 | `importer.py` — 流式节点创建 + 关系创建 | 无 |
| 6 | `serializer.py` — 按标签读取增强（如有需要） | 无 |
| 7 | `tests/test_rate_limiter.py` | Step 1 |
| 8 | `tests/test_importer.py` — 新增测试 | Steps 3-5 |
| 9 | 运行全部测试验证 | Steps 7-8 |

---

## 附录：当前架构关键数字

| 指标 | 当前值 | 优化后 |
|---|---|---|
| API 调用（10 万篇，expand_depth=1） | ~23750 次 | ~3750 次（跳过引用） |
| API 耗时（polite pool 10/s） | ~40 分钟 | ~5 分钟 |
| 峰值内存 | ~588 MB + overhead ≈ 1-2 GB | ~250 MB |
| Neo4j 写入 | ~2 分钟 | ~2 分钟（不变） |
| 总耗时（embedding 关闭） | ~8 分钟 | ~8 分钟（瓶颈在 API 限频） |
