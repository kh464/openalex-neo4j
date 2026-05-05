"""Tests for data models."""

import pytest

from openalex_neo4j.models import (
    extract_openalex_id,
    ImportSession,
    Work,
    Author,
    Institution,
    Source,
    Topic,
    Publisher,
    Funder,
)


class TestExtractOpenAlexId:
    """Tests for extract_openalex_id function."""

    def test_extract_from_url(self):
        """Test extracting ID from full URL."""
        assert extract_openalex_id("https://openalex.org/W123456") == "W123456"
        assert extract_openalex_id("https://openalex.org/A987654") == "A987654"

    def test_extract_from_id(self):
        """Test when input is already an ID."""
        assert extract_openalex_id("W123456") == "W123456"

    def test_none_input(self):
        """Test with None input."""
        assert extract_openalex_id(None) is None

    def test_empty_string(self):
        """Test with empty string."""
        assert extract_openalex_id("") is None


class TestWork:
    """Tests for Work model."""

    def test_from_openalex_minimal(self):
        """Test creating Work from minimal OpenAlex data."""
        data = {
            "id": "https://openalex.org/W123456",
            "title": "Test Paper",
        }
        work = Work.from_openalex(data)
        assert work.id == "W123456"
        assert work.title == "Test Paper"
        assert work.author_ids == []

    def test_from_openalex_full(self):
        """Test creating Work from full OpenAlex data."""
        data = {
            "id": "https://openalex.org/W123456",
            "title": "Test Paper",
            "publication_year": 2023,
            "publication_date": "2023-01-15",
            "doi": "10.1234/test",
            "type": "article",
            "cited_by_count": 42,
            "open_access": {"is_oa": True},
            "authorships": [
                {
                    "author": {"id": "https://openalex.org/A111"},
                    "institutions": [
                        {"id": "https://openalex.org/I222"}
                    ],
                }
            ],
            "primary_location": {
                "source": {"id": "https://openalex.org/S333"}
            },
            "topics": [
                {"id": "https://openalex.org/T444"}
            ],
            "grants": [
                {"funder": {"id": "https://openalex.org/F555"}}
            ],
            "referenced_works": [
                "https://openalex.org/W789"
            ],
        }
        work = Work.from_openalex(data)
        assert work.id == "W123456"
        assert work.title == "Test Paper"
        assert work.publication_year == 2023
        assert work.doi == "10.1234/test"
        assert work.cited_by_count == 42
        assert work.is_oa is True
        assert "A111" in work.author_ids
        assert "I222" in work.institution_ids
        assert work.source_id == "S333"
        assert "T444" in work.topic_ids
        assert "F555" in work.funder_ids
        assert "W789" in work.referenced_work_ids

    def test_from_openalex_no_id_raises(self):
        """Test that missing ID raises ValueError."""
        data = {"title": "Test"}
        with pytest.raises(ValueError, match="must have an id"):
            Work.from_openalex(data)

    def test_to_node_dict(self):
        """Test converting Work to node dictionary."""
        work = Work(
            id="W123",
            title="Test",
            publication_year=2023,
            cited_by_count=10,
        )
        node_dict = work.to_node_dict()
        assert node_dict["id"] == "W123"
        assert node_dict["title"] == "Test"
        assert node_dict["publication_year"] == 2023
        assert node_dict["cited_by_count"] == 10

    def test_to_node_dict_with_type(self):
        """Test converting Work with type to node dictionary includes _label."""
        work = Work(
            id="W123",
            title="Test",
            type="journal-article",
            publication_year=2023,
            cited_by_count=10,
        )
        node_dict = work.to_node_dict()
        assert node_dict["id"] == "W123"
        assert node_dict["type"] == "journal-article"
        assert node_dict["_label"] == "JournalArticle"

    def test_abstract_reconstruction(self):
        """Test reconstructing abstract from inverted index."""
        data = {
            "id": "https://openalex.org/W123",
            "abstract_inverted_index": {
                "This": [0],
                "is": [1],
                "a": [2],
                "test": [3],
            }
        }
        work = Work.from_openalex(data)
        assert work.abstract == "This is a test"


class TestAuthor:
    """Tests for Author model."""

    def test_from_openalex(self):
        """Test creating Author from OpenAlex data."""
        data = {
            "id": "https://openalex.org/A123",
            "display_name": "Jane Doe",
            "orcid": "0000-0001-2345-6789",
            "works_count": 42,
            "cited_by_count": 1337,
        }
        author = Author.from_openalex(data)
        assert author.id == "A123"
        assert author.display_name == "Jane Doe"
        assert author.orcid == "0000-0001-2345-6789"
        assert author.works_count == 42
        assert author.cited_by_count == 1337

    def test_to_node_dict(self):
        """Test converting Author to node dictionary."""
        author = Author(id="A123", display_name="Jane Doe")
        node_dict = author.to_node_dict()
        assert node_dict["id"] == "A123"
        assert node_dict["display_name"] == "Jane Doe"


