# WoS 导入功能执行方案

## 概述

基于 `docs/design/dual-import-design.md` 和 `docs/design/wos-import-strategy.md`，实现路径 B：本地 WoS HTML 文件 → OpenAlex API 桥接 → 本地 JSONL 缓存 → Neo4j 图数据库。

> **整体状态：⬜ 未完成**（全部 6 个模块待实现）

## 新增/修改文件清单

| 文件 | 操作 | 说明 | 状态 |
|---|---|---|---|
| `src/openalex_neo4j/wos_parser.py` | **新增** | WoS HTML 解析器 | ⬜ 未完成 |
| `src/openalex_neo4j/models.py` | **修改** | Work 新增 WoS 独有属性 | ⬜ 未完成 |
| `src/openalex_neo4j/importer.py` | **修改** | 新增 `import_from_wos()` 方法 | ⬜ 未完成 |
| `src/openalex_neo4j/cli.py` | **修改** | 新增 `import-wos` 命令 | ⬜ 未完成 |
| `tests/test_wos_parser.py` | **新增** | WoS 解析器单元测试 | ⬜ 未完成 |
| `tests/test_importer.py` | **修改** | 新增 `import_from_wos` 测试 | ⬜ 未完成 |

## 依赖引入

无需新增外部依赖。现有依赖满足需求：
- `beautifulsoup4` — 解析 WoS HTML（已有依赖）
- `pyalex` — OpenAlex API 回查（已有依赖）
- `pathlib` / `json` — 标准库

## 详细实现步骤

### Step 1: models.py — Work 新增 WoS 独有字段 — ⬜ 未完成

在 `Work` dataclass 末尾新增字段：

```python
# WoS 独有字段（可选，仅 WoS 导入时填充）
wos_keywords: list[str] = field(default_factory=list)
wos_times_cited: int = 0
wos_usage_180d: int = 0
wos_usage_since2013: int = 0
publisher_address: str | None = None
```

更新 `to_node_dict()` 方法，当 WoS 字段有值时加入返回的字典。条件判断：

```python
if self.wos_keywords:
    node_dict["wos_keywords"] = self.wos_keywords
if self.wos_times_cited:
    node_dict["wos_times_cited"] = self.wos_times_cited
if self.wos_usage_180d:
    node_dict["wos_usage_180d"] = self.wos_usage_180d
if self.wos_usage_since2013:
    node_dict["wos_usage_since2013"] = self.wos_usage_since2013
if self.publisher_address:
    node_dict["publisher_address"] = self.publisher_address
```

注意：这些字段只应出现在 JSONL 缓存的 dict 中，不应出现在 `_REL_FIELDS` 中（它们不是关系字段，而是节点属性）。

### Step 2: wos_parser.py — WoS HTML 解析器（新增）— ⬜ 未完成

创建 `src/openalex_neo4j/wos_parser.py`，含以下组件：

#### 2.1 `WosRecord` dataclass — ⬜ 未完成

```python
@dataclass
class WosRecord:
    """Single parsed WoS record."""
    title: str | None = None
    authors: list[dict] = field(default_factory=list)  # [{"full_name": ..., "abbr_name": ...}]
    doi: str | None = None
    source: str | None = None         # journal/conference name
    volume: str | None = None
    issue: str | None = None
    pages: str | None = None
    published_date: str | None = None
    abstract: str | None = None
    keywords: list[str] = field(default_factory=list)
    times_cited: int = 0
    usage_180d: int = 0
    usage_since2013: int = 0
    addresses: list[str] = field(default_factory=list)
    publisher: str | None = None
    publisher_address: str | None = None
    funding: str | None = None
    issn: str | None = None
```

#### 2.2 `WosParser` class — ⬜ 未完成

```python
class WosParser:
    """Parse WoS HTML saved-records files."""

    def __init__(self, file_path: Path | str):
        self.path = Path(file_path)

    def parse(self) -> list[WosRecord]:
        """Parse the HTML file into a list of WosRecords.
        
        解析策略：
        1. 用 BeautifulSoup 加载 HTML
        2. 每条记录由 <hr> 或 <table> 分隔（WoS 导出格式每条记录在独立 table 中）
        3. 对每条记录，通过 <b> 标签文本匹配字段名，提取紧随的 <value> 或文本节点
        """

    @staticmethod
    def scan_directory(directory: Path) -> list[Path]:
        """Scan directory for savedrecs.html files, return sorted paths."""

    @staticmethod
    def extract_dois(records: list[WosRecord]) -> list[str]:
        """Extract unique, non-None DOIs from a list of WosRecords."""
```

