# 双路径导入设计方案

## 概述

系统支持两种导入路径，通过不同的 CLI 命令区分：

| 路径 | 命令 | 数据源 | 状态 |
|---|---|---|---|
| **路径 A** | `import` | OpenAlex API 检索 → 本地缓存 → 写库 | ✅ 已实现（待调整为本地缓存） |
| **路径 B** | `import-wos` | 本地 WoS HTML 文件 → OpenAlex API 桥接 | 🚧 待实现 |

两条路径的**共同特征**：数据抓取阶段先写入本地临时缓存，抓取完成后从本地缓存读取并写入 Neo4j，最后清理缓存。

---

## 整体架构

```
┌──────────────────────────────────────────────────────────────────────┐
│                          CLI (cli.py)                                │
│                                                                      │
│  openalex-neo4j import          openalex-neo4j import-wos            │
│         │                               │                           │
│         ▼                               ▼                           │
│  ┌──────────────┐            ┌──────────────────────┐               │
│  │ import_data  │            │ import_wos_data      │               │
│  └──────┬───────┘            └──────────┬───────────┘               │
└─────────┼───────────────────────────────┼───────────────────────────┘
          │                               │
          ▼                               ▼
┌────────────────────┐     ┌──────────────────────────┐
│ OpenAlex API 检索  │     │ WoS HTML 解析 + DOI 提取  │
│ search_works()     │     │ wos_parser.py             │
│ _expand_relationships│   │                           │
└────────┬───────────┘     └───────────┬──────────────┘
         │                             │
         │                        ┌────▼──────┐
         │                        │ OpenAlex  │
         │                        │ API 回查  │
         │                        │ fetch_by_ │
         │                        │ doi()     │
         └─────────┬──────────────┴────┬──────┘
                   │                  │
                   ▼                  ▼
         ┌──────────────────────────────────┐
         │   DataSerializer                 │
         │   (序列化 → 本地 JSONL 文件)      │
         │                                  │
         │   ~/.openalex-neo4j/cache/{sid}/ │
         │     ├── works.jsonl              │
         │     ├── authors.jsonl            │
         │     ├── sources.jsonl            │
         │     ├── institutions.jsonl       │
         │     ├── topics.jsonl             │
         │     ├── publishers.jsonl         │
         │     ├── funders.jsonl            │
         │     └── manifest.json            │
         └──────────────┬───────────────────┘
                        │ 抓取完成，从缓存读取
                        ▼
         ┌──────────────────────────────────┐
         │   DataSerializer                 │
         │   (反序列化 → 内存 dict)          │
         └──────────────┬───────────────────┘
                        │
                        ▼
┌──────────────────────────────────────────────┐
│      导入管线 (importer.py)                   │
│                                              │
│  _import_nodes() → batch_create_nodes()      │
│  _import_relationships() → batch_create_rels │
└──────────────────┬───────────────────────────┘
                   │
                   ▼
┌──────────────────────────────────────────────┐
│       Neo4j 图数据库                          │
│       约束 / 索引 / 会话管理                  │
└──────────────────────────────────────────────┘
                   │
                   ▼
         删除本地缓存目录
```

---

## 路径 A：OpenAlex 检索导入（本地缓存版）

### CLI 命令

```bash
# 按关键词搜索并导入（自动使用本地缓存）
uv run openalex-neo4j import --query "quantum computing" --limit 100

# 支持时间范围和嵌入向量等
uv run openalex-neo4j import --query "AI" --from-year 2020 --to-year 2024 --limit 50 \
  --generate-embeddings --expand-depth 2

# 指定缓存目录（默认 ~/.openalex-neo4j/cache/）
uv run openalex-neo4j import --query "machine learning" --cache-dir /tmp/import-cache

# 保留缓存（不会自动删除，可用于调试或恢复）
uv run openalex-neo4j import --query "data mining" --keep-cache
```

### 命令选项