class TestInstitution:
    """Tests for Institution model."""

    def test_from_openalex(self):
        """Test creating Institution from OpenAlex data."""
        data = {
            "id": "https://openalex.org/I123",
            "display_name": "MIT",
            "ror": "https://ror.org/123",
            "country_code": "US",
            "type": "education",
            "works_count": 10000,
        }
        inst = Institution.from_openalex(data)
        assert inst.id == "I123"
        assert inst.display_name == "MIT"
        assert inst.country_code == "US"

    def test_to_node_dict(self):
        """Test converting Institution to node dictionary."""
        inst = Institution(id="I123", display_name="MIT")
        node_dict = inst.to_node_dict()
        assert node_dict["id"] == "I123"
        assert node_dict["display_name"] == "MIT"


class TestSource:
    """Tests for Source model."""

    def test_from_openalex(self):
        """Test creating Source from OpenAlex data."""
        data = {
            "id": "https://openalex.org/S123",
            "display_name": "Nature",
            "issn_l": "0028-0836",
            "issn": ["0028-0836", "1476-4687"],
            "type": "journal",
            "host_organization": "https://openalex.org/P456",
            "works_count": 50000,
        }
        source = Source.from_openalex(data)
        assert source.id == "S123"
        assert source.display_name == "Nature"
        assert source.issn_l == "0028-0836"
        assert source.publisher_id == "P456"

    def test_to_node_dict(self):
        """Test converting Source to node dictionary."""
        source = Source(id="S123", display_name="Nature")
        node_dict = source.to_node_dict()
        assert node_dict["id"] == "S123"
        assert node_dict["display_name"] == "Nature"


class TestTopic:
    """Tests for Topic model."""

    def test_from_openalex(self):
        """Test creating Topic from OpenAlex data."""
        data = {
            "id": "https://openalex.org/T123",
            "display_name": "Machine Learning",
            "description": "AI and ML research",
            "keywords": ["AI", "ML", "neural networks"],
        }
        topic = Topic.from_openalex(data)
        assert topic.id == "T123"
        assert topic.display_name == "Machine Learning"
        assert "AI" in topic.keywords


class TestPublisher:
    """Tests for Publisher model."""

    def test_from_openalex(self):
        """Test creating Publisher from OpenAlex data."""
        data = {
            "id": "https://openalex.org/P123",
            "display_name": "Springer",
            "country_codes": ["DE", "US"],
            "works_count": 100000,
        }
        pub = Publisher.from_openalex(data)
        assert pub.id == "P123"
        assert pub.display_name == "Springer"
        assert "DE" in pub.country_codes


class TestFunder:
    """Tests for Funder model."""

    def test_from_openalex(self):
        """Test creating Funder from OpenAlex data."""
        data = {
            "id": "https://openalex.org/F123",
            "display_name": "NSF",
            "country_code": "US",
            "description": "National Science Foundation",
        }
        funder = Funder.from_openalex(data)
        assert funder.id == "F123"
        assert funder.display_name == "NSF"
        assert funder.country_code == "US"


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


class TestInstitutionSessionTracking:
    """Tests for Institution session tracking fields."""

    def test_to_node_dict_without_session(self):
        inst = Institution(id="I1", display_name="MIT")
        node_dict = inst.to_node_dict()
        assert "import_sessions" not in node_dict

    def test_to_node_dict_with_session(self):
        inst = Institution(id="I1", display_name="MIT")
        node_dict = inst.to_node_dict(current_session="S1")
        assert node_dict["import_sessions"] == ["S1"]


class TestSourceSessionTracking:
    """Tests for Source session tracking fields."""

    def test_to_node_dict_with_session(self):
        source = Source(id="S1", display_name="Nature")
        node_dict = source.to_node_dict(current_session="S1")
        assert node_dict["import_sessions"] == ["S1"]


class TestTopicSessionTracking:
    """Tests for Topic session tracking fields."""

    def test_to_node_dict_with_session(self):
        topic = Topic(id="T1", display_name="ML")
        node_dict = topic.to_node_dict(current_session="S1")
        assert node_dict["import_sessions"] == ["S1"]


class TestPublisherSessionTracking:
    """Tests for Publisher session tracking fields."""

    def test_to_node_dict_with_session(self):
        pub = Publisher(id="P1", display_name="Springer")
        node_dict = pub.to_node_dict(current_session="S1")
        assert node_dict["import_sessions"] == ["S1"]


class TestFunderSessionTracking:
    """Tests for Funder session tracking fields."""

    def test_to_node_dict_with_session(self):
        funder = Funder(id="F1", display_name="NSF")
        node_dict = funder.to_node_dict(current_session="S1")
        assert node_dict["import_sessions"] == ["S1"]