#### 关键解析规则

| WoS HTML 标记 | 提取目标 | 处理方式 |
|---|---|---|
| `<b>Title:</b>` | 标题 | 取下一个 `<value>` 内容 |
| `<b>Author(s):</b>` | 作者列表 | 按 `; ` 拆分 `Name (Abbr)` 格式 |
| `<b>DOI:</b>` | DOI | 取下一个 `<value>` 内容 |
| `<b>Source:</b>` | 期刊名 | 取下一个 `<value>` 内容 |
| `<b>Published Date:</b>` | 日期 | 取文本节点 |
| `<b>Volume:</b>` | 卷号 | 取下一个 `<value>` 内容 |
| `<b>Issue:</b>` | 期号 | 取下一个 `<value>` 内容 |
| `<b>Pages:</b>` | 页码 | 取下一个 `<value>` 内容 |
| `<b>Abstract:</b>` | 摘要 | 取后续文本直到下一个 `<b>` |
| `<b>Author Keywords:</b>` | 关键词 | 按 `; ` 拆分 |
| `<b>Times Cited:</b>` | 被引次数 | 解析整数 |
| `<b>Usage Count:</b>` | 使用量 | 提取 `180 天` 和 `2013 年以来` 两个值 |
| `<b>Addresses:</b>` | 机构地址 | 按 `; ` 拆分 |
| `<b>Publisher:</b>` | 出版商 | 取文本节点 |
| `<b>Funding:</b>` | 资助声明 | 取文本节点 |
| `<b>ISSN:</b>` | ISSN | 取下一个 `<value>` 内容 |

### Step 3: importer.py — 新增 `import_from_wos()` 方法 — ⬜ 未完成

在 `OpenAlexImporter` 类中新增方法。核心流程：

```python
def import_from_wos(
    self,
    wos_dir: str | Path | None = None,
    wos_file: str | Path | None = None,
    limit: int = 1000,
    cache_dir: str | Path | None = None,
    keep_cache: bool = False,
    skip_abstracts: bool = False,
    generate_embeddings: bool = False,
    tag: str | None = None,
    skip_constraints: bool = False,
) -> dict[str, int]:
    """Path B: WoS HTML → OpenAlex bridge → JSONL cache → Neo4j.

    Args:
        wos_dir: Directory containing savedrecs.html files.
        wos_file: Single WoS HTML file (mutually exclusive with wos_dir).
        limit: Max works to process.
        cache_dir: Local JSONL cache root.
        keep_cache: Keep cache after import.
        skip_abstracts: Strip abstracts.
        generate_embeddings: Generate vector embeddings.
        tag: Session tag.
        skip_constraints: Skip Neo4j constraint/index creation.

    Returns:
        Dict of import counts.
    """
```

#### 3.1 方法内部流程

```
1. 初始化会话和缓存目录
   - 生成 session_id
   - 创建 DataSerializer

2. 解析 WoS HTML → WosRecord 列表
   - wos_parser.scan_directory() / WosParser(file).parse()
   - 应用 limit 截断
   - 应用 skip_abstracts 选项

3. 提取 DOI 并去重
   - WosParser.extract_dois()

4. 逐条回查 OpenAlex API → 合并 → 写入 JSONL
   - OpenAlexSource.fetch_by_doi()
   - 查到 (含 OpenAlex ID):
       调用 Work.from_openalex(data) 创建 Work
       将 WoS 字段填充到 Work 的 wos_* 字段
       → _save_works_batch([work])
   - 查不到:
       生成自定义 ID: f"WOS-{hashlib.sha256(doi.encode()).hexdigest()[:12]}"
       构造最小 Work 对象
       → DataSerializer.append("Work", work_dict)

5. 写入 manifest

6. 导入阶段 (复用现有方法)
   - Neo4j 约束/索引创建
   - _import_nodes_from_dict()
   - _import_relationships_from_dict()

7. 清理缓存 (除非 keep_cache)

8. 返回统计
```