| 选项 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `--query, -q` | str | 必填 | OpenAlex 搜索查询 |
| `--limit, -l` | int | 100 | 最大获取数量 |
| `--cache-dir` | path | `~/.openalex-neo4j/cache/` | 本地缓存根目录 |
| `--keep-cache` | flag | False | 导入完成后保留缓存（不删除） |
| `--expand-depth` | int | 1 | 关系扩展深度 |
| `--skip-abstracts` | flag | False | 跳过摘要 |
| `--generate-embeddings` | flag | False | 生成嵌入向量 |
| `--tag` | str | None | 会话标签 |
| `--skip-constraints` | flag | False | 跳过约束创建 |
| `--from-year` | int | None | 起始年份 |
| `--to-year` | int | None | 截止年份 |

### 详细执行流程

```
import_data()
│
├── Step 1: 初始化
│   ├── 创建 Neo4jClient / OpenAlexClient / SessionManager
│   └── 生成 session_id
│
├── Step 2: 抓取数据 → 写入本地缓存
│   │
│   ├── 2a: 搜索初始 Work
│   │   ├── OpenAlexClient.search_works(query, limit, from_year, to_year)
│   │   ├── Work.from_openalex(data) → to_node_dict() → dict
│   │   └── DataSerializer.append("Work", node_dict)  ← 追加到 works.jsonl
│   │
│   ├── 2b: 扩展关系（按 depth 循环）
│   │   ├── 分析所有 cached Work → 提取缺失的 author_ids / source_ids 等
│   │   ├── OpenAlexClient.fetch_authors_by_ids(ids)
│   │   │   → Author.from_openalex() → to_node_dict()
│   │   │   → DataSerializer.append("Author", node_dict)  ← 追加到 authors.jsonl
│   │   ├── OpenAlexClient.fetch_sources_by_ids(ids)
│   │   │   → DataSerializer.append("Source", node_dict)  ← 追加到 sources.jsonl
│   │   ├── ...（Institution / Topic / Funder / Publisher 同理）
│   │   └── OpenAlexClient.fetch_works_by_ids(ids)  ← 引用的 work
│   │       → DataSerializer.append("Work", node_dict)
│   │
│   └── 2c: 写入 manifest.json
│       ├── session_id / query / 参数
│       └── 各实体类型计数
│
├── Step 3: 从本地缓存读取 → 写库
│   │
│   ├── 3a: 创建约束和索引
│   │
│   ├── 3b: 读取所有实体到内存（此时数据量已固定，可控）
│   │   ├── works = DataSerializer.read("Work")        → list[dict]
│   │   ├── authors = DataSerializer.read("Author")    → list[dict]
│   │   ├── sources = DataSerializer.read("Source")    → list[dict]
│   │   ├── institutions = DataSerializer.read("Institution") → list[dict]
│   │   ├── topics = DataSerializer.read("Topic")      → list[dict]
│   │   ├── funders = DataSerializer.read("Funder")    → list[dict]
│   │   └── publishers = DataSerializer.read("Publisher") → list[dict]
│   │
│   ├── 3c: 批量创建节点
│   │   ├── batch_create_nodes("Work", works, dynamic_label=True, ...)
│   │   ├── batch_create_nodes("Author", authors, ...)
│   │   └── ...（其余 5 种实体）
│   │
│   ├── 3d: 构建关系并批量创建
│   │   ├── 遍历 works + authors → 构建 AUTHORED 关系列表
│   │   ├── 遍历 works + sources → 构建 PUBLISHED_IN 关系列表
│   │   ├── ...（其余 5 种关系）
│   │   └── batch_create_relationships(rels)
│   │
│   └── 3e: 完成会话（更新统计）
│
├── Step 4: 清理
│   ├── 默认：删除 session_id 对应的缓存目录
│   └── 如 --keep-cache：保留缓存并提示路径
│
└── Step 5: 输出导入统计
```

---

## 新增模块：DataSerializer

职责：将实体数据序列化到本地 JSONL 文件，以及反序列化回内存。

