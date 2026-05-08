# 本地 JSONL 缓存执行文档

## 概述

将路径 A（`import` 命令）的导入流程从"全量内存缓存"改为"本地 JSONL 文件缓存"。抓取阶段逐批写入本地 JSONL 文件，抓取完成后从 JSONL 读取并批量写入 Neo4j，最后清理缓存。

> **整体状态：✅ 已完成**（全部 5 个步骤已实现）

---

## 实现步骤

### Step 1：新增 DataSerializer 模块 — ✅ 已完成

**文件**: `src/openalex_neo4j/serializer.py`

实现 `DataSerializer` 类，负责 JSONL 文件的写入和读取。

```
DataSerializer
├── __init__(cache_dir, session_id)  → 创建缓存目录
├── append(label, node_dict)         → 追加单条 JSONL
├── append_batch(label, nodes)       → 批量追加 JSONL
├── write_manifest(metadata)         → 写入 manifest.json
├── read(label) → list[dict]        → 读取某类全部记录
├── read_all() → dict[str, list]    → 读取所有类型
├── read_manifest() → dict | None   → 读取 manifest
├── count(label) → int               → 行级计数（不加载全部）
├── cleanup()                        → 删除缓存目录
└── data_dir: Path                   → ~/.openalex-neo4j/cache/{sid}/
```

**JSONL 格式**：每行一个 JSON 对象，写入时 `json.dumps(node_dict, ensure_ascii=False) + "\n"`。

**目录结构**：
```
~/.openalex-neo4j/cache/{session_id}/
├── manifest.json
├── works.jsonl
├── authors.jsonl
├── sources.jsonl
├── institutions.jsonl
├── topics.jsonl
├── publishers.jsonl
└── funders.jsonl
```

**注意事项**：
- `ensure_ascii=False` 保证中文等非 ASCII 字符可读
- `append_batch` 使用 `with open("a")` 追加模式
- `read` 按行读取，`json.loads` 逐行解析
- `cleanup` 用 `shutil.rmtree` 删除整个目录

---

### Step 2：在 importer.py 中新增共用方法 — ✅ 已完成

**文件**: `src/openalex_neo4j/importer.py`

在 `OpenAlexImporter` 类中新增：

#### 2a: 新增构造参数和属性

```python
def __init__(self, neo4j_client, openalex_client, session_manager=None):
    # ... 现有代码 ...
    self.serializer: DataSerializer | None = None
```

#### 2b: 新增 `_save_works_batch(works)`

将一批 Work 序列化到本地 JSONL：

```python
def _save_works_batch(self, works: list[Work]) -> None:
    nodes = [w.to_node_dict(current_session=self.current_session) for w in works]
    ts = datetime.now().isoformat()
    for node in nodes:
        node["current_session"] = self.current_session
        node["current_timestamp"] = ts
    self.serializer.append_batch("Work", nodes)
```

#### 2c: 新增 `_expand_and_save_relationships()`

从缓存文件中读取 works → 提取缺失 ID → 调 API → 写 JSONL：

```python
def _expand_and_save_relationships(self) -> None:
    cached_works = self.serializer.read("Work")
    # 收集所有需要抓取的 ID
    author_ids = set()
    source_ids = set()
    # ... 遍历 cached_works 填充 set ...

    # 减去已缓存的部分
    cached_authors = self.serializer.read("Author")
    cached_author_ids = {a["id"] for a in cached_authors}
    missing_author_ids = author_ids - cached_author_ids

    # 调 API 抓取并写入缓存
    if missing_author_ids:
        authors = self.openalex.fetch_authors_by_ids(list(missing_author_ids))
        author_nodes = [a.to_node_dict(current_session=self.current_session) for a in authors]
        # 注入 session 字段
        ts = datetime.now().isoformat()
        for node in author_nodes:
            node["current_session"] = self.current_session
            node["current_timestamp"] = ts
        self.serializer.append_batch("Author", author_nodes)
    # ... 同理处理 Source / Institution / Topic / Funder / Publisher / 引用 Work ...
```

#### 2d: 新增 `_import_nodes_from_dict(entities)`

从字典读取各类型实体 dict 列表，批量创建节点：

