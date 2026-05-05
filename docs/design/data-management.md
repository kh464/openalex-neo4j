# OpenAlex-Neo4j 数据管理增强设计文档

## 1. 概述

### 1.1 目标

解决当前项目无法管理多次导入数据的问题，实现：

1. **导入可追溯** — 每次 import 记录查询内容、时间、数据量
2. **数据可隔离** — 能区分"某次导入带来了哪些节点和关系"
3. **按会话清理** — 能删除某次导入的数据而不影响其他导入
4. **可视化概览** — CLI 提供查看数据库状态和导入历史的命令
5. **数据质量保障** — 抓取后校验数据完整性，清洗不合格数据，生成质量报告
6. **多源数据补全** — 通过第二数据源填补缺失字段（摘要、DOI、作者信息等）

### 1.2 非目标

- 不实现 Web UI / GUI
- 不做多用户隔离
- 不做导入数据 diff / 比对

---

## 2. 核心概念：Import Session

### 2.1 Session 定义

每次 `openalex-neo4j import` 创建一个 **ImportSession**，包含：

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | str | 格式 `YYYYMMDD_HHMMSS`，精确到秒 |
| `query` | str | 搜索查询词 |
| `limit` | int | 原始 limit 参数 |
| `expand_depth` | int | 展开深度 |
| `created_at` | datetime | 导入开始时间 |
| `status` | str | `completed` / `failed` |
| `stats` | dict | 节点和关系计数 |

Session 元数据存储在**两个地方**：

- **Neo4j 中**：以 `:ImportSession` 节点持久化，方便在数据库内查询
- **本地文件**：`~/.openalex-neo4j/sessions.json` 作为轻量索引，供 CLI 快速列出历史

### 2.2 节点上的 Session 标记

所有实体节点（Work, Author, Institution, Source, Topic, Publisher, Funder）增加两个属性：

```
(Work {
  id: "W123",
  title: "...",
  import_sessions: ["20260505_143900", "20260505_160000"],  // 新增
  first_imported_at: "2026-05-05T14:39:00",                  // 新增
  last_imported_at: "2026-05-05T16:00:00"                     // 新增
})
```

- **`import_sessions`** (list[str]) — 记录该节点出现在哪些导入中
- **`first_imported_at`** (str) — 首次导入时间
- **`last_imported_at`** (str) — 最近一次导入时间

关系上不直接标记 session，通过两端节点的 session 归属间接推导。

---

## 3. 数据模型变更

### 3.1 新增模型：ImportSession 数据类

```python
@dataclass
class ImportSession:
    id: str                        # "20260505_143900"
    query: str                     # "machine learning"
    limit: int = 100
    expand_depth: int = 1
    created_at: datetime | None = None
    status: str = "completed"      # "completed" | "failed"
    stats: dict[str, int] | None = None  # 节点/关系计数
```

### 3.2 实体模型变更

每个实体（Work, Author 等）增加两个字段：

```python
@dataclass
class Work:
    ...
    import_sessions: list[str] = field(default_factory=list)
    first_imported_at: str | None = None
    last_imported_at: str | None = None
```

在 `to_node_dict()` 中输出：

```python
def to_node_dict(self, current_session: str | None = None) -> dict[str, Any]:
    node_dict = {
        "id": self.id,
        ...
    }
    if current_session:
        node_dict["import_sessions"] = self.import_sessions or [current_session]
        node_dict["first_imported_at"] = self.first_imported_at
        node_dict["last_imported_at"] = self.last_imported_at
    return node_dict
```

---

## 4. 导入流程变更

### 4.1 当前流程（简化）

```
search -> expand -> to_node_dict -> MERGE nodes -> MERGE rels
```

### 4.2 新流程

```
生成 session_id -> search -> expand -> 打 session 标记 -> MERGE nodes (合并 sessions) -> MERGE rels -> 记录 ImportSession 节点
```

关键变化在 **`batch_create_nodes`**：从简单 `MERGE` 变为带 `import_sessions` 合并逻辑：

```cypher
UNWIND $batch AS item
MERGE (n:{label} {id: item.id})
ON CREATE SET
  n += item {.*, import_sessions: null, first_imported_at: null, last_imported_at: null},
  n.import_sessions = [item.current_session],
  n.first_imported_at = item.current_timestamp,
  n.last_imported_at = item.current_timestamp
ON MATCH SET
  n += item {.*, import_sessions: null, first_imported_at: null, last_imported_at: null},
  n.import_sessions =
    CASE WHEN item.current_session IN n.import_sessions
    THEN n.import_sessions
    ELSE n.import_sessions + item.current_session
    END,
  n.last_imported_at = item.current_timestamp
SET n:$(item._label)
```