```python
class DataSerializer:
    """实体数据序列化器。

    写入模式：每抓取一批实体就追加到对应的 JSONL 文件。
    读取模式：一次将所有实体从 JSONL 文件读入内存。
    """

    # 支持的实体标签
    LABELS = ["Work", "Author", "Institution", "Source",
              "Topic", "Publisher", "Funder"]

    def __init__(self, cache_dir: Path, session_id: str):
        """初始化序列化器。

        Args:
            cache_dir: 缓存根目录（如 ~/.openalex-neo4j/cache/）
            session_id: 当前导入会话 ID
        """
        self.data_dir = cache_dir / session_id
        self.data_dir.mkdir(parents=True, exist_ok=True)

    # ─── 写入 ───

    def append(self, label: str, node_dict: dict) -> None:
        """追加单条实体记录到对应的 JSONL 文件。"""
        file_path = self.data_dir / f"{label.lower()}.jsonl"
        with open(file_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(node_dict, ensure_ascii=False) + "\n")

    def append_batch(self, label: str, nodes: list[dict]) -> None:
        """批量追加实体记录。"""
        file_path = self.data_dir / f"{label.lower()}.jsonl"
        with open(file_path, "a", encoding="utf-8") as f:
            for node in nodes:
                f.write(json.dumps(node, ensure_ascii=False) + "\n")

    def write_manifest(self, metadata: dict) -> None:
        """写入会话清单文件。"""
        file_path = self.data_dir / "manifest.json"
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)

    # ─── 读取 ───

    def read(self, label: str) -> list[dict]:
        """读取某类实体的全部记录。"""
        file_path = self.data_dir / f"{label.lower()}.jsonl"
        if not file_path.exists():
            return []
        records = []
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        return records

    def read_all(self) -> dict[str, list[dict]]:
        """读取所有实体的全部记录，按 label 分组。"""
        return {label: self.read(label) for label in self.LABELS}

    def read_manifest(self) -> dict | None:
        """读取会话清单。"""
        file_path = self.data_dir / "manifest.json"
        if file_path.exists():
            with open(file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return None

    # ─── 查询 ───

    def count(self, label: str) -> int:
        """获取某类实体的记录数（逐行计数，不加载完整内容）。"""
        file_path = self.data_dir / f"{label.lower()}.jsonl"
        if not file_path.exists():
            return 0
        count = 0
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    count += 1
        return count

    # ─── 清理 ───

    def cleanup(self) -> None:
        """删除当前会话的缓存目录。"""
        if self.data_dir.exists():
            import shutil
            shutil.rmtree(self.data_dir)
```

### JSONL 格式示例

```
~/.openalex-neo4j/cache/S20260508_1234_0001/
├── manifest.json
├── works.jsonl
├── authors.jsonl
├── sources.jsonl
├── institutions.jsonl
├── topics.jsonl
├── publishers.jsonl
└── funders.jsonl
```

**manifest.json**:
```json
{
  "session_id": "S20260508_1234_0001",
  "query": "quantum computing",
  "source": "openalex-api",
  "created_at": "2026-05-08T12:34:56.789012",
  "parameters": {
    "limit": 100,
    "expand_depth": 1,
    "from_year": null,
    "to_year": null
  },
  "entity_counts": {
    "Work": 100,
    "Author": 256,
    "Source": 48,
    "Institution": 112,
    "Topic": 30,
    "Publisher": 12,
    "Funder": 8
  }
}
```

**works.jsonl**（每行一个 JSON 对象）:
```jsonl
{"id": "W123456", "title": "Quantum Computing...", "publication_year": 2023, "doi": "10.1038/xxx", "type": "article", "author_ids": ["A789", "A790"], "source_id": "S456", "topic_ids": ["T111"], "referenced_work_ids": ["W999", "W1000"], "import_sessions": ["S20260508_1234_0001"], "first_imported_at": "2026-05-08T12:34:56", "last_imported_at": "2026-05-08T12:34:56", "_label": "Article"}
{"id": "W123457", "title": "Quantum Error Correction...", ...}
```

