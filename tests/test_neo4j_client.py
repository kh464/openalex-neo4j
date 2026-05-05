"""Tests for Neo4j client."""

from unittest.mock import Mock, MagicMock, patch

import pytest

from openalex_neo4j.neo4j_client import Neo4jClient


@pytest.fixture
def mock_driver():
    """Create a mock Neo4j driver."""
    driver = MagicMock()
    driver.verify_connectivity = Mock()
    driver.close = Mock()
    return driver


class TestNeo4jClient:
    """Tests for Neo4jClient."""

    @pytest.fixture
    def mock_driver(self, mock_driver):
        return mock_driver
        """Create a mock Neo4j driver."""
        driver = Mock()
        driver.verify_connectivity = Mock()
        driver.close = Mock()
        return driver

    @pytest.fixture
    def client(self, mock_driver):
        """Create a Neo4jClient with mocked driver."""
        with patch("openalex_neo4j.neo4j_client.GraphDatabase.driver", return_value=mock_driver):
            client = Neo4jClient("bolt://localhost", "neo4j", "password")
            client.connect()
            return client

    def test_init(self):
        """Test client initialization."""
        client = Neo4jClient("bolt://localhost", "neo4j", "password")
        assert client.uri == "bolt://localhost"
        assert client.username == "neo4j"
        assert client.password == "password"
        assert client._driver is None

    def test_connect(self, mock_driver):
        """Test connecting to Neo4j."""
        with patch("openalex_neo4j.neo4j_client.GraphDatabase.driver", return_value=mock_driver):
            client = Neo4jClient("bolt://localhost", "neo4j", "password")
            client.connect()

            assert client._driver == mock_driver
            mock_driver.verify_connectivity.assert_called_once()

    def test_close(self, client, mock_driver):
        """Test closing connection."""
        client.close()
        mock_driver.close.assert_called_once()

    def test_context_manager(self, mock_driver):
        """Test using client as context manager."""
        with patch("openalex_neo4j.neo4j_client.GraphDatabase.driver", return_value=mock_driver):
            with Neo4jClient("bolt://localhost", "neo4j", "password") as client:
                assert client._driver == mock_driver

            mock_driver.close.assert_called_once()

    def test_driver_property_not_connected(self):
        """Test accessing driver property when not connected."""
        client = Neo4jClient("bolt://localhost", "neo4j", "password")
        with pytest.raises(RuntimeError, match="Not connected"):
            _ = client.driver

    def test_create_constraints(self, client, mock_driver):
        """Test creating constraints."""
        mock_session = MagicMock()
        mock_context_manager = MagicMock()
        mock_context_manager.__enter__ = Mock(return_value=mock_session)
        mock_context_manager.__exit__ = Mock(return_value=False)
        mock_driver.session.return_value = mock_context_manager

        client.create_constraints()

        # Should create constraint for each entity type
        assert mock_session.run.call_count == len(Neo4jClient.ENTITY_TYPES)

    def test_batch_create_nodes(self, client, mock_driver):
        """Test batch creating nodes."""
        mock_session = MagicMock()
        mock_result = Mock()
        mock_result.single.return_value = {"count": 3}
        mock_session.run.return_value = mock_result
        mock_context_manager = MagicMock()
        mock_context_manager.__enter__ = Mock(return_value=mock_session)
        mock_context_manager.__exit__ = Mock(return_value=False)
        mock_driver.session.return_value = mock_context_manager

        nodes = [
            {"id": "W1", "title": "Paper 1"},
            {"id": "W2", "title": "Paper 2"},
            {"id": "W3", "title": "Paper 3"},
        ]

        count = client.batch_create_nodes("Work", nodes, batch_size=2)

        # Should create 2 batches (2 + 1)
        assert mock_session.run.call_count == 2
        assert count == 6  # 3 + 3 from mock

    def test_batch_create_nodes_empty(self, client):
        """Test batch creating nodes with empty list."""
        count = client.batch_create_nodes("Work", [])
        assert count == 0

    def test_batch_create_relationships(self, client, mock_driver):
        """Test batch creating relationships."""
        mock_session = MagicMock()
        mock_result = Mock()
        mock_result.single.return_value = {"count": 2}
        mock_session.run.return_value = mock_result
        mock_context_manager = MagicMock()
        mock_context_manager.__enter__ = Mock(return_value=mock_session)
        mock_context_manager.__exit__ = Mock(return_value=False)
        mock_driver.session.return_value = mock_context_manager

        rels = [
            {"source_id": "A1", "target_id": "W1"},
            {"source_id": "A2", "target_id": "W2"},
        ]

        count = client.batch_create_relationships(
            "AUTHORED", "Author", "Work", rels
        )

        assert mock_session.run.call_count == 1
        assert count == 2

    def test_batch_create_relationships_empty(self, client):
        """Test batch creating relationships with empty list."""
        count = client.batch_create_relationships(
            "AUTHORED", "Author", "Work", []
        )
        assert count == 0

    def test_get_node_count(self, client, mock_driver):
        """Test getting node count."""
        mock_session = MagicMock()
        mock_result = Mock()
        mock_result.single.return_value = {"count": 42}
        mock_session.run.return_value = mock_result
        mock_context_manager = MagicMock()
        mock_context_manager.__enter__ = Mock(return_value=mock_session)
        mock_context_manager.__exit__ = Mock(return_value=False)
        mock_driver.session.return_value = mock_context_manager

        count = client.get_node_count("Work")
        assert count == 42

    def test_get_relationship_count(self, client, mock_driver):
        """Test getting relationship count."""
        mock_session = MagicMock()
        mock_result = Mock()
        mock_result.single.return_value = {"count": 100}
        mock_session.run.return_value = mock_result
        mock_context_manager = MagicMock()
        mock_context_manager.__enter__ = Mock(return_value=mock_session)
        mock_context_manager.__exit__ = Mock(return_value=False)
        mock_driver.session.return_value = mock_context_manager

        count = client.get_relationship_count("AUTHORED")
        assert count == 100


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