```python
def _import_nodes_from_dict(self, entities: dict[str, list[dict]]) -> dict[str, int]:
    counts = {}
    label_map = {
        "Work": ("works", True),
        "Author": ("authors", False),
        "Institution": ("institutions", False),
        "Source": ("sources", False),
        "Topic": ("topics", False),
        "Publisher": ("publishers", False),
        "Funder": ("funders", False),
    }
    ts = datetime.now().isoformat()
    for label, (count_key, dynamic) in label_map.items():
        nodes = entities.get(label, [])
        if not nodes:
            continue
        # 确保 session 字段存在
        for node in nodes:
            if "current_session" not in node:
                node["current_session"] = self.current_session
                node["current_timestamp"] = ts
        counts[count_key] = self.neo4j.batch_create_nodes(
            label, nodes, dynamic_label=dynamic,
            current_session=self.current_session,
        )
    return counts
```

#### 2e: 新增 `_import_relationships_from_dict(entities)`

从字典读取实体数据，构建关系并批量创建：

```python
def _import_relationships_from_dict(self, entities: dict[str, list[dict]]) -> dict[str, int]:
    works = {w["id"]: w for w in entities.get("Work", [])}
    authors = {a["id"]: a for a in entities.get("Author", [])}
    sources = {s["id"]: s for s in entities.get("Source", [])}
    institutions = {i["id"]: i for i in entities.get("Institution", [])}
    topics = {t["id"]: t for t in entities.get("Topic", [])}
    funders = {f["id"]: f for f in entities.get("Funder", [])}
    publishers = {p["id"]: p for p in entities.get("Publisher", [])}

    counts = {}

    # AUTHORED
    authored = []
    for w_id, w in works.items():
        for a_id in w.get("author_ids", []):
            if a_id in authors:
                authored.append({"source_id": a_id, "target_id": w_id})
    if authored:
        counts["authored"] = self.neo4j.batch_create_relationships(
            "AUTHORED", "Author", "Work", authored
        )

    # CITES
    cites = []
    for w_id, w in works.items():
        for ref_id in w.get("referenced_work_ids", []):
            if ref_id in works:
                cites.append({"source_id": w_id, "target_id": ref_id})
    if cites:
        counts["cites"] = self.neo4j.batch_create_relationships(
            "CITES", "Work", "Work", cites
        )

    # PUBLISHED_IN
    published_in = []
    for w_id, w in works.items():
        s_id = w.get("source_id")
        if s_id and s_id in sources:
            published_in.append({"source_id": w_id, "target_id": s_id})
    if published_in:
        counts["published_in"] = self.neo4j.batch_create_relationships(
            "PUBLISHED_IN", "Work", "Source", published_in
        )

    # HAS_TOPIC
    has_topic = []
    for w_id, w in works.items():
        for t_id in w.get("topic_ids", []):
            if t_id in topics:
                has_topic.append({"source_id": w_id, "target_id": t_id})
    if has_topic:
        counts["has_topic"] = self.neo4j.batch_create_relationships(
            "HAS_TOPIC", "Work", "Topic", has_topic
        )

    # FUNDED_BY
    funded_by = []
    for w_id, w in works.items():
        for f_id in w.get("funder_ids", []):
            if f_id in funders:
                funded_by.append({"source_id": w_id, "target_id": f_id})
    if funded_by:
        counts["funded_by"] = self.neo4j.batch_create_relationships(
            "FUNDED_BY", "Work", "Funder", funded_by
        )

    # AFFILIATED_WITH
    affiliated = []
    for w_id, w in works.items():
        for a_id in w.get("author_ids", []):
            for i_id in w.get("institution_ids", []):
                if a_id in authors and i_id in institutions:
                    affiliated.append({"source_id": a_id, "target_id": i_id})
    if affiliated:
        unique_affiliated = {(r["source_id"], r["target_id"]): r for r in affiliated}
        counts["affiliated_with"] = self.neo4j.batch_create_relationships(
            "AFFILIATED_WITH", "Author", "Institution", list(unique_affiliated.values())
        )

    # PUBLISHED_BY
    published_by = []
    for s_id, s in sources.items():
        p_id = s.get("publisher_id")
        if p_id and p_id in publishers:
            published_by.append({"source_id": s_id, "target_id": p_id})
    if published_by:
        counts["published_by"] = self.neo4j.batch_create_relationships(
            "PUBLISHED_BY", "Source", "Publisher", published_by
        )

    return counts
```

