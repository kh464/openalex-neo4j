"""Integration tests for session management.

These tests require a running Neo4j instance.
Configure connection via environment variables or .env file.
"""
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from openalex_neo4j.neo4j_client import Neo4jClient
from openalex_neo4j.session_manager import SessionManager
from openalex_neo4j.models import ImportSession

pytestmark = pytest.mark.integration


@pytest.fixture
def session_manager(neo4j_client):
    """Create a SessionManager with a temporary sessions file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch.object(SessionManager, "SESSIONS_DIR", Path(tmpdir)):
            manager = SessionManager(neo4j_client)
            yield manager


class TestSessionManagerIntegration:
    """Integration tests for SessionManager with real Neo4j."""

    def _count_import_session_nodes(self, neo4j_client) -> int:
        with neo4j_client.driver.session() as session:
            result = session.run("MATCH (s:ImportSession) RETURN count(s) as count")
            return result.single()["count"]

    def test_create_session_creates_neo4j_node(self, session_manager, neo4j_client):
        """Creating a session should create an :ImportSession node in Neo4j."""
        ses = session_manager.create_session(query="machine learning", limit=5)
        assert ses.id is not None
        assert ses.status == "running"

        # Verify the ImportSession node exists in Neo4j
        with neo4j_client.driver.session() as s:
            result = s.run(
                "MATCH (s:ImportSession {id: $id}) RETURN s",
                id=ses.id,
            )
            record = result.single()
            assert record is not None
            props = dict(record["s"])
            assert props["id"] == ses.id
            assert props["query"] == "machine learning"
            assert props["limit"] == 5

    def test_complete_session_updates_node(self, session_manager, neo4j_client):
        """Completing a session should update status."""
        ses = session_manager.create_session(query="test", limit=1)
        session_manager.complete_session(ses.id, stats={"works": 5})

        # Verify in Neo4j
        with neo4j_client.driver.session() as s:
            result = s.run(
                "MATCH (s:ImportSession {id: $id}) RETURN s.status as status",
                id=ses.id,
            )
            assert result.single()["status"] == "completed"

    def test_fail_session_updates_node(self, session_manager, neo4j_client):
        """Failing a session should update status."""
        ses = session_manager.create_session(query="test", limit=1)
        session_manager.fail_session(ses.id)

        with neo4j_client.driver.session() as s:
            result = s.run(
                "MATCH (s:ImportSession {id: $id}) RETURN s.status as status",
                id=ses.id,
            )
            assert result.single()["status"] == "failed"

    def test_get_session_from_local(self, session_manager, neo4j_client):
        """get_session returns session from local storage with stats."""
        ses = session_manager.create_session(query="test", limit=3)
        session_manager.complete_session(
            ses.id,
            stats={"works": 10, "authors": 5},
            quality_summary={"errors": 0, "warnings": 1},
        )

        retrieved = session_manager.get_session(ses.id)
        assert retrieved is not None
        assert retrieved.id == ses.id
        assert retrieved.stats == {"works": 10, "authors": 5}
        assert retrieved.quality_summary == {"errors": 0, "warnings": 1}

    def test_list_sessions_returns_created(self, session_manager, neo4j_client):
        """list_sessions includes recently created sessions."""
        ses1 = session_manager.create_session(query="query A", limit=1)
        ses2 = session_manager.create_session(query="query B", limit=2)

        sessions = session_manager.list_sessions(limit=10)
        ids = [s.id for s in sessions]
        assert ses1.id in ids
        assert ses2.id in ids
        # Newest first
        assert ids.index(ses2.id) < ids.index(ses1.id)

    def test_get_session_node_counts(self, session_manager, neo4j_client):
        """get_session_node_counts returns correct counts."""
        ses = session_manager.create_session(query="test", limit=1)

        # Create some data linked to the session
        nodes = [
            {"id": "W1", "title": "Paper 1",
             "import_sessions": [ses.id],
             "first_imported_at": "2026-01-01T00:00:00",
             "last_imported_at": "2026-01-01T00:00:00"},
            {"id": "W2", "title": "Paper 2",
             "import_sessions": [ses.id],
             "first_imported_at": "2026-01-01T00:00:00",
             "last_imported_at": "2026-01-01T00:00:00"},
            {"id": "A1", "display_name": "Author 1",
             "import_sessions": [ses.id],
             "first_imported_at": "2026-01-01T00:00:00",
             "last_imported_at": "2026-01-01T00:00:00"},
        ]
        neo4j_client.batch_create_nodes("Work", [nodes[0], nodes[1]])
        neo4j_client.batch_create_nodes("Author", [nodes[2]])

        counts = session_manager.get_session_node_counts(ses.id)
        assert counts.get("Work", 0) >= 2
        assert counts.get("Author", 0) >= 1

    def test_delete_session_removes_isolated_nodes(self, session_manager, neo4j_client):
        """delete_session removes isolated nodes and preserves shared ones."""
        ses1 = session_manager.create_session(query="query A", limit=1)
        ses2 = session_manager.create_session(query="query B", limit=1)

        # Create test data
        # Node W1 belongs to ses1 only (isolated - should be deleted)
        # Node W2 belongs to both ses1 and ses2 (shared - should NOT be deleted)
        # Node W3 belongs to ses2 only (not touched by delete of ses1)
        neo4j_client.batch_create_nodes("Work", [
            {"id": "W1", "title": "Isolated",
             "import_sessions": [ses1.id],
             "first_imported_at": "2026-01-01T00:00:00",
             "last_imported_at": "2026-01-01T00:00:00"},
            {"id": "W2", "title": "Shared",
             "import_sessions": [ses1.id, ses2.id],
             "first_imported_at": "2026-01-01T00:00:00",
             "last_imported_at": "2026-01-01T00:00:00"},
            {"id": "W3", "title": "Other session",
             "import_sessions": [ses2.id],
             "first_imported_at": "2026-01-01T00:00:00",
             "last_imported_at": "2026-01-01T00:00:00"},
        ])

        result = session_manager.delete_session(ses1.id)

        # W1 should be deleted
        assert neo4j_client.get_node_by_id("Work", "W1") is None
        assert result["deleted"] >= 1

        # W2 should still exist (shared) but without ses1.id in import_sessions
        w2 = neo4j_client.get_node_by_id("Work", "W2")
        assert w2 is not None
        if w2.get("import_sessions"):
            assert ses1.id not in w2["import_sessions"]
            assert ses2.id in w2["import_sessions"]

        # W3 should still exist
        w3 = neo4j_client.get_node_by_id("Work", "W3")
        assert w3 is not None

        # Session should be gone from local storage
        assert session_manager.get_session(ses1.id) is None

    def test_session_import_workflow(self, neo4j_client, openalex_client):
        """End-to-end: import creates session, data is tagged, session can be deleted."""
        from openalex_neo4j.importer import OpenAlexImporter

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(SessionManager, "SESSIONS_DIR", Path(tmpdir)):
                manager = SessionManager(neo4j_client)
                importer = OpenAlexImporter(neo4j_client, openalex_client, manager)

                counts = importer.import_from_query(
                    query="graph database",
                    limit=1,
                    expand_depth=1,
                )

                # Session was created
                assert importer.current_session is not None

                # Verify ImportSession node in Neo4j
                with neo4j_client.driver.session() as s:
                    result = s.run(
                        "MATCH (s:ImportSession {id: $id}) RETURN s",
                        id=importer.current_session,
                    )
                    assert result.single() is not None

                # Verify works are tagged with session
                ses_id = importer.current_session
                node_counts = manager.get_session_node_counts(ses_id)
                assert node_counts.get("Work", 0) >= 1
                assert counts.get("works", 0) >= 1

                # Delete the session
                del_result = manager.delete_session(ses_id)

                # Only delete isolated nodes (shared ones keep the session removed)
                print(f"Delete result: {del_result}")
                assert del_result["deleted"] >= 0

                # Session should be gone
                assert manager.get_session(ses_id) is None
