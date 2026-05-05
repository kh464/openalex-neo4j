# 执行文档：数据管理功能实现

## 使用方式

本文档按**阶段**组织，每个阶段有明确的入口文件、出口文件和验收标准。请**严格按阶段顺序**执行，阶段之间不能跳跃。

每个阶段的结构：
- **入口**：前置条件，本阶段依赖哪些已有代码
- **操作目标文件**：需要创建或修改的文件列表
- **具体步骤**：精确到函数签名和核心代码
- **验收**：如何验证本阶段工作正确
- **出口**：完成后产出的可测试结果

---

## 阶段 0：环境准备与测试基础设施

### 入口

项目已安装，`pip install -e ".[dev]"` 可用，Neo4j 实例可访问。

### 操作目标文件

| 文件 | 操作 |
|------|------|
| `pyproject.toml` | 修改 |
| `tests/conftest.py` | 新建 |
| `tests/test_data_quality.py` | 新建 |

### 步骤

#### 1. 创建 `tests/conftest.py`

```python
"""Shared fixtures for all tests."""
from unittest.mock import Mock

import pytest


@pytest.fixture
def mock_neo4j_client():
    """Create a mock Neo4j client with common methods."""
    client = Mock()
    client.create_constraints = Mock()
    client.create_indexes = Mock()
    client.batch_create_nodes = Mock(return_value=0)
    client.batch_create_relationships = Mock(return_value=0)
    client.clear_database = Mock()
    client.close = Mock()
    client.driver = Mock()
    return client


@pytest.fixture
def sample_work_dict():
    """Sample Work node dict for Neo4j operations."""
    return {
        "id": "W123",
        "title": "Test Paper",
        "publication_year": 2023,
        "type": "article",
        "cited_by_count": 10,
        "doi": "10.1234/test",
    }
```

### 验收

```bash
pytest tests/conftest.py --collect-only  # 确认 fixture 可加载
```

### 出口

测试基础设施就绪，可以开始实现阶段 1。

---

## 阶段 1：数据模型扩展

### 入口

- 阶段 0 完成
- `src/openalex_neo4j/models.py` 已存在

### 操作目标文件

| 文件 | 操作 |
|------|------|
| `src/openalex_neo4j/models.py` | 修改 |
| `tests/test_models.py` | 追加测试 |

### 步骤

#### 1.1 在 `models.py` 顶部新增 ImportSession 数据类

在 `def extract_openalex_id` 之前（第 7 行之后）插入：

```python
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class ImportSession:
    """Represents a single import session (one run of import command)."""
    id: str
    query: str
    limit: int = 100
    expand_depth: int = 1
    tag: str | None = None
    created_at: datetime | None = None
    status: str = "completed"                 # "completed" | "failed"
    stats: dict[str, int] | None = None       # node/relationship counts from the import
    quality_summary: dict | None = None        # {"errors": N, "warnings": N, "infos": N}, from optional quality check

    def to_node_dict(self) -> dict[str, Any]:
        """Convert to Neo4j node properties. Omits stats/quality_summary (stored in local JSON only)."""
        return {
            "id": self.id,
            "query": self.query,
            "limit": self.limit,
            "expand_depth": self.expand_depth,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "status": self.status,
            "tag": self.tag,
        }
```

#### 1.2 修改 Work 数据类

在 Work 类的现有字段后（`referenced_work_ids` 之后），添加三个新字段：

```python
@dataclass
class Work:
    """Represents a scholarly work."""
    # ... existing fields ...
    referenced_work_ids: list[str] = field(default_factory=list)
    # --- 新增 session 追踪字段 ---
    import_sessions: list[str] = field(default_factory=list)
    first_imported_at: str | None = None
    last_imported_at: str | None = None
```

#### 1.3 修改 Work.to_node_dict()

在 `models.py` 中找到 `Work.to_node_dict()`（第 142 行），将方法签名改为：

```python
    def to_node_dict(self, current_session: str | None = None) -> dict[str, Any]:
        """Convert to dictionary for Neo4j node creation.

        Args:
            current_session: If provided, include session tracking fields.
        """
        from .neo4j_client import to_camel_case_label

        node_dict = {
            "id": self.id,
            "title": self.title,
            "publication_year": self.publication_year,
            "publication_date": self.publication_date,
            "doi": self.doi,
            "type": self.type,
            "cited_by_count": self.cited_by_count,
            "is_oa": self.is_oa,
            "abstract": self.abstract,
        }
        # Add CamelCase type as dynamic label field (or "Work" as default)
        if self.type:
            node_dict["_label"] = to_camel_case_label(self.type)
        else:
            node_dict["_label"] = "Work"
        # Only include embedding if it exists
        if self.embedding:
            node_dict["embedding"] = self.embedding
        # --- 新增：session 追踪字段 ---
        if current_session:
            node_dict["import_sessions"] = self.import_sessions or [current_session]
            node_dict["first_imported_at"] = self.first_imported_at
            node_dict["last_imported_at"] = self.last_imported_at
        return node_dict
```

#### 1.4 修改 Author、Institution、Source、Topic、Publisher、Funder 数据类

每个实体类的字段列表末尾、`to_node_dict()` 方法、`from_openalex()` 方法都需要修改。以 Author 为例（其他 5 个完全相同的模式）：

**字段追加**：
```python
@dataclass
class Author:
    """Represents an author."""
    id: str
    display_name: str | None = None
    orcid: str | None = None
    works_count: int = 0
    cited_by_count: int = 0
    # --- 新增 session 追踪字段 ---
    import_sessions: list[str] = field(default_factory=list)
    first_imported_at: str | None = None
    last_imported_at: str | None = None
```

**`to_node_dict` 签名改为**：
```python
    def to_node_dict(self, current_session: str | None = None) -> dict[str, Any]:
        """Convert to dictionary for Neo4j node creation."""
        node_dict = {
            "id": self.id,
            "display_name": self.display_name,
            "orcid": self.orcid,
            "works_count": self.works_count,
            "cited_by_count": self.cited_by_count,
        }
        if current_session:
            node_dict["import_sessions"] = self.import_sessions or [current_session]
            node_dict["first_imported_at"] = self.first_imported_at
            node_dict["last_imported_at"] = self.last_imported_at
        return node_dict
```

对其他 5 个实体（Institution、Source、Topic、Publisher、Funder）执行完全相同的修改。具体字段列表：

| 实体 | 已有字段 | 新增 3 个字段 |
|------|---------|--------------|
| Institution | id, display_name, ror, country_code, type, works_count | 同上 |
| Source | id, display_name, issn_l, issn, type, publisher_id, works_count | 同上 |
| Topic | id, display_name, description, keywords | 同上 |
| Publisher | id, display_name, country_codes, works_count | 同上 |
| Funder | id, display_name, country_code, description | 同上 |

#### 1.5 追加测试

在 `tests/test_models.py` 中添加测试类：

```python
class TestImportSession:
    """Tests for ImportSession model."""

    def test_to_node_dict_minimal(self):
        """Test ImportSession to_node_dict with minimal fields."""
        session = ImportSession(
            id="20260505_120000",
            query="machine learning",
        )
        node_dict = session.to_node_dict()
        assert node_dict["id"] == "20260505_120000"
        assert node_dict["query"] == "machine learning"
        assert node_dict["status"] == "completed"
        assert "created_at" in node_dict

    def test_to_node_dict_with_datetime(self):
        """Test ImportSession to_node_dict with datetime."""
        from datetime import datetime
        session = ImportSession(
            id="20260505_120000",
            query="test",
            created_at=datetime(2026, 5, 5, 12, 0, 0),
        )
        node_dict = session.to_node_dict()
        assert node_dict["created_at"] == "2026-05-05T12:00:00"

    def test_to_node_dict_excludes_stats(self):
        """Test that stats and quality_summary are excluded from node dict."""
        session = ImportSession(
            id="20260505_120000", query="test",
            stats={"works": 10}, quality_summary={"errors": 1},
        )
        node_dict = session.to_node_dict()
        assert "stats" not in node_dict
        assert "quality_summary" not in node_dict


class TestWorkSessionTracking:
    """Tests for Work session tracking fields."""

    def test_to_node_dict_without_session(self):
        """Test to_node_dict returns original format when no session given."""
        work = Work(id="W1", title="Test")
        node_dict = work.to_node_dict()
        assert "import_sessions" not in node_dict
        assert "first_imported_at" not in node_dict

    def test_to_node_dict_with_session(self):
        """Test to_node_dict includes session fields when session given."""
        work = Work(id="W1", title="Test", import_sessions=["20260505_120000"])
        node_dict = work.to_node_dict(current_session="20260505_120000")
        assert node_dict["import_sessions"] == ["20260505_120000"]
        assert "first_imported_at" in node_dict

    def test_to_node_dict_embedding_preserved(self):
        """Test embedding field still works with session tracking."""
        work = Work(id="W1", title="Test", embedding=[0.1, 0.2])
        node_dict = work.to_node_dict(current_session="S1")
        assert node_dict["embedding"] == [0.1, 0.2]
        assert node_dict["import_sessions"] == ["S1"]


class TestAuthorSessionTracking:
    """Tests for Author session tracking fields."""

    def test_to_node_dict_without_session(self):
        author = Author(id="A1", display_name="John Doe")
        node_dict = author.to_node_dict()
        assert "import_sessions" not in node_dict

    def test_to_node_dict_with_session(self):
        author = Author(id="A1", display_name="John Doe")
        node_dict = author.to_node_dict(current_session="S1")
        assert node_dict["import_sessions"] == ["S1"]
        assert "first_imported_at" in node_dict
```

### 验收

```bash
pytest tests/test_models.py -v  # 所有测试通过，新增测试覆盖 session 追踪字段
```

### 出口

- `models.py` 包含 ImportSession 数据类，所有实体有 `import_sessions` / `first_imported_at` / `last_imported_at` 字段
- 所有 `to_node_dict()` 支持可选的 `current_session` 参数
- 不传 `current_session` 时行为与修改前完全一致（向后兼容）

---

## 阶段 2：SessionManager 类

### 入口

- 阶段 1 完成（ImportSession 数据类存在）
- `~/.openalex-neo4j/` 目录可以被创建

### 操作目标文件

| 文件 | 操作 |
|------|------|
| `src/openalex_neo4j/session_manager.py` | 新建 |
| `tests/test_session_manager.py` | 新建 |

### 步骤

#### 2.1 创建 `session_manager.py`

完整实现如下：