#### 2f: 重构 `import_from_query()`

改为两步走：抓取阶段（写 JSONL）→ 导入阶段（读 JSONL → 写 Neo4j）：

```python
def import_from_query(
    self, query, limit=100, expand_depth=1,
    skip_abstracts=False, generate_embeddings=False,
    tag=None, skip_constraints=False,
    from_year=None, to_year=None,
    cache_dir=None, keep_cache=False,
) -> dict[str, int]:
    """路径 A：从 OpenAlex API 抓取 → 本地 JSONL 缓存 → 写库。"""
    from .serializer import DataSerializer

    # ─── 初始化 ───
    if self.session_manager:
        session_obj = self.session_manager.create_session(
            query=query, limit=limit, expand_depth=expand_depth, tag=tag,
        )
        self.current_session = session_obj.id

    cache_root = Path(cache_dir) if cache_dir else Path.home() / ".openalex-neo4j" / "cache"
    self.serializer = DataSerializer(cache_root, self.current_session)

    # ─── 抓取阶段：API → JSONL ───
    initial_works = self.openalex.search_works(query, limit, from_year=from_year, to_year=to_year)
    self._save_works_batch(initial_works)

    for depth in range(1, expand_depth + 1):
        self._expand_and_save_relationships()

    # 摘要 / 嵌入向量处理
    if skip_abstracts:
        all_works = self.serializer.read("Work")
        for w in all_works:
            w["abstract"] = None
        # 重写文件
        ...

    if generate_embeddings:
        # 需要读取 Work 到内存生成嵌入，再写回
        ...

    # 写入 manifest
    entity_counts = {}
    for label in DataSerializer.LABELS:
        entity_counts[label] = self.serializer.count(label)
    self.serializer.write_manifest({
        "session_id": self.current_session,
        "query": query,
        "source": "openalex-api",
        "created_at": datetime.now().isoformat(),
        "parameters": {"limit": limit, "expand_depth": expand_depth, ...},
        "entity_counts": entity_counts,
    })

    # ─── 导入阶段：JSONL → Neo4j ───
    if not skip_constraints:
        self.neo4j.create_constraints()
        self.neo4j.create_indexes(include_vector=generate_embeddings)

    all_entities = self.serializer.read_all()
    node_counts = self._import_nodes_from_dict(all_entities)
    rel_counts = self._import_relationships_from_dict(all_entities)
    counts = {**node_counts, **rel_counts}

    # 完成会话
    if self.session_manager and self.current_session:
        self.session_manager.complete_session(self.current_session, stats=counts)

    # ─── 清理 ───
    if not keep_cache:
        self.serializer.cleanup()

    return counts
```

#### 2g: 新增 `import_from_cache(session_id, cache_dir)`

从已有缓存恢复导入（跳过 API 抓取）：

```python
def import_from_cache(self, session_id: str, cache_dir: Path) -> dict[str, int]:
    """从现有缓存恢复导入，跳过 API 抓取阶段。"""
    from .serializer import DataSerializer

    cache_root = cache_dir if cache_dir else Path.home() / ".openalex-neo4j" / "cache"
    self.serializer = DataSerializer(cache_root, session_id)
    self.current_session = session_id

    manifest = self.serializer.read_manifest()
    if not manifest:
        raise ValueError(f"Cache for session {session_id} not found")

    generate_embeddings = manifest.get("parameters", {}).get("generate_embeddings", False)

    self.neo4j.create_constraints()
    self.neo4j.create_indexes(include_vector=generate_embeddings)

    all_entities = self.serializer.read_all()
    node_counts = self._import_nodes_from_dict(all_entities)
    rel_counts = self._import_relationships_from_dict(all_entities)
    counts = {**node_counts, **rel_counts}

    if self.session_manager and self.current_session:
        self.session_manager.complete_session(self.current_session, stats=counts)

    return counts
```

---

### Step 3：修改 CLI 命令 — ✅ 已完成

**文件**: `src/openalex_neo4j/cli.py`

