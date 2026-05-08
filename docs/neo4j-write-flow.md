# Neo4j 写入流程

本文档说明 OpenAlex 学术数据是如何从 API 接口最终写入 Neo4j 图数据库的。

## 整体流程

```
┌─ 抓取阶段 ──────────────────────────────────────┐
│  OpenAlex API (JSON)                              │
│      ↓                                            │
│  from_openalex() → to_node_dict()                 │
│      ↓                                            │
│  DataSerializer → 本地 JSONL 缓存文件              │
│  (~/.openalex-neo4j/cache/{session_id}/*.jsonl)   │
└──────────────────────────────────────────────────┘
                         ↓ (抓取完成)
┌─ 导入阶段 ──────────────────────────────────────┐
│  DataSerializer 读取 JSONL → Python dict         │
│      ↓                                            │
│  batch_create_nodes() / batch_create_relationships│
│      ↓                                            │
│  Cypher UNWIND + MERGE  →  Neo4j 节点/关系        │
└──────────────────────────────────────────────────┘
```

数据写入分两个阶段：

1. **节点创建** — 先写入所有实体节点（Work、Author、Institution 等）
2. **关系创建** — 所有节点写入完成后，再建立它们之间的关系

---

## 第一阶段：节点写入

### 1.1 数据转换链路

**OpenAlex API JSON** → `Work.from_openalex(data)` 解析嵌套 JSON：

| API 原始字段 | Python 字段 | Neo4j 属性 |
|---|---|---|
| `data["id"]` → `"https://openalex.org/W123"` | `work.id = "W123"` | `n.id = "W123"` |
| `data["title"]` | `work.title` | `n.title` |
| `data["publication_year"]` | `work.publication_year` | `n.publication_year` |
| `data["doi"]` | `work.doi` | `n.doi` |
| `data["abstract_inverted_index"]` | 重构成 `work.abstract` | `n.abstract` |
| `data["authorships"][]["author"]["id"]` | `work.author_ids = ["A1", "A2"]` | （不直接存，用于建关系）|

解析后通过 `to_node_dict()` 转为纯字典，并经 `DataSerializer` 写入 **本地 JSONL 文件缓存**（`~/.openalex-neo4j/cache/{session_id}/`）：

```python
# 抓取阶段：写 JSONL
self.serializer.append_batch("Work", [node_dict, ...])

# 导入阶段：读 JSONL
all_entities = self.serializer.read_all()
# → {"Work": [...], "Author": [...], ...}
```

每种实体类型的数据保存为独立的 `.jsonl` 文件，抓取阶段逐批追加，抓取完成后再一次性读取并写库。

### 1.2 JSONL → 字典（to_node_dict）

导入阶段从 JSONL 读取后，逐条调用 `to_node_dict()`（在抓取阶段已执行，dict 已在 JSONL 中），或直接从 JSONL 解析出的 dict 使用。核心转换方法 `to_node_dict()` 将 dataclass 转为纯字典：

```python
w.to_node_dict(current_session="S20260508_1234_0001")
# → {
#     "id": "W123",
#     "title": "Quantum Computing",
#     "publication_year": 2023,
#     "doi": "https://doi.org/10.xxx",
#     "type": "article",
#     "import_sessions": ["S20260508_1234_0001"],
#     "_label": "Article",       # ← 用于动态标签
#     "embedding": [0.1, 0.2, ...],  # 可选
# }
```

每种实体类型（Work、Author、Source 等）都有各自的 `to_node_dict()` 实现。

### 1.3 Cypher UNWIND + MERGE

`batch_create_nodes()` 将字典列表通过 `UNWIND` 批量发送给 Neo4j：