```python
"""Manages import session metadata in Neo4j and local storage."""
import json
import logging
from datetime import datetime
from pathlib import Path

from .models import ImportSession
from .neo4j_client import Neo4jClient

logger = logging.getLogger(__name__)


class SessionManager:
    """Manages import session metadata in Neo4j and local storage.

    Session metadata is stored in two places:
      - Neo4j: :ImportSession node for in-database queries
      - Local: ~/.openalex-neo4j/sessions.json for fast CLI listing

    The local file is the source of truth for stats, quality_summary,
    and tag (fields not stored in Neo4j).
    """

    SESSIONS_DIR = Path.home() / ".openalex-neo4j"
    SESSIONS_FILE = SESSIONS_DIR / "sessions.json"

    def __init__(self, neo4j_client: Neo4jClient):
        self.neo4j = neo4j_client
        self._local_sessions: dict[str, dict] = {}
        self._ensure_dir()
        self._load_local()

    # --- 内部辅助 ---

    def _ensure_dir(self) -> None:
        """Create sessions directory if it doesn't exist."""
        self.SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

    def _load_local(self) -> None:
        """Load local session metadata from JSON file."""
        if self.SESSIONS_FILE.exists():
            try:
                with open(self.SESSIONS_FILE, "r", encoding="utf-8") as f:
                    self._local_sessions = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"Failed to load sessions file: {e}")
                self._local_sessions = {}
        else:
            self._local_sessions = {}

    def _save_local(self) -> None:
        """Save local session metadata to JSON file."""
        try:
            with open(self.SESSIONS_FILE, "w", encoding="utf-8") as f:
                json.dump(self._local_sessions, f, indent=2, ensure_ascii=False)
        except OSError as e:
            logger.error(f"Failed to save sessions file: {e}")

    def _next_id(self) -> str:
        """Generate a unique session ID based on current timestamp."""
        return datetime.now().strftime("%Y%m%d_%H%M%S")

    # --- Session 生命周期管理 ---

    def create_session(self, query: str, limit: int = 100,
                       expand_depth: int = 1,
                       tag: str | None = None) -> ImportSession:
        """Create a new import session.

        Generates a session ID, creates an :ImportSession node in Neo4j,
        and writes metadata to local storage.

        Returns:
            ImportSession with id, created_at, etc. populated.
        """
        session_id = self._next_id()
        now = datetime.now()
        session = ImportSession(
            id=session_id,
            query=query,
            limit=limit,
            expand_depth=expand_depth,
            tag=tag,
            created_at=now,
            status="running",
        )

        # Write to local storage
        self._local_sessions[session_id] = {
            "query": query,
            "limit": limit,
            "expand_depth": expand_depth,
            "tag": tag,
            "created_at": now.isoformat(),
            "status": "running",
            "stats": None,
            "quality_summary": None,
        }
        self._save_local()

        # Write :ImportSession node to Neo4j
        try:
            node = session.to_node_dict()
            self.neo4j.batch_create_nodes("ImportSession", [node])
        except Exception as e:
            logger.warning(f"Failed to write ImportSession node to Neo4j: {e}")

        logger.info(f"Created import session: {session_id} (query='{query}')")
        return session

    def complete_session(self, session_id: str, stats: dict[str, int] | None = None,
                         quality_summary: dict | None = None) -> None:
        """Mark a session as completed and update statistics.

        Args:
            session_id: The session ID to update.
            stats: Node/relationship counts from the import.
            quality_summary: Quality check results, e.g. {"errors": 0, "warnings": 2}.
        """
        if session_id not in self._local_sessions:
            logger.warning(f"Session {session_id} not found in local storage")
            return

        self._local_sessions[session_id]["status"] = "completed"
        if stats is not None:
            self._local_sessions[session_id]["stats"] = stats
        if quality_summary is not None:
            self._local_sessions[session_id]["quality_summary"] = quality_summary
        self._save_local()

        # Update ImportSession node in Neo4j
        try:
            with self.neo4j.driver.session() as session:
                session.run(
                    "MATCH (s:ImportSession {id: $id}) SET s.status = $status",
                    id=session_id, status="completed",
                )
        except Exception as e:
            logger.warning(f"Failed to update ImportSession node in Neo4j: {e}")

        logger.info(f"Completed import session: {session_id}")

    def fail_session(self, session_id: str) -> None:
        """Mark a session as failed."""
        if session_id not in self._local_sessions:
            return
        self._local_sessions[session_id]["status"] = "failed"
        self._save_local()
        try:
            with self.neo4j.driver.session() as session:
                session.run(
                    "MATCH (s:ImportSession {id: $id}) SET s.status = $status",
                    id=session_id, status="failed",
                )
        except Exception:
            pass
        logger.info(f"Failed import session: {session_id}")

    def tag_session(self, session_id: str, tag: str) -> None:
        """Set a human-readable tag/alias for a session."""
        if session_id not in self._local_sessions:
            raise KeyError(f"Session {session_id} not found")
        self._local_sessions[session_id]["tag"] = tag
        self._save_local()

    # --- 查询 ---

    def get_session(self, session_id: str) -> ImportSession | None:
        """Get a session by ID. Checks local storage first, falls back to Neo4j."""
        local = self._local_sessions.get(session_id)
        if local:
            return ImportSession(
                id=session_id,
                query=local.get("query", ""),
                limit=local.get("limit", 100),
                expand_depth=local.get("expand_depth", 1),
                tag=local.get("tag"),
                created_at=datetime.fromisoformat(local["created_at"]) if local.get("created_at") else None,
                status=local.get("status", "unknown"),
                stats=local.get("stats"),
                quality_summary=local.get("quality_summary"),
            )
        # Fallback: query Neo4j
        try:
            with self.neo4j.driver.session() as ns:
                result = ns.run(
                    "MATCH (s:ImportSession {id: $id}) RETURN s",
                    id=session_id,
                )
                record = result.single()
                if record:
                    props = dict(record["s"])
                    return ImportSession(
                        id=props["id"],
                        query=props.get("query", ""),
                        limit=props.get("limit", 100),
                        expand_depth=props.get("expand_depth", 1),
                        tag=props.get("tag"),
                        created_at=datetime.fromisoformat(props["created_at"]) if props.get("created_at") else None,
                        status=props.get("status", "unknown"),
                    )
        except Exception:
            pass
        return None

    def list_sessions(self, limit: int = 20) -> list[ImportSession]:
        """List recent sessions, newest first."""
        all_sessions = []
        for sid, data in self._local_sessions.items():
            all_sessions.append(ImportSession(
                id=sid,
                query=data.get("query", ""),
                limit=data.get("limit", 100),
                expand_depth=data.get("expand_depth", 1),
                tag=data.get("tag"),
                created_at=datetime.fromisoformat(data["created_at"]) if data.get("created_at") else None,
                status=data.get("status", "unknown"),
                stats=data.get("stats"),
                quality_summary=data.get("quality_summary"),
            ))

        all_sessions.sort(key=lambda s: s.created_at or datetime.min, reverse=True)
        return all_sessions[:limit]

    # --- 删除 ---

    def get_session_node_counts(self, session_id: str) -> dict[str, int]:
        """Count nodes associated with a session, grouped by label.

        Uses the import_sessions array property on nodes.
        """
        counts: dict[str, int] = {}
        try:
            with self.neo4j.driver.session() as session:
                result = session.run("""
                    MATCH (n)
                    WHERE $session_id IN n.import_sessions
                    RETURN labels(n) as labels, count(n) as count
                """, session_id=session_id)
                for record in result:
                    # Use the first label (most specific) as the group key
                    label = record["labels"][0] if record["labels"] else "Unknown"
                    counts[label] = record["count"]
        except Exception as e:
            logger.error(f"Failed to count session nodes: {e}")
        return counts

    def get_session_relationship_counts(self, session_id: str) -> dict[str, int]:
        """Count relationships associated with a session.

        A relationship is counted if both its source and target nodes
        contain the session_id in their import_sessions.
        """
        counts: dict[str, int] = {}
        try:
            with self.neo4j.driver.session() as session:
                result = session.run("""
                    MATCH (a)-[r]->(b)
                    WHERE $session_id IN a.import_sessions
                      AND $session_id IN b.import_sessions
                    RETURN type(r) as rel_type, count(r) as count
                """, session_id=session_id)
                for record in result:
                    counts[record["rel_type"]] = record["count"]
        except Exception as e:
            logger.error(f"Failed to count session relationships: {e}")
        return counts

    def delete_session(self, session_id: str) -> dict[str, int]:
        """Delete all data associated with a session.

        Strategy (strict deletion):
          1. Delete nodes whose import_sessions == [session_id] (isolated nodes).
             DETACH DELETE cascades to their relationships.
          2. Remove session_id from import_sessions of shared nodes.
          3. Delete the ImportSession node itself.

        Returns:
            Dict with counts of deleted and updated nodes.
        """
        result: dict[str, int] = {"deleted": 0, "updated": 0}

        try:
            with self.neo4j.driver.session() as session:
                # Step 1: Delete isolated nodes (only belong to this session)
                step1 = session.run("""
                    MATCH (n)
                    WHERE n.import_sessions = [$session_id]
                    DETACH DELETE n
                    RETURN count(n) as deleted
                """, session_id=session_id)
                result["deleted"] = step1.single()["deleted"]

                # Step 2: Delete the ImportSession node
                session.run(
                    "MATCH (s:ImportSession {id: $id}) DELETE s",
                    id=session_id,
                )

                # Step 3: Remove session_id from shared nodes
                step3 = session.run("""
                    MATCH (n)
                    WHERE $session_id IN n.import_sessions
                    SET n.import_sessions = [s IN n.import_sessions WHERE s <> $session_id]
                    RETURN count(n) as updated
                """, session_id=session_id)
                result["updated"] = step3.single()["updated"]

        except Exception as e:
            logger.error(f"Failed to delete session {session_id}: {e}")
            raise

        # Clean up local storage
        self._local_sessions.pop(session_id, None)
        self._save_local()

        logger.info(f"Deleted session {session_id}: {result['deleted']} nodes removed, "
                    f"{result['updated']} nodes updated")
        return result
```

#### 2.2 创建 `tests/test_session_manager.py`

```python
"""Tests for SessionManager."""
import json
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock, mock_open

import pytest

from openalex_neo4j.session_manager import SessionManager
from openalex_neo4j.models import ImportSession


class TestSessionManager:
    """Tests for SessionManager."""

    @pytest.fixture
    def mock_neo4j(self):
        client = Mock()
        client.driver.session = Mock()
        client.batch_create_nodes = Mock(return_value=1)
        return client

    @pytest.fixture
    def manager(self, mock_neo4j, tmpdir):
        """Create SessionManager with temp directory for local storage."""
        manager = SessionManager(mock_neo4j)
        # Override to use temp dir
        manager.SESSIONS_DIR = Path(tmpdir)
        manager.SESSIONS_FILE = Path(tmpdir) / "sessions.json"
        manager._ensure_dir()
        return manager

    def test_init_creates_dir(self, mock_neo4j, tmpdir):
        """Test that init creates the sessions directory."""
        test_dir = Path(tmpdir) / "subdir"
        manager = SessionManager(mock_neo4j)
        manager.SESSIONS_DIR = test_dir
        manager.SESSIONS_FILE = test_dir / "sessions.json"
        manager._ensure_dir()
        assert test_dir.exists()

    def test_create_session(self, manager):
        """Test creating a new session."""
        session = manager.create_session(query="machine learning", limit=10)

        assert session.id is not None
        assert session.query == "machine learning"
        assert session.limit == 10
        assert session.status == "running"
        assert session.created_at is not None

        # Check local storage
        assert session.id in manager._local_sessions
        assert manager._local_sessions[session.id]["status"] == "running"

        # Check Neo4j was called
        manager.neo4j.batch_create_nodes.assert_called_once()

    def test_complete_session(self, manager):
        """Test completing a session."""
        session = manager.create_session(query="test", limit=5)
        manager.complete_session(session.id, stats={"works": 10})

        assert manager._local_sessions[session.id]["status"] == "completed"
        assert manager._local_sessions[session.id]["stats"] == {"works": 10}

    def test_fail_session(self, manager):
        """Test failing a session."""
        session = manager.create_session(query="test")
        manager.fail_session(session.id)

        assert manager._local_sessions[session.id]["status"] == "failed"

    def test_tag_session(self, manager):
        """Test tagging a session."""
        session = manager.create_session(query="test")
        manager.tag_session(session.id, "my-import")
        assert manager._local_sessions[session.id]["tag"] == "my-import"

        # Verify persisted to file
        with open(manager.SESSIONS_FILE) as f:
            data = json.load(f)
        assert data[session.id]["tag"] == "my-import"

    def test_tag_session_not_found(self, manager):
        """Test tagging a non-existent session raises KeyError."""
        with pytest.raises(KeyError):
            manager.tag_session("nonexistent", "tag")

    def test_list_sessions_order(self, manager):
        """Test that sessions are listed newest first."""
        s1 = manager.create_session(query="first")
        s2 = manager.create_session(query="second")
        sessions = manager.list_sessions()

        assert len(sessions) >= 2
        assert sessions[0].id == s2.id  # newest first

    def test_get_session_from_local(self, manager):
        """Test get_session retrieves from local storage."""
        created = manager.create_session(query="test", limit=50)
        retrieved = manager.get_session(created.id)

        assert retrieved is not None
        assert retrieved.id == created.id
        assert retrieved.query == "test"
        assert retrieved.limit == 50

    def test_get_session_not_found(self, manager):
        """Test get_session returns None for unknown session."""
        assert manager.get_session("nonexistent") is None

    def test_delete_session_nonexistent(self, manager, mock_neo4j):
        """Test delete_session on nonexistent session raises no error."""
        # delete_session with mock driver might fail; handle gracefully
        mock_neo4j.driver.session.side_effect = Exception("no connection")
        # Should not raise when session doesn't exist locally
        # (local pop is safe, Neo4j call will error but caught)
        manager.delete_session("nonexistent")
        # Just verify no crash

    def test_local_persistence(self, manager):
        """Test that sessions persist to JSON file."""
        session = manager.create_session(query="persistence test")
        manager.complete_session(session.id, stats={"works": 5})

        # Read file directly
        assert manager.SESSIONS_FILE.exists()
        with open(manager.SESSIONS_FILE) as f:
            data = json.load(f)

        assert session.id in data
        assert data[session.id]["query"] == "persistence test"
        assert data[session.id]["status"] == "completed"
        assert data[session.id]["stats"] == {"works": 5}
```

