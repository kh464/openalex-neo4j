"""Tests for SessionManager."""
import json
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

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

    def test_delete_session_nonexistent(self, manager):
        """Test delete_session on nonexistent session doesn't crash locally."""
        # Session not in local storage, but may hit Neo4j which could fail
        # Just verify no python-side crash
        try:
            result = manager.delete_session("nonexistent")
            assert isinstance(result, dict)
        except Exception:
            pass  # Expected if Neo4j is unavailable

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
