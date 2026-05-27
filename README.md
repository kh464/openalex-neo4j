# OpenAlex to Neo4j 导入工具

将 [OpenAlex](https://openalex.org/) 学术数据导入 [Neo4j](https://neo4j.com/) 图数据库的 Python CLI 工具。支持智能混合搜索、导入会话管理、数据质量验证、多数据源富化和本地 WoS 桥接导入。

## 功能特性

### 数据导入
- 通过自然语言查询从 OpenAlex 搜索学术数据
- 自动创建 Neo4j 约束保证数据完整性
- 使用 Cypher UNWIND 语句高效批量导入
- 可配置深度的关系扩展（关联作者、机构、来源、主题、出版商、资助方）
- 可选嵌入向量生成（用于语义搜索）
- 支持所有主流 OpenAlex 实体类型
- **本地 JSONL 缓存** — 抓取阶段写入缓存，导入完成自动清理，支持断点恢复
- **WoS 桥接** — 解析本地 WoS HTML 文件，通过 DOI 回查 OpenAlex 后导入

### 导入会话管理
- 每次导入自动生成唯一会话 ID
- 查看导入历史记录
- 按会话隔离数据，支持选择性删除
- 为会话添加可读标签

### OpenAlex 查询统计
- 查询关键词匹配的文献总数（零数据拉取，1 秒出结果）
- 支持按年份范围过滤统计

### 混合搜索
- **向量相似度搜索** — 基于句子嵌入（all-MiniLM-L6-v2）
- **全文搜索** — 基于 Lucene FULLTEXT 索引
- **倒数排名融合（RRF）** — 智能合并两种搜索结果
- 可配置向量搜索与全文搜索的权重

### 数据质量
- 导入后自动运行质量检查
- 7 条预置规则（缺失标题、异常年份、缺失摘要、空实体、无效类型、短标题、缺失名称）
- 质量报告查看与汇总

### 多数据源富化
- 基于 DataSource 抽象基类的可扩展架构
- 内置 OpenAlex 回填适配器（按 ID 或 DOI 重新获取缺失字段）
- 可合并策略（fill_null / overwrite）
- 支持按会话或全量数据富化

### 数据库管理
- 清除全部数据
- 查看统计（节点数和关系数）
- 清理孤立数据

## 环境要求

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)（推荐）或 pip
- Neo4j 4.0+（本地或远程实例）

## 安装

### 使用 uv（推荐）

```bash
# 克隆仓库
git clone <repository-url>
cd openalex-neo4j

# 安装依赖（uv 自动管理虚拟环境）
uv sync

# 可选：安装嵌入向量支持（用于语义搜索）
uv sync --extra embeddings

# 或安装开发模式
uv pip install -e ".[dev]"
```

### 使用 pip

```bash
# 克隆仓库
git clone <repository-url>
cd openalex-neo4j

# 创建虚拟环境
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 安装依赖
pip install -e ".[dev]"
```

## 配置

在项目根目录创建 `.env` 文件（或使用环境变量）：

```bash
NEO4J_URI=bolt://localhost:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=password
OPENALEX_EMAIL=your.email@example.com
```

参考 `.env.example` 获取模板。

## 使用指南

```bash
# 查看所有命令
uv run openalex-neo4j --help
```

可用命令：

| 命令 | 用途 |
|------|------|
| `import` | 从 OpenAlex API 搜索并导入数据（支持 `--fetch-only` 仅缓存） |
| `export` | 按节点自定义标签从 Neo4j 导出数据到 JSONL |
| `count` | 查询关键词在 OpenAlex 中的匹配总数 |
| `search` | 在已导入的 Neo4j 图谱中混合搜索 |
| `enrich` | 从多数据源富化缺失字段 |
| `session` / `sessions` | 管理导入会话 |
| `report` | 查看和汇总质量报告 |
| `stats` | 查看数据库统计 |
| `clear` | 清空全部数据 |
| `prune` | 清理孤立节点 |

---

### import — 从 OpenAlex 导入数据