---

## 路径 B：本地 WoS → OpenAlex 桥接导入

### CLI 命令

```bash
# 基本用法：指定 WoS 目录，由系统自动提取 DOI 并回查 OpenAlex
uv run openalex-neo4j import-wos --dir wos/ --limit 1000

# 指定单个文件
uv run openalex-neo4j import-wos --file wos/1-100/savedrecs.html

# 保留本地缓存（调试用）
uv run openalex-neo4j import-wos --dir wos/ --keep-cache

# 配合 verbose 查看合并细节
uv run openalex-neo4j import-wos --dir wos/ --verbose
```

### 命令选项

| 选项 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `--dir` | path | 二选一 | WoS HTML 目录 |
| `--file` | path | 二选一 | 单个 WoS HTML 文件 |
| `--limit` | int | 1000 | 最大处理文献数 |
| `--cache-dir` | path | `~/.openalex-neo4j/cache/` | 本地缓存根目录 |
| `--keep-cache` | flag | False | 保留缓存 |
| `--skip-abstracts` | flag | False | 跳过摘要 |
| `--generate-embeddings` | flag | False | 生成嵌入向量 |
| `--tag` | str | None | 会话标签 |
| `--skip-constraints` | flag | False | 跳过约束创建 |

### 详细执行流程

```
import_wos_data()
│
├── Step 1: 解析 WoS
│   ├── wos_parser.scan_directory(dir) → 找到所有 savedrecs.html
│   ├── wos_parser.parse(file) → list[dict]（WoS 原始字段）
│   └── 应用 limit 截断
│
├── Step 2: 提取 DOI
│   ├── wos_parser.extract_dois(records) → ["10.1016/xxx", ...]
│   └── 去重后得到待查询的 DOI 列表
│
├── Step 3: OpenAlex API 回查 → 写入本地缓存
│   ├── 遍历 DOI 列表
│   ├── fetch_by_doi(doi) → DataRecord | None
│   │   ├── 查到时：合并 WoS + OpenAlex → DataSerializer.append("Work", ...)
│   │   └── 查不到时：仅用 WoS 数据 → 生成自定义 ID → 记录日志
│   └── DataSerializer 逐条写入 works.jsonl
│
├── Step 4: 从本地缓存读取 → 写库（与路径 A 的 Step 3 共用）
│   ├── DataSerializer.read_all() → dict[label, list[dict]]
│   ├── 创建约束/索引
│   ├── _import_nodes() → batch_create_nodes()
│   └── _import_relationships() → batch_create_relationships()
│
├── Step 5: 清理
│   └── 默认删除缓存，--keep-cache 保留
│
└── Step 6: 输出导入统计
```

### 字段合并规则

#### 情况 A：OpenAlex 回查成功（含 OpenAlex ID）

```
WoS 数据                          OpenAlex 数据                 合并结果
─────                              ──────────                   ──────
id (无)                            id: "W123456"                id: "W123456" ★
title: "From hard tissues..."      title: "From hard tissues..." title: "From hard tissues..."
abstract: "..."                    abstract: null                abstract: "..." (WoS 补) ★
keywords: ["strontium", ...]       (无 keywords 字段)            wos_keywords: ["strontium", ...] ★
volume: "49"                       volume: null                  volume: "49" (WoS 补)
times_cited: 27                    cited_by_count: 35            cited_by_count: 35 (OpenAlex)
                                                                 wos_times_cited: 27 (WoS 独有) ★
author_ids (无)                    author_ids: ["A123"]          author_ids: ["A123"] ★
```

#### 情况 B：OpenAlex 回查失败（无 OpenAlex ID）