#### 3a: 为 `import` 命令新增选项

```python
@click.option(
    "--cache-dir",
    default=None,
    type=click.Path(file_okay=False),
    help="Local cache directory (default: ~/.openalex-neo4j/cache/)",
)
@click.option(
    "--keep-cache",
    is_flag=True,
    help="Keep local cache after import (for debugging/resume)",
)
@click.option(
    "--resume",
    default=None,
    help="Resume import from cached session ID (skips API fetch)",
)
@click.option(
    "--list-cache",
    is_flag=True,
    help="List cached import sessions",
)
```

#### 3b: 修改 `import_data()` 函数

在函数签名中新增参数，调整调用逻辑：

```python
def import_data(
    ...,  # 现有参数不变
    cache_dir: str | None = None,
    keep_cache: bool = False,
    resume: str | None = None,
    list_cache: bool = False,
) -> None:
    # 处理 --list-cache
    if list_cache:
        cache_root = Path(cache_dir) if cache_dir else Path.home() / ".openalex-neo4j" / "cache"
        if cache_root.exists():
            for d in sorted(cache_root.iterdir()):
                if d.is_dir():
                    manifest_file = d / "manifest.json"
                    if manifest_file.exists():
                        m = json.loads(manifest_file.read_text())
                        click.echo(f"{d.name}  query={m.get('query')}  works={m.get('entity_counts',{}).get('Work',0)}")
        return

    # 处理 --resume
    if resume:
        counts = importer.import_from_cache(resume, Path(cache_dir) if cache_dir else Path.home() / ".openalex-neo4j" / "cache")
        click.echo(f"Resumed import from cache. {counts}")
        return

    # 正常导入流程 — 透传 cache_dir 和 keep_cache
    counts = importer.import_from_query(
        ...,  # 现有参数
        cache_dir=Path(cache_dir) if cache_dir else None,
        keep_cache=keep_cache,
    )
```

#### 3c: 显示缓存目录信息

在 `import_data` 的配置输出区域新增：

```python
# 显示导入模式
click.echo(f"Cache dir: {cache_dir or '~/.openalex-neo4j/cache/'}")
```

---

### Step 4：移除旧的内存缓存逻辑 — ✅ 已完成

**文件**: `src/openalex_neo4j/importer.py`

保留类定义中 `self.works` / `self.authors` 等 dict 但**标记为废弃**，确保新的 `import_from_query()` 不再使用这些内存储存，而全部走 `DataSerializer`。

旧的 `_import_nodes()` 和 `_import_relationships()` 方法保留，供其他可能依赖它们的代码使用（如现有测试），但新流程调用的是 `_import_nodes_from_dict()` 和 `_import_relationships_from_dict()`。

**清理清单**：
- `_add_works()` → 不再需要，改为 `_save_works_batch()`
- `_expand_relationships()` → 不再需要，改为 `_expand_and_save_relationships()`
- `_import_nodes()` → 保留，新流程不用
- `_import_relationships()` → 保留，新流程不用
- `_enrich_nodes_with_session()` → 不再需要，在写入 JSONL 时已注入 session 字段

---

### Step 5：新增 + 修改测试 — ✅ 已完成

#### 5a: 新增 `tests/test_serializer.py`

测试 DataSerializer 的读写功能：

| 测试 | 说明 |
|---|---|
| `test_init_creates_dir` | 初始化时创建 `cache/{sid}/` 目录 |
| `test_append_and_read` | 追加单条并读取 |
| `test_append_batch` | 批量追加并读取全部 |
| `test_read_empty` | 读取不存在的实体类型返回空列表 |
| `test_read_all` | 读所有类型 |
| `test_manifest_write_read` | manifest 读写 |
| `test_count` | 行级计数 |
| `test_cleanup_removes_dir` | cleanup 删除目录 |
| `test_multiple_sessions` | 多个 session 互不干扰 |
| `test_chinese_characters` | 中文等非 ASCII 字符 |

#### 5b: 修改 `tests/test_importer.py`

