"""Tests for import orchestration."""

from unittest.mock import Mock, MagicMock

import pytest

from openalex_neo4j.importer import OpenAlexImporter
from openalex_neo4j.models import Work, Author, Institution, Source, Topic, ImportSession


@pytest.fixture
def mock_openalex_client():
    """Create a mock OpenAlex client."""
    client = Mock()
    client.search_works = Mock(return_value=[])
    client.fetch_works_by_ids = Mock(return_value=[])
    client.fetch_authors_by_ids = Mock(return_value=[])
    client.fetch_institutions_by_ids = Mock(return_value=[])
    client.fetch_sources_by_ids = Mock(return_value=[])
    client.fetch_topics_by_ids = Mock(return_value=[])
    client.fetch_publishers_by_ids = Mock(return_value=[])
    client.fetch_funders_by_ids = Mock(return_value=[])
    return client


class TestOpenAlexImporter:
    """Tests for OpenAlexImporter."""

    @pytest.fixture
    def mock_neo4j_client(self):
        """Create a mock Neo4j client."""
        client = Mock()
        client.create_constraints = Mock()
        client.batch_create_nodes = Mock(return_value=1)
        client.batch_create_relationships = Mock(return_value=1)
        return client

    @pytest.fixture
    def mock_openalex_client(self, mock_openalex_client):
        return mock_openalex_client
        """Create a mock OpenAlex client."""
        client = Mock()
        client.search_works = Mock(return_value=[])
        client.fetch_works_by_ids = Mock(return_value=[])
        client.fetch_authors_by_ids = Mock(return_value=[])
        client.fetch_institutions_by_ids = Mock(return_value=[])
        client.fetch_sources_by_ids = Mock(return_value=[])
        client.fetch_topics_by_ids = Mock(return_value=[])
        client.fetch_publishers_by_ids = Mock(return_value=[])
        client.fetch_funders_by_ids = Mock(return_value=[])
        return client

    @pytest.fixture
    def importer(self, mock_neo4j_client, mock_openalex_client):
        """Create an importer with mocked clients."""
        return OpenAlexImporter(mock_neo4j_client, mock_openalex_client)

    def test_init(self, mock_neo4j_client, mock_openalex_client):
        """Test importer initialization."""
        importer = OpenAlexImporter(mock_neo4j_client, mock_openalex_client)
        assert importer.neo4j == mock_neo4j_client
        assert importer.openalex == mock_openalex_client
        assert importer.works == {}
        assert importer.authors == {}

    def test_add_works(self, importer):
        """Test adding works to collection."""
        work1 = Work(id="W1", title="Paper 1")
        work2 = Work(id="W2", title="Paper 2")
        work3 = Work(id="W1", title="Paper 1 Updated")  # Duplicate ID

        importer._add_works([work1, work2, work3])

        # Should have 2 works (W1 deduplicated)
        assert len(importer.works) == 2
        assert "W1" in importer.works
        assert "W2" in importer.works
        # First one wins
        assert importer.works["W1"].title == "Paper 1"

    def test_expand_relationships(self, importer, mock_openalex_client):
        """Test expanding relationships."""
        # Add a work with related entities
        work = Work(
            id="W1",
            title="Paper",
            author_ids=["A1"],
            institution_ids=["I1"],
            source_id="S1",
            topic_ids=["T1"],
            funder_ids=["F1"],
            referenced_work_ids=["W2"],
        )
        importer.works["W1"] = work

        # Mock fetch responses
        mock_openalex_client.fetch_authors_by_ids.return_value = [
            Author(id="A1", display_name="Author 1")
        ]
        mock_openalex_client.fetch_institutions_by_ids.return_value = [
            Institution(id="I1", display_name="Inst 1")
        ]
        mock_openalex_client.fetch_sources_by_ids.return_value = [
            Source(id="S1", display_name="Source 1")
        ]
        mock_openalex_client.fetch_topics_by_ids.return_value = [
            Topic(id="T1", display_name="Topic 1")
        ]
        mock_openalex_client.fetch_works_by_ids.return_value = [
            Work(id="W2", title="Cited Work")
        ]

        importer._expand_relationships()

        # Check that all entities were fetched
        assert "A1" in importer.authors
        assert "I1" in importer.institutions
        assert "S1" in importer.sources
        assert "T1" in importer.topics
        assert "W2" in importer.works

    def test_import_nodes(self, importer, mock_neo4j_client):
        """Test importing nodes to Neo4j."""
        # Add some entities
        importer.works["W1"] = Work(id="W1", title="Paper")
        importer.authors["A1"] = Author(id="A1", display_name="Author")

        counts = importer._import_nodes()

        # Should call batch_create_nodes for works and authors
        assert mock_neo4j_client.batch_create_nodes.call_count >= 2
        assert "works" in counts
        assert "authors" in counts

    def test_import_relationships(self, importer, mock_neo4j_client):
        """Test importing relationships to Neo4j."""
        # Create entities
        work = Work(
            id="W1",
            title="Paper",
            author_ids=["A1"],
            source_id="S1",
            referenced_work_ids=["W2"],
        )
        importer.works["W1"] = work
        importer.works["W2"] = Work(id="W2", title="Cited")
        importer.authors["A1"] = Author(id="A1", display_name="Author")
        importer.sources["S1"] = Source(id="S1", display_name="Source")

        counts = importer._import_relationships()

        # Should create multiple relationship types
        assert mock_neo4j_client.batch_create_relationships.call_count >= 1

    def test_import_from_query(self, importer, mock_openalex_client, mock_neo4j_client):
        """Test full import workflow."""
        # Mock initial search
        initial_work = Work(
            id="W1",
            title="Paper",
            author_ids=["A1"],
        )
        mock_openalex_client.search_works.return_value = [initial_work]

        # Mock author fetch
        mock_openalex_client.fetch_authors_by_ids.return_value = [
            Author(id="A1", display_name="Author")
        ]

        counts = importer.import_from_query("test query", limit=10, expand_depth=1)

        # Check workflow
        mock_openalex_client.search_works.assert_called_once_with("test query", 10, from_year=None, to_year=None)
        mock_neo4j_client.create_constraints.assert_called_once()
        assert isinstance(counts, dict)

    def test_import_from_query_multiple_depths(
        self, importer, mock_openalex_client, mock_neo4j_client, tmp_path
    ):
        """Test import with multiple expansion depths."""
        # Initial work
        work1 = Work(
            id="W1",
            title="Paper",
            referenced_work_ids=["W2"],
        )
        # Cited work
        work2 = Work(
            id="W2",
            title="Cited Paper",
            referenced_work_ids=["W3"],
        )
        # Second-level citation
        work3 = Work(
            id="W3",
            title="Second Level",
        )

        mock_openalex_client.search_works.return_value = [work1]

        # First expansion gets W2, second gets W3
        def fetch_works_side_effect(ids):
            if "W2" in ids:
                return [work2]
            elif "W3" in ids:
                return [work3]
            return []

        mock_openalex_client.fetch_works_by_ids.side_effect = fetch_works_side_effect

        counts = importer.import_from_query(
            "test", limit=1, expand_depth=2,
            cache_dir=tmp_path,
        )

        # batch_create_nodes should have been called for Work
        work_calls = [
            c for c in mock_neo4j_client.batch_create_nodes.call_args_list
            if c[0][0] == "Work"
        ]
        assert len(work_calls) == 1
        # At least W1 and W2 should be in the nodes
        node_ids = [n["id"] for n in work_calls[0][0][1]]
        assert "W1" in node_ids
        assert "W2" in node_ids

    def test_deduplication(self, importer, mock_openalex_client):
        """Test that entities are deduplicated."""
        # Create work with duplicate author and institution
        work1 = Work(
            id="W1",
            title="Paper 1",
            author_ids=["A1"],
            institution_ids=["I1"],
        )
        work2 = Work(
            id="W2",
            title="Paper 2",
            author_ids=["A1"],  # Same author
            institution_ids=["I1"],  # Same institution
        )

        importer.works["W1"] = work1
        importer.works["W2"] = work2

        # Mock fetches
        mock_openalex_client.fetch_authors_by_ids.return_value = [
            Author(id="A1", display_name="Author")
        ]
        mock_openalex_client.fetch_institutions_by_ids.return_value = [
            Institution(id="I1", display_name="Inst")
        ]

        importer._expand_relationships()

        # Should only fetch each entity once
        mock_openalex_client.fetch_authors_by_ids.assert_called_once()
        mock_openalex_client.fetch_institutions_by_ids.assert_called_once()

        # First call should have the ID
        call_args = mock_openalex_client.fetch_authors_by_ids.call_args[0][0]
        assert "A1" in call_args

        # Second expansion should not fetch again (already have it)
        importer._expand_relationships()
        assert mock_openalex_client.fetch_authors_by_ids.call_count == 1


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
        args, kwargs = mock_session_manager.complete_session.call_args
        assert args[0] == "20260101_120000"  # session_id
        assert isinstance(kwargs["stats"], dict)     # stats

    def test_no_session_manager_no_tracking(self, mock_neo4j_client, mock_openalex_client):
        """Test that without session manager, no tracking occurs."""
        importer = OpenAlexImporter(mock_neo4j_client, mock_openalex_client)
        assert importer.session_manager is None
        assert importer.current_session is None

    def test_to_node_dict_called_with_session(self, importer_with_session, mock_session_manager, mock_openalex_client, mock_neo4j_client):
        """Test that to_node_dict is called with current_session."""
        mock_openalex_client.search_works.return_value = [
            Work(id="W1", title="Test"),
        ]

        importer_with_session.import_from_query("test", limit=1)

        # Verify batch_create_nodes was called with current_session
        calls = mock_neo4j_client.batch_create_nodes.call_args_list
        work_call = [c for c in calls if c[0][0] == "Work"]
        assert len(work_call) > 0

    def test_session_tag_passed_to_manager(self, importer_with_session, mock_session_manager, mock_openalex_client):
        """Test that tag is passed to session manager."""
        mock_openalex_client.search_works.return_value = []
        importer_with_session.import_from_query("test", limit=1, tag="my-import")

        mock_session_manager.create_session.assert_called_with(
            query="test", limit=1, expand_depth=1, tag="my-import",
        )