### 验收

```bash
pytest tests/test_session_manager.py -v  # 全部通过
```

### 出口

- `SessionManager` 完成：create / complete / fail / tag / list / get / delete
- 本地 JSON 持久化与 Neo4j 双写
- 单元测试覆盖所有方法

---

## 阶段 3：修改 Neo4jClient 支持 Session 标记

### 入口

- 阶段 2 完成
- `src/openalex_neo4j/neo4j_client.py` 已存在

### 操作目标文件

| 文件 | 操作 |
|------|------|
| `src/openalex_neo4j/neo4j_client.py` | 修改 `batch_create_nodes` |
| `tests/test_neo4j_client.py` | 追加测试 |

### 步骤

#### 3.1 修改 `batch_create_nodes` 方法

将第 211–266 行的 `batch_create_nodes` 方法替换为：

```python
    def batch_create_nodes(
        self,
        label: str,
        nodes: list[dict[str, Any]],
        batch_size: int = 500,
        dynamic_label: bool = False,
        current_session: str | None = None,
    ) -> int:
        """Create nodes in batches using UNWIND and MERGE.

        Args:
            label: Node label (e.g., "Work", "Author")
            nodes: List of node properties dictionaries
            batch_size: Number of nodes per batch
            dynamic_label: If True, use item._label field from node dict as additional
                dynamic label using Neo4j's dynamic label syntax: SET n:$(item._label)
            current_session: If provided, enables session tracking.
                Each node dict should include:
                  - current_session: session ID
                  - current_timestamp: ISO timestamp string
                  - import_sessions: list[str]
                  - first_imported_at: str or None
                  - last_imported_at: str or None

        Returns:
            Total number of nodes created/updated
        """
        if not nodes:
            return 0

        logger.info(f"Creating {len(nodes)} {label} nodes in batches of {batch_size}")
        total_created = 0

        if current_session and label != "ImportSession":
            # With session tracking: use ON CREATE / ON MATCH to merge import_sessions
            if dynamic_label:
                query = f"""
                UNWIND $batch AS item
                MERGE (n:{label} {{id: item.id}})
                ON CREATE SET
                  n += item {{.*, _label: null, current_session: null, current_timestamp: null,
                              import_sessions: null, first_imported_at: null, last_imported_at: null}},
                  n.import_sessions = [item.current_session],
                  n.first_imported_at = item.current_timestamp,
                  n.last_imported_at = item.current_timestamp
                ON MATCH SET
                  n += item {{.*, _label: null, current_session: null, current_timestamp: null,
                              import_sessions: null, first_imported_at: null, last_imported_at: null}},
                  n.import_sessions =
                    CASE WHEN item.current_session IN coalesce(n.import_sessions, [])
                    THEN n.import_sessions
                    ELSE coalesce(n.import_sessions, []) + item.current_session
                    END,
                  n.last_imported_at = item.current_timestamp
                SET n:$(item._label)
                RETURN count(n) as count
                """
            else:
                query = f"""
                UNWIND $batch AS item
                MERGE (n:{label} {{id: item.id}})
                ON CREATE SET
                  n += item {{.*, current_session: null, current_timestamp: null,
                              import_sessions: null, first_imported_at: null, last_imported_at: null}},
                  n.import_sessions = [item.current_session],
                  n.first_imported_at = item.current_timestamp,
                  n.last_imported_at = item.current_timestamp
                ON MATCH SET
                  n += item {{.*, current_session: null, current_timestamp: null,
                              import_sessions: null, first_imported_at: null, last_imported_at: null}},
                  n.import_sessions =
                    CASE WHEN item.current_session IN coalesce(n.import_sessions, [])
                    THEN n.import_sessions
                    ELSE coalesce(n.import_sessions, []) + item.current_session
                    END,
                  n.last_imported_at = item.current_timestamp
                RETURN count(n) as count
                """
        else:
            # Original behavior (no session tracking)
            if dynamic_label:
                query = f"""
                UNWIND $batch AS item
                MERGE (n:{label} {{id: item.id}})
                SET n += item {{.*, _label: null}},
                    n:$(item._label)
                RETURN count(n) as count
                """
            else:
                query = f"""
                UNWIND $batch AS item
                MERGE (n:{label} {{id: item.id}})
                SET n += item
                RETURN count(n) as count
                """

        with self.driver.session() as session:
            for i in range(0, len(nodes), batch_size):
                batch = nodes[i:i + batch_size]
                try:
                    result = session.run(query, batch=batch)
                    count = result.single()["count"]
                    total_created += count
                    logger.debug(f"Batch {i // batch_size + 1}: Created/updated {count} {label} nodes")
                except Exception as e:
                    logger.error(f"Failed to create batch of {label} nodes: {e}")

        logger.info(f"Finished creating {total_created} {label} nodes")
        return total_created
```

#### 3.2 追加测试

在 `tests/test_neo4j_client.py` 中追加 `TestBatchCreateNodes`：

```python
class TestBatchCreateNodesWithSession:
    """Tests for batch_create_nodes with session tracking."""

    @pytest.fixture
    def mock_session(self):
        session = MagicMock()
        mock_result = MagicMock()
        mock_result.single.return_value = {"count": 1}
        session.run.return_value = mock_result
        return session

    @pytest.fixture
    def client_with_session(self, mock_driver, mock_session):
        client = Neo4jClient("bolt://localhost", "neo4j", "password")
        client._driver = mock_driver
        mock_driver.session.return_value.__enter__.return_value = mock_session
        return client

    def test_session_query_contains_import_sessions(self, client_with_session, mock_driver, mock_session):
        """Test that session tracking mode uses ON CREATE/ON MATCH with import_sessions."""
        nodes = [{
            "id": "W1",
            "title": "Test",
            "current_session": "S1",
            "current_timestamp": "2026-01-01T00:00:00",
            "import_sessions": ["S1"],
        }]
        client_with_session.batch_create_nodes("Work", nodes, current_session="S1")

        # Verify the query contains ON CREATE and ON MATCH clauses
        call_args = mock_session.run.call_args
        assert call_args is not None
        query = call_args[0][0]
        assert "ON CREATE SET" in query
        assert "ON MATCH SET" in query
        assert "import_sessions" in query
        assert "last_imported_at" in query

    def test_session_query_with_dynamic_label(self, client_with_session, mock_session):
        """Test that dynamic_label works with session tracking."""
        nodes = [{
            "id": "W1",
            "title": "Test",
            "_label": "Article",
            "current_session": "S1",
            "current_timestamp": "2026-01-01T00:00:00",
            "import_sessions": ["S1"],
        }]
        client_with_session.batch_create_nodes("Work", nodes, dynamic_label=True, current_session="S1")

        call_args = mock_session.run.call_args
        query = call_args[0][0]
        assert "n:$(item._label)" in query

    def test_no_session_original_behavior(self, client_with_session, mock_session):
        """Test that without current_session, original query is used."""
        nodes = [{"id": "W1", "title": "Test"}]
        client_with_session.batch_create_nodes("Work", nodes)

        call_args = mock_session.run.call_args
        query = call_args[0][0]
        assert "ON CREATE SET" not in query
        assert "SET n += item" in query or "SET n:$(item._label)" in query

    def test_empty_nodes(self, client_with_session):
        """Test with empty nodes list."""
        result = client_with_session.batch_create_nodes("Work", [], current_session="S1")
        assert result == 0

    def test_import_session_label_skips_tracking(self, client_with_session, mock_session):
        """Test that ImportSession nodes don't get session tracking applied."""
        nodes = [{"id": "S1", "query": "test"}]
        client_with_session.batch_create_nodes("ImportSession", nodes, current_session="S1")

        call_args = mock_session.run.call_args
        query = call_args[0][0]
        assert "ON CREATE SET" not in query
```

### 验收

```bash
pytest tests/test_neo4j_client.py -v  # 全部通过
```

### 出口

- `batch_create_nodes` 支持 `current_session` 参数
- 传 `current_session` 时：ON CREATE 初始化，ON MATCH 合并 `import_sessions`
- 不传时：行为完全不变
- `ImportSession` 标签豁免 session 追踪

---

## 阶段 4：修改 Importer 集成 Session 生命周期

### 入口

- 阶段 3 完成
- `src/openalex_neo4j/importer.py` 已存在

### 操作目标文件

| 文件 | 操作 |
|------|------|
| `src/openalex_neo4j/importer.py` | 修改 |
| `tests/test_importer.py` | 追加测试 |

### 步骤

#### 4.1 修改 `OpenAlexImporter.__init__`

增加 `session_manager` 参数：

```python
from .session_manager import SessionManager

class OpenAlexImporter:
    """Orchestrates import of OpenAlex data into Neo4j."""

    def __init__(
        self,
        neo4j_client: Neo4jClient,
        openalex_client: OpenAlexClient,
        session_manager: SessionManager | None = None,
    ):
        """Initialize importer.

        Args:
            neo4j_client: Neo4j client instance
            openalex_client: OpenAlex client instance
            session_manager: Optional session manager for tracking imports.
                If None, session tracking is disabled.
        """
        self.neo4j = neo4j_client
        self.openalex = openalex_client
        self.session_manager = session_manager

        # Current session ID, set when import_from_query is called
        self.current_session: str | None = None

        # Storage for collected entities
        self.works: dict[str, Work] = {}
        # ... rest stays the same ...
```

#### 4.2 修改 `import_from_query` 方法

方法签名改为：

```python
    def import_from_query(
        self,
        query: str,
        limit: int = 100,
        expand_depth: int = 1,
        skip_abstracts: bool = False,
        generate_embeddings: bool = False,
        tag: str | None = None,
        skip_constraints: bool = False,
    ) -> dict[str, int]:
```

在 Step 5（创建约束和索引）之前插入 session 创建逻辑：

找到 `# Step 3: Optionally skip abstracts` 之前的代码，在 Step 2 和 Step 3 之间插入：

```python
        # Step 2.5: Create import session (if session manager available)
        if self.session_manager:
            session_obj = self.session_manager.create_session(
                query=query,
                limit=limit,
                expand_depth=expand_depth,
                tag=tag,
            )
            self.current_session = session_obj.id
            logger.info(f"Import session: {self.current_session}")
```

在 `# Step 6: Import nodes` 之前，将 session 参数传递给 `_import_nodes`：

找到 `node_counts = self._import_nodes()`，改为：

```python
        # Step 6: Import nodes
        logger.info("Importing nodes to Neo4j")
        node_counts = self._import_nodes()
```

#### 4.3 修改 `_import_nodes` 传递 `current_session`

在 `_import_nodes` 方法中，给每个 `batch_create_nodes` 调用加上 `current_session=self.current_session`。需要逐个修改所有 7 个实体类型。

以 Work 为例（第 189-192 行）：

```python
        # Works (with dynamic type labels)
        if self.works:
            work_nodes = [w.to_node_dict(current_session=self.current_session) for w in self.works.values()]
            counts["works"] = self.neo4j.batch_create_nodes(
                "Work", work_nodes, dynamic_label=True,
                current_session=self.current_session,
            )
```

对其他 6 个实体做同样修改的模式：

```python
        # Authors
        if self.authors:
            author_nodes = [a.to_node_dict(current_session=self.current_session) for a in self.authors.values()]
            counts["authors"] = self.neo4j.batch_create_nodes(
                "Author", author_nodes,
                current_session=self.current_session,
            )
```