```bash
# 基本导入
uv run openalex-neo4j import --query "人工智能" --limit 50

# 带时间范围
uv run openalex-neo4j import --query "量子计算" --from-year 2020 --to-year 2024

# 按 OpenAlex 文献类型过滤，可重复使用；下面只抓 article 和 review
uv run openalex-neo4j import --query "terrorism" --from-year 1990 --to-year 2003 \
  --type article \
  --type review

# 导入时生成嵌入向量（用于语义搜索）
uv run openalex-neo4j import \
  --query "机器学习伦理" \
  --limit 100 \
  --generate-embeddings

# 导入时跳过摘要（更快、更省空间）
uv run openalex-neo4j import --query "量子计算" --limit 50 --skip-abstracts

# 导入后显示质量报告
uv run openalex-neo4j import --query "自然语言处理" --limit 30 --quality-report

# 导入时自动清洗数据
uv run openalex-neo4j import --query "计算机视觉" --limit 30 --clean auto

# 为导入会话添加标签
uv run openalex-neo4j import --query "深度学习" --limit 50 --tag "nlp-2024"

# 为本次导入的所有节点添加自定义标签属性
uv run openalex-neo4j import --query "graph neural networks" --limit 100 --node-tag "batch-2026q2"

# 为本次导入的所有节点添加多个自定义标签属性
uv run openalex-neo4j import --query "knowledge graph" --limit 100 \
  --node-tag "batch-2026q2" \
  --node-tag "project-alpha"

# 仅抓取到本地缓存，不导入 Neo4j（无需 Neo4j 连接）
uv run openalex-neo4j import --query "regenerative medicine" --limit 100 --from-year 2023 --to-year 2024 --fetch-only

# 保留本地缓存（调试用，默认会自动删除）
uv run openalex-neo4j import --query "数据挖掘" --limit 20 --keep-cache

# 查看已有缓存会话
uv run openalex-neo4j import --query dummy --limit 1 --list-cache

# 从缓存恢复导入（跳过 API 抓取，直接写库）
uv run openalex-neo4j import --resume S20260508_1234_0001
```

当前实现说明：

- 不指定 `--limit` 时，CLI 会持续分页抓取，直到 OpenAlex 不再返回结果。
- `--limit` 可以设置为大于 `10000` 的值；项目本身不再施加 `10000` 条抓取上限。
- 是否展开 `referenced_works` 仅由 `--expand-depth` 控制，不会因为抓取规模过大而自动跳过引用展开。

**import 选项：**

| 选项 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `--query, -q` | str | 必填 | OpenAlex 搜索查询 |
| `--limit, -l` | int | all matching | 最大获取数量；省略时抓取全部匹配结果 |
| `--from-year` | int | — | 起始出版年（含） |
| `--to-year` | int | — | 截止出版年（含） |
| `--type` | str | — | OpenAlex 文献类型过滤，例如 `article` / `review`，可重复使用 |
| `--expand-depth` | int | 1 | 关系扩展深度 |
| `--skip-abstracts` | flag | — | 跳过摘要存储 |
| `--generate-embeddings` | flag | — | 生成嵌入向量 |
| `--quality-report` | flag | — | 导入后运行质量检查 |
| `--clean` | str | off | 数据清洗: off / report / auto |
| `--tag` | str | — | 导入会话标签 |
| `--node-tag` | str | — | 给本次导入的所有节点写入 `import_tags` 属性，可重复使用 |
| `--skip-constraints` | flag | — | 跳过约束创建 |
| `--cache-dir` | path | `~/.openalex-neo4j/cache/` | 本地缓存根目录 |
| `--keep-cache` | flag | — | 保留缓存不删除 |
| `--resume` | str | — | 从缓存会话 ID 恢复导入 |
| `--list-cache` | flag | — | 列出缓存会话 |
| `--neo4j-uri` | str | env | Neo4j 连接地址 |
| `--neo4j-username` | str | env | Neo4j 用户名 |
| `--neo4j-password` | str | env | Neo4j 密码 |
| `--email` | str | env | OpenAlex polite pool 邮箱 |
| `--verbose, -v` | flag | — | 详细日志 |
| `--fetch-only` | flag | — | 仅抓取到本地缓存，跳过 Neo4j 导入 |

---

### export — 按自定义节点标签导出

将带有指定 `import_tags` 的节点导出为 JSONL，每行一个节点属性对象，并附带 `_labels` 字段。