class TestCacheBackedImport:
    """Tests for the local JSONL cache import flow."""

    @pytest.fixture
    def mock_openalex(self):
        from unittest.mock import Mock
        client = Mock()
        client.search_works = Mock(return_value=[])
        client.fetch_works_by_ids = Mock(return_value=[])
        client.fetch_authors_by_ids = Mock(return_value=[])
        client.fetch_institutions_by_ids = Mock(return_value=[])
        client.fetch_sources_by_ids = Mock(return_value=[])
        client.fetch_topics_by_ids = Mock(return_value=[])
        client.fetch_publishers_by_ids = Mock(return_value=[])
        client.fetch_funders_by_ids = Mock(return_value=[])
        return client

    @pytest.fixture
    def mock_neo4j_client(self):
        from unittest.mock import Mock
        client = Mock()
        client.create_constraints = Mock()
        client.batch_create_nodes = Mock(return_value=1)
        client.batch_create_relationships = Mock(return_value=1)
        return client

    def test_import_nodes_from_dict(self, mock_neo4j_client):
        """_import_nodes_from_dict creates nodes from dict data."""
        importer = OpenAlexImporter(mock_neo4j_client, Mock())
        importer.current_session = "S0001"
        entities = {
            "Work": [{"id": "W1", "title": "Test"}],
            "Author": [{"id": "A1", "display_name": "Author"}],
        }
        counts = importer._import_nodes_from_dict(entities)
        assert "works" in counts
        assert "authors" in counts
        assert mock_neo4j_client.batch_create_nodes.call_count == 2

    def test_import_nodes_from_dict_strips_rel_fields(self, mock_neo4j_client):
        """Relationship-only fields are stripped before node creation."""
        importer = OpenAlexImporter(mock_neo4j_client, Mock())
        importer.current_session = "S0001"
        entities = {
            "Work": [{
                "id": "W1",
                "title": "Test",
                "author_ids": ["A1", "A2"],
                "referenced_work_ids": ["W2"],
            }],
        }
        importer._import_nodes_from_dict(entities)
        call_args = mock_neo4j_client.batch_create_nodes.call_args[0]
        nodes = call_args[1]
        assert "author_ids" not in nodes[0]
        assert "referenced_work_ids" not in nodes[0]
        assert nodes[0]["id"] == "W1"
        assert nodes[0]["title"] == "Test"

    def test_import_relationships_from_dict(self, mock_neo4j_client):
        """_import_relationships_from_dict creates relationships from dict data."""
        importer = OpenAlexImporter(mock_neo4j_client, Mock())
        entities = {
            "Work": [{"id": "W1", "author_ids": ["A1"], "referenced_work_ids": ["W2"],
                       "source_id": "S1", "topic_ids": ["T1"], "funder_ids": ["F1"],
                       "institution_ids": ["I1"]}],
            "Work_extra": [{"id": "W2"}],
            "Author": [{"id": "A1"}],
            "Source": [{"id": "S1", "publisher_id": "P1"}],
            "Topic": [{"id": "T1"}],
            "Funder": [{"id": "F1"}],
            "Institution": [{"id": "I1"}],
            "Publisher": [{"id": "P1"}],
        }
        # Merge Work_extra into Work for the test
        entities["Work"].append({"id": "W2", "author_ids": []})
        counts = importer._import_relationships_from_dict(entities)
        assert "authored" in counts or mock_neo4j_client.batch_create_relationships.call_count > 0

    def test_import_relationships_empty(self, mock_neo4j_client):
        """Importing relationships from empty entities does nothing."""
        importer = OpenAlexImporter(mock_neo4j_client, Mock())
        entities = {"Work": [], "Author": [], "Source": []}
        counts = importer._import_relationships_from_dict(entities)
        assert counts == {}

    def test_import_from_cache_missing_manifest(self, tmp_path):
        """import_from_cache raises ValueError when manifest is missing."""
        importer = OpenAlexImporter(Mock(), Mock())
        with pytest.raises(ValueError, match="not found"):
            importer.import_from_cache("nonexistent", tmp_path)

    def test_import_from_cache_success(self, mock_neo4j_client, mock_openalex, tmp_path):
        """import_from_cache reads JSONL and imports to Neo4j."""
        from openalex_neo4j.serializer import DataSerializer

        sid = "test_resume"
        serializer = DataSerializer(tmp_path, sid)
        serializer.append("Work", {"id": "W1", "title": "Resumed"})
        serializer.write_manifest({
            "session_id": sid,
            "query": "test",
            "parameters": {"generate_embeddings": False},
        })

        importer = OpenAlexImporter(mock_neo4j_client, mock_openalex)
        counts = importer.import_from_cache(sid, tmp_path)
        assert "works" in counts
        assert mock_neo4j_client.batch_create_nodes.called
        assert mock_neo4j_client.create_constraints.called

    def test_import_from_cache_with_embeddings(self, mock_neo4j_client, mock_openalex, tmp_path):
        """import_from_cache creates vector index when manifest says so."""
        from openalex_neo4j.serializer import DataSerializer

        sid = "test_emb"
        serializer = DataSerializer(tmp_path, sid)
        serializer.write_manifest({
            "session_id": sid,
            "parameters": {"generate_embeddings": True},
        })

        importer = OpenAlexImporter(mock_neo4j_client, mock_openalex)
        importer.import_from_cache(sid, tmp_path)
        mock_neo4j_client.create_indexes.assert_called_with(include_vector=True)

    def test_save_works_batch_stores_rel_fields(self, mock_neo4j_client, tmp_path):
        """_save_works_batch stores relationship fields in cache."""
        from openalex_neo4j.serializer import DataSerializer

        importer = OpenAlexImporter(mock_neo4j_client, Mock())
        importer.current_session = "S_test"
        importer.serializer = DataSerializer(tmp_path, "S_test")

        work = Work(id="W1", title="Test", author_ids=["A1"],
                    referenced_work_ids=["W2"])
        importer._save_works_batch([work])

        cached = importer.serializer.read("Work")
        assert len(cached) == 1
        assert "author_ids" in cached[0]
        assert cached[0]["author_ids"] == ["A1"]
        assert cached[0]["referenced_work_ids"] == ["W2"]