处理逻辑：

- **ON CREATE**（新节点）：`import_sessions = [session_id]`
- **ON MATCH**（已有节点）：`import_sessions` append 新 session（重复不添加）

### 4.3 动态标签处理

Work 节点的 `_label` 动态标签仍需要保留。在 `{.*, import_sessions: null, ...}` 中排除掉这些辅助字段，避免它们被作为属性写入节点。

需要从 node dict 中排除写入的字段：
- `current_session` — 仅用于 MERGE 条件，不写入属性
- `current_timestamp` — 仅用于 MERGE 条件，不写入属性
- `_label` — 已经通过 `n:$(item._label)` 单独处理

排除方式：在 SET n += item 时使用 `item {.*, _label: null, current_session: null, current_timestamp: null}`。

### 4.4 Relationship 的 Session 归属

关系不直接加 session 属性。删除某次导入时，通过关系两端节点的 `import_sessions` 交集判断：

- 一条关系 `(A)-[:CITES]->(B)` 在删除 session S 时被删除，**当且仅当** S 从 A 和 B 中都移除后，两者之一不再有任何 session（即 S 是某个节点的最后一个 session）

更精确地说：删除 session S 时，对于每条关系 `(A)-[r]->(B)`：
- 如果 A 或 B 在移除 S 后 `import_sessions` 为空，该关系会被 A 或 B 的级联删除自动带走
- 如果 A 和 B 都还有其他 session，关系保留

---

## 5. 删除策略

### 5.1 节点删除规则

采用**严格删除**策略：

1. 找到所有 `import_sessions` 仅包含 S 的节点 → 直接删除（孤立节点）
2. 找到 `import_sessions` 包含 S 的节点 → 从 `import_sessions` 中移除 S

```cypher
// Step 1: 删除仅属于该 session 的孤立节点
MATCH (n)
WHERE n.import_sessions = [$session_id]
DETACH DELETE n

// Step 2: 删除 ImportSession 节点自身
MATCH (n:ImportSession {id: $session_id})
DELETE n

// Step 3: 从共享节点移除 session 标记
MATCH (n)
WHERE $session_id IN n.import_sessions
SET n.import_sessions = [s IN n.import_sessions WHERE s <> $session_id]
```

### 5.2 孤立节点清理

反复导入/删除后，可能出现没有 `import_sessions` 的节点（由于直接 Cypher 操作）。提供一个 `prune` 命令清理：

```
openalex-neo4j prune
```

找出所有 `import_sessions` 为空或不存在的节点并删除。

---

## 6. CLI 命令设计

### 6.1 import 命令变更

新增参数：

```
openalex-neo4j import [OPTIONS]
  --tag TEXT                  可选的导入标签别名（如 "my-test-import"），便于记忆
  --no-track                  不记录 session 信息（还原旧行为）
  --skip-constraints          跳过创建约束和索引（加快重复导入）
```

behavior 变化：
- 自动生成 session ID 并写入节点
- 首次导入/新增实体类型时才创建约束和索引（`--skip-constraints` 可以明确跳过）
- 输出末尾显示 session ID

### 6.2 新增管理命令

#### `sessions` — 查看导入历史

```
openalex-neo4j sessions [OPTIONS]
  --limit INTEGER   默认 20
  --neo4j-uri TEXT
  --neo4j-username TEXT
  --neo4j-password TEXT
```

输出示例：

```
 Session ID         | Query                | Works | Status   | Time
--------------------+----------------------+-------+----------+--------------------
 20260505_143900    | machine learning     |   404 | completed| 2026-05-05 14:39:00
 20260505_160000    | regenerative medicine|   312 | completed| 2026-05-05 16:00:00
```

#### `session show` — 查看单次导入详情

```
openalex-neo4j session show <session_id> [OPTIONS]
```

输出该次导入包含的所有数据概览，包括各类节点数量、前 10 篇论文标题等。

#### `session delete` — 删除某次导入

```
openalex-neo4j session delete <session_id> [OPTIONS]
  --yes / -y     跳过确认
```

执行严格删除策略（见 5.1 节）。

#### `session tag` — 给 session 添加别名

```
openalex-neo4j session tag <session_id> --name <tag_name>
```

方便记忆，后续可以通过 tag 引用 session。

#### `stats` — 数据库统计

```
openalex-neo4j stats [OPTIONS]
```

输出当前数据库中各类节点和关系的计数。

#### `prune` — 清理孤立数据

```
openalex-neo4j prune [OPTIONS]
  --yes / -y     跳过确认
```

删除所有没有 `import_sessions` 属性或 `import_sessions` 为空数组的节点。

#### `clear` — 清空全部

