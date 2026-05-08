# WoS 数据导入策略

将 Web of Science (WoS) 导出的 HTML 数据导入 Neo4j 图数据库的方案设计。

---

## 数据源概况

`wos/` 目录下 10 个 HTML 文件（每个 100 条），共约 **1000 条文献记录**，导出格式为 `savedrecs.html`。

### WoS 可用字段

| 字段 | 覆盖率 | 说明 |
|---|---|---|
| Title | 100% | 论文标题 |
| Author(s) | 100% | 作者全名 + 缩写名 |
| DOI | ~95% | 数字对象标识符 |
| Source | 100% | 期刊/会议名称 |
| Published Date | 100% | 出版日期 |
| Volume / Issue / Pages | ~90% | 卷期页码 |
| Abstract | ~95% | 论文摘要 |
| Author Keywords | ~70% | 作者标注的关键词 |
| Times Cited (WoS) | 100% | WoS 核心合集被引次数 |
| Usage Count | 100% | 180天/2013至今使用量 |
| Cited References | 100% | 参考文献文字列表（含 DOI）|
| Addresses | ~95% | 作者机构全称 + 地址 |
| Publisher | 100% | 出版商名称 + 地址 |
| Funding | ~50% | 资助声明和基金号 |

---

## 方案 B：WoS → OpenAlex ID 桥接（推荐）

### 核心流程

```
WoS HTML (10个文件, 1000条)
    │
    ▼
WosParser.extract_dois()
    │  提取 DOI 列表（去重）
    ▼
OpenAlexClient.fetch_by_doi(doi)
    │  按 DOI 逐条回查 OpenAlex
    ▼
OpenAlex 完整记录
    │  含: OpenAlex ID, Author IDs, Source ID, Topic IDs
    │      引用关系 ID 化, ORCID, ROR
    ▼
merge_record(WoS_data, OpenAlex_data, strategy="fill_null")
    │  WoS 为基础，OpenAlex 补充 ID 类字段
    ▼
batch_create_nodes() + batch_create_relationships()
    │  走现有导入流程
    ▼
Neo4j 图数据库
```

### 步骤详述

#### 1. 解析 WoS HTML

每条记录的结构：

```html
<b>Title:</b>
<value>From hard tissues to beyond: ...</value>

<b>Author(s):</b>
Wang, LY (Wang, Liyun); Jiang, SJ (Jiang, Shengjie); ...

<b>DOI:</b>
<value>10.1016/j.bioactmat.2025.02.039</value>

<b>Cited References:</b>
Abdalla MM, 2022, MATERIALS, V15, DOI 10.3390/ma15175854
<br>...
```

提取策略：用 BeautifulSoup 解析 HTML，通过 `<b>` 标签内容定位各字段，提取 `<value>` 或紧跟的文本。

新增模块：`src/openalex_neo4j/wos_parser.py`

#### 2. 按 DOI 回查 OpenAlex

从 WoS 提取 DOI 后，通过 OpenAlex API 获取完整记录：

```python
# 利用 pyalex 按 DOI 过滤
works = Works().filter(doi="10.1016/xxx").get()
work_data = works[0]  # 完整 OpenAlex 记录
```

为什么是 DOI 而非按标题查？
- DOI 是稳定的跨平台标识，OpenAlex 对 DOI 索引完整，查准率接近 100%
- 按标题查会有匹配歧义问题

#### 3. 字段合并（WoS + OpenAlex）

合并规则（`merge_record`, strategy="fill_null"）：

| 目标属性 | 来源 | 优先级 |
|---|---|---|
| `id` (OpenAlex ID) | OpenAlex | **必须**（用 OpenAlex ID 做 MERGE 去重） |
| `title` | WoS / OpenAlex | 两者一致，取任一方 |
| `doi` | WoS | 两者一致 |
| `abstract` | **WoS 优先** | OpenAlex 约 30-40% 缺失 |
| `publication_year` | OpenAlex | 标准化格式 |
| `author_ids` | OpenAlex | **必须**（用于 AUTHORED 关系） |
| `source_id` | OpenAlex | **必须**（用于 PUBLISHED_IN 关系） |
| `topic_ids` | OpenAlex | **必须**（用于 HAS_TOPIC 关系） |
| `wos_keywords` | WoS 独有 | Work 新属性 |
| `wos_times_cited` | WoS 独有 | Work 新属性 |
| `wos_usage_180d` | WoS 独有 | Work 新属性 |

#### 4. 批量写入

复用现有 `batch_create_nodes()` / `batch_create_relationships()` 流程：

```python
importer = OpenAlexImporter(neo4j_client, openalex_client, session_manager=session_manager)

# 第一步：将 WoS+OpenAlex 合并后的数据写入本地 JSONL 缓存
importer._save_works_batch(merged_works)

# 第二步：扩展关系并写入缓存（抓取关联的 author/source/topic）
importer._expand_and_save_relationships()

# 第三步：从 JSONL 读取并批量写库
all_entities = importer.serializer.read_all()
node_counts = importer._import_nodes_from_dict(all_entities)
rel_counts = importer._import_relationships_from_dict(all_entities)
```

---

## WoS 对 OpenAlex 的字段补充分析

### OpenAlex 容易缺失的字段