注意：`Publisher` 的过滤条件不变（仍需 `if p is not None`）。

#### 4.4 在 import 完成后更新 session

在所有步骤完成后、`counts` 返回前插入：

```python
        # Step 7.5: Complete import session
        if self.session_manager and self.current_session:
            self.session_manager.complete_session(self.current_session, stats=counts)
```

完整插入位置：在 `_import_relationships()` 调用之后、`counts` 合并之前。

#### 4.5 追加测试

在 `tests/test_importer.py` 中追加 `TestImporterSessionTracking`：

```python
class TestImporterSessionTracking:
    """Tests for importer session tracking integration."""

    @pytest.fixture
    def mock_session_manager(self):
        manager = Mock()
        session = ImportSession(id="20260101_120000", query="test")
        manager.create_session.return_value = session
        return manager

    @pytest.fixture
    def importer_with_session(self, mock_neo4j_client, mock_openalex_client, mock_session_manager):
        return OpenAlexImporter(mock_neo4j_client, mock_openalex_client, mock_session_manager)

    def test_session_created_on_import(self, importer_with_session, mock_session_manager, mock_openalex_client):
        """Test that a session is created when importing."""
        mock_openalex_client.search_works.return_value = [
            Work(id="W1", title="Test Paper"),
        ]

        importer_with_session.import_from_query("test query", limit=1)

        mock_session_manager.create_session.assert_called_once_with(
            query="test query", limit=1, expand_depth=1, tag=None,
        )

    def test_session_completed_after_import(self, importer_with_session, mock_session_manager, mock_openalex_client):
        """Test that session is marked completed after import."""
        mock_openalex_client.search_works.return_value = [
            Work(id="W1", title="Test Paper"),
        ]

        counts = importer_with_session.import_from_query("test", limit=1)

        mock_session_manager.complete_session.assert_called_once()
        args, _ = mock_session_manager.complete_session.call_args
        assert args[0] == "20260101_120000"  # session_id
        assert isinstance(args[1], dict)     # stats

    def test_no_session_manager_no_tracking(self, mock_neo4j_client, mock_openalex_client):
        """Test that without session manager, no tracking occurs."""
        importer = OpenAlexImporter(mock_neo4j_client, mock_openalex_client)
        assert importer.session_manager is None
        assert importer.current_session is None

    def test_to_node_dict_called_with_session(self, importer_with_session, mock_session_manager, mock_openalex_client):
        """Test that to_node_dict is called with current_session."""
        mock_openalex_client.search_works.return_value = [
            Work(id="W1", title="Test"),
        ]

        importer_with_session.import_from_query("test", limit=1)

        # Verify batch_create_nodes was called with current_session
        calls = mock_neo4j_client.batch_create_nodes.call_args_list
        work_call = [c for c in calls if c[0][0] == "Work"]
        assert len(work_call) > 0
        # ... (check might vary by mock setup)

    def test_session_tag_passed_to_manager(self, importer_with_session, mock_session_manager, mock_openalex_client):
        """Test that tag is passed to session manager."""
        mock_openalex_client.search_works.return_value = []
        importer_with_session.import_from_query("test", limit=1, tag="my-import")

        mock_session_manager.create_session.assert_called_with(
            query="test", limit=1, expand_depth=1, tag="my-import",
        )
```

### 验收

```bash
pytest tests/test_importer.py -v  # 全部通过
```

### 出口

- `OpenAlexImporter` 接受可选的 `session_manager`
- `import_from_query` 自动创建和完成 session
- 节点写入时传递 `current_session`
- 向后兼容：无 `session_manager` 时行为不变

---

## 阶段 5：CLI 命令（session 管理 + stats + clear）

### 入口

- 阶段 4 完成
- `src/openalex_neo4j/cli.py` 已存在

### 操作目标文件

| 文件 | 操作 |
|------|------|
| `src/openalex_neo4j/cli.py` | 修改 |

### 步骤

#### 5.1 新增辅助函数

在所有命令之前、`@click.group()` 之后，添加共享的 Neo4j 连接函数：

```python
def _get_neo4j_client(neo4j_uri, neo4j_username, neo4j_password) -> Neo4jClient:
    """Create and connect a Neo4j client."""
    client = Neo4jClient(neo4j_uri, neo4j_username, neo4j_password)
    client.connect()
    return client


def _common_neo4j_options(f):
    """Decorator adding standard Neo4j connection options to a command."""
    f = click.option("--neo4j-uri",
        default=lambda: os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        help="Neo4j connection URI (env: NEO4J_URI)")(f)
    f = click.option("--neo4j-username",
        default=lambda: os.getenv("NEO4J_USERNAME", "neo4j"),
        help="Neo4j username (env: NEO4J_USERNAME)")(f)
    f = click.option("--neo4j-password",
        default=lambda: os.getenv("NEO4J_PASSWORD"),
        help="Neo4j password (env: NEO4J_PASSWORD)")(f)
    return f
```

#### 5.2 修改 import 命令

在 `import_data` 函数参数列表末尾追加：

```python
    tag: str | None = None,
    skip_constraints: bool = False,
```

对应的 `@click.option` decorator 添加在 `import_data` 函数定义上方：

```python
@click.option(
    "--tag", "-t",
    default=None,
    help="Optional tag/alias for this import session",
)
@click.option(
    "--skip-constraints",
    is_flag=True,
    help="Skip creating constraints and indexes (faster when Neo4j already set up)",
)
```

在函数体内，创建 `OpenAlexImporter` 之后、调用 `import_from_query` 之前插入：

```python
        # Initialize session manager for tracking
        from .session_manager import SessionManager
        session_manager = SessionManager(neo4j_client)

        # Create importer with session tracking
        importer = OpenAlexImporter(neo4j_client, openalex_client, session_manager=session_manager)
```

将 `import_from_query` 调用改为：

```python
        counts = importer.import_from_query(
            query, limit, expand_depth,
            skip_abstracts=skip_abstracts,
            generate_embeddings=generate_embeddings,
            tag=tag,
            skip_constraints=skip_constraints,
        )
```

在 import 完成后的输出末尾、`neo4j_client.close()` 之前添加 session ID 显示：

```python
        # Display session ID
        if importer.current_session:
            click.echo()
            click.echo(f"Session ID: {importer.current_session}")
            click.echo(f"Use 'openalex-neo4j session show {importer.current_session}' for details")
```

#### 5.3 新增 `clear` 命令

```python
@cli.command(name="clear")
@_common_neo4j_options
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt")
def clear_data(neo4j_uri, neo4j_username, neo4j_password, yes):
    """Clear ALL data from the Neo4j database.

    WARNING: This will permanently delete all nodes and relationships.
    """
    if not neo4j_password:
        click.echo("Error: Neo4j password is required", err=True)
        sys.exit(1)

    if not yes:
        click.echo()
        click.echo("!" * 70)
        click.echo("WARNING: This will permanently delete ALL data in the database!")
        click.echo("!" * 70)
        click.echo()
        click.confirm("Are you sure you want to continue?", abort=True)

    click.echo("Connecting to Neo4j...")
    neo4j_client = _get_neo4j_client(neo4j_uri, neo4j_username, neo4j_password)

    click.echo("Clearing database...")
    neo4j_client.clear_database()
    neo4j_client.close()

    click.echo("Database cleared successfully.")
```

#### 5.4 新增 `stats` 命令

```python
@cli.command(name="stats")
@_common_neo4j_options
def stats_command(neo4j_uri, neo4j_username, neo4j_password):
    """Show statistics about imported data in the Neo4j database."""
    if not neo4j_password:
        click.echo("Error: Neo4j password is required", err=True)
        sys.exit(1)

    neo4j_client = _get_neo4j_client(neo4j_uri, neo4j_username, neo4j_password)

    click.echo("=" * 70)
    click.echo("Database Statistics")
    click.echo("=" * 70)

    labels = ["Work", "Author", "Institution", "Source", "Topic", "Publisher", "Funder", "ImportSession"]
    for label in labels:
        try:
            count = neo4j_client.get_node_count(label)
            click.echo(f"  {label}: {count}")
        except Exception:
            click.echo(f"  {label}: (error)")

    click.echo()
    click.echo("Relationships:")
    rels = ["AUTHORED", "AFFILIATED_WITH", "PUBLISHED_IN", "CITES", "HAS_TOPIC", "FUNDED_BY", "PUBLISHED_BY"]
    for rel in rels:
        try:
            count = neo4j_client.get_relationship_count(rel)
            click.echo(f"  {rel}: {count}")
        except Exception:
            click.echo(f"  {rel}: (error)")

    click.echo("=" * 70)
    neo4j_client.close()
```

#### 5.5 新增 `sessions` 命令

```python
@cli.group(name="session")
def session_group():
    """Manage import sessions."""
    pass


@session_group.command(name="list")
@_common_neo4j_options
@click.option("--limit", default=20, type=int, help="Number of sessions to show")
def session_list(neo4j_uri, neo4j_username, neo4j_password, limit):
    """List all import sessions."""
    if not neo4j_password:
        click.echo("Error: Neo4j password is required", err=True)
        sys.exit(1)

    neo4j_client = _get_neo4j_client(neo4j_uri, neo4j_username, neo4j_password)
    manager = SessionManager(neo4j_client)
    sessions = manager.list_sessions(limit=limit)

    if not sessions:
        click.echo("No import sessions found.")
        neo4j_client.close()
        return

    click.echo("-" * 90)
    click.echo(f"{'Session ID':<20} {'Query':<30} {'Status':<12} {'Stats':<20}")
    click.echo("-" * 90)
    for s in sessions:
        stats_str = ""
        if s.stats:
            stats_str = f"works={s.stats.get('works', 0)}"
        tag_str = f" [{s.tag}]" if s.tag else ""
        click.echo(
            f"{s.id:<20} {s.query[:28]:<30} {s.status:<12} {stats_str:<20}"
            f"{tag_str}"
        )
    click.echo("-" * 90)

    neo4j_client.close()


@session_group.command(name="show")
@_common_neo4j_options
@click.argument("session_id")
def session_show(neo4j_uri, neo4j_username, neo4j_password, session_id):
    """Show details for a specific import session."""
    if not neo4j_password:
        click.echo("Error: Neo4j password is required", err=True)
        sys.exit(1)

    neo4j_client = _get_neo4j_client(neo4j_uri, neo4j_username, neo4j_password)
    manager = SessionManager(neo4j_client)
    session = manager.get_session(session_id)

    if not session:
        click.echo(f"Session '{session_id}' not found.", err=True)
        neo4j_client.close()
        sys.exit(1)

    click.echo("=" * 70)
    click.echo(f"Session: {session.id}")
    click.echo("=" * 70)
    click.echo(f"  Query:         {session.query}")
    click.echo(f"  Status:        {session.status}")
    click.echo(f"  Limit:         {session.limit}")
    click.echo(f"  Expand depth:  {session.expand_depth}")
    click.echo(f"  Tag:           {session.tag or '(none)'}")
    click.echo(f"  Created at:    {session.created_at}")
    if session.stats:
        click.echo()
        click.echo("  Import Stats:")
        for key, val in sorted(session.stats.items()):
            click.echo(f"    {key}: {val}")

    # Query current node counts for this session
    click.echo()
    click.echo("  Current nodes in this session (from graph):")
    try:
        node_counts = manager.get_session_node_counts(session_id)
        for label, count in sorted(node_counts.items()):
            click.echo(f"    {label}: {count}")
    except Exception as e:
        click.echo(f"    (could not query: {e})")

    click.echo("=" * 70)
    neo4j_client.close()


@session_group.command(name="delete")
@_common_neo4j_options
@click.argument("session_id")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt")
def session_delete(neo4j_uri, neo4j_username, neo4j_password, session_id, yes):
    """Delete all data associated with a session.

    This removes nodes that were exclusively created by this session,
    and unlinks shared nodes from this session's tracking.
    """
    if not neo4j_password:
        click.echo("Error: Neo4j password is required", err=True)
        sys.exit(1)

    if not yes:
        click.echo()
        click.echo(f"WARNING: This will delete all data for session '{session_id}'!")
        click.echo("This action cannot be undone.")
        click.echo()
        click.confirm("Are you sure you want to continue?", abort=True)

    neo4j_client = _get_neo4j_client(neo4j_uri, neo4j_username, neo4j_password)
    manager = SessionManager(neo4j_client)

    try:
        result = manager.delete_session(session_id)
        click.echo(f"Session '{session_id}' deleted.")
        click.echo(f"  Nodes removed:  {result.get('deleted', 0)}")
        click.echo(f"  Nodes updated:  {result.get('updated', 0)}")
    except Exception as e:
        click.echo(f"Error deleting session: {e}", err=True)
        sys.exit(1)
    finally:
        neo4j_client.close()


@session_group.command(name="tag")
@_common_neo4j_options
@click.argument("session_id")
@click.option("--name", required=True, help="Tag name for the session")
def session_tag(neo4j_uri, neo4j_username, neo4j_password, session_id, name):
    """Set a human-readable tag for a session."""
    if not neo4j_password:
        click.echo("Error: Neo4j password is required", err=True)
        sys.exit(1)

    neo4j_client = _get_neo4j_client(neo4j_uri, neo4j_username, neo4j_password)
    manager = SessionManager(neo4j_client)

    try:
        manager.tag_session(session_id, name)
        click.echo(f"Session '{session_id}' tagged as '{name}'.")
    except KeyError:
        click.echo(f"Session '{session_id}' not found.", err=True)
        sys.exit(1)
    finally:
        neo4j_client.close()
```