```
openalex-neo4j clear [OPTIONS]
  --yes / -y     跳过确认
```

清空整个数据库。

### 6.3 Neo4j 连接参数

所有管理命令共享 Neo4j 连接参数（复用 import/search 的模式）：

```
  --neo4j-uri TEXT      默认: bolt://localhost:7687
  --neo4j-username TEXT 默认: neo4j
  --neo4j-password TEXT 必须
```

这些参数默认从环境变量 `NEO4J_URI`、`NEO4J_USERNAME`、`NEO4J_PASSWORD` 读取，与现有命令一致。

---

## 7. 后端架构变更

### 7.1 文件结构

```
src/openalex_neo4j/
  __init__.py
  cli.py                  ← 新增管理命令
  models.py               ← 新增 ImportSession 数据类 + 实体增加 session 字段 + 质量报告数据类
  session_manager.py      ← 新增：session 元数据读写与删除逻辑
  neo4j_client.py         ← 修改：batch_create_nodes 支持 session 标记
  openalex_client.py      ← 不变
  importer.py             ← 修改：集成 session 生命周期 + 数据清洗管道
  data_quality.py         ← 新增：数据校验规则、质量报告生成
  search.py               ← 不变
  embeddings.py           ← 不变
  datasource/             ← 新增：数据源适配器
    __init__.py
    base.py               ← DataSource 抽象基类 + DataRecord 标准格式
    openalex_impl.py      ← OpenAlex 自身作为数据源的适配（用于二次补全）
    crossref.py           ← Crossref 数据源适配器（示例实现）
    semantic_scholar.py   ← Semantic Scholar 数据源适配器（示例实现）
```

### 7.2 SessionManager 类

`session_manager.py` 新增，职责：

```python
class SessionManager:
    """Manages import session metadata in Neo4j and local storage."""

    SESSIONS_DIR = Path.home() / ".openalex-neo4j"
    SESSIONS_FILE = SESSIONS_DIR / "sessions.json"

    def __init__(self, neo4j_client: Neo4jClient):
        self.neo4j = neo4j_client
        self._local_sessions: dict[str, dict] = {}
        self._load_local()

    # --- 本地文件操作 ---
    def _load_local(self) -> None: ...
    def _save_local(self) -> None: ...

    # --- Session 生命周期 ---
    def create_session(self, query: str, limit: int,
                       expand_depth: int, tag: str | None = None) -> ImportSession:
        """生成 session ID，写入 Neo4j 和本地文件."""

    def complete_session(self, session_id: str, stats: dict[str, int]) -> None:
        """标记 session 为 completed，更新统计."""

    def fail_session(self, session_id: str) -> None:
        """标记 session 为 failed."""

    # --- 查询 ---
    def get_session(self, session_id: str) -> ImportSession | None: ...
    def list_sessions(self, limit: int = 20) -> list[ImportSession]: ...
    def get_session_nodes(self, session_id: str) -> dict[str, int]:
        """统计某 session 关联的各类型节点数量."""

    # --- 删除 ---
    def delete_session(self, session_id: str) -> dict[str, int]:
        """执行严格删除，返回删除计数."""
```

### 7.3 本地存储

`~/.openalex-neo4j/sessions.json` 结构：

```json
{
  "20260505_143900": {
    "query": "machine learning",
    "limit": 10,
    "expand_depth": 1,
    "created_at": "2026-05-05T14:39:00",
    "status": "completed",
    "stats": { "works": 404, "authors": 25, "institutions": 16, "sources": 6, "topics": 15, "publishers": 5, "funders": 0 },
    "tag": null
  }
}
```

Neo4j 中对应 `:ImportSession` 节点：

```cypher
(:ImportSession {
  id: "20260505_143900",
  query: "machine learning",
  limit: 10,
  expand_depth: 1,
  created_at: "2026-05-05T14:39:00",
  status: "completed"
})
```

不考虑在 ImportSession 和实体节点间创建关系（`(Session)-[:IMPORTED]->(Work)`），因为连接数过大，代价高且收益有限。用节点的 `import_sessions` 属性反向查询即可。

---

## 8. 兼容性

### 8.1 已有数据

升级后，数据库中已有的节点没有 `import_sessions` 属性。处理方式：

- `batch_create_nodes` 的 `ON MATCH` 分支使用 `coalesce(n.import_sessions, [])` 处理 `import_sessions` 不存在的情况
- `session delete` 忽略没有 session 标记的节点
- `prune` 将无 session 标记的节点视为"未管理"状态，可选择性地清理

### 8.2 向后兼容选项