```
WoS 数据                          合并结果
─────                              ──────
title: "..."                      id: 自定义（如 "WOS-{doi_hash}"）
doi: "10.1016/xxx"                doi: "10.1016/xxx"
abstract: "..."                   abstract: "..."
keywords: "..."                   wos_keywords: "..."
                                  ⚠ 无 OpenAlex ID，无法与现有数据去重
                                  ⚠ 无 author_ids，无法建立 AUTHORED 关系
```

```python
# 没有 OpenAlex ID 时，基于 DOI 哈希生成命名空间 ID（保证幂等）
node_id = f"WOS-{hash(doi) % 10**8:08d}"
```

---

## 新增模块：wos_parser.py

职责：解析 WoS HTML、提取结构化数据。

```python
class WosParser:
    """WoS HTML saved records 解析器。"""

    def __init__(self, file_path: Path | str):
        self.path = Path(file_path)

    def parse(self) -> list[dict]:
        """解析 HTML 文件，返回记录列表。

        每条记录包含：
            - title: str
            - authors: list[{"full_name", "abbr_name"}]
            - doi: str | None
            - source: str
            - volume: str | None
            - issue: str | None
            - pages: str | None
            - published_date: str
            - abstract: str | None
            - keywords: list[str]
            - times_cited: int
            - usage_180d: int
            - usage_since2013: int
            - addresses: list[str]
            - publisher: str
            - publisher_address: str | None
            - funding: str | None
            - issn: str | None
        """
        ...

    @staticmethod
    def scan_directory(directory: Path) -> list[Path]:
        """扫描目录下所有 savedrecs.html 文件。"""
        ...

    @staticmethod
    def extract_dois(records: list[dict]) -> list[str]:
        """从记录列表中提取所有有效的 DOI，去重。"""
        ...
```

---

## importer.py 的修改

```python
class OpenAlexImporter:
    """Orchestrates import of OpenAlex / WoS data into Neo4j."""

    def __init__(self, neo4j_client, openalex_client, session_manager=None):
        """初始化。不再在内存中缓存实体数据。"""
        self.neo4j = neo4j_client
        self.openalex = openalex_client
        self.session_manager = session_manager
        self.current_session: str | None = None
        self.serializer: DataSerializer | None = None  # 新增

    def import_from_query(self, query, limit=100, ..., cache_dir=None, keep_cache=False):
        """路径 A：从 OpenAlex API 抓取 → 本地缓存 → 写库。

        流程：
        1. 抓取阶段：每获取一批实体，立即通过 DataSerializer 写入 JSONL
        2. 导入阶段：从 JSONL 读取全部实体，统一写库
        3. 清理阶段：默认删除缓存
        """
        # 初始化会话和缓存目录
        session_obj = self.session_manager.create_session(...)
        self.current_session = session_obj.id
        self.serializer = DataSerializer(cache_dir, self.current_session)

        # ─── 抓取阶段：OpenAlex API → DataSerializer → JSONL ───

        # 搜索初始 Works
        initial_works = self.openalex.search_works(query, limit, ...)
        self._save_works_batch(initial_works)

        # 扩展关系
        for depth in range(expand_depth):
            self._expand_and_save_relationships()

        # 写入 manifest
        self.serializer.write_manifest({...})

        # ─── 导入阶段：JSONL → Neo4j ───

        # 创建约束和索引
        self.neo4j.create_constraints()

        # 读取所有实体到内存
        all_entities = self.serializer.read_all()

        # 批量创建节点
        self._import_nodes_from_dict(all_entities)

        # 批量创建关系
        self._import_relationships_from_dict(all_entities)

        # ─── 清理 ───
        if not keep_cache:
            self.serializer.cleanup()

        return counts

    def _save_works_batch(self, works: list[Work]) -> None:
        """将一批 Work 序列化到本地 JSONL。"""
        nodes = [w.to_node_dict(current_session=self.current_session) for w in works]
        # 注入 session 追踪字段
        ts = datetime.now().isoformat()
        for node in nodes:
            node["current_session"] = self.current_session
            node["current_timestamp"] = ts
        self.serializer.append_batch("Work", nodes)

    def _expand_and_save_relationships(self) -> None:
        """展开关联实体并序列化到本地 JSONL。

        读取已缓存的 works → 提取缺失的 ID → 调 API → 写 JSONL。
        """
        cached_works = self.serializer.read("Work")
        # 分析缺失的 IDs
        ...

        # 调 API 获取
        authors = self.openalex.fetch_authors_by_ids(missing_author_ids)
        author_nodes = [a.to_node_dict(current_session=self.current_session)
                       for a in authors]
        self.serializer.append_batch("Author", author_nodes)
        ...

    def _import_nodes_from_dict(self, entities: dict[str, list[dict]]) -> dict:
        """从字典读取各类型实体的 dict 列表，批量创建节点。"""
        counts = {}
        for label, nodes in entities.items():
            if nodes:
                counts[label.lower()] = self.neo4j.batch_create_nodes(label, nodes)
        return counts

    def _import_relationships_from_dict(self, entities: dict[str, list[dict]]) -> dict:
        """从字典读取实体数据，构建关系并批量创建。"""
        # works = entities["Work"]
        # authors = entities["Author"]
        # ... 构建 AUTHORED / CITES / PUBLISHED_IN 等关系
        ...

    def import_from_wos(self, ..., cache_dir=None, keep_cache=False):
        """路径 B：从 WoS 文件抓取 → 本地缓存 → 写库。"""
        # 解析 WoS → 回查 OpenAlex → DataSerializer 写入 JSONL
        # → 共用 _import_nodes_from_dict + _import_relationships_from_dict
        ...
```