```bash
# 导出某个批次标签下的所有节点
uv run openalex-neo4j export --node-tag "batch-2026q2" --output exports/batch-2026q2.jsonl

# 仅导出指定标签下的 Work 和 Author 节点
uv run openalex-neo4j export \
  --node-tag "project-alpha" \
  --label Work \
  --label Author \
  --output exports/project-alpha-work-author.jsonl
```

导入后也可以直接在 Neo4j 中按标签筛选：

```cypher
MATCH (n)
WHERE "batch-2026q2" IN coalesce(n.import_tags, [])
RETURN labels(n), n.id, n.title
LIMIT 50
```

---

### count — 查询 OpenAlex 匹配总数

不拉取数据，仅返回关键词匹配的文献总数。适合在导入前评估数据规模。

```bash
# 基本查询
uv run openalex-neo4j count --query "machine learning"
# → Matching: 3,932,785

# 按年份过滤
uv run openalex-neo4j count --query "quantum computing" --from-year 2023 --to-year 2024

# 指定 email（使用 polite pool）
uv run openalex-neo4j count --query "cancer research" --email user@example.com
```

**count 选项：**

| 选项 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `--query, -q` | str | 必填 | 搜索查询 |
| `--from-year` | int | — | 起始出版年 |
| `--to-year` | int | — | 截止出版年 |
| `--email` | str | env | OpenAlex polite pool 邮箱 |

---

### search — 在图谱中混合搜索

在已导入的 Neo4j 图谱中进行向量 + 全文混合搜索。

```bash
# 基本搜索
uv run openalex-neo4j search --query "神经网络计算机视觉"

# 自定义权重
uv run openalex-neo4j search \
  --query "transformer 架构" \
  --limit 20 \
  --vector-weight 0.7 \
  --fulltext-weight 0.3
```

**search 选项：**

| 选项 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `--query, -q` | str | 必填 | 自然语言搜索查询 |
| `--limit, -l` | int | 10 | 返回结果数 |
| `--vector-weight` | float | 0.5 | 向量搜索权重 0-1 |
| `--fulltext-weight` | float | 0.5 | 全文搜索权重 0-1 |
| `--rrf-k` | int | 60 | RRF 常量 |
| `--neo4j-uri` | str | env | Neo4j 连接地址 |
| `--neo4j-username` | str | env | Neo4j 用户名 |
| `--neo4j-password` | str | env | Neo4j 密码 |

---

### enrich — 多数据源富化

从其他数据源补充已导入文献的缺失字段。

```bash
# 预览富化效果（dry-run，不写入数据库）
uv run openalex-neo4j enrich --dry-run

# 按会话富化
uv run openalex-neo4j enrich --session S20260508_1234 --datasource openalex --strategy fill_null

# 使用多个数据源按顺序回退
uv run openalex-neo4j enrich \
  --datasource crossref \
  --datasource openalex \
  --strategy overwrite \
  --limit 100

# 限制处理的文献数量
uv run openalex-neo4j enrich --limit 50 --dry-run
```

**enrich 选项：**

| 选项 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `--session` | str | 全部 | 只富化指定会话的数据 |
| `--datasource` | str | openalex | 数据源（可重复使用） |
| `--strategy` | str | fill_null | 合并策略: fill_null / overwrite |
| `--dry-run` | flag | — | 预览不写入 |
| `--limit` | int | — | 最大处理数 |
| `--neo4j-uri` | str | env | Neo4j 连接地址 |
| `--neo4j-username` | str | env | Neo4j 用户名 |
| `--neo4j-password` | str | env | Neo4j 密码 |

---

### session — 会话管理

每次导入自动生成唯一会话 ID。

```bash
# 列出最近会话
uv run openalex-neo4j sessions
uv run openalex-neo4j session list --limit 20

# 查看某个会话详情
uv run openalex-neo4j session show S20260508_1234_0001

# 为会话添加标签
uv run openalex-neo4j session tag S20260508_1234_0001 --name "my-important-import"

# 删除某次导入的数据
# 该会话独有的节点 → 彻底删除；共享节点 → 仅移除会话标记
uv run openalex-neo4j session delete S20260508_1234_0001
```

**session 选项：**

| 选项 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `--limit` | int | 20 | 列出会话数量（list） |
| `--tag / --name` | str | 必填 | 会话标签（tag） |
| `--yes, -y` | flag | — | 跳过确认（delete） |
| `--neo4j-uri` | str | env | Neo4j 连接地址 |
| `--neo4j-username` | str | env | Neo4j 用户名 |
| `--neo4j-password` | str | env | Neo4j 密码 |