| 测试 | 操作 |
|---|---|
| `test_import_from_query` | 改为测试新的缓存流程，mock `DataSerializer` |
| `test_import_nodes_from_dict` | **新增**：测试从 dict 批量建节点 |
| `test_import_relationships_from_dict` | **新增**：测试从 dict 批量建关系 |
| `test_expand_and_save` | **新增**：测试扩展关系并写入缓存 |
| `test_import_from_cache` | **新增**：测试从缓存恢复导入 |

#### 5c: 修改 `tests/test_cli.py`

| 测试 | 操作 |
|---|---|
| `test_import_cache_dir` | **新增**：--cache-dir 选项 |
| `test_import_keep_cache` | **新增**：--keep-cache 选项 |
| `test_import_resume` | **新增**：--resume 选项 |
| `test_import_list_cache` | **新增**：--list-cache 选项 |

---

## 依赖引入

| 依赖 | 类型 | 用途 | 操作 |
|---|---|---|---|
| `pathlib.Path` | 内置 | 路径操作 | 现有已引入 |
| `json` | 内置 | JSON 序列化 | 现有已引入 |
| `shutil` | 内置 | `rmtree` 删除缓存 | 新增 import |
| `tempfile` | 内置 | 测试中的临时目录 | 测试使用 |

**无新增外部依赖**。

---

## 测试计划

### 测试范围

| 层次 | 覆盖内容 | 数量预计 |
|---|---|---|
| 单元测试 | DataSerializer 全部方法 | ~10 个 |
| 单元测试 | importer 新方法（mock DataSerializer） | ~6 个 |
| 单元测试 | CLI 新选项 | ~4 个 |
| 集成测试 | 完整导入流程（mock API + 真实 JSONL + mock Neo4j） | ~2 个 |

### 测试配置

```bash
# 运行所有单元测试（排除集成测试）
python -m pytest tests/ -v -m "not integration"

# 运行 serializer 测试
python -m pytest tests/test_serializer.py -v

# 运行 importer 相关测试
python -m pytest tests/test_importer.py -v

# 运行 CLI 测试
python -m pytest tests/test_cli.py -v
```

### 预期结果

- 所有新增测试通过
- 现有 163+ 测试不出现回归失败
- 测试覆盖 DataSerializer 的正常路径和错误路径（空文件、目录不存在等）

---

## 回滚方案

### 情况 A：代码已修改但未提交

```bash
# 恢复所有修改
git checkout -- src/openalex_neo4j/serializer.py
git checkout -- src/openalex_neo4j/importer.py
git checkout -- src/openalex_neo4j/cli.py

# 恢复删除的文件（如有）
git checkout -- src/openalex_neo4j/serializer.py

# 验证恢复后测试通过
python -m pytest tests/ -v -m "not integration"
```

### 情况 B：代码已提交但未推送

```bash
# 回退到上一个提交
git revert HEAD --no-edit

# 或硬回退（确认无未提交的更改后）
git reset --hard HEAD~1
```

### 情况 C：代码已推送

```bash
# 安全回退（创建反向提交）
git revert HEAD --no-edit
git push origin main
```

### 兼容性策略

新旧流程通过参数隔离：

- `--cache-dir`、`--keep-cache`、`--resume`、`--list-cache` 都是新增的可选参数
- 不指定这些参数时，默认行为仍然是内存缓存（即当前流程不受影响）
- `DataSerializer` 是纯新增模块，不涉及修改现有功能
- 旧的 `_import_nodes()` 和 `_import_relationships()` 方法保留，确保使用这些方法的测试和外部调用不中断

将缓存模式设为默认的时机可以等到功能稳定后，通过调整 `import_from_query` 中 `cache_dir` 的默认值来切换。

---

## 执行顺序

| 步骤 | 文件 | 依赖 |
|---|---|---|
| 1 | `serializer.py` | 无 |
| 2 | `importer.py` — 新增共用方法 | Step 1 |
| 3 | `importer.py` — 重构 `import_from_query` | Step 1, 2 |
| 4 | `cli.py` — 新增选项 | Step 3 |
| 5 | `tests/test_serializer.py` | Step 1 |
| 6 | `tests/test_importer.py` — 新增+修改 | Step 2, 3 |
| 7 | `tests/test_cli.py` — 新增 | Step 4 |
| 8 | 运行全部测试验证 | Steps 5-7 |