- `--no-track` 参数恢复旧行为（不写 session 信息，纯 MERGE）
- 不修改现有数据类序列化兼容性（session 字段有默认值）
- 不修改现有索引和约束
- search 模块无需修改，不受 session 影响

---

## 9. 风险与注意事项

1. **MERGE 性能**：`ON MATCH` 分支多了 `CASE` 判断和列表操作，批量导入性能略有下降。需要通过基准测试确认影响幅度。

2. **列表膨胀**：一个节点被导入数十次后 `import_sessions` 列表会很长。Neo4j 对数组属性有高效支持；如果确实成为问题，可设置上限（保留最近 20 个 session）。

3. **并发导入**：暂不考虑并发写。如果用户同时运行两个 import，`sessions.json` 本地文件可能冲突。文档说明不要并发运行。

4. **删除后的数据完整性**：删除 session S 后，如果 S 的某些引用节点被其他 session 保留，而引用关系被删除，查询结果会缺少连接。这在图数据库中是可接受的行为。

5. **删除操作的完整性**：删除 session 时没有事务包裹（跨多个 Cypher 语句），如果中途失败可能留下不一致状态。考虑在 session delete 中使用 Neo4j 事务。

---

## 10. 实施建议顺序

| 阶段 | 内容 | 涉及文件 |
|------|------|----------|
| 1 | `models.py` 新增 `ImportSession` 数据类 + 实体增加 session 字段 + 质量报告数据类 | models.py |
| 2 | `neo4j_client.py` `batch_create_nodes` 增加 session 参数和 `ON CREATE/ON MATCH` 逻辑 | neo4j_client.py |
| 3 | `session_manager.py` 新建，实现 CRUD | 新文件 |
| 4 | `importer.py` 集成 session 生命周期 + 数据清洗管道 | importer.py |
| 5 | `data_quality.py` 新建：校验规则与质量报告 | 新文件 |
| 6 | `datasource/` 新建：抽象基类 + OpenAlex 自身适配器 | 新目录 |
| 7 | `datasource/crossref.py` 和 `datasource/semantic_scholar.py` 适配器实现 | 新文件 |
| 8 | `cli.py` 新增所有管理命令（sessions, session, stats, prune, clear 等） | cli.py |
| 9 | CLI 新增质量报告查看命令 | cli.py |
| 10 | 测试和文档 | tests/ |

---

## 11. 附录：Cypher 查询参考

### 查看某 session 有哪些 Work

```cypher
MATCH (w:Work)
WHERE "20260505_143900" IN w.import_sessions
RETURN w.id, w.title, w.publication_year
LIMIT 50
```

### 查看某 session 有哪些 Author

```cypher
MATCH (a:Author)
WHERE "20260505_143900" IN a.import_sessions
RETURN a.id, a.display_name
LIMIT 50
```

### 统计所有 session

```cypher
MATCH (s:ImportSession)
RETURN s.id, s.query, s.status, s.created_at
ORDER BY s.created_at DESC
```

### 查找跨 session 共享的 Work

```cypher
MATCH (w:Work)
WHERE size(w.import_sessions) > 1
RETURN w.id, w.title, w.import_sessions
LIMIT 20
```

### 查找孤立节点（无 session 标记）

```cypher
MATCH (n)
WHERE n.import_sessions IS NULL OR n.import_sessions = []
RETURN labels(n), count(n) as count
```

---

## 12. 数据质量与清洗

### 12.1 问题定义

从 OpenAlex API 抓取的数据存在以下质量问题：

| 问题类型 | 示例 | 频率 |
|---------|------|------|
| 关键字段缺失 | title=None, abstract=None | 常见 |
| 异常年份 | publication_year=1800 或 2099 | 偶发 |
| 类型异常 | type 字段为 unexpected 枚举值 | 罕见 |
| 空壳节点 | 仅有 id，其他字段全空（通常来自引用展开） | 较常见 |
| 字段格式不一致 | DOI 格式不统一（含/不含 https://） | 常见 |

### 12.2 架构

新增 `data_quality.py`，包含三大组件：

```
data_quality.py
  QualityRule          — 单个校验规则（抽象基类）
  RuleCatalog          — 规则注册表，管理所有可用规则
  QualityReport        — 质量报告数据类
  DataQualityPipeline  — 对一批数据执行所有规则，生成报告
```

### 12.3 校验规则体系

每个规则继承抽象基类 `QualityRule`：

```python
class QualityRule(ABC):
    """单个数据质量校验规则."""

    name: str                          # 规则唯一名称，如 "missing_title"
    description: str                   # 人类可读描述
    severity: str                      # "error" | "warning" | "info"
    applies_to: list[str]              # 适用实体类型，如 ["Work", "Author"]

    @abstractmethod
    def check(self, entity: Any) -> RuleViolation | None:
        """校验单个实体。返回违规信息或 None。"""
```

