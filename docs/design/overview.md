# OpenAlex-Neo4j 总体设计文档

## 1. 概述

### 1.1 项目目标

将 [OpenAlex](https://openalex.org/) 学术数据导入 [Neo4j](https://neo4j.com/) 图数据库的命令行工具。提供学术数据的检索、导入、存储、查询、质量管理和多源补全的一站式工作流。

### 1.2 核心能力

- **数据导入** — 从 OpenAlex API 按关键词搜索获取，或从本地 WoS HTML 文件桥接导入
- **图存储** — 7 种实体节点 + 7 种关系，支持 Cypher UNWIND 批量写入
- **混合搜索** — 向量相似度 + Lucene 全文搜索 + RRF 融合
- **会话管理** — 每次导入生成唯一会话，支持按会话隔离和清理
- **数据质量** — 7 条预置校验规则，自动清洗，质量报告
- **多源富化** — 基于 DataSource 抽象基类的可扩展架构，支持 OpenAlex/COSSI/WoS 桥接
- **本地缓存** — JSONL 格式本地文件缓存，支持断点恢复

---

## 2. 系统架构

```
┌──────────────────────────────────────────────────────────┐
│                    CLI (cli.py)                           │
│  import | count | search | enrich | session | report     │
│  stats | clear | prune | import-wos                      │
└────────────────────┬─────────────────────────────────────┘
                     │
┌────────────────────▼─────────────────────────────────────┐
│                OpenAlexImporter (importer.py)              │
│  ┌──────────┐  ┌──────────┐  ┌────────────────────────┐  │
│  │ 路径 A   │  │ 路径 B   │  │ import_from_cache()    │  │
│  │ API检索   │  │ WoS桥接  │  │ (断点恢复)              │  │
│  └────┬─────┘  └────┬─────┘  └────────────────────────┘  │
└───────┼──────────────┼────────────────────────────────────┘
        │              │
┌───────▼──────────────▼────────────────────────────────────┐
│              数据层                                        │
│                                                           │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────────┐ │
│  │ OpenAlex    │  │ DataSerializer│  │ SessionManager  │ │
│  │ Client      │  │ (JSONL 缓存)  │  │ (会话元数据)      │ │
│  └─────────────┘  └──────────────┘  └──────────────────┘ │
│                                                           │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────────┐ │
│  │ Neo4jClient │  │ WosParser    │  │ data_quality     │ │
│  │ (写库/查询)  │  │ (WoS HTML)   │  │ (质量校验)        │ │
│  └─────────────┘  └──────────────┘  └──────────────────┘ │
└──────────────────────────────────────────────────────────┘
```

### 2.1 层次说明

| 层次 | 职责 | 关键模块 |
|---|---|---|
| **CLI 层** | 命令解析、参数校验、用户输出 | `cli.py`（11 个命令） |
| **编排层** | 导入流程编排（抓取→缓存→写库） | `importer.py` |
| **数据获取层** | API 通信、文件解析、限频控制 | `openalex_client.py`, `wos_parser.py`, `rate_limiter.py` |
| **缓存层** | JSONL 序列化/反序列化 | `serializer.py` |
| **存储层** | Neo4j 读写、约束/索引管理 | `neo4j_client.py` |
| **会话层** | Session 生命周期、删除策略 | `session_manager.py` |
| **质量层** | 校验规则、清洗策略 | `data_quality.py` |
| **数据源层** | 多数据源适配 | `datasource/` 子包 |

---

## 3. CLI 命令

| 命令 | 用途 | 状态 |
|---|---|---|
| `import` | OpenAlex API 检索 → JSONL 缓存 → 写库。支持 `--fetch-only` 仅缓存 | ✅ |
| `count` | 查询 OpenAlex 匹配总数（零数据拉取） | ✅ |
| `search` | 在 Neo4j 图谱中混合搜索（向量 + 全文 RRF） | ✅ |
| `enrich` | 从多数据源富化缺失字段（dry-run / fill_null / overwrite） | ✅ |
| `session` / `sessions` | 导入会话管理（list / show / delete / tag） | ✅ |
| `report` | 质量报告查看（show / list） | ✅ |
| `stats` | 数据库节点/关系统计 | ✅ |
| `clear` | 清空全部数据 | ✅ |
| `prune` | 清理孤立节点（无 import_sessions 或为空） | ✅ |
| `import-wos` | WoS HTML → DOI → OpenAlex 桥接 → 写库 | 🚧 |

---

## 4. 数据模型

### 4.1 实体节点

7 种实体类型，统一通过 `to_node_dict(current_session)` 序列化，均含 `import_sessions` / `first_imported_at` / `last_imported_at` 会话追踪字段。

| 实体 | 核心属性 | 主键 |
|---|---|---|
| **Work** | id, title, publication_year, doi, type, abstract, cited_by_count, is_oa, embedding, author_ids, source_id, topic_ids, referenced_work_ids, wos_keywords*, wos_times_cited*, wos_usage_180d* | `id` (OpenAlex ID 如 `W123`) |
| **Author** | id, display_name, orcid, works_count, cited_by_count | `id` (如 `A456`) |
| **Institution** | id, display_name, ror, country_code, type | `id` (如 `I789`) |
| **Source** | id, display_name, issn_l, issn, type, publisher_id | `id` (如 `S321`) |
| **Topic** | id, display_name, description, keywords | `id` (如 `T654`) |
| **Publisher** | id, display_name, country_codes | `id` (如 `P987`) |
| **Funder** | id, display_name, country_code, description | `id` (如 `F147`) |

`*` = WoS 独有字段，仅 WoS 导入时填充

### 4.2 关系类型

| 关系 | 起点 → 终点 | 基数 | 来源字段 |
|---|---|---|---|
| `AUTHORED` | Author → Work | N:M | `work.author_ids` |
| `AFFILIATED_WITH` | Author → Institution | N:M | `work.institution_ids` |
| `PUBLISHED_IN` | Work → Source | N:1 | `work.source_id` |
| `CITES` | Work → Work | N:M | `work.referenced_work_ids` |
| `HAS_TOPIC` | Work → Topic | N:M | `work.topic_ids` |
| `FUNDED_BY` | Work → Funder | N:M | `work.funder_ids` |
| `PUBLISHED_BY` | Source → Publisher | N:1 | `source.publisher_id` |

### 4.3 ImportSession 节点

```cypher
(:ImportSession {
  id: "S20260508_1234_0001",
  query: "machine learning",
  limit: 100,
  expand_depth: 1,
  created_at: "2026-05-08T12:34:56",
  status: "completed"
})
```

会话元数据同时存储在本地 `~/.openalex-neo4j/sessions.json` 作为轻量索引。

---

## 5. 导入流程

### 5.1 共同特征

两条导入路径共用两阶段管线：

```
Phase 1 — 抓取阶段：API/WoS → JSONL 本地缓存
Phase 2 — 导入阶段：JSONL → Neo4j 批量写库
         → 默认清理缓存（--keep-cache 保留）
```

### 5.2 路径 A：OpenAlex API 检索

```
import --query "..."
  │
  ├── 生成 session_id
  ├── search_works() → 初始 Work 列表（limit=None 时拉取全部）
  ├── DataSerializer.append_batch("Work", works)
  │
  ├── expand_depth 循环（并行展开，令牌桶限频）:
  │   ├── 从缓存读取 works → 提取缺失 ID
  │   ├── ThreadPoolExecutor 并行 fetch:
  │   │   ├── fetch_authors_by_ids()
  │   │   ├── fetch_institutions_by_ids()
  │   │   ├── fetch_sources_by_ids() + 链式 publishers
  │   │   ├── fetch_topics_by_ids()
  │   │   ├── fetch_funders_by_ids()
  │   │   └── fetch_works_by_ids()（大导入跳过）
  │   └── 分别写入 JSONL
  │
  ├── 写入 manifest.json
  │
  ├── 可选：skip_abstracts / generate_embeddings（读缓存→改写）
  │
  ├── _import_nodes_streaming() → 按类型逐次:
  │   ├── 读 Work → 写 Neo4j → 释放（收集 ID）
  │   ├── 读 Author → 写 Neo4j → 释放（收集 ID）
  │   ├── 读 Institution → 写 Neo4j → 释放（收集 ID）
  │   ├── 读 Source → 写 Neo4j → 释放（收集 ID）
  │   ├── 读 Topic → 写 Neo4j → 释放（收集 ID）
  │   ├── 读 Publisher → 写 Neo4j → 释放（收集 ID）
  │   └── 读 Funder → 写 Neo4j → 释放（收集 ID）
  │
  ├── _import_relationships_streaming(entity_ids):
  │   ├── 读 Work JSONL（含关系字段）
  │   ├── 配合 ID 集合构建 AUTHORED / CITES / PUBLISHED_IN 等
  │   └── batch_create_relationships() → UNWIND MERGE
  │
  └── 清理缓存
```

### 5.3 路径 B：WoS 桥接（待实现）

```
import-wos --dir wos/
  │
  ├── wos_parser.scan_directory() → WosParser.parse() → WosRecord 列表
  ├── wos_parser.extract_dois() → 去重 DOI 列表
  │
  ├── 逐条回查 OpenAlex API (fetch_by_doi)
  │   ├── 成功 → merge WoS+OpenAlex → Work (含 OpenAlex ID)
  │   └── 失败 → 生成 WOS-{hash} 自定义 ID → 最小 Work
  │
  ├── DataSerializer.append("Work", dict)
  ├── 写 manifest
  └── 共用路径 A 的导入阶段 (_import_nodes_from_dict / _import_relationships_from_dict)
```

---

## 6. JSONL 本地缓存

### 6.1 目录结构

```
~/.openalex-neo4j/cache/{session_id}/
├── manifest.json          ← 会话元信息 + 实体计数
├── work.jsonl             ← 每行一个 Work dict
├── author.jsonl           ← 每行一个 Author dict
├── source.jsonl
├── institution.jsonl
├── topic.jsonl
├── publisher.jsonl
└── funder.jsonl
```

### 6.2 关键设计

- **关系字段保留**：`author_ids`, `source_id`, `referenced_work_ids` 等字段必须写入 JSONL 供关系扩展和关系创建使用，但在写节点前通过 `_REL_FIELDS` 过滤剥离，防止成为 Neo4j 节点属性
- **幂等写入**：MERGE + UNIQUE 约束 + ON MATCH 不覆盖核心字段，仅追加 `import_sessions`
- **断点恢复**：`import --resume <session_id>` 直接从缓存读取写库，跳过 API 抓取

---

## 7. 会话隔离与数据管理

### 7.1 写库逻辑

```cypher
UNWIND $batch AS item
MERGE (n:Work {id: item.id})
ON CREATE SET
  n += item {.*, _label: null, current_session: null, ...},
  n.import_sessions = [item.current_session],
  n.first_imported_at = item.current_timestamp,
  n.last_imported_at = item.current_timestamp
ON MATCH SET
  n += item {.*, _label: null, current_session: null, ...},
  n.import_sessions =
    CASE WHEN item.current_session IN n.import_sessions
    THEN n.import_sessions
    ELSE n.import_sessions + item.current_session
    END,
  n.last_imported_at = item.current_timestamp
SET n:Article  (← dynamic label from _label field)
```

### 7.2 会话删除逻辑

1. 删除仅属于该会话的孤立节点 → `DETACH DELETE`
2. 从共享节点移除该会话 ID
3. 删除 `:ImportSession` 节点
4. 清理本地会话元数据

---

## 8. 数据质量体系

### 8.1 校验规则

| 规则 | 级别 | 实体 | 说明 |
|---|---|---|---|
| missing_title | error | Work | 标题为空 |
| outlier_year | warning | Work | 年份超出 [1900, 当前+2] |
| missing_abstract | info | Work | 摘要缺失 |
| missing_display_name | error | Author/Institution/Source/Topic/Publisher/Funder | 名称缺失 |
| empty_entity | warning | 全部 | 仅 ID 其他全空 |
| invalid_work_type | warning | Work | 非标准类型 |
| short_title | info | Work | 标题 < 10 字符 |

### 8.2 清洗策略

| 级别 | CLI 参数 | 行为 |
|---|---|---|
| off | `--clean off` | 原样入库（默认） |
| report | `--clean report` | 检查并报告，不修改 |
| auto | `--clean auto` | 自动修复（空串→None、异常年份→None、DOI 标准化） |

---

## 9. 多数据源富化

### 9.1 DataSource 抽象基类

```python
class DataSource(ABC):
    name()                     # 数据源标识
    fetch_by_doi()             # 按 DOI 查询
    fetch_by_openalex_id()     # 按 OpenAlex ID 查询
    fetch_by_title()           # 按标题查询（可选）
    confidence()               # 置信度评分 0~1
    to_openalex_id()           # 映射回 OpenAlex ID
    batch_fetch()              # 批量查询（基类提供串行默认实现）
```

### 9.2 已注册数据源

| 数据源 | 模块 | 状态 |
|---|---|---|
| OpenAlex | `datasource/openalex_impl.py` | ✅ |

### 9.3 富化流程

```
enrich --datasource openalex --strategy fill_null
  │
  ├── 查询 Neo4j 获取待富化 Work 列表
  ├── 遍历 Work: fetch_by_doi / fetch_by_openalex_id
  ├── merge_record(target, record, strategy)
  │   ├── fill_null → 仅填充 None 字段
  │   └── overwrite → 替换已有值（需 confidence > 0.9）
  └── SET n.field = val 写入 Neo4j
```

---

## 10. 混合搜索

向量相似度 + 全文搜索 + RRF（倒数排名融合）：

```
search --query "neural networks"
  │
  ├── 向量搜索: db.index.vector.queryNodes("work_embedding_vector", ...)
  ├── 全文搜索: db.index.fulltext.queryNodes("work_fulltext", ...)
  ├── RRF 融合: score = Σ 1/(k + rank(source))
  └── 按融合分排序返回
```

依赖索引：
- `work_fulltext` — FULLTEXT on Work.title + Work.abstract
- `work_embedding_vector` — VECTOR（384 维, cosine, 可选，Neo4j 5.11+）

---

## 11. 关键设计决策

| 决策 | 选择 | 理由 |
|---|---|---|
| 写入方式 | Cypher UNWIND 批量 | 性能优于逐条写入，易于调试 |
| 去重机制 | MERGE + UNIQUE 约束 | Neo4j 原生幂等 |
| 会话隔离 | 节点属性 `import_sessions` 数组 | 无需额外的关联节点，查询高效 |
| 缓存格式 | JSONL | 支持逐行追加，无需完整数据集驻留内存 |
| WoS 识别 | DOI 回查 OpenAlex | DOI 是唯一稳定跨平台标识 |
| WoS 降级 | 自定义 WOS-{hash} ID | 保证幂等，但无法与 OpenAlex 数据关联 |
| 搜索融合 | RRF | 无需调参，优于加权平均 |
| 数据源扩展 | ABC + 注册表 | 降低新增数据源的耦合 |
| API 限频 | 令牌桶（TokenBucket） | 线程安全，支持并行请求时不超过 10 req/s polite pool 限制 |
| 关系展开 | 由 `expand_depth` 控制 | 不再因抓取规模自动跳过 `referenced_works` |
| 内存管理 | 流式逐类型读写 | 替代一次性 `read_all()`，峰值内存从 ~588 MB 降至 ~250 MB |

---

## 12. 存储路径

| 存储 | 路径 | 说明 |
|---|---|---|
| Neo4j 数据库 | 由 `NEO4J_URI` 指定 | 节点数据、关系、ImportSession 节点 |
| 会话元数据 | `~/.openalex-neo4j/sessions.json` | 会话统计、质量报告摘要、标签 |
| JSONL 缓存 | `~/.openalex-neo4j/cache/{sid}/` | 导入中间数据，默认自动删除 |

---

## 13. 实现状态总览

| 模块 | 状态 | 说明 |
|---|---|---|
| CLI 框架 | ✅ | 11 个命令，Click 命令组 |
| 路径 A (import) | ✅ | 含 JSONL 缓存、skip_abstracts、embeddings、fetch-only、流式写入、大导入保护、并行展开 |
| 大规模导入优化 | ✅ | 自动限 depth、令牌桶限频、流式节点创建（`docs/analysis/large-scale-import.md`） |
| `count` 命令 | ✅ | 零数据拉取查询匹配总数 |
| 路径 B (import-wos) | 🚧 | 执行文档已完成，待编码 |
| Neo4j 写库 | ✅ | UNWIND MERGE、约束/索引、动态标签 |
| JSONL 缓存 | ✅ | DataSerializer 完整实现 |
| 会话管理 | ✅ | SessionManager 完整实现 |
| 混合搜索 | ✅ | 向量 + 全文 RRF |
| 数据质量 | ✅ | 7 条规则、3 级清洗策略 |
| 多数据源 | ✅ | DataSource ABC + OpenAlex 适配器 |
| 令牌桶限频器 | ✅ | `rate_limiter.py`，10 req/s 线程安全 |
| WoS 独有字段 | 🚧 | Work 模型待扩展 |
| WoS 解析器 | 🚧 | wos_parser.py 待实现 |
| 文档体系 | ✅ | 设计文档 + 执行文档完整 |

---

## 14. 相关文档

| 文档 | 位置 | 说明 |
|---|---|---|
| 数据管理设计 | `docs/design/data-management.md` | 会话隔离、质量规则、数据源架构 |
| 双路径导入设计 | `docs/dual-import-design.md` | 两条导入路径、DataSerializer |
| WoS 策略设计 | `docs/wos-import-strategy.md` | WoS → OpenAlex 字段映射 |
| Neo4j 写入流程 | `docs/neo4j-write-flow.md` | Cypher 写入原理 |
| JSONL 缓存执行 | `docs/implementation/local-jsonl-cache.md` | ✅ 已完成 |
| WoS 导入执行 | `docs/implementation/wos-import.md` | ⬜ 未完成 |
| 大规模导入分析 | `docs/analysis/large-scale-import.md` | 瓶颈分析 + 优化建议 |
| 大规模导入优化执行 | `docs/implementation/large-scale-optimization.md` | ✅ 已完成 |
| 限频器 | `rate_limiter.py` | 令牌桶算法，10 req/s（OpenAlex polite pool） |
