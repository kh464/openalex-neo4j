# CLAUDE.md — openalex-neo4j

## 项目概述
将 OpenAlex 学术数据导入 Neo4j 图数据库的 Python CLI 工具。支持混合搜索、导入会话管理、数据质量验证、多数据源富化、OpenAlex 查询统计，以及通过本地 WoS 文件桥接导入。

## 技术栈
- Python 3.11+
- Neo4j Python Driver
- pyalex (OpenAlex API 客户端)
- Click (CLI)
- pytest (测试)
- beautifulsoup4 (WoS HTML 解析)
- sentence-transformers (可选，用于嵌入向量)

## 项目结构
```
src/openalex_neo4j/
├── cli.py               # CLI 入口，11 个命令（Click 命令组）
├── openalex_client.py   # OpenAlex API 数据获取（含 count_works）
├── neo4j_client.py      # Neo4j 数据库操作
├── models.py            # 7 种实体 dataclass + ImportSession
├── importer.py          # 导入编排（API/WoS→JSONL 缓存→批量写库）
├── serializer.py        # DataSerializer（实体↔JSONL 文件）✅
├── session_manager.py   # 导入会话管理（本地 JSON + Neo4j 节点）
├── search.py            # 混合搜索（向量 + 全文 RRF）
├── data_quality.py      # 数据质量校验和清洗
├── embeddings.py        # 嵌入向量生成
├── wos_parser.py        # 🚧 WoS HTML 解析器
├── datasource/
│   ├── __init__.py      # 数据源注册表
│   ├── base.py          # DataSource ABC + DataRecord + merge_record
│   └── openalex_impl.py # OpenAlex 数据源适配器
└── neo4j_utils.py       # 工具函数
tests/                   # 测试（188+ 单元测试 + 集成测试）
docs/
├── design/              # 设计文档目录
│   ├── overview.md      # 总体设计文档
│   └── ...              # 子设计文档
├── implementation/      # 执行文档目录
│   ├── local-jsonl-cache.md  # JSONL 缓存执行方案
│   └── wos-import.md         # WoS 导入执行方案
├── neo4j-write-flow.md       # Neo4j 写入流程文档
├── wos-import-strategy.md    # WoS 导入策略文档
└── dual-import-design.md     # 双路径导入设计方案
```

## 文档要求（强制规则）
- 项目必须包含设计文档和执行文档。
- 设计文档放在 `docs/design/`，描述目标、架构、接口、数据模型、关键决策。
- 执行文档放在 `docs/implementation/`，描述实现步骤、依赖引入、测试计划、回滚方案。
- 写代码之前必须先写好对应的设计文档和执行文档。
- 如果你被要求实现某个功能，但对应的设计文档和执行文档缺失，你必须停下来，明确提示用户缺失的文档，并询问是否现在补充设计文档或执行文档。只有在文档就绪后才能继续编码。
- **执行文档中的每个实现步骤和模块，必须强制标明完成状态：`✅ 已完成` 或 `⬜ 未完成`。** 执行文档开头的文件清单表也应标注整体状态。当代码实现有进展时，同步更新对应状态。

## 常用命令

```bash
# 开发
uv sync                           # 安装依赖
uv sync --extra embeddings        # 含嵌入向量支持
uv pip install -e ".[dev]"        # 开发模式安装

# 测试
python -m pytest tests/ -v -m "not integration"   # 单元测试
python -m pytest tests/ -v                        # 全部测试
python -m pytest tests/ -x                        # 失败即停止
python -m pytest tests/test_xxx.py -v -k "keyword" # 指定测试

# 运行
uv run openalex-neo4j import --query "..." --limit 50
uv run openalex-neo4j import --query "..." --from-year 2020 --to-year 2024
uv run openalex-neo4j import --query "..." --cache-dir /tmp/cache --keep-cache
uv run openalex-neo4j import --query "..." --fetch-only    # 仅缓存，不写库
uv run openalex-neo4j import --list-cache
uv run openalex-neo4j import --resume S20260508_1234_0001
uv run openalex-neo4j count --query "machine learning"
uv run openalex-neo4j count --query "quantum computing" --from-year 2023
uv run openalex-neo4j search --query "neural networks"
uv run openalex-neo4j enrich --dry-run
uv run openalex-neo4j sessions
uv run openalex-neo4j session show <session_id>
uv run openalex-neo4j session delete <session_id>
uv run openalex-neo4j clear
uv run openalex-neo4j stats
uv run openalex-neo4j prune
uv run openalex-neo4j import-wos --dir wos/          # 🚧 待实现
```