#### 5.6 保留旧的 `sessions` 快捷命令

在 `session_group` 之后，添加一个快捷别名 `sessions`（直接映射到 `session list`）：

```python
@cli.command(name="sessions")
@_common_neo4j_options
@click.option("--limit", default=20, type=int)
def sessions_shortcut(neo4j_uri, neo4j_username, neo4j_password, limit):
    """List all import sessions (shortcut for 'session list')."""
    # Delegate to session_list
    ctx = click.get_current_context()
    ctx.invoke(session_list, neo4j_uri=neo4j_uri, neo4j_username=neo4j_username,
               neo4j_password=neo4j_password, limit=limit)
```

#### 5.7 新增 prune 命令

```python
@cli.command(name="prune")
@_common_neo4j_options
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt")
def prune_data(neo4j_uri, neo4j_username, neo4j_password, yes):
    """Remove orphaned nodes (no import_sessions or empty import_sessions)."""
    if not neo4j_password:
        click.echo("Error: Neo4j password is required", err=True)
        sys.exit(1)

    neo4j_client = _get_neo4j_client(neo4j_uri, neo4j_username, neo4j_password)

    # Count orphaned nodes first
    with neo4j_client.driver.session() as session:
        result = session.run("""
            MATCH (n)
            WHERE n.import_sessions IS NULL
               OR n.import_sessions = []
            RETURN labels(n) as label, count(n) as count
            ORDER BY count DESC
        """)
        orphans = {record["label"][0]: record["count"] for record in result}

    if not orphans:
        click.echo("No orphaned nodes found.")
        neo4j_client.close()
        return

    total = sum(orphans.values())
    click.echo(f"Found {total} orphaned nodes:")
    for label, count in sorted(orphans.items(), key=lambda x: -x[1]):
        click.echo(f"  {label}: {count}")

    if not yes:
        click.echo()
        click.confirm(f"Delete these {total} nodes?", abort=True)

    with neo4j_client.driver.session() as session:
        result = session.run("""
            MATCH (n)
            WHERE n.import_sessions IS NULL
               OR n.import_sessions = []
            DETACH DELETE n
            RETURN count(n) as deleted
        """)
        deleted = result.single()["deleted"]

    click.echo(f"Deleted {deleted} orphaned nodes.")
    neo4j_client.close()
```

### 验收

```bash
openalex-neo4j --help  # 确认新命令出现在列表中
openalex-neo4j stats   # 正常输出数据统计
```

### 出口

- `clear` 命令（带确认提示）
- `stats` 命令（节点和关系计数）
- `session list` / `session show` / `session delete` / `session tag` 命令
- `sessions` 快捷命令
- `prune` 命令（清理孤立节点）

---

## 阶段 6：数据质量与清洗

### 入口

- `src/openalex_neo4j/models.py` 包含 ImportSession（阶段 1）
- `src/openalex_neo4j/session_manager.py` 可用（阶段 2）

### 操作目标文件

| 文件 | 操作 |
|------|------|
| `src/openalex_neo4j/data_quality.py` | 新建 |
| `tests/test_data_quality.py` | 新建 |

### 步骤

#### 6.1 创建 `data_quality.py`

```python
"""Data quality validation and cleaning pipeline."""
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


# --- 数据类 ---

@dataclass
class RuleViolation:
    """A single quality rule violation."""
    rule_name: str
    entity_id: str
    entity_type: str
    severity: str            # "error" | "warning" | "info"
    message: str
    field: str | None = None
    value: Any | None = None


@dataclass
class QualityReport:
    """Quality check report for a session's data."""
    session_id: str
    total_entities: int = 0
    violations: list[RuleViolation] = field(default_factory=list)
    created_at: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()

    @property
    def error_count(self) -> int:
        return sum(1 for v in self.violations if v.severity == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for v in self.violations if v.severity == "warning")

    @property
    def info_count(self) -> int:
        return sum(1 for v in self.violations if v.severity == "info")

    @property
    def summary(self) -> dict:
        return {
            "errors": self.error_count,
            "warnings": self.warning_count,
            "infos": self.info_count,
        }

    def by_entity_type(self) -> dict[str, list[RuleViolation]]:
        result: dict[str, list[RuleViolation]] = {}
        for v in self.violations:
            result.setdefault(v.entity_type, []).append(v)
        return result

    def by_severity(self) -> dict[str, list[RuleViolation]]:
        result: dict[str, list[RuleViolation]] = {}
        for v in self.violations:
            result.setdefault(v.severity, []).append(v)
        return result


# --- 校验规则基类 ---

class QualityRule(ABC):
    """Base class for a single quality validation rule."""

    name: str = ""
    description: str = ""
    severity: str = "info"
    applies_to: list[str] = []

    @abstractmethod
    def check(self, entity: Any) -> RuleViolation | None:
        """Check a single entity. Return a RuleViolation or None."""
        ...


# --- 预置规则实现 ---

class MissingTitleRule(QualityRule):
    name = "missing_title"
    description = "Work title is missing"
    severity = "error"
    applies_to = ["Work"]

    def check(self, entity: Any) -> RuleViolation | None:
        if not entity.title or not entity.title.strip():
            return RuleViolation(
                rule_name=self.name,
                entity_id=entity.id,
                entity_type="Work",
                severity=self.severity,
                message="Title is missing or empty",
                field="title",
                value=entity.title,
            )
        return None


class OutlierYearRule(QualityRule):
    name = "outlier_year"
    description = "Publication year is outside reasonable range"
    severity = "warning"
    applies_to = ["Work"]

    def __init__(self, min_year: int = 1900, max_year: int | None = None):
        self.min_year = min_year
        self.max_year = max_year or (datetime.now().year + 2)

    def check(self, entity: Any) -> RuleViolation | None:
        if entity.publication_year is not None:
            if entity.publication_year < self.min_year or entity.publication_year > self.max_year:
                return RuleViolation(
                    rule_name=self.name,
                    entity_id=entity.id,
                    entity_type="Work",
                    severity=self.severity,
                    message=f"Publication year {entity.publication_year} outside "
                            f"range [{self.min_year}, {self.max_year}]",
                    field="publication_year",
                    value=entity.publication_year,
                )
        return None


class MissingAbstractRule(QualityRule):
    name = "missing_abstract"
    description = "Work has no abstract"
    severity = "info"
    applies_to = ["Work"]

    def check(self, entity: Any) -> RuleViolation | None:
        if not entity.abstract:
            return RuleViolation(
                rule_name=self.name,
                entity_id=entity.id,
                entity_type="Work",
                severity=self.severity,
                message="Abstract is missing",
                field="abstract",
                value=None,
            )
        return None


class MissingDisplayNameRule(QualityRule):
    name = "missing_display_name"
    description = "Entity display_name is missing"
    severity = "error"
    applies_to = ["Author", "Institution", "Source", "Topic", "Publisher", "Funder"]

    def check(self, entity: Any) -> RuleViolation | None:
        if not entity.display_name or not entity.display_name.strip():
            return RuleViolation(
                rule_name=self.name,
                entity_id=entity.id,
                entity_type=type(entity).__name__,
                severity=self.severity,
                message="Display name is missing",
                field="display_name",
                value=entity.display_name,
            )
        return None


class EmptyEntityRule(QualityRule):
    name = "empty_entity"
    description = "Entity has only an ID, all other fields are empty"
    severity = "warning"
    applies_to = ["Work", "Author", "Institution", "Source", "Topic", "Publisher", "Funder"]

    # Fields that are allowed to be empty/NULL and not considered
    # "meaningful content" for empty-entity detection
    OPTIONAL_FIELDS = {"cited_by_count", "works_count", "embedding"}

    def check(self, entity: Any) -> RuleViolation | None:
        entity_type = type(entity).__name__

        # Collect all meaningful field values
        meaningful = []
        for field_name in entity.__dataclass_fields__:
            if field_name in ("id", "import_sessions", "first_imported_at", "last_imported_at",
                              *self.OPTIONAL_FIELDS):
                continue
            val = getattr(entity, field_name)
            if val is not None and val != [] and val != "":
                meaningful.append(field_name)

        # If only id has a value, this is an empty shell
        if len(meaningful) == 0:
            return RuleViolation(
                rule_name=self.name,
                entity_id=entity.id,
                entity_type=entity_type,
                severity=self.severity,
                message=f"{entity_type} has no meaningful data beyond ID",
                field=None,
                value=None,
            )
        return None


class InvalidWorkTypeRule(QualityRule):
    name = "invalid_work_type"
    description = "Work type is not a recognized OpenAlex type"
    severity = "warning"
    applies_to = ["Work"]

    VALID_TYPES = {
        "article", "book-chapter", "dataset", "dissertation", "book",
        "editorial", "erratum", "grant", "letter", "note", "paragraph",
        "reference-entry", "report", "review", "standard", "other",
    }

    def check(self, entity: Any) -> RuleViolation | None:
        if entity.type and entity.type not in self.VALID_TYPES:
            return RuleViolation(
                rule_name=self.name,
                entity_id=entity.id,
                entity_type="Work",
                severity=self.severity,
                message=f"Unknown work type: '{entity.type}'",
                field="type",
                value=entity.type,
            )
        return None


class ShortTitleRule(QualityRule):
    name = "short_title"
    description = "Title is suspiciously short (possibly a placeholder)"
    severity = "info"
    applies_to = ["Work"]

    def __init__(self, min_length: int = 10):
        self.min_length = min_length

    def check(self, entity: Any) -> RuleViolation | None:
        if entity.title and len(entity.title.strip()) < self.min_length:
            return RuleViolation(
                rule_name=self.name,
                entity_id=entity.id,
                entity_type="Work",
                severity=self.severity,
                message=f"Title is only {len(entity.title.strip())} characters (min {self.min_length})",
                field="title",
                value=entity.title,
            )
        return None


# --- 规则注册表 ---

class RuleCatalog:
    """Registry of all available quality rules."""

    def __init__(self):
        self._rules: dict[str, QualityRule] = {}

    def register(self, rule: QualityRule) -> None:
        """Register a single rule."""
        self._rules[rule.name] = rule

    def register_defaults(self) -> None:
        """Register all built-in rules."""
        self.register(MissingTitleRule())
        self.register(OutlierYearRule())
        self.register(MissingAbstractRule())
        self.register(MissingDisplayNameRule())
        self.register(EmptyEntityRule())
        self.register(InvalidWorkTypeRule())
        self.register(ShortTitleRule())

    def get(self, name: str) -> QualityRule | None:
        return self._rules.get(name)

    def get_for_entity(self, entity_type: str) -> list[QualityRule]:
        """Get all rules that apply to a given entity type."""
        return [r for r in self._rules.values() if entity_type in r.applies_to]

    def list(self) -> list[QualityRule]:
        return list(self._rules.values())


# --- 清洗管道 ---

class DataQualityPipeline:
    """Runs quality checks on a collection of entities and produces reports."""

    def __init__(self, catalog: RuleCatalog | None = None):
        self.catalog = catalog or RuleCatalog()
        if not self.catalog.list():
            self.catalog.register_defaults()

    def run(self, session_id: str, entities: dict[str, list[Any]]) -> QualityReport:
        """Run all applicable rules on a collection of entities.

        Args:
            session_id: The import session ID.
            entities: Dict mapping entity type names to lists of entities.
                Example: {"Work": [work1, work2], "Author": [author1]}

        Returns:
            QualityReport with all violations.
        """
        report = QualityReport(session_id=session_id)
        total = 0

        for entity_type, entity_list in entities.items():
            rules = self.catalog.get_for_entity(entity_type)
            total += len(entity_list)

            for entity in entity_list:
                for rule in rules:
                    try:
                        violation = rule.check(entity)
                        if violation:
                            report.violations.append(violation)
                    except Exception as e:
                        logger.warning(f"Rule {rule.name} failed on {entity.id}: {e}")

        report.total_entities = total
        return report


# --- 数据清洗函数 ---

def clean_entity_fields(entity: Any) -> dict[str, Any]:
    """Auto-fix common data quality issues on an entity.

    Modifies the entity in-place. Returns a dict of changes made.

    Fixes:
      - Strip whitespace from string fields
      - Convert empty strings to None
      - Set outlier years to None
    """
    changes = {}

    if hasattr(entity, "title") and isinstance(entity.title, str):
        stripped = entity.title.strip()
        if stripped == "":
            entity.title = None
            changes["title"] = "empty string -> None"
        elif stripped != entity.title:
            entity.title = stripped
            changes["title"] = "stripped whitespace"

    if hasattr(entity, "display_name") and isinstance(entity.display_name, str):
        stripped = entity.display_name.strip()
        if stripped == "":
            entity.display_name = None
            changes["display_name"] = "empty string -> None"
        elif stripped != entity.display_name:
            entity.display_name = stripped
            changes["display_name"] = "stripped whitespace"

    if hasattr(entity, "doi") and isinstance(entity.doi, str):
        # Normalize DOI: remove URL prefix if present
        doi = entity.doi.strip()
        for prefix in ["https://doi.org/", "http://doi.org/", "doi:"]:
            if doi.startswith(prefix):
                doi = doi[len(prefix):]
                entity.doi = doi
                changes["doi"] = f"normalized from URL"
                break

    if hasattr(entity, "publication_year") and entity.publication_year is not None:
        year = entity.publication_year
        max_year = datetime.now().year + 2
        if year < 1900 or year > max_year:
            entity.publication_year = None
            changes["publication_year"] = f"{year} -> None (outlier)"

    return changes
```