预定义规则清单：

| 规则 | 级别 | 适用实体 | 说明 |
|------|------|---------|------|
| `missing_title` | error | Work | 标题缺失 |
| `missing_display_name` | error | Author, Institution, Source, Topic, Publisher, Funder | 显示名缺失 |
| `outlier_year` | warning | Work | 年份 < 1900 或 > 当前年份+2 |
| `missing_abstract` | info | Work | 摘要为空（仅记录，不视为异常） |
| `missing_doi` | info | Work | DOI 缺失 |
| `empty_entity` | warning | Work, Author, ... | 除 id 外所有字段为空 |
| `invalid_type` | warning | Work | type 不属于已知枚举（article, book-chapter, dataset 等） |
| `short_title` | info | Work | 标题长度 < 10 字符（可能是占位符） |

### 12.4 质量报告数据类

```python
@dataclass
class RuleViolation:
    """单条违规记录。"""
    rule_name: str
    entity_id: str
    entity_type: str        # "Work", "Author" 等
    severity: str           # "error" | "warning" | "info"
    message: str            # 人类可读的描述
    field: str | None       # 违规字段名，如 "title"
    value: Any | None       # 违规时的实际值


@dataclass
class QualityReport:
    """一次数据质量检查的完整报告。"""

    session_id: str                 # 关联的 import session ID
    total_entities: int             # 检查的实体总数
    violations: list[RuleViolation] # 所有违规
    created_at: str                 # 报告生成时间

    # --- 派生指标 ---
    error_count: int = 0
    warning_count: int = 0
    info_count: int = 0

    def by_entity_type(self) -> dict[str, list[RuleViolation]]:
        """按实体类型分组违规。"""

    def by_severity(self) -> dict[str, list[RuleViolation]]:
        """按严重级别分组违规。"""

    def summary(self) -> str:
        """返回人类可读的摘要文本。"""
```

### 12.5 清洗策略

清洗分三个级别，由 CLI 参数控制：

```python
# importer.py 中新增
class CleaningLevel(Enum):
    OFF = "off"           # 不清洗，原样入库（默认，保持兼容）
    REPORT = "report"     # 仅检查并生成报告，不入库时不操作
    AUTO = "auto"         # 自动修复可修复的问题，不可修复的打标签跳过
```

**自动修复规则**（`CleaningLevel.AUTO`）：

| 问题 | 修复策略 |
|------|---------|
| 标题空白/纯空格 | strip() 后若为空设为 None |
| 年份 < 1900 | 设为 None |
| 年份 > 当前年份+2 | 设为 None |
| DOI 含完整 URL | 提取 ID 部分，统一为 `10.xxx/xxx` 格式 |
| 空字符串字段 | 统一转为 None（避免 Neo4j 中空串与 null 不一致） |

**跳过标记**：对于 `empty_entity` 这类不可修复的脏数据，在节点属性中增加 `_quality_issue: "empty_entity"` 标记，使其在搜索和视图中可被过滤。

### 12.6 CLI 集成

#### import 命令新增参数

```
openalex-neo4j import [OPTIONS]
  --clean LEVEL             数据清洗级别: off / report / auto (默认: off)
  --quality-report          导入完成后打印质量报告摘要
```

#### 新增命令

```
openalex-neo4j report <session_id> [OPTIONS]
  查看某次导入的质量报告详情
  --severity TEXT     过滤级别: error, warning, info (可重复)
  --type TEXT         按实体类型过滤

openalex-neo4j report list [OPTIONS]
  列出所有可用的质量报告
```

### 12.7 质量报告存储

质量报告保存为 JSON 文件 `~/.openalex-neo4j/reports/<session_id>.json`，同时在 Neo4j 的 `:ImportSession` 节点上关联报告摘要：

```json
// ~/.openalex-neo4j/reports/20260505_143900.json
{
  "session_id": "20260505_143900",
  "created_at": "2026-05-05T14:39:30",
  "total_entities": 456,
  "summary": {
    "errors": 2,
    "warnings": 15,
    "infos": 40
  },
  "by_type": {
    "Work": { "total": 404, "errors": 2, "warnings": 10, "infos": 35 },
    "Author": { "total": 25, "errors": 0, "warnings": 3, "infos": 3 },
    "Institution": { "total": 16, "errors": 0, "warnings": 1, "infos": 1 },
    "Source": { "total": 6, "errors": 0, "warnings": 1, "infos": 1 },
    "Topic": { "total": 5, "errors": 0, "warnings": 0, "infos": 0 }
  },
  "violations": [
    { "rule": "missing_title", "entity_id": "W999", "entity_type": "Work",
      "severity": "error", "message": "Title is missing", "field": "title", "value": null },
    ...
  ]
}
```

