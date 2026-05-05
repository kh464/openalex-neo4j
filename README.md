# OpenAlex to Neo4j 导入工具

将 [OpenAlex](https://openalex.org/) 学术数据导入 [Neo4j](https://neo4j.com/) 图数据库的 Python CLI 工具。支持智能混合搜索、导入会话管理、数据质量验证和多数据源富化。

## 功能特性

### 数据导入
- 通过自然语言查询从 OpenAlex 搜索学术数据
- 自动创建 Neo4j 约束保证数据完整性
- 使用 Cypher UNWIND 语句高效批量导入
- 可配置深度的关系扩展（关联作者、机构、来源、主题、出版商、资助方）
- 可选嵌入向量生成（用于语义搜索）
- 支持所有主流 OpenAlex 实体类型

### 导入会话管理
- 每次导入自动生成唯一会话 ID
- 查看导入历史记录
- 按会话隔离数据，支持选择性删除
- 为会话添加可读标签

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

### 导入数据

从 OpenAlex 搜索并导入学术数据到 Neo4j：

```bash
# 基本导入
uv run openalex-neo4j import --query "人工智能" --limit 50

# 导入时生成嵌入向量（用于语义搜索）
uv run openalex-neo4j import \
  --query "机器学习伦理" \
  --limit 100 \
  --generate-embeddings \
  --expand-depth 2

# 导入时跳过摘要（更快、更省空间）
uv run openalex-neo4j import --query "量子计算" --limit 50 --skip-abstracts

# 导入后自动运行质量检查
uv run openalex-neo4j import --query "自然语言处理" --limit 30 --quality-report

# 导入时清理数据（自动修正空字符串、标准化 DOI、清除异常年份）
uv run openalex-neo4j import --query "计算机视觉" --limit 30 --clean

# 为导入会话添加标签
uv run openalex-neo4j import --query "深度学习" --limit 50 --tag "nlp-2024"

# 跳过约束和索引创建（用于已初始化的数据库）
uv run openalex-neo4j import --query "数据挖掘" --limit 20 --skip-constraints
```

**导入选项：**

| 选项 | 说明 |
|------|------|
| `--query, -q` | OpenAlex 搜索查询（必填） |
| `--limit, -l` | 最大获取数量（默认: 100） |
| `--neo4j-uri` | Neo4j 连接地址（环境变量: NEO4J_URI） |
| `--neo4j-username` | Neo4j 用户名（环境变量: NEO4J_USERNAME） |
| `--neo4j-password` | Neo4j 密码（环境变量: NEO4J_PASSWORD） |
| `--email` | OpenAlex polite pool 邮箱（环境变量: OPENALEX_EMAIL） |
| `--expand-depth` | 关系扩展深度（默认: 1） |
| `--skip-abstracts` | 跳过摘要存储 |
| `--generate-embeddings` | 生成嵌入向量（需要安装 embeddings extra） |
| `--quality-report` | 导入后运行质量检查 |
| `--clean` | 导入前清理数据 |
| `--tag` | 为导入会话添加标签 |
| `--skip-constraints` | 跳过约束和索引创建 |
| `--verbose, -v` | 启用详细日志 |

### 搜索知识图谱

混合搜索（向量相似度 + 全文搜索）：

```bash
# 基本搜索
uv run openalex-neo4j search --query "神经网络计算机视觉"

# 自定义权重的搜索
uv run openalex-neo4j search \
  --query "transformer 架构" \
  --limit 20 \
  --vector-weight 0.7 \
  --fulltext-weight 0.3
```

**搜索选项：**

| 选项 | 说明 |
|------|------|
| `--query, -q` | 自然语言搜索查询（必填） |
| `--limit, -l` | 返回结果数（默认: 10） |
| `--neo4j-uri` | Neo4j 连接地址（环境变量: NEO4J_URI） |
| `--neo4j-username` | Neo4j 用户名（环境变量: NEO4J_USERNAME） |
| `--neo4j-password` | Neo4j 密码（环境变量: NEO4J_PASSWORD） |
| `--vector-weight` | 向量搜索权重 0-1（默认: 0.5） |
| `--fulltext-weight` | 全文搜索权重 0-1（默认: 0.5） |
| `--rrf-k` | RRF 常量（默认: 60） |

### 会话管理

每次导入自动生成唯一会话 ID，你可以管理这些会话：

```bash
# 查看会话列表
uv run openalex-neo4j sessions

# 或
uv run openalex-neo4j session list --limit 20

# 查看某个会话详情
uv run openalex-neo4j session show <session_id>

# 为会话添加标签
uv run openalex-neo4j session tag <session_id> --name "my-important-import"

# 删除某次导入的数据（仅删除该次导入的独立节点，共享节点保留）
uv run openalex-neo4j session delete <session_id>
```

### 质量报告

```bash
# 查看某次导入的质量报告
uv run openalex-neo4j report show <session_id>

# 列出有质量报告的会话
uv run openalex-neo4j report list --limit 20
```

### 数据富化

从其他数据源（或 OpenAlex 本身）补充缺失字段：

```bash
# 预览富化效果（dry-run，不写入数据库）
uv run openalex-neo4j enrich --dry-run

# 按会话富化
uv run openalex-neo4j enrich --session <session_id> --datasource openalex --strategy fill_null

# 使用多个数据源按顺序回退
uv run openalex-neo4j enrich \
  --datasource crossref \
  --datasource openalex \
  --strategy overwrite \
  --limit 100

# 限制处理的文献数量
uv run openalex-neo4j enrich --limit 50 --dry-run
```

### 数据库管理

```bash
# 查看统计数据（各类型节点数和关系数）
uv run openalex-neo4j stats

# 清除全部数据（需确认）
uv run openalex-neo4j clear

# 免确认清除
uv run openalex-neo4j clear --yes

# 清理孤立节点（import_sessions 为 null 或空数组）
uv run openalex-neo4j prune
```

## 架构

导入流程分为两个阶段：

1. **节点创建** — 先使用批量 MERGE 操作创建所有实体节点
2. **关系创建** — 所有节点创建完成后建立关系

这种方式确保引用完整性和最佳性能。

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

项目包含单元测试和集成测试：

### 单元测试

```bash
# 运行所有单元测试
uv run pytest tests/ -v -m "not integration"

# 带覆盖率运行
uv run pytest --cov=openalex_neo4j tests/ -m "not integration"
```

### 集成测试

```bash
# 运行所有集成测试（需要 Neo4j + 网络）
uv run pytest tests/integration/ -v

# Neo4j 集成测试（需要运行中的 Neo4j 实例）
uv run pytest tests/integration/test_neo4j_integration.py -v

# OpenAlex API 集成测试（需要网络）
uv run pytest tests/integration/test_openalex_integration.py -v

# 端到端导入测试（需要 Neo4j + 网络）
uv run pytest tests/integration/test_full_import.py -v
```

### 全部测试

```bash
uv run pytest tests/ -v
uv run pytest --cov=openalex_neo4j tests/
```

## 项目结构

```
openalex-neo4j/
├── src/openalex_neo4j/
│   ├── cli.py                    # CLI 接口
│   ├── neo4j_client.py           # Neo4j 数据库操作
│   ├── openalex_client.py        # OpenAlex API 数据获取
│   ├── models.py                 # 数据模型（7 种实体 + ImportSession）
│   ├── importer.py               # 导入编排
│   ├── session_manager.py        # 导入会话管理
│   ├── data_quality.py           # 数据质量校验和清洗
│   ├── search.py                 # 混合搜索（向量 + 全文）
│   ├── datasource/
│   │   ├── __init__.py           # 数据源注册表
│   │   ├── base.py               # DataSource 抽象基类 + DataRecord + merge_record
│   │   └── openalex_impl.py      # OpenAlex 数据源适配器
│   └── neo4j_utils.py            # 工具函数
├── tests/
│   ├── test_cli.py               # CLI 命令测试
│   ├── test_neo4j_client.py      # Neo4j 客户端测试
│   ├── test_openalex_client.py   # OpenAlex 客户端测试
│   ├── test_importer.py          # 导入器测试
│   ├── test_models.py            # 数据模型测试
│   ├── test_session_manager.py   # 会话管理测试
│   ├── test_data_quality.py      # 数据质量测试
│   ├── test_datasource.py        # 数据源适配器测试
│   ├── test_search.py            # 搜索测试
│   ├── test_neo4j_utils.py       # 工具函数测试
│   ├── conftest.py               # 共享测试 Fixture
│   └── integration/
│       ├── conftest.py           # 集成测试 Fixture
│       ├── test_neo4j_integration.py
│       ├── test_openalex_integration.py
│       ├── test_full_import.py
│       └── test_session_integration.py
└── docs/
    └── execution/
        └── data-management.md    # 数据管理功能执行文档
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



一个关键词  共多少篇

是否可以按照时间范围，按照时间范围查询多少篇