---

## 两条路径的共用组件

| 组件 | 用途 |
|---|---|
| `DataSerializer` | 实体数据 ↔ JSONL 文件（新增） |
| `Neo4jClient` |写库、创建约束/索引、清库 |
| `SessionManager` | 导入会话管理 |
| `models.py` | 7 种实体 dataclass |
| `batch_create_nodes()` | UNWIND + MERGE 批量写节点 |
| `batch_create_relationships()` | UNWIND + MERGE 批量写关系 |
| `_import_nodes_from_dict()` | 从 dict 批量写节点（新增，共用） |
| `_import_relationships_from_dict()` | 从 dict 批量写关系（新增，共用） |

## 两条路径的差异点

| 维度 | 路径 A (import) | 路径 B (import-wos) |
|---|---|---|
| 数据入口 | OpenAlex API 搜索 | 本地 WoS HTML 文件 |
| 数据获取 | 关键词检索 + 关系扩展 | DOI 回查（逐条精确匹配） |
| Work 构建 | `Work.from_openalex()` | WoS 字段 + `Work.from_openalex()` 合并 |
| 关系扩展 | 全量抓取 author/source/topic 等 | 仅对已有 OpenAlex ID 的关系做扩展 |
| WoS 独有字段 | 无 | `wos_keywords`, `wos_times_cited` 等 |
| 缓存内容 | 所有实体类型 | Work 为主 + 部分 Author/Source |
| 数据确定性 | 受 API limit 限制 | 1000 篇确定量 |

---

## 追加指令：恢复导入（resume）

如果导入中途失败（如 Neo4j 断连），且缓存未清理，可以恢复：

```bash
# 查看已有缓存会话
uv run openalex-neo4j import --list-cache

# 恢复指定会话的导入（跳过抓取，直接从缓存写库）
uv run openalex-neo4j import --resume S20260508_1234_0001
```

```python
def import_from_cache(self, session_id: str, cache_dir: Path) -> dict[str, int]:
    """从现有缓存恢复导入，跳过 API 抓取阶段。"""
    self.serializer = DataSerializer(cache_dir, session_id)
    self.current_session = session_id

    manifest = self.serializer.read_manifest()
    if not manifest:
        raise ValueError(f"Cache for session {session_id} not found")

    # 直接跳转到导入阶段
    all_entities = self.serializer.read_all()
    node_counts = self._import_nodes_from_dict(all_entities)
    rel_counts = self._import_relationships_from_dict(all_entities)
    ...
```