Neo4j 中 `:ImportSession` 节点增加 `quality` 属性：

```cypher
(:ImportSession {
  id: "20260505_143900",
  ...
  quality: { errors: 2, warnings: 15, infos: 40 }
})
```

### 12.8 与 Session 管理的关系

- 质量报告绑定到 session，随 session 创建而触发，随 session 删除而清理
- `--clean auto` 仅作用于当前导入写入的数据，不修改库中已有数据
- `report` 命令可对已完成的任意 session 重新生成报告（重新从 Neo4j 读取数据做校验）

---

## 13. 第二数据源集成

### 13.1 设计目标

OpenAlex 并非所有字段都完整（尤其是摘要和作者机构信息）。第二数据源用于**填补缺失字段**，不替代 OpenAlex 作为主数据源。

使用场景：

1. **导入时即时补全** — 在 `--clean auto` 模式下，发现有缺失字段时自动查询第二数据源
2. **延期补全** — 对已入库的数据运行 `enrich` 命令，指定一个或多个数据源补充
3. **自定义数据源** — 用户可以通过实现 `DataSource` 接口接入自己的数据源

### 13.2 架构

```
datasource/
  __init__.py           ← 导出 DataSource, DataRecord, registry
  base.py               ← 抽象基类 + DataRecord 规范格式
  openalex_impl.py      ← OpenAlex 自身适配器（二次查询补全）
  crossref.py           ← Crossref REST API 适配器
  semantic_scholar.py   ← Semantic Scholar API 适配器
```

**核心接口**：

```python
# datasource/base.py

@dataclass
class DataRecord:
    """第二数据源返回的标准记录格式。

    这是所有数据源适配器必须输出的统一格式。
    字段分为三类：
      - REQUIRED: 必须提供（可为 None，但键必须存在）
      - CONDITIONAL: 按实体类型选择性提供
      - RAW: 原始数据快照，供调试和溯源
    """

    # --- REQUIRED: 标识字段（至少提供一种 ID） ---
    source_name: str                 # 数据源名称，如 "crossref", "semantic_scholar"
    source_confidence: float         # 置信度 0.0 ~ 1.0
    openalex_id: str | None          # OpenAlex ID（如有）
    external_ids: dict[str, str]     # 外部 ID 映射，如 {"doi": "10.xxx/yyy", "pmid": "12345"}

    # --- 文献元数据（适用于 Work） ---
    title: str | None
    abstract: str | None
    publication_date: str | None     # ISO 格式 "2024-01-15" 或 "2024-01" 或 "2024"
    doi: str | None                  # 标准化 DOI
    authors: list[dict] | None       # 作者列表，每项格式见下方
    source_display_name: str | None  # 期刊/会议名称

    # --- 作者元数据（适用于 Author） ---
    display_name: str | None
    orcid: str | None

    # --- 原始数据快照（RAW） ---
    raw_data: dict[str, Any]         # 数据源原始返回的完整 JSON（用于调试）


# 作者条目格式规范
# authors 列表中每项的格式：
# {
#   "name": "John Doe",
#   "orcid": "0000-0000-0000-0000",        # 可选
#   "position": "first",                    # "first" | "middle" | "last" | "corresponding"
#   "affiliations": ["MIT"],                # 可选
# }
```

### 13.3 DataSource 抽象基类

```python
class DataSource(ABC):
    """数据源适配器的抽象基类。"""

    @property
    @abstractmethod
    def name(self) -> str:
        """数据源唯一标识，如 "crossref"、"semantic_scholar" """
        ...

    @abstractmethod
    def fetch_by_doi(self, doi: str) -> DataRecord | None:
        """通过 DOI 查询单条记录。"""
        ...

    @abstractmethod
    def fetch_by_openalex_id(self, openalex_id: str) -> DataRecord | None:
        """通过 OpenAlex ID 查询单条记录。核心方法，用于补全 Work。"""
        ...

    def fetch_by_title(self, title: str, year: int | None = None) -> DataRecord | None:
        """通过标题模糊匹配查询。基类提供默认实现（调用子类的 fetch_by_doi 或 fetch_by_openalex_id），子类可覆盖。"""
        ...

    def batch_fetch(self, queries: list[dict]) -> list[DataRecord | None]:
        """批量查询。基类提供默认实现（串行循环调用），子类可覆盖为并行实现。"""
        ...

    @abstractmethod
    def confidence(self, record: DataRecord) -> float:
        """评估该数据源返回结果的置信度。匹配方式越可靠分数越高。"""
        ...

    @abstractmethod
    def to_openalex_id(self, record: DataRecord) -> str | None:
        """将数据源记录映射回 OpenAlex ID。用于关联到已有节点。"""
        ...
```