---

### report — 质量报告

```bash
# 查看某次导入的质量报告
uv run openalex-neo4j report show S20260508_1234_0001

# 列出有质量报告的会话
uv run openalex-neo4j report list --limit 20
```

---

### stats / clear / prune — 数据库管理

```bash
# 查看数据库统计（各类节点和关系数）
uv run openalex-neo4j stats

# 清除全部数据（需确认）
uv run openalex-neo4j clear
# 免确认清除
uv run openalex-neo4j clear --yes

# 清理孤立节点（import_sessions 为空或缺失的节点）
uv run openalex-neo4j prune
```

---

## 架构

### 导入流程

数据写入分两个阶段：

1. **抓取阶段** — 从 OpenAlex API（或 WoS 文件）获取数据，序列化为 JSONL 格式写入本地缓存目录
2. **导入阶段** — 从本地缓存读取全部实体，批量写入 Neo4j

```
OpenAlex API / WoS HTML
       ↓
  JSONL 本地缓存 (~/.openalex-neo4j/cache/{sid}/)
       ↓
  批量 UNWIND + MERGE → Neo4j
       ↓
  默认自动删除缓存
```

支持 `--resume` 从缓存恢复（跳过 API 抓取，直接写库）。

### 实体类型与关系

```
Work --AUTHORED--> Author
Work --PUBLISHED_IN--> Source
Work --CITES--> Work
Work --HAS_TOPIC--> Topic
Work --FUNDED_BY--> Funder
Author --AFFILIATED_WITH--> Institution
Source --PUBLISHED_BY--> Publisher
```

### 会话数据隔离

每次导入生成的节点带有 `import_sessions` 数组属性，记录了该节点所属的所有导入会话。删除某次会话时：

1. **孤立节点**（仅属于该会话）→ 彻底删除
2. **共享节点**（被多次导入共享）→ 仅从 `import_sessions` 中移除该会话 ID
3. 删除 `:ImportSession` 节点
4. 清理本地会话元数据文件

### 数据源富化架构

```
DataSource (抽象基类)
  ├── name()          — 数据源唯一标识
  ├── fetch_by_doi()  — 按 DOI 获取记录
  ├── fetch_by_openalex_id() — 按 OpenAlex ID 获取记录
  ├── fetch_by_title() — 按标题获取记录（可选）
  ├── confidence()    — 记录可信度评分
  ├── to_openalex_id()— 映射回 OpenAlex ID
  └── batch_fetch()   — 批量获取

DataRecord (标准输出格式)
  ├── source_name, source_confidence
  ├── external_ids, raw_data
  ├── title, abstract, publication_date, doi
  └── authors, source_display_name

merge_record(target, source, strategy)
  ├── fill_null  — 仅填充空字段
  └── overwrite  — 替换现有值（要求 confidence > 0.9）
```

### 搜索性能索引

自动创建的索引类型：

**FULLTEXT 索引**（Lucene 全文搜索）：
- `work_fulltext` — 跨 `Work.title` 和 `Work.abstract` 搜索
  - 使用 `db.index.fulltext.queryNodes()` 查询
  - 支持 Lucene 语法（AND、OR、NOT、通配符、模糊搜索）

**TEXT 索引**（字符串匹配）：
- Work.title、Author.display_name、Institution.display_name
- Source.display_name、Topic.display_name

**常规索引**（精确匹配和范围查询）：
- Work.doi、Work.publication_year、Work.type、Work.is_oa
- Author.orcid、Institution.ror、Institution.country_code、Source.issn_l

**向量索引**（语义搜索，可选，需 Neo4j 5.11+）：
- `work_embedding_vector` — 384 维 all-MiniLM-L6-v2 嵌入

### 数据质量规则

| 规则 | 严重级别 | 适用实体 | 说明 |
|------|----------|----------|------|
| missing_title | error | Work | 标题缺失或为空 |
| outlier_year | warning | Work | 出版年份超出 [1900, 当前+2] |
| missing_abstract | info | Work | 摘要缺失 |
| missing_display_name | error | Author/Institution/Source/Topic/Publisher/Funder | 显示名称缺失 |
| empty_entity | warning | 所有实体 | 只有 ID，其他字段全部为空 |
| invalid_work_type | warning | Work | 非标准的 OpenAlex 类型 |
| short_title | info | Work | 标题过短（默认 < 10 字符） |