#### 6.2 创建 `tests/test_data_quality.py`

```python
"""Tests for data quality module."""
from openalex_neo4j.data_quality import (
    QualityReport,
    RuleCatalog,
    MissingTitleRule,
    OutlierYearRule,
    MissingAbstractRule,
    EmptyEntityRule,
    InvalidWorkTypeRule,
    ShortTitleRule,
    DataQualityPipeline,
    clean_entity_fields,
)
from openalex_neo4j.models import Work, Author


class TestQualityReport:
    """Tests for QualityReport."""

    def test_empty_report(self):
        report = QualityReport(session_id="S1")
        assert report.error_count == 0
        assert report.warning_count == 0
        assert report.info_count == 0
        assert report.summary == {"errors": 0, "warnings": 0, "infos": 0}

    def test_report_with_violations(self):
        from openalex_neo4j.data_quality import RuleViolation
        report = QualityReport(
            session_id="S1",
            total_entities=10,
            violations=[
                RuleViolation("r1", "W1", "Work", "error", "e1"),
                RuleViolation("r2", "A1", "Author", "warning", "w1"),
                RuleViolation("r3", "W2", "Work", "info", "i1"),
            ],
        )
        assert report.error_count == 1
        assert report.warning_count == 1
        assert report.info_count == 1

    def test_by_entity_type(self):
        from openalex_neo4j.data_quality import RuleViolation
        report = QualityReport(session_id="S1", violations=[
            RuleViolation("r1", "W1", "Work", "error", "e1"),
            RuleViolation("r2", "A1", "Author", "warning", "w1"),
            RuleViolation("r3", "W2", "Work", "info", "i1"),
        ])
        by_type = report.by_entity_type()
        assert len(by_type["Work"]) == 2
        assert len(by_type["Author"]) == 1


class TestQualityRules:
    """Tests for individual quality rules."""

    def test_missing_title_violation(self):
        rule = MissingTitleRule()
        work = Work(id="W1", title=None)
        v = rule.check(work)
        assert v is not None
        assert v.rule_name == "missing_title"

    def test_missing_title_ok(self):
        rule = MissingTitleRule()
        work = Work(id="W1", title="Good Title")
        assert rule.check(work) is None

    def test_missing_title_empty_string(self):
        rule = MissingTitleRule()
        work = Work(id="W1", title="")
        v = rule.check(work)
        assert v is not None

    def test_outlier_year_too_old(self):
        rule = OutlierYearRule(min_year=1900)
        work = Work(id="W1", title="Old", publication_year=1800)
        v = rule.check(work)
        assert v is not None

    def test_outlier_year_too_future(self):
        rule = OutlierYearRule(max_year=2099)
        work = Work(id="W1", title="Future", publication_year=3000)
        v = rule.check(work)
        assert v is not None

    def test_outlier_year_ok(self):
        rule = OutlierYearRule()
        work = Work(id="W1", title="Normal", publication_year=2023)
        assert rule.check(work) is None

    def test_outlier_year_none(self):
        rule = OutlierYearRule()
        work = Work(id="W1", title="No Year", publication_year=None)
        assert rule.check(work) is None

    def test_missing_abstract(self):
        rule = MissingAbstractRule()
        work = Work(id="W1", title="Test", abstract=None)
        assert rule.check(work) is not None

    def test_missing_abstract_ok(self):
        rule = MissingAbstractRule()
        work = Work(id="W1", title="Test", abstract="Has abstract")
        assert rule.check(work) is None

    def test_empty_entity_only_id(self):
        rule = EmptyEntityRule()
        work = Work(id="W1")
        v = rule.check(work)
        assert v is not None
        assert v.rule_name == "empty_entity"

    def test_empty_entity_with_data(self):
        rule = EmptyEntityRule()
        work = Work(id="W1", title="Real Paper", publication_year=2023)
        assert rule.check(work) is None

    def test_invalid_work_type(self):
        rule = InvalidWorkTypeRule()
        work = Work(id="W1", title="Test", type="not-a-real-type")
        v = rule.check(work)
        assert v is not None

    def test_valid_work_type(self):
        rule = InvalidWorkTypeRule()
        work = Work(id="W1", title="Test", type="article")
        assert rule.check(work) is None

    def test_short_title(self):
        rule = ShortTitleRule(min_length=10)
        work = Work(id="W1", title="Short")
        v = rule.check(work)
        assert v is not None

    def test_short_title_ok(self):
        rule = ShortTitleRule(min_length=5)
        work = Work(id="W1", title="Long enough title")
        assert rule.check(work) is None


class TestRuleCatalog:
    """Tests for RuleCatalog."""

    def test_register_defaults(self):
        catalog = RuleCatalog()
        catalog.register_defaults()
        assert len(catalog.list()) >= 7  # at least 7 built-in rules

    def test_get_for_entity(self):
        catalog = RuleCatalog()
        catalog.register_defaults()
        work_rules = catalog.get_for_entity("Work")
        assert len(work_rules) >= 5  # most rules apply to Work
        author_rules = catalog.get_for_entity("Author")
        assert len(author_rules) >= 2  # display_name + empty_entity


class TestDataQualityPipeline:
    """Tests for DataQualityPipeline."""

    def test_run_on_works(self):
        pipeline = DataQualityPipeline()
        entities = {
            "Work": [
                Work(id="W1", title="Good Paper", publication_year=2023),
                Work(id="W2", title=None),                           # missing title
                Work(id="W3", title="Old", publication_year=1800),   # outlier
            ],
        }
        report = pipeline.run("S1", entities)
        assert report.total_entities == 3
        # W2 missing title -> error
        assert report.error_count >= 1
        # W3 outlier year -> warning
        assert report.warning_count >= 1

    def test_run_on_author(self):
        pipeline = DataQualityPipeline()
        entities = {
            "Author": [
                Author(id="A1", display_name="John Doe"),
                Author(id="A2", display_name=None),  # missing name
            ],
        }
        report = pipeline.run("S1", entities)
        assert report.total_entities == 2
        assert report.error_count >= 1


class TestCleanEntityFields:
    """Tests for clean_entity_fields."""

    def test_strip_title_whitespace(self):
        work = Work(id="W1", title="  Hello World  ")
        changes = clean_entity_fields(work)
        assert work.title == "Hello World"
        assert "title" in changes

    def test_empty_title_to_none(self):
        work = Work(id="W1", title="   ")
        changes = clean_entity_fields(work)
        assert work.title is None
        assert "title" in changes

    def test_normalize_doi(self):
        work = Work(id="W1", title="Test", doi="https://doi.org/10.1234/abc")
        clean_entity_fields(work)
        assert work.doi == "10.1234/abc"

    def test_normalize_doi_short(self):
        work = Work(id="W1", title="Test", doi="doi:10.1234/abc")
        clean_entity_fields(work)
        assert work.doi == "10.1234/abc"

    def test_outlier_year_to_none(self):
        work = Work(id="W1", title="Test", publication_year=1800)
        clean_entity_fields(work)
        assert work.publication_year is None

    def test_good_year_unchanged(self):
        work = Work(id="W1", title="Test", publication_year=2023)
        clean_entity_fields(work)
        assert work.publication_year == 2023
```

### 验收

```bash
pytest tests/test_data_quality.py -v  # 全部通过
```

### 出口

- `QualityRule` 抽象基类 + 7 条预置规则
- `RuleCatalog` 注册表
- `DataQualityPipeline` 管道
- `clean_entity_fields` 清洗函数
- `QualityReport` 报告数据类

---

## 阶段 7：CLI 集成质量报告

### 入口

- 阶段 6 完成
- 阶段 5 完成（session 管理 CLI 存在）

### 操作目标文件

| 文件 | 操作 |
|------|------|
| `src/openalex_neo4j/cli.py` | 修改 import + 新增 report 命令 |

### 步骤

#### 7.1 import 命令增加清洗参数

在 `import_data` 参数列表追加：

```python
    clean_level: str = "off",
    quality_report: bool = False,
```

对应的 decorator：

```python
@click.option(
    "--clean",
    type=click.Choice(["off", "report", "auto"]),
    default="off",
    help="Data cleaning level: off (default), report, or auto",
)
@click.option(
    "--quality-report", "--qr",
    is_flag=True,
    help="Print quality report after import",
)
```

在 `import_data` 函数体内，`importer.import_from_query` 调用之后、打印统计之前插入：

```python
        # Run quality check if requested
        if clean_level != "off" or quality_report:
            from .data_quality import DataQualityPipeline, clean_entity_fields

            pipeline = DataQualityPipeline()

            # Build entities dict from importer collections
            entities = {}
            if importer.works:
                entities["Work"] = list(importer.works.values())
            if importer.authors:
                entities["Author"] = list(importer.authors.values())
            if importer.institutions:
                entities["Institution"] = list(importer.institutions.values())
            if importer.sources:
                entities["Source"] = list(importer.sources.values())
            if importer.topics:
                entities["Topic"] = list(importer.topics.values())

            # Auto-clean if requested
            if clean_level == "auto":
                total_changes = 0
                for entity_list in entities.values():
                    for entity in entity_list:
                        changes = clean_entity_fields(entity)
                        total_changes += len(changes)
                if total_changes > 0:
                    click.echo(f"Auto-cleaned {total_changes} fields")

            # Run quality check
            report = pipeline.run(
                importer.current_session or "unknown",
                entities,
            )

            # Store quality summary in session
            if importer.session_manager and importer.current_session:
                importer.session_manager.complete_session(
                    importer.current_session,
                    stats=counts,
                    quality_summary=report.summary,
                )

            # Print summary if requested
            if quality_report:
                click.echo()
                click.echo("=" * 70)
                click.echo("Quality Report")
                click.echo("=" * 70)
                click.echo(f"  Total entities checked: {report.total_entities}")
                click.echo(f"  Errors:   {report.error_count}")
                click.echo(f"  Warnings: {report.warning_count}")
                click.echo(f"  Infos:    {report.info_count}")
                if report.violations:
                    click.echo()
                    click.echo("  Top violations:")
                    for v in report.violations[:10]:
                        click.echo(f"    [{v.severity.upper()}] {v.entity_type}:{v.entity_id} - {v.message}")
                    if len(report.violations) > 10:
                        click.echo(f"    ... and {len(report.violations) - 10} more")
                click.echo("=" * 70)
                click.echo()
```