## CLI 命令一览

| 命令 | 用途 | 状态 |
|---|---|---|
| `import` | OpenAlex API 检索 → JSONL 缓存 → 写库 | ✅ |
| `count` | 查询 OpenAlex 匹配总数（零数据拉取） | ✅ |
| `search` | 在 Neo4j 图谱中混合搜索（向量 + 全文 RRF） | ✅ |
| `enrich` | 多数据源富化缺失字段 | ✅ |
| `session` / `sessions` | 导入会话管理 | ✅ |
| `report` | 质量报告查看与汇总 | ✅ |
| `stats` | 数据库节点/关系统计 | ✅ |
| `clear` | 清空全部数据 | ✅ |
| `prune` | 清理孤立节点 | ✅ |
| `import-wos` | WoS HTML → DOI → OpenAlex 桥接 → 写库 | 🚧 |

## 数据写入流程（当前）

**两阶段 JSONL 缓存模式**（已实现）：
```
OpenAlex API / WoS → from_openalex() → to_node_dict()
  → DataSerializer.append() → JSONL 本地文件
    (~/.openalex-neo4j/cache/{sid}/{label}.jsonl)
      → 抓取完成 → DataSerializer.read_all()
        → UNWIND + MERGE 批量写入 Neo4j
          → 默认删除缓存目录
```

详见 `docs/dual-import-design.md`

## 两条导入路径

| 命令 | 路径 | 状态 |
|---|---|---|
| `import` | OpenAlex API 检索 → JSONL 缓存 → 写库 | ✅ 已实现 |
| `import-wos` | WoS HTML → DOI 提取 → OpenAlex API 回查 → 合并 → 写库 | 🚧 待实现 |

## 架构要点

### 存储位置
- **Neo4j**: 节点数据、关系、ImportSession 节点
- **本地 JSON** (`~/.openalex-neo4j/sessions.json`): 会话统计、质量报告、标签
- **本地缓存** (`~/.openalex-neo4j/cache/{sid}/`): 导入中间数据（JSONL 格式），导入完成自动删除

### 会话隔离
每次导入生成唯一会话 ID（如 `S20260508_1234_0001`），通过节点上的 `import_sessions` 数组属性标记归属。无 session_manager 时，自动生成 `S{YYYYMMDD_HHMMSS}` 格式 ID。

### 幂等写入
MERGE + 唯一 ID 约束 + ON MATCH 不覆盖核心字段，仅合并 `import_sessions` 数组。

### 关系字段剥离（_REL_FIELDS）
`author_ids`, `institution_ids`, `source_id`, `topic_ids`, `funder_ids`, `referenced_work_ids`, `publisher_id` 必须保存在 JSONL 中供关系扩展使用，但写入 Neo4j 节点前通过 `_import_nodes_from_dict()` 中 `_REL_FIELDS` 过滤剥离，防止成为节点属性。

## 编码规范
- 默认不写注释，除非逻辑不直观
- dataclass 用 `field(default_factory=...)` 处理可变默认值
- CLI 选项用 Click decorator，共享选项用 `_common_neo4j_options`
- 测试用 pytest + unittest.mock，mock 路径指向内部函数（非模块级 import）
- `session_mock.run.call_args` 获取上一次调用，需要精确断言时用 `call_args_list`
- 导入中间数据用 JSONL 格式（每行一个 JSON 对象，DataSerializer 负责序列化/反序列化）
- `cache_dir` 默认 `~/.openalex-neo4j/cache/`，可通过 `--cache-dir` 覆盖
- 旧方法（`_add_works`, `_expand_relationships`, `_import_nodes`）保留用于向后兼容

## 文档索引
- `docs/design/overview.md` — 总体设计文档
- `docs/neo4j-write-flow.md` — Cypher 写入原理 + 会话隔离
- `docs/dual-import-design.md` — 双路径导入设计方案
- `docs/wos-import-strategy.md` — WoS → OpenAlex 字段补充策略
- `docs/implementation/local-jsonl-cache.md` — JSONL 缓存执行方案
- `docs/implementation/wos-import.md` — WoS 导入执行方案

## Git 提交
- 远程仓库: `origin` → `https://github.com/kh464/openalex-neo4j.git`
- 提交信息简洁，用 `Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>` 结尾