---

## 新增/修改文件清单

| 文件 | 操作 | 说明 |
|---|---|---|
| `src/openalex_neo4j/serializer.py` | **新增** | DataSerializer：JSONL 序列化/反序列化 |
| `src/openalex_neo4j/wos_parser.py` | **新增** | WoS HTML 解析器 |
| `src/openalex_neo4j/importer.py` | **修改** | 重构为缓存模式，新增共用方法 |
| `src/openalex_neo4j/cli.py` | **修改** | 新增 import-wos、--cache-dir、--keep-cache、--resume |
| `src/openalex_neo4j/models.py` | **修改** | Work 新增 WoS 独有属性 |
| `tests/test_serializer.py` | **新增** | DataSerializer 单元测试 |
| `tests/test_wos_parser.py` | **新增** | WoS 解析器单元测试 |
| `tests/test_importer.py` | **修改** | 新增缓存模式测试 |

---

## 数据模型扩展（Work）

```python
@dataclass
class Work:
    # ... 现有字段不变 ...

    # WoS 独有字段（可选）
    wos_keywords: list[str] = field(default_factory=list)
    wos_times_cited: int = 0
    wos_usage_180d: int = 0
    wos_usage_since2013: int = 0
    publisher_address: str | None = None
```

---

## CLI 示例汇总

```bash
# ─── 路径 A：OpenAlex 检索导入（本地缓存） ───

# 基本检索（自动使用 ~/.openalex-neo4j/cache/）
uv run openalex-neo4j import --query "machine learning" --limit 100

# 带时间范围
uv run openalex-neo4j import --query "quantum computing" --from-year 2020 --to-year 2024

# 保留缓存（用于调试）
uv run openalex-neo4j import --query "AI" --limit 50 --keep-cache

# 恢复失败导入
uv run openalex-neo4j import --resume S20260508_1234_0001

# 查看缓存列表
uv run openalex-neo4j import --list-cache


# ─── 路径 B：WoS 本地文件导入 ───

# 指定目录
uv run openalex-neo4j import-wos --dir wos/ --limit 1000

# 指定单个文件
uv run openalex-neo4j import-wos --file wos/1-100/savedrecs.html

# 保留缓存
uv run openalex-neo4j import-wos --dir wos/ --keep-cache
```

---

## 注意事项

1. **JSONL 优势**：每行一个独立 JSON，支持逐条追加写入，无需在内存中维护完整数据集。读取时按行加载，性能良好。

2. **抓取阶段的内存占用**：抓取阶段只需维护**已抓取的 ID 集合**（用于去重和识别缺失的关联实体），不需要持有完整的实体对象。显著降低大规模导入的内存压力。

3. **导入阶段的内存占用**：导入阶段需要将全部实体加载到内存以构建关系列表。这是必要的——批量构建关系需要交叉引用（如遍历 works 的 author_ids 判断哪些 author 已存在）。数据量大时可以分批处理。

4. **恢复机制**：如果导入中途失败，只需重新运行 `--resume` 即可从缓存恢复，**不需要重新请求 OpenAlex API**。API 请求是瓶颈，这个优化很有价值。

5. **OpenAlex API 限频**：路径 A 受 OpenAlex API 限频影响。路径 B 按 DOI 回查 1000 篇需要约 1000 次调用，pyalex 默认有请求间隔，约需 1-2 分钟。可考虑 `.filter(doi="id1|id2|id3")` 批量查询优化。

6. **WoS 无 OpenAlex ID 的降级处理**：如果某篇论文回查不到 OpenAlex ID，推荐跳过入库，避免产生无法与现有数据关联的孤立节点。

7. **WoS 引文分期实现**：第一期只导入 Work 和基本关系，第二期再处理 Citied References 的 CITES 关系构建。