注意：需要将原有的 `session_manager.complete_session` 调用合并到这个分支逻辑中，避免重复调用。可以用条件判断重构一下：

```python
        # Complete import session
        if importer.session_manager and importer.current_session:
            quality_summary = None
            if clean_level != "off" or quality_report:
                # quality check logic above would set quality_summary
                ...
            importer.session_manager.complete_session(
                importer.current_session,
                stats=counts,
                quality_summary=quality_summary,
            )
```

#### 7.2 新增 `report` 命令

```python
@cli.group(name="report")
def report_group():
    """View quality reports for import sessions."""
    pass


@report_group.command(name="show")
@_common_neo4j_options
@click.argument("session_id")
def report_show(neo4j_uri, neo4j_username, neo4j_password, session_id):
    """Show quality report for a session."""
    if not neo4j_password:
        click.echo("Error: Neo4j password is required", err=True)
        sys.exit(1)

    neo4j_client = _get_neo4j_client(neo4j_uri, neo4j_username, neo4j_password)
    manager = SessionManager(neo4j_client)
    session = manager.get_session(session_id)

    if not session:
        click.echo(f"Session '{session_id}' not found.", err=True)
        neo4j_client.close()
        sys.exit(1)

    if not session.quality_summary:
        click.echo(f"No quality report available for session '{session_id}'.")
        click.echo("Run import with --quality-report or --clean report/auto to generate one.")
        neo4j_client.close()
        return

    click.echo("=" * 70)
    click.echo(f"Quality Report: {session_id}")
    click.echo("=" * 70)
    click.echo(f"  Query:     {session.query}")
    click.echo(f"  Errors:    {session.quality_summary.get('errors', 'N/A')}")
    click.echo(f"  Warnings:  {session.quality_summary.get('warnings', 'N/A')}")
    click.echo(f"  Infos:     {session.quality_summary.get('infos', 'N/A')}")
    click.echo("=" * 70)
    neo4j_client.close()


@report_group.command(name="list")
@_common_neo4j_options
@click.option("--limit", default=20, type=int)
def report_list(neo4j_uri, neo4j_username, neo4j_password, limit):
    """List sessions that have quality reports."""
    if not neo4j_password:
        click.echo("Error: Neo4j password is required", err=True)
        sys.exit(1)

    neo4j_client = _get_neo4j_client(neo4j_uri, neo4j_username, neo4j_password)
    manager = SessionManager(neo4j_client)
    sessions = manager.list_sessions(limit=limit)

    click.echo("-" * 80)
    click.echo(f"{'Session ID':<20} {'Errors':<8} {'Warnings':<10} {'Infos':<8}")
    click.echo("-" * 80)

    count = 0
    for s in sessions:
        if s.quality_summary:
            click.echo(
                f"{s.id:<20} "
                f"{s.quality_summary.get('errors', 0):<8} "
                f"{s.quality_summary.get('warnings', 0):<10} "
                f"{s.quality_summary.get('infos', 0):<8}"
            )
            count += 1

    if count == 0:
        click.echo("No sessions with quality reports found.")
    click.echo("-" * 80)
    neo4j_client.close()
```

### 验收

```bash
openalex-neo4j import --query "test" --limit 1 --quality-report  # 导入并显示质量报告
openalex-neo4j report list                                         # 列出有质量报告的 session
openalex-neo4j report show <session_id>                            # 查看某次的质量报告
```

### 出口

- import 支持 `--clean` / `--quality-report` 参数
- `report show` / `report list` 命令

---

## 阶段 8：DataSource 抽象基类与第二数据源

### 入口

- `src/openalex_neo4j/` 目录存在

### 操作目标文件

| 文件 | 操作 |
|------|------|
| `src/openalex_neo4j/datasource/__init__.py` | 新建 |
| `src/openalex_neo4j/datasource/base.py` | 新建 |
| `src/openalex_neo4j/datasource/openalex_impl.py` | 新建 |
| `tests/test_datasource.py` | 新建 |

### 步骤

#### 8.1 创建 `datasource/__init__.py`

```python
"""Data source adapters for enriching data from multiple sources."""
from .base import DataSource, DataRecord, merge_record

_datasource_registry: dict[str, type[DataSource]] = {}


def register_datasource(cls: type[DataSource]) -> type[DataSource]:
    """Decorator: register a DataSource class."""
    instance = cls()  # instantiate to get .name
    _datasource_registry[instance.name] = cls
    return cls


def get_datasource(name: str, **config) -> DataSource:
    """Get a DataSource instance by name."""
    if name not in _datasource_registry:
        raise KeyError(f"Unknown datasource: '{name}'. Available: {list(_datasource_registry.keys())}")
    return _datasource_registry[name](**config)


def list_datasources() -> list[str]:
    """List all registered datasource names."""
    return list(_datasource_registry.keys())
```

#### 8.2 创建 `datasource/base.py`

```python
"""Abstract base class for data source adapters."""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class DataRecord:
    """Standard record format that all data source adapters must output.

    Fields are categorized as:
      - REQUIRED: source_name, source_confidence, external_ids, raw_data
      - Work fields: title, abstract, publication_date, doi, authors, source_display_name
      - Author fields: display_name, orcid
    """
    # --- 标识字段（REQUIRED） ---
    source_name: str
    source_confidence: float            # 0.0 ~ 1.0
    external_ids: dict[str, str]        # e.g. {"doi": "10.1234/abc"}
    raw_data: dict[str, Any]            # original API response JSON

    # --- 映射字段 ---
    openalex_id: str | None = None

    # --- Work 字段 ---
    title: str | None = None
    abstract: str | None = None
    publication_date: str | None = None  # ISO format
    doi: str | None = None
    authors: list[dict] | None = None    # see format below
    source_display_name: str | None = None

    # --- Author 字段 ---
    display_name: str | None = None
    orcid: str | None = None

    def __post_init__(self):
        """Validate required fields."""
        if not self.source_name:
            raise ValueError("source_name is required")
        if not 0.0 <= self.source_confidence <= 1.0:
            raise ValueError(f"source_confidence must be between 0 and 1, got {self.source_confidence}")


# Author entry format for DataRecord.authors list:
# [
#   {
#       "name": "John Doe",                          # str, required
#       "orcid": "0000-0000-0000-0000",              # str, optional
#       "position": "first",                         # str: "first"|"last"|"corresponding"|"middle", optional
#       "affiliations": ["Massachusetts Institute of Technology"],  # list[str], optional
#   },
# ]


class DataSource(ABC):
    """Abstract base class for a data source adapter.

    Subclasses must implement:
      - name (property)
      - fetch_by_doi()
      - fetch_by_openalex_id()
      - confidence()
      - to_openalex_id()
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier for this data source, e.g. 'crossref'."""
        ...

    @abstractmethod
    def fetch_by_doi(self, doi: str) -> DataRecord | None:
        """Fetch a record by DOI. Returns None if not found."""
        ...

    @abstractmethod
    def fetch_by_openalex_id(self, openalex_id: str) -> DataRecord | None:
        """Fetch a record by OpenAlex ID. Returns None if not found."""
        ...

    def fetch_by_title(self, title: str, year: int | None = None) -> DataRecord | None:
        """Fetch by title (fuzzy match). Default implementation returns None.

        Subclasses that support title search should override this.
        """
        return None

    @abstractmethod
    def confidence(self, record: DataRecord) -> float:
        """Return how confident we are in this record, 0.0 ~ 1.0.

        The confidence should reflect how the record was matched:
          - DOI exact match: 0.95
          - Title exact match: 0.8
          - Title fuzzy match: 0.6
        """
        ...

    @abstractmethod
    def to_openalex_id(self, record: DataRecord) -> str | None:
        """Map a DataRecord back to an OpenAlex ID, if possible.

        For example, lookup the DOI in OpenAlex to find the corresponding Wxxxxxx ID.
        """
        ...

    def batch_fetch(self, queries: list[dict]) -> list[DataRecord | None]:
        """Batch fetch multiple records.

        Each query dict must have one of: 'doi', 'openalex_id', 'title'.
        Default implementation calls individual methods sequentially.
        Subclasses may override for parallel fetching.

        Args:
            queries: [{"doi": "10.xxx/yyy"}, {"openalex_id": "W123"}, {"title": "paper title", "year": 2023}]
        """
        results = []
        for q in queries:
            if "doi" in q:
                results.append(self.fetch_by_doi(q["doi"]))
            elif "openalex_id" in q:
                results.append(self.fetch_by_openalex_id(q["openalex_id"]))
            elif "title" in q:
                results.append(self.fetch_by_title(q["title"], q.get("year")))
            else:
                results.append(None)
        return results


# --- 合并策略 ---

FIELD_MAP = {
    "title": "title",
    "abstract": "abstract",
    "publication_date": "publication_date",
    "doi": "doi",
    "source_display_name": "source_display_name",
    "display_name": "display_name",
    "orcid": "orcid",
}


def merge_record(target: dict, source: DataRecord, strategy: str = "fill_null") -> dict[str, Any]:
    """Merge a DataRecord into a target node dict.

    Args:
        target: Existing node properties dict.
        source: DataRecord from a data source.
        strategy: "fill_null" (only fill None fields) or "overwrite" (replace all).

    Returns:
        Dict of changes made: {field_name: (old_value, new_value)}.
    """
    if strategy not in ("fill_null", "overwrite"):
        raise ValueError(f"Unknown merge strategy: '{strategy}'")

    changes: dict[str, Any] = {}

    for target_field, source_field in FIELD_MAP.items():
        source_val = getattr(source, source_field, None)
        if source_val is None:
            continue

        # Only merge if source has confidence >= 0.5
        if source.source_confidence < 0.5:
            continue

        if target_field not in target or target[target_field] is None:
            target[target_field] = source_val
            changes[target_field] = (None, source_val)
        elif strategy == "overwrite" and source.source_confidence > 0.9:
            old_val = target[target_field]
            target[target_field] = source_val
            changes[target_field] = (old_val, source_val)

    return changes
```

#### 8.3 创建 `datasource/openalex_impl.py`

```python
"""OpenAlex itself as a data source (for re-fetching to get missing fields)."""
import logging

import pyalex
from pyalex import Works

from .base import DataSource, DataRecord
from ..models import Work

logger = logging.getLogger(__name__)


class OpenAlexSource(DataSource):
    """Use OpenAlex API as a data source for enrichment.

    Useful for re-fetching a work by ID to get fields that were
    missed or stripped in the initial import (e.g., abstracts).
    """

    def __init__(self, email: str | None = None):
        if email:
            pyalex.config.email = email

    @property
    def name(self) -> str:
        return "openalex"

    def fetch_by_openalex_id(self, openalex_id: str) -> DataRecord | None:
        """Re-fetch a work from OpenAlex by ID."""
        try:
            works = Works().filter(openalex_id=f"https://openalex.org/{openalex_id}").get()
            if not works:
                return None
            data = works[0]
            work = Work.from_openalex(data)
            return self._work_to_record(work, data)
        except Exception as e:
            logger.warning(f"OpenAlex fetch failed for {openalex_id}: {e}")
            return None

    def fetch_by_doi(self, doi: str) -> DataRecord | None:
        try:
            results = Works().filter(doi=doi).get()
            if not results:
                return None
            data = results[0]
            work = Work.from_openalex(data)
            return self._work_to_record(work, data)
        except Exception as e:
            logger.warning(f"OpenAlex fetch by DOI failed for {doi}: {e}")
            return None

    def _work_to_record(self, work: Work, raw_data: dict) -> DataRecord:
        return DataRecord(
            source_name=self.name,
            source_confidence=1.0,
            openalex_id=work.id,
            external_ids={"doi": work.doi} if work.doi else {},
            raw_data=raw_data,
            title=work.title,
            abstract=work.abstract,
            publication_date=work.publication_date,
            doi=work.doi,
            source_confidence=1.0,
        )

    def confidence(self, record: DataRecord) -> float:
        return 1.0  # OpenAlex is the source of truth

    def to_openalex_id(self, record: DataRecord) -> str | None:
        return record.openalex_id
```