### 13.4 数据源注册与路由

```python
# datasource/__init__.py

_datasource_registry: dict[str, type[DataSource]] = {}

def register_datasource(ds_class: type[DataSource]) -> type[DataSource]:
    """装饰器：注册数据源适配器。"""
    ...

def get_datasource(name: str, **config) -> DataSource:
    """获取数据源实例，按名称查找并实例化。"""
    ...

def list_datasources() -> list[str]:
    """列出所有已注册的数据源名称。"""
    ...
```

### 13.5 数据补全流程（Enrichment Pipeline）

```
            +-------------------+
            | 需要补全的节点列表   |
            +-------------------+
                     |
                     v
            +-------------------+
            | 按数据源优先级排序   |   crossref 优先于 semantic_scholar
            +-------------------+
                     |
            +--------+--------+
            |                 |
            v                 v
     +-----------+     +-----------+
     | 查 DOI     |     | 查 Title  |
     +-----------+     +-----------+
            |                 |
            +--------+--------+
                     |
                     v
            +-------------------+
            | 字段级合并策略      |
            | 只填补 NULL 字段，  |
            | 不覆盖已有值       |
            +-------------------+
                     |
                     v
            +-------------------+
            | 写入 Neo4j        |
            | SET n.field = val |
            +-------------------+
```

**字段级合并规则**（`merge_record` 函数）：

```python
def merge_record(target: dict, source: DataRecord, strategy: str = "fill_null") -> dict:
    """将 DataRecord 合并到目标字典。

    Args:
        target: 目标节点属性字典（已有的值优先）
        source: 数据源返回的记录
        strategy: 合并策略
            "fill_null"  - 仅填补 target 中为 None 的字段（默认，最保守）
            "overwrite"  - 用 source 的值覆盖 target（仅在 source_confidence > 0.9 时建议使用）

    Returns:
        合并后的字典
    """
```

### 13.6 DataRecord 与 DataQualityPipeline 的联动

数据补全集成到清洗流程中：

```
from_openalex() 解析
       │
       ▼
QualityReport.check()  →  发现缺失字段
       │
       ▼
EnrichmentPipeline.run()  →  遍历缺失字段，查第二数据源
       │
       ▼
merge_record()  →  填补到实体对象
       │
       ▼
再次执行 QualityReport.check()  →  确认补全效果
```

### 13.7 内置数据源适配器规范

#### Crossref 适配器

| 属性 | 值 |
|------|-----|
| source_name | `crossref` |
| 查询依据 | 优先 DOI，其次标题+年份 |
| 填充字段 | abstract（最核心）、title（标准化）、authors、date |
| 置信度规则 | DOI 精确匹配 = 0.95；标题模糊匹配 = 0.7 |
| API | `https://api.crossref.org/works/{doi}` |
| 速率限制 | 50 req/s（无 API key），可配置 mailto 提升 |

```python
# datasource/crossref.py

class CrossrefSource(DataSource):
    """Crossref REST API 数据源适配器。"""

    def __init__(self, mailto: str | None = None):
        self.mailto = mailto
        self.base_url = "https://api.crossref.org"

    @property
    def name(self) -> str:
        return "crossref"

    def fetch_by_doi(self, doi: str) -> DataRecord | None:
        """GET /works/{doi} → 解析为 DataRecord。"""
        ...

    def fetch_by_openalex_id(self, openalex_id: str) -> DataRecord | None:
        """OpenAlex 自身通常包含 DOI，先用 OpenAlex 的 DOI 转向查询。"""
        ...

    def fetch_by_title(self, title: str, year: int | None = None) -> DataRecord | None:
        """GET /works?query.title={title}&rows=1 模糊匹配。"""
        ...

    def confidence(self, record: DataRecord) -> float:
        # 通过 DOI 查询 → 0.95
        # 通过标题查询 → 0.7
        ...

    def to_openalex_id(self, record: DataRecord) -> str | None:
        # 通过 DOI 反向查询 OpenAlex: https://api.openalex.org/works/doi:{doi}
        ...
```

#### Semantic Scholar 适配器

| 属性 | 值 |
|------|-----|
| source_name | `semantic_scholar` |
| 查询依据 | 优先 DOI/CorpusID，其次标题 |
| 填充字段 | abstract（质量高）、authors、citation count、embedding（如可用） |
| 置信度规则 | DOI 精确匹配 = 0.90；标题精确匹配 = 0.85 |
| API | `https://api.semanticscholar.org/graph/v1/paper/{id}` |
| 速率限制 | 100 req/min（无 key），10000 req/min（有 key） |