## 测试

项目包含单元测试和集成测试（188+ 测试用例）：

```bash
# 运行所有单元测试
uv run pytest tests/ -v -m "not integration"

# 带覆盖率
uv run pytest --cov=openalex_neo4j tests/ -m "not integration"

# 运行所有测试
uv run pytest tests/ -v
```

## 项目结构

```
openalex-neo4j/
├── src/openalex_neo4j/
│   ├── cli.py                    # CLI 入口（11 个命令）
│   ├── neo4j_client.py           # Neo4j 数据库操作
│   ├── openalex_client.py        # OpenAlex API 数据获取
│   ├── models.py                 # 数据模型（7 种实体 + ImportSession）
│   ├── importer.py               # 导入编排（API + WoS 两条路径）
│   ├── serializer.py             # DataSerializer — JSONL 序列化/反序列化
│   ├── session_manager.py        # 导入会话管理
│   ├── search.py                 # 混合搜索（向量 + 全文 RRF）
│   ├── data_quality.py           # 数据质量校验和清洗
│   ├── embeddings.py             # 嵌入向量生成（可选）
│   ├── wos_parser.py             # 🚧 WoS HTML 解析器
│   ├── datasource/
│   │   ├── __init__.py           # 数据源注册表
│   │   ├── base.py               # DataSource 抽象基类 + merge_record
│   │   └── openalex_impl.py      # OpenAlex 数据源适配器
│   └── neo4j_utils.py            # 工具函数
├── tests/
│   ├── test_cli.py               # CLI 命令测试
│   ├── test_serializer.py        # JSONL 序列化测试
│   ├── test_importer.py          # 导入器测试（含缓存模式）
│   ├── test_neo4j_client.py      # Neo4j 客户端测试
│   ├── test_openalex_client.py   # OpenAlex 客户端测试
│   ├── test_models.py            # 数据模型测试
│   ├── test_session_manager.py   # 会话管理测试
│   ├── test_data_quality.py      # 数据质量测试
│   ├── test_datasource.py        # 数据源适配器测试
│   ├── test_search.py            # 搜索测试
│   ├── test_neo4j_utils.py       # 工具函数测试
│   ├── conftest.py               # 共享测试 Fixture
│   └── integration/              # 集成测试
├── docs/                         # 设计文档和执行文档
├── wos/                          # WoS 原始 HTML 数据（示例）
└── .env.example                  # 环境变量模板
```

## Cypher 查询示例

```cypher
// 按 DOI 查找文献
MATCH (w:Work {doi: "10.1038/nature12373"})
RETURN w.title, w.publication_year

// 查找 2023 年开放获取文献
MATCH (w:Work)
WHERE w.is_oa = true AND w.publication_year = 2023
RETURN w.title, w.doi
LIMIT 10

// 全文搜索（Lucene 语法）
CALL db.index.fulltext.queryNodes("work_fulltext", "quantum AND computing")
YIELD node, score
RETURN node.title, node.publication_year, score
ORDER BY score DESC
LIMIT 20

// 查找某位作者的全部论文
MATCH (a:Author {display_name: "Geoffrey Hinton"})-[:AUTHORED]->(w:Work)
RETURN w.title, w.publication_year
ORDER BY w.publication_year DESC
LIMIT 10

// 向量相似度搜索（需导入时生成嵌入）
MATCH (w:Work {id: "W2741809807"})
CALL db.index.vector.queryNodes("work_embedding_vector", 10, w.embedding)
YIELD node, score
WHERE node <> w
RETURN node.title, node.publication_year, score
ORDER BY score DESC
LIMIT 10

// 查看某次会话导入的数据
MATCH (n)
WHERE "S20260101_120000_0001" IN n.import_sessions
RETURN labels(n) as type, n.id, n.title
LIMIT 50
```

## 许可

MIT

## 参考资源

- [OpenAlex API 文档](https://docs.openalex.org/)
- [PyAlex 库](https://github.com/J535D165/pyalex)
- [Neo4j Python 驱动](https://neo4j.com/docs/python-manual/current/)
- [Cypher 查询语言](https://neo4j.com/docs/cypher-manual/current/)