#### 8.4 创建 `tests/test_datasource.py`

```python
"""Tests for data source adapters."""
from openalex_neo4j.datasource.base import DataRecord, merge_record, FIELD_MAP
from openalex_neo4j.datasource.openalex_impl import OpenAlexSource


class TestDataRecord:
    """Tests for DataRecord."""

    def test_minimal_record(self):
        record = DataRecord(
            source_name="test",
            source_confidence=0.5,
            external_ids={"doi": "10.1234/abc"},
            raw_data={},
        )
        assert record.source_name == "test"
        assert record.source_confidence == 0.5

    def test_record_with_work_fields(self):
        record = DataRecord(
            source_name="test",
            source_confidence=0.9,
            external_ids={"doi": "10.1234/abc"},
            raw_data={},
            title="Test Paper",
            abstract="This is an abstract",
            doi="10.1234/abc",
        )
        assert record.title == "Test Paper"
        assert record.abstract == "This is an abstract"

    def test_invalid_confidence_low(self):
        with pytest.raises(ValueError):
            DataRecord(source_name="test", source_confidence=-0.1, external_ids={}, raw_data={})

    def test_invalid_confidence_high(self):
        with pytest.raises(ValueError):
            DataRecord(source_name="test", source_confidence=1.5, external_ids={}, raw_data={})

    def test_missing_source_name(self):
        with pytest.raises(ValueError):
            DataRecord(source_name="", source_confidence=0.5, external_ids={}, raw_data={})


class TestMergeRecord:
    """Tests for merge_record function."""

    def test_fill_null_basic(self):
        target = {"id": "W1", "title": "Existing", "abstract": None}
        source = DataRecord(
            source_name="test", source_confidence=0.9,
            external_ids={}, raw_data={},
            abstract="New abstract from source",
        )
        changes = merge_record(target, source, strategy="fill_null")
        assert target["abstract"] == "New abstract from source"
        assert "abstract" in changes

    def test_fill_null_does_not_overwrite(self):
        target = {"id": "W1", "title": "Existing", "abstract": "Already have"}
        source = DataRecord(
            source_name="test", source_confidence=0.9,
            external_ids={}, raw_data={},
            abstract="Would overwrite",
        )
        changes = merge_record(target, source, strategy="fill_null")
        assert target["abstract"] == "Already have"
        assert changes == {}  # no changes made

    def test_overwrite_strategy(self):
        target = {"id": "W1", "title": "Old Title"}
        source = DataRecord(
            source_name="test", source_confidence=0.95,
            external_ids={}, raw_data={},
            title="New Title",
        )
        changes = merge_record(target, source, strategy="overwrite")
        assert target["title"] == "New Title"
        assert "title" in changes

    def test_low_confidence_no_merge(self):
        target = {"id": "W1", "abstract": None}
        source = DataRecord(
            source_name="test", source_confidence=0.3,
            external_ids={}, raw_data={},
            abstract="Low confidence abstract",
        )
        changes = merge_record(target, source, strategy="fill_null")
        assert target["abstract"] is None
        assert changes == {}

    def test_invalid_strategy(self):
        source = DataRecord(source_name="test", source_confidence=0.5, external_ids={}, raw_data={})
        with pytest.raises(ValueError):
            merge_record({}, source, strategy="invalid")


class TestOpenAlexSource:
    """Tests for OpenAlexSource adapter."""

    def test_name(self):
        source = OpenAlexSource()
        assert source.name == "openalex"

    def test_confidence(self):
        source = OpenAlexSource()
        record = DataRecord(source_name="openalex", source_confidence=1.0, external_ids={}, raw_data={})
        assert source.confidence(record) == 1.0
```

### 验收

```bash
pytest tests/test_datasource.py -v  # 全部通过
```

### 出口

- `DataSource` 抽象基类，定义 6 个方法
- `DataRecord` 标准格式（包含字段规范）
- `merge_record` 合并函数（fill_null / overwrite）
- `OpenAlexSource` 适配器
- 数据源注册表

---

## 阶段 9：enrich CLI 命令

### 入口

- 阶段 8 完成（DataSource 体系存在）
- 阶段 4 完成（session 追踪存在）

### 操作目标文件

| 文件 | 操作 |
|------|------|
| `src/openalex_neo4j/cli.py` | 添加 enrich 命令 |

### 步骤

#### 9.1 添加 `enrich` 命令

```python
@cli.command(name="enrich")
@_common_neo4j_options
@click.option("--session", "session_id", help="Session ID to enrich (omit for all unsessioned works)")
@click.option("--datasource", "datasource_names", multiple=True,
              default=["openalex"], help="Data source(s) to use (can repeat, tried in order)")
@click.option("--strategy", default="fill_null", type=click.Choice(["fill_null", "overwrite"]),
              help="Merge strategy")
@click.option("--dry-run", is_flag=True, help="Show what would be enriched without writing")
@click.option("--limit", default=None, type=int, help="Max works to enrich")
def enrich_command(neo4j_uri, neo4j_username, neo4j_password,
                   session_id, datasource_names, strategy, dry_run, limit):
    """Enrich works with data from additional sources.

    Fills missing fields (abstract, etc.) by querying other data sources.
    """
    if not neo4j_password:
        click.echo("Error: Neo4j password is required", err=True)
        sys.exit(1)

    neo4j_client = _get_neo4j_client(neo4j_uri, neo4j_username, neo4j_password)

    # Resolve datasources
    from .datasource import get_datasource, list_datasources
    sources = []
    for name in datasource_names:
        try:
            ds = get_datasource(name)
            sources.append(ds)
        except KeyError:
            click.echo(f"Unknown datasource: '{name}'. Available: {list_datasources()}", err=True)
            neo4j_client.close()
            sys.exit(1)

    if not sources:
        click.echo("No datasources specified.", err=True)
        neo4j_client.close()
        sys.exit(1)

    # Find works to enrich
    with neo4j_client.driver.session() as session:
        if session_id:
            # Enrich works from a specific session
            result = session.run("""
                MATCH (w:Work)
                WHERE $session_id IN w.import_sessions
                RETURN w.id as id, w.title as title, w.doi as doi
            """, session_id=session_id)
        else:
            # Enrich all works
            result = session.run("""
                MATCH (w:Work)
                RETURN w.id as id, w.title as title, w.doi as doi
            """)

        works = []
        for record in result:
            works.append({
                "id": record["id"],
                "title": record["title"],
                "doi": record["doi"],
            })

    if limit:
        works = works[:limit]

    if not works:
        click.echo("No works found to enrich.")
        neo4j_client.close()
        return

    click.echo(f"Found {len(works)} works to enrich.")
    click.echo(f"Data sources: {', '.join(datasource_names)}")
    click.echo(f"Strategy: {strategy}")
    if dry_run:
        click.echo("DRY RUN: no changes will be written.")

    # Enrich each work
    enriched_count = 0
    total_changes = 0

    for i, work in enumerate(works):
        if (i + 1) % 50 == 0:
            click.echo(f"  Progress: {i + 1}/{len(works)}...")

        # Try each datasource in order until we get a record
        record = None
        used_source = None
        for ds in sources:
            if work.get("doi"):
                record = ds.fetch_by_doi(work["doi"])
                if record:
                    used_source = ds.name
                    break
            if work.get("id"):
                record = ds.fetch_by_openalex_id(work["id"])
                if record:
                    used_source = ds.name
                    break

        if record is None:
            continue

        # Merge into a dict of existing properties
        target = {"title": work.get("title")}
        changes = merge_record(target, record, strategy=strategy)

        if not changes:
            continue

        if not dry_run:
            # Write changes to Neo4j
            with neo4j_client.driver.session() as ws:
                for field, (old_val, new_val) in changes.items():
                    ws.run(
                        f"MATCH (w:Work {{id: $id}}) SET w.{field} = $val",
                        id=work["id"], val=new_val,
                    )

        enriched_count += 1
        total_changes += len(changes)

        if dry_run:
            click.echo(f"  [{used_source}] {work['id']}: {len(changes)} fields to update")

    click.echo()
    if dry_run:
        click.echo(f"Dry-run complete. Would enrich {enriched_count} works ({total_changes} field changes).")
    else:
        click.echo(f"Enriched {enriched_count} works ({total_changes} field changes).")

    neo4j_client.close()
```

### 验收

```bash
openalex-neo4j enrich --session <session_id> --datasource openalex --dry-run  # dry-run 查看效果
```

### 出口

- `enrich` 命令支持 session 过滤、多数据源回退、dry-run、limit

---

## 阶段 10：集成验收

### 前置条件

所有阶段 1–9 完成。

### 验收步骤

按以下顺序运行：

```bash
# 1. 单元测试全部通过
pytest tests/ -v --ignore=tests/integration

# 2. 查看 CLI 帮助
openalex-neo4j --help

# 3. 执行导入并记录 session
openalex-neo4j import --query "machine learning" --limit 3 --tag "test-ml" --quality-report

# 4. 查看 session 列表
openalex-neo4j sessions

# 5. 查看某次 session 详情
openalex-neo4j session show <session_id>

# 6. 查看统计
openalex-neo4j stats

# 7. 查看质量报告
openalex-neo4j report show <session_id>

# 8. 再次导入不同主题
openalex-neo4j import --query "quantum computing" --limit 3 --tag "test-qc"

# 9. 验证两次 session 都存在
openalex-neo4j sessions

# 10. 删除第一次导入
openalex-neo4j session delete <first_session_id>

# 11. 验证第一次的数据已被清理
openalex-neo4j session show <first_session_id>  # 应显示 not found
openalex-neo4j stats                              # 计数应减少

# 12. 测试 enrich (dry-run)
openalex-neo4j enrich --session <remaining_session_id> --datasource openalex --dry-run

# 13. 清空全部数据
openalex-neo4j clear -y

# 14. 验证清空
openalex-neo4j stats   # 所有计数应为 0
```

### 回归验证

```bash
# 旧功能仍可工作
openalex-neo4j search --query "machine learning" --limit 5
```

---

## 变更文件清单汇总

| 文件 | 操作 | 所在阶段 |
|------|------|----------|
| `src/openalex_neo4j/models.py` | 修改 | 1 |
| `src/openalex_neo4j/session_manager.py` | 新建 | 2 |
| `src/openalex_neo4j/neo4j_client.py` | 修改 | 3 |
| `src/openalex_neo4j/importer.py` | 修改 | 4 |
| `src/openalex_neo4j/cli.py` | 修改 | 5, 7, 9 |
| `src/openalex_neo4j/data_quality.py` | 新建 | 6 |
| `src/openalex_neo4j/datasource/__init__.py` | 新建 | 8 |
| `src/openalex_neo4j/datasource/base.py` | 新建 | 8 |
| `src/openalex_neo4j/datasource/openalex_impl.py` | 新建 | 8 |
| `tests/conftest.py` | 新建 | 0 |
| `tests/test_session_manager.py` | 新建 | 2 |
| `tests/test_data_quality.py` | 新建 | 6 |
| `tests/test_datasource.py` | 新建 | 8 |
| `tests/test_models.py` | 追加 | 1 |
| `tests/test_neo4j_client.py` | 追加 | 3 |
| `tests/test_importer.py` | 追加 | 4 |

总计：**5 个新建文件，6 个现有文件修改，3 个测试文件追加**。