#### 3.2 WoS → Work 转换逻辑（OpenAlex 回查成功）— ⬜ 未完成

```python
def _wos_record_to_work_with_openalex(
    self, wos: WosRecord, oa_record: DataRecord,
) -> Work:
    """Merge WoS record into an OpenAlex Work.

    WoS fills gaps and adds WoS-specific fields to the OpenAlex Work node.
    """
    # OpenAlex data → Work (保证有 OpenAlex ID)
    work = Work.from_openalex(oa_record.raw_data)

    # WoS 补充 OpenAlex 缺失的字段
    if wos.abstract and not work.abstract:
        work.abstract = wos.abstract
    if wos.volume:
        work.volume = wos.volume  # 需要新增 volume/issue/pages 临时属性？不，它们不是 Work 字段
    # volume/issue/pages 作为节点属性处理，但 Work dataclass 没有这些字段
    # 设计文档说明要补充这些字段，但当前 Work 模型不包含它们
    # 第一期暂不处理 volume/issue/pages，只处理 Work 已有的字段

    # WoS 独有字段
    if wos.keywords:
        work.wos_keywords = wos.keywords
    if wos.times_cited:
        work.wos_times_cited = wos.times_cited
    if wos.usage_180d:
        work.wos_usage_180d = wos.usage_180d
    if wos.usage_since2013:
        work.wos_usage_since2013 = wos.usage_since2013
    if wos.publisher_address:
        work.publisher_address = wos.publisher_address

    return work
```

注意：由于 Work dataclass 当前没有 `volume`/`issue`/`pages` 字段，第一期实现中这些字段暂不处理。可以在 JSONL 层通过 `node_dict` 注入（在 `_save_works_batch` 或 `to_node_dict` 中处理），但放在第二期。

#### 3.3 WoS → Work 转换逻辑（OpenAlex 回查失败）— ⬜ 未完成

```python
def _wos_record_to_work_fallback(self, wos: WosRecord) -> Work:
    """Create a minimal Work from WoS data when OpenAlex lookup fails.

    Uses a deterministic hash-based ID derived from DOI or title.
    """
    if wos.doi:
        node_id = f"WOS-{hashlib.sha256(wos.doi.encode()).hexdigest()[:12]}"
    else:
        node_id = f"WOS-{hashlib.sha256(wos.title.encode()).hexdigest()[:12]}" if wos.title else f"WOS-{uuid.uuid4().hex[:12]}"

    return Work(
        id=node_id,
        title=wos.title,
        doi=wos.doi,
        publication_year=self._parse_year(wos.published_date),
        publication_date=wos.published_date,
        abstract=wos.abstract,
        wos_keywords=wos.keywords,
        wos_times_cited=wos.times_cited,
        wos_usage_180d=wos.usage_180d,
        wos_usage_since2013=wos.usage_since2013,
        publisher_address=wos.publisher_address,
    )
```

### Step 4: cli.py — 新增 `import-wos` 命令

在 `cli.py` 中新增 `import-wos` 子命令：

```python
@cli.command(name="import-wos")
@click.option("--dir", "wos_dir", type=click.Path(exists=True, file_okay=False),
              help="Directory containing WoS HTML files")
@click.option("--file", "wos_file", type=click.Path(exists=True, dir_okay=False),
              help="Single WoS HTML file")
@click.option("--limit", default=1000, type=int)
# ... 共用选项：--cache-dir, --keep-cache, --skip-abstracts, 
#     --generate-embeddings, --tag, --skip-constraints,
#     --neo4j-uri, --neo4j-username, --neo4j-password, --email, --verbose
def import_wos_data(...):
    """Import Web of Science data into Neo4j via OpenAlex bridge.

    Parses WoS HTML files, extracts DOIs, looks up full records via
    OpenAlex API, and imports the merged data into Neo4j.
    """
```

选项参数：