```python
# datasource/semantic_scholar.py

class SemanticScholarSource(DataSource):
    """Semantic Scholar API 数据源适配器。"""

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key
        self.base_url = "https://api.semanticscholar.org/graph/v1"

    @property
    def name(self) -> str:
        return "semantic_scholar"

    def fetch_by_doi(self, doi: str) -> DataRecord | None:
        """GET /paper/DOI:{doi}?fields=title,abstract,..."""
        ...

    def fetch_by_openalex_id(self, openalex_id: str) -> DataRecord | None:
        """Semantic Scholar 不支持 OpenAlex ID，回退到 title 查询。"""
        ...

    def fetch_by_title(self, title: str, year: int | None = None) -> DataRecord | None:
        """GET /paper/search?query={title}&limit=1"""
        ...
```

### 13.8 CLI 集成

#### import 命令新增参数

```
openalex-neo4j import [OPTIONS]
  --enrich DATASOURCE        数据补全数据源（可重复使用，按顺序尝试）
                             如: --enrich crossref --enrich semantic_scholar
  --enrich-strategy TEXT     合并策略: fill_null (默认) / overwrite
```

#### 新增 enrich 命令（延期补全）

```
openalex-neo4j enrich [OPTIONS]
  --session TEXT     补全指定 session 内的所有 Work（可省略，省略时补全全部）
  --datasource TEXT  使用的数据源（可重复，按顺序尝试）
  --strategy TEXT    合并策略: fill_null (默认) / overwrite
  --dry-run          仅输出将要补全的字段，不实际写入
  --limit INTEGER    限制补全的 Work 数量
```

执行示例：

```
$ openalex-neo4j enrich --session 20260505_143900 --datasource crossref --dry-run

Enrichment Dry-Run (20260505_143900)
=====================================
Data source: crossref
Strategy: fill_null
Works to enrich: 404
Fields to fill:
  abstract: 312 works missing → 预计可补 ~280
  authors:  45 works with incomplete data
=====================================
Use --dry-run 移除后实际执行。
```

#### datasource 命令

```
openalex-neo4j datasource list
  列出所有已注册的数据源

openalex-neo4j datasource test <name> --id <openalex_id>
  测试某个数据源对指定 ID 的查询效果，返回 DataRecord 内容
```

### 13.9 数据源配置

数据源的 API key、mailto 等配置通过环境变量或 `.env` 文件传入：

```
# .env
CROSSREF_MAILTO=your@email.com
SEMANTIC_SCHOLAR_API_KEY=sk-xxxxx
```

在 `DataSource.__init__()` 中从环境变量读取默认值：

```python
class CrossrefSource(DataSource):
    def __init__(self, mailto: str | None = None):
        self.mailto = mailto or os.getenv("CROSSREF_MAILTO")
```

### 13.10 DataRecord 格式规范总表

以下为 `DataRecord` 所有字段的详细规范，所有数据源适配器的输出必须严格遵循：

| 字段 | 类型 | 必须 | 说明 |
|------|------|------|------|
| `source_name` | str | 是 | 数据源名称，全小写英文 |
| `source_confidence` | float | 是 | 0.0 ~ 1.0，匹配可靠性 |
| `openalex_id` | str \| None | 否 | 如能映射回 OpenAlex ID |
| `external_ids` | dict[str, str] | 是 | 至少包含一个外部 ID，如 `{"doi": "10.1234/abc"}` |
| `title` | str \| None | 否 | 文献标题 |
| `abstract` | str \| None | 否 | 摘要文本 |
| `publication_date` | str \| None | 否 | ISO 格式 |
| `doi` | str \| None | 否 | 标准化 DOI（不含 https://） |
| `authors` | list[dict] \| None | 否 | 见下方作者格式 |
| `source_display_name` | str \| None | 否 | 期刊/会议名称 |
| `display_name` | str \| None | 否 | 作者显示名（用于补全 Author 节点） |
| `orcid` | str \| None | 否 | ORCID（用于补全 Author 节点） |
| `raw_data` | dict | 是 | API 原始返回值 JSON |

**作者条目格式**：

```json
{
  "name": "John Doe",
  "orcid": "0000-0000-0000-0000",
  "position": "first"
}
```

| 字段 | 类型 | 必须 | 说明 |
|------|------|------|------|
| `name` | str | 是 | 作者全名 |
| `orcid` | str \| None | 否 | 标准化 ORCID（含分隔符） |
| `position` | str \| None | 否 | `first` / `last` / `corresponding` / `middle` |
| `affiliations` | list[str] \| None | 否 | 机构名称列表 |