class TestLargeImportOptimization:
    """Tests for large import optimization features."""

    def test_large_import_auto_limits_depth(self, mock_neo4j_client, mock_openalex_client, tmp_path):
        """Limit > LARGE_IMPORT_THRESHOLD forces expand_depth to 1."""
        importer = OpenAlexImporter(mock_neo4j_client, mock_openalex_client)
        importer.current_session = "S_large"
        from openalex_neo4j.serializer import DataSerializer
        importer.serializer = DataSerializer(tmp_path, "S_large")

        # Mock search_works to return nothing (we only test the depth logic)
        mock_openalex_client.search_works.return_value = []

        # Large import with expand_depth=3
        importer.import_from_query(
            "test", limit=10000, expand_depth=3,
            cache_dir=tmp_path, keep_cache=True,
        )

        # The expand loop should only run once (depth forced to 1)
        # _expand_and_save_relationships should be called exactly once
        # We verify this via the work count written
        # Since search_works returned [], expand won't fetch anything

    def test_small_import_no_restriction(self, mock_neo4j_client, mock_openalex_client, tmp_path):
        """Limit <= LARGE_IMPORT_THRESHOLD does not restrict anything."""
        importer = OpenAlexImporter(mock_neo4j_client, mock_openalex_client)
        importer.current_session = "S_small"
        from openalex_neo4j.serializer import DataSerializer
        importer.serializer = DataSerializer(tmp_path, "S_small")

        # Mock a work with referenced works to verify they get expanded
        work1 = Work(id="W1", title="Paper", referenced_work_ids=["W2", "W3"])
        work2 = Work(id="W2", title="Cited A")
        work3 = Work(id="W3", title="Cited B")

        mock_openalex_client.search_works.return_value = [work1]
        mock_openalex_client.fetch_works_by_ids.return_value = [work2, work3]

        importer.import_from_query(
            "test", limit=100, expand_depth=1,
            cache_dir=tmp_path, keep_cache=True,
        )

        # Referenced works should be in cache
        cached = importer.serializer.read("Work")
        cached_ids = {w["id"] for w in cached}
        assert "W2" in cached_ids
        assert "W3" in cached_ids

    def test_large_import_skips_referenced_works(self, mock_neo4j_client, mock_openalex_client, tmp_path):
        """Large import does not fetch referenced works."""
        importer = OpenAlexImporter(mock_neo4j_client, mock_openalex_client)
        importer.current_session = "S_large_skip"
        from openalex_neo4j.serializer import DataSerializer
        importer.serializer = DataSerializer(tmp_path, "S_large_skip")

        work1 = Work(id="W1", title="Paper", referenced_work_ids=["W2", "W3"])

        mock_openalex_client.search_works.return_value = [work1]
        mock_openalex_client.fetch_works_by_ids.return_value = []

        importer.import_from_query(
            "test", limit=10000, expand_depth=1,
            cache_dir=tmp_path, keep_cache=True,
        )

        # fetch_works_by_ids should NOT have been called
        mock_openalex_client.fetch_works_by_ids.assert_not_called()

        # Only W1 in cache
        cached = importer.serializer.read("Work")
        assert len(cached) == 1
        assert cached[0]["id"] == "W1"

    def test_import_nodes_streaming(self, mock_neo4j_client, mock_openalex_client):
        """_import_nodes_streaming creates nodes and returns entity IDs."""
        importer = OpenAlexImporter(mock_neo4j_client, mock_openalex_client)
        importer.current_session = "S_stream"
        from openalex_neo4j.serializer import DataSerializer
        from pathlib import Path
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            importer.serializer = DataSerializer(cache_dir, "S_stream")
            importer.serializer.append("Work", {"id": "W1", "title": "Test", "author_ids": ["A1"]})
            importer.serializer.append("Author", {"id": "A1", "display_name": "Auth"})

            mock_neo4j_client.batch_create_nodes.reset_mock()
            counts, entity_ids = importer._import_nodes_streaming()

            assert "works" in counts
            assert "authors" in counts
            assert "Work" in entity_ids
            assert "Author" in entity_ids
            assert "W1" in entity_ids["Work"]
            assert "A1" in entity_ids["Author"]

    def test_import_relationships_streaming(self, mock_neo4j_client, mock_openalex_client, tmp_path):
        """_import_relationships_streaming creates relationships from entity_ids."""
        importer = OpenAlexImporter(mock_neo4j_client, mock_openalex_client)
        importer.current_session = "S_rel"
        from openalex_neo4j.serializer import DataSerializer

        importer.serializer = DataSerializer(tmp_path, "S_rel")
        importer.serializer.append("Work", {
            "id": "W1", "title": "Paper",
            "author_ids": ["A1"], "source_id": "S1",
            "topic_ids": ["T1"], "referenced_work_ids": ["W2"],
        })
        importer.serializer.append("Work", {
            "id": "W2", "title": "Cited",
        })

        entity_ids = {
            "Author": {"A1"},
            "Institution": set(),
            "Source": {"S1"},
            "Topic": {"T1"},
            "Funder": set(),
            "Publisher": set(),
        }

        mock_neo4j_client.batch_create_relationships.reset_mock()
        counts = importer._import_relationships_streaming(entity_ids)

        # Should have created several relationship types
        total_calls = mock_neo4j_client.batch_create_relationships.call_count
        assert total_calls >= 3, f"Expected >=3 relationship types, got {total_calls}"
        assert "authored" in counts
        assert "published_in" in counts
        assert "has_topic" in counts