| 字段 | OpenAlex 缺失率 | WoS 能否补 | 说明 |
|---|---|---|---|
| **Abstract** | **30-40%** | ✅ 基本都有 | 最大收益点。WoS 摘要完整，可直接填充 |
| **Keyword** | 不存储（Work 无 keywords） | ✅ **Author Keywords** | OpenAlex Work 级别无关键词，WoS 的 Author Keywords 和 Keywords Plus 是独有价值 |
| **Volume / Issue / Pages** | 约 10% 缺失 | ✅ 基本完整 | WoS 对期刊论文的卷期页码记录完整 |
| **Publisher Address** | 不存储 | ✅ 有 Publisher Address | OpenAlex Publisher 只有名称和国家代码，WoS 有详细地址 |
| **Usage Count** | 不存储 | ✅ 独有数据 | WoS 的使用量指标（180天/2013至今）OpenAlex 没有 |
| **Times Cited (WoS)** | 不存储（OpenAlex 有自己的 cited_by_count）| ✅ 独立指标 | 两种引用计数的范围不同，可以并存作为独立属性 |
| **Funding 详情** | 约 20% 缺失基金细分信息 | ✅ 有完整资助声明 | 可以补充 Work 的 funding 相关字段 |
| **机构子单位** | 不存储 | ⚠️ 可补但复杂 | Addresses 含 Dept/Lab/医院信息，需设计额外模型 |
| **Department / Lab** | OpenAlex 没有 | ⚠️ 需要扩展模型 | 如需支持，需新建 SubInstitution 实体类型 |

### OpenAlex 独有、WoS 无法替代的字段

| 字段 | 原因 |
|---|---|
| **OpenAlex ID (`Wxxx`)** | 图数据库的主键，用于 MERGE 去重 |
| **Author ID (`Axxx`)** | 用于 AUTHORED 关系的标识 |
| **Topic (`Txxx`)** | OpenAlex 的主题分类体系 |
| **引用关系的 ID 化** | WoS 的引文只有文字，无法直接关联现有节点 |
| **ORCID / ROR** | 作者/机构的持久标识 |

### 字段补充的代码示例

```python
def merge_wos_openalex(wos_record: dict, oa_record: DataRecord) -> dict:
    """将 WoS 数据与 OpenAlex 数据合并为一个 Work 节点字典。

    Args:
        wos_record: 从 WoS HTML 解析出的字段字典
        oa_record: 从 OpenAlex API 获取的 DataRecord

    Returns:
        合并后的 Work 节点字典
    """
    # 以 OpenAlex 为基础（保证有 ID）
    merged = oa_record.raw_data.copy()

    # WoS 补充 OpenAlex 缺失的字段
    if wos_record.get("abstract") and not merged.get("abstract"):
        merged["abstract"] = wos_record["abstract"]

    if wos_record.get("volume"):
        merged["volume"] = wos_record["volume"]
    if wos_record.get("issue"):
        merged["issue"] = wos_record["issue"]
    if wos_record.get("pages"):
        merged["pages"] = wos_record["pages"]

    # WoS 独有字段，作为新属性写入
    if wos_record.get("keywords"):
        merged["wos_keywords"] = wos_record["keywords"]

    if wos_record.get("times_cited"):
        merged["wos_times_cited"] = wos_record["times_cited"]

    if wos_record.get("usage_180d"):
        merged["wos_usage_180d"] = wos_record["usage_180d"]

    if wos_record.get("publisher_address"):
        merged["publisher_address"] = wos_record["publisher_address"]

    return merged
```

---

## 数据模型扩展

需要为 Work 新增的独有属性：

| 属性 | 类型 | 说明 |
|---|---|---|
| `wos_keywords` | list[str] | WoS 作者关键词 |
| `wos_times_cited` | int | WoS 核心合集被引数 |
| `wos_usage_180d` | int | 近 180 天使用量 |
| `wos_usage_since2013` | int | 2013 年以来使用量 |
| `publisher_address` | str | WoS 出版商地址信息 |

这些字段在 `Work.to_node_dict()` 中按需包含。

---

## 与现有流程的对比

| 维度 | 纯 OpenAlex 导入 | 方案 B (WoS→OpenAlex) |
|---|---|---|
| 数据量 | 受 API limit 限制 | 1000 篇可全量 |
| 数据完整性 | 约 30% 缺摘要 | 摘要基本完整 |
| 关键词 | 无 | 有 Author Keywords |
| 引用网络 | 仅 OpenAlex 索引内的引用 | WoS 完整引用（文字级） |
| ID 体系 | 完整的 OpenAlex ID | 同样完整（通过 API 桥接） |
| 去重 | OpenAlex ID | OpenAlex ID（一致） |
| 实现成本 | 已实现 | 需新增 WosParser |

---

## 文件组织

```
src/openalex_neo4j/
├── wos_parser.py          ← 新增：WoS HTML 解析器
│   ├── parse_file(path) → list[dict]  # 解析单个 HTML 文件
│   ├── extract_dois(records) → list[str]  # 提取 DOI
│   └── extract_keywords(record) → list[str]  # 提取关键词
│
├── models.py              ← 修改：Work 新增 WoS 独有属性
└── importer.py            ← 新增：import_from_wos() 方法
```

CLI 接口示例：

```bash
# 通过 WoS → OpenAlex 桥接导入
uv run openalex-neo4j import-wos --dir wos/ --limit 1000

# 指定 WoS 解析 + OpenAlex 补充
uv run openalex-neo4j import-wos --dir wos/ --supplement openalex --strategy fill_null
```