```cypher
UNWIND $batch AS item
MERGE (n:Work {id: item.id})

// 新节点：创建并设置会话跟踪
ON CREATE SET
  n += item {.*, _label: null, current_session: null, ...},
  n.import_sessions = [item.current_session],
  n.first_imported_at = item.current_timestamp,
  n.last_imported_at = item.current_timestamp

// 已存在节点：只追加会话 ID，不覆盖已有数据
ON MATCH SET
  n += item {.*, _label: null, current_session: null, ...},
  n.import_sessions =
    CASE WHEN item.current_session IN coalesce(n.import_sessions, [])
    THEN n.import_sessions                          // 已包含，不变
    ELSE coalesce(n.import_sessions, []) + item.current_session  // 追加
    END,
  n.last_imported_at = item.current_timestamp

// 动态标签：将 type 字段转为 CamelCase 作为额外标签
SET n:Article
```

**关键行为：**

| 场景 | 行为 |
|------|------|
| 节点不存在 (MERGE) | 创建节点，写入全部属性 |
| 节点已存在 (ON MATCH) | **不覆盖**文本/年份等核心字段，只追加 `import_sessions` 数组和更新 `last_imported_at` |
| 第二次导入同一 work | 该 work 的 `import_sessions` 变为 `["S001", "S002"]` |

### 1.4 分批策略

每 500 个节点一批发送，避免单次事务过大：

```python
batch_size = 500
for i in range(0, len(nodes), batch_size):
    batch = nodes[i:i + batch_size]
    session.run(query, batch=batch)
```

---

## 第二阶段：关系写入

### 2.1 关系类型

| 关系 | 起点 → 终点 | 来源 |
|---|---|---|
| `AUTHORED` | Author → Work | `work.author_ids` |
| `AFFILIATED_WITH` | Author → Institution | `work.institution_ids` |
| `PUBLISHED_IN` | Work → Source | `work.source_id` |
| `CITES` | Work → Work | `work.referenced_work_ids` |
| `HAS_TOPIC` | Work → Topic | `work.topic_ids` |
| `FUNDED_BY` | Work → Funder | `work.funder_ids` |
| `PUBLISHED_BY` | Source → Publisher | `source.publisher_id` |

### 2.2 Cypher UNWIND + MERGE

```cypher
UNWIND $batch AS rel
MATCH (a:Author {id: rel.source_id})
MATCH (b:Work {id: rel.target_id})
MERGE (a)-[r:AUTHORED]->(b)
RETURN count(r) as count
```

同样是批量操作，500 条关系一批。

---

## 约束与索引

节点写入前会创建以下约束和索引：

### 主键约束

```
CREATE CONSTRAINT work_id_unique IF NOT EXISTS FOR (n:Work) REQUIRE n.id IS UNIQUE
CREATE CONSTRAINT author_id_unique IF NOT EXISTS FOR (n:Author) REQUIRE n.id IS UNIQUE
...（所有 7 种实体类型）
```

### 全文索引

```
CREATE FULLTEXT INDEX work_fulltext IF NOT EXISTS
FOR (n:Work) ON EACH [n.title, n.abstract]
```

### 向量索引（可选，Neo4j 5.11+）

```
CREATE VECTOR INDEX work_embedding_vector IF NOT EXISTS
FOR (n:Work) ON (n.embedding)
OPTIONS {indexConfig: { "vector.dimensions": 384, "vector.similarity_function": "cosine" }}
```

---

## 会话隔离机制

每次导入生成唯一会话 ID（如 `S20260508_1234_0001`），通过 `import_sessions` 数组实现数据隔离：

```
Work {id: "W123", import_sessions: ["S001", "S002"], ...}
         ↑                      ↑
      数据本身           属于 S001 和 S002 两次导入
```

删除会话时：
1. 该会话独有的节点（`import_sessions = ["S001"]`）→ DETACH DELETE 彻底删除
2. 共享节点（`import_sessions = ["S001", "S002"]`）→ 只从中移除 `"S001"`
3. 删除 `:ImportSession` 节点

---

## 写入方式总结

| 特性 | 实现方式 |
|---|---|
| 写入方式 | Cypher UNWIND 批量操作 |
| 去重机制 | MERGE + 唯一 ID 约束 |
| 幂等性 | ON MATCH 不覆盖核心字段，仅合并会话 |
| 分批 | 每批 500 条，避免大事务 |
| 隔离 | `import_sessions` 数组标记归属 |
| 动态标签 | `SET n:$(item._label)` 根据 type 加子标签 |