| 选项 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `--dir` | path | 与 `--file` 二选一 | WoS HTML 目录 |
| `--file` | path | 与 `--dir` 二选一 | 单个 WoS HTML 文件 |
| `--limit` | int | 1000 | 最大文献数 |
| `--cache-dir` | path | `~/.openalex-neo4j/cache/` | 缓存目录 |
| `--keep-cache` | flag | False | 保留缓存 |
| `--skip-abstracts` | flag | False | 跳过摘要 |
| `--generate-embeddings` | flag | False | 生成向量嵌入 |
| `--tag` | str | None | 会话标签 |
| `--skip-constraints` | flag | False | 跳过约束创建 |
| `--neo4j-uri` | str | env | Neo4j 连接 |
| `--neo4j-username` | str | env | Neo4j 用户名 |
| `--neo4j-password` | str | env | Neo4j 密码 |
| `--email` | str | env | OpenAlex polite pool |
| `--verbose` | flag | False | 详细日志 |

## 测试计划

### 单元测试：test_wos_parser.py（新增）

| 测试用例 | 验证点 |
|---|---|
| `test_parse_basic_record` | 解析一个完整记录的标题/DOI/作者等字段 |
| `test_parse_multiple_records` | 一个文件包含多条记录 |
| `test_extract_dois` | 从记录列表提取 DOI 去重 |
| `test_extract_dois_empty` | 无 DOI 的记录 |
| `test_scan_directory` | 扫描目录找到所有 savedrecs.html |
| `test_parse_missing_fields` | 某些字段缺失不报错 |
| `test_parse_keywords` | Author Keywords 解析 |
| `test_parse_times_cited` | 被引次数解析为整数 |
| `test_parse_usage_count` | 使用量提取 |
| `test_parse_authors` | 作者列表解析 |
| `test_parse_abstract` | 摘要提取 |

测试策略：
- 使用真实的 WoS HTML 片段作为 fixture
- `wos/` 目录下的文件可以抽取片段作为测试数据
- 也可以用字符串构造最小 HTML 片段

### 单元测试：test_importer.py（追加）

| 测试用例 | 验证点 |
|---|---|
| `test_import_from_wos` | 完整 WoS 导入流程 mock 测试 |
| `test_wos_record_to_work_with_openalex` | WoS + OpenAlex 合并逻辑 |
| `test_wos_record_to_work_fallback` | OpenAlex 回查失败的降级处理 |
| `test_import_from_wos_empty_dir` | 空目录处理 |

### 集成测试（手动标记）

| 测试 | 说明 |
|---|---|
| `test_import_wos_real_file` | 选取 `wos/1-100/savedrecs.html` 运行完整流程 |
| `test_import_wos_with_cache` | `--keep-cache` 验证缓存留存 |

## 回滚方案

### 代码回滚

```bash
git revert <commit-hash> --no-edit
git push origin main
```

### 数据回滚

通过会话机制删除导入的数据：

```bash
uv run openalex-neo4j session show <session-id>    # 确认导入范围
uv run openalex-neo4j session delete <session-id>  # 删除该会话的数据
```

## 分阶段实施

### 第一期（当前实现范围）— ⬜ 未完成

| 模块 | 范围 |
|---|---|
| `wos_parser.py` | 完整实现（含所有字段解析） |
| `models.py` | 新增 WoS 5 个字段 |
| `importer.py` | `import_from_wos()` 完整流程 |
| `cli.py` | `import-wos` 命令 |
| 测试 | 全部单元测试 |

第一期输出：`uv run openalex-neo4j import-wos --dir wos/ --limit 1000` 能完整跑通，Work 节点含 WoS 独有字段。

### 第二期（后续规划）

| 功能 | 说明 |
|---|---|
| `volume`/`issue`/`pages` | 补充 Work 节点属性 |
| Cited References 的 CITES 关系 | 基于 WoS 引文文字构建引用边 |
| 引文 DOI 解析 | 从 WoS 引用文字中提取 DOI 并关联已有节点 |
| Addresses → SubInstitution | 机构子单位模型扩展 |

## 风险与缓解

| 风险 | 影响 | 缓解措施 |
|---|---|---|
| WoS HTML 格式变化 | 解析失败 | 解析器加异常捕获，跳过错格式的记录 |
| DOI 回查全部失败 | 无 OpenAlex ID | 降级为自定义 ID，写入时有日志 |
| 大量 DOI 回查耗时 | 1000 次约 1-2 分钟 | 考虑批量 DOI filter 优化（`Works().filter(doi="id1\|id2\|id3")`） |
| WoS 文件编码 | 中文乱码 | 使用 `utf-8` 编码读取，BS4 自动检测 |
