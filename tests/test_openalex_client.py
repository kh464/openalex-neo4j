"""Tests for OpenAlex client."""

from unittest.mock import Mock, patch

import pytest

from openalex_neo4j.openalex_client import OpenAlexClient
from openalex_neo4j.models import Work, Author


class TestOpenAlexClient:
    """Tests for OpenAlexClient."""

    @pytest.fixture
    def client(self):
        """Create OpenAlexClient."""
        return OpenAlexClient(email="test@example.com")

    def test_init_with_email(self):
        """Test initialization with email."""
        with patch("openalex_neo4j.openalex_client.pyalex") as mock_pyalex:
            client = OpenAlexClient(email="test@example.com")
            assert mock_pyalex.config.email == "test@example.com"

    def test_init_without_email(self):
        """Test initialization without email."""
        client = OpenAlexClient(email=None)
        assert client is not None

    def test_search_works(self, client):
        """Test searching for works."""
        mock_work_data = {
            "id": "https://openalex.org/W123",
            "title": "Test Paper",
            "publication_year": 2023,
        }

        mock_page = [mock_work_data]
        mock_pager = [mock_page]

        with patch("openalex_neo4j.openalex_client.Works") as mock_works:
            mock_works.return_value.search.return_value.paginate.return_value = mock_pager

            works = client.search_works("test query", limit=10)

            assert len(works) == 1
            assert works[0].id == "W123"
            assert works[0].title == "Test Paper"
            mock_works.return_value.search.return_value.paginate.assert_called_once_with(
                per_page=10,
                n_max=10,
            )

    def test_search_works_respects_limit(self, client):
        """Test that search respects the limit parameter."""
        mock_work_data = {
            "id": "https://openalex.org/W123",
            "title": "Test Paper",
        }

        # Create 5 pages of 2 works each (10 total)
        mock_pager = [[mock_work_data, mock_work_data] for _ in range(5)]

        with patch("openalex_neo4j.openalex_client.Works") as mock_works:
            mock_works.return_value.search.return_value.paginate.return_value = mock_pager

            works = client.search_works("test query", limit=3)

            # Should stop at 3, not fetch all 10
            assert len(works) <= 3

    def test_search_works_without_limit_disables_default_max(self, client):
        """search_works passes n_max=None so PyAlex does not stop at 10,000."""
        mock_work_data = {
            "id": "https://openalex.org/W123",
            "title": "Test Paper",
        }

        with patch("openalex_neo4j.openalex_client.Works") as mock_works:
            mock_works.return_value.search.return_value.paginate.return_value = [[mock_work_data]]

            works = client.search_works("test query", limit=None)

            assert len(works) == 1
            mock_works.return_value.search.return_value.paginate.assert_called_once_with(
                per_page=200,
                n_max=None,
            )

    def test_search_works_filters_by_type(self, client):
        """search_works applies repeated work types as an OpenAlex OR filter."""
        mock_work_data = {
            "id": "https://openalex.org/W123",
            "title": "Test Paper",
        }

        with patch("openalex_neo4j.openalex_client.Works") as mock_works:
            search_request = mock_works.return_value.search.return_value
            type_request = Mock()
            search_request.filter.return_value = type_request
            type_request.paginate.return_value = [[mock_work_data]]

            works = client.search_works(
                "test query", limit=10, work_types=["article", "review"],
            )

            assert len(works) == 1
            search_request.filter.assert_called_once_with(type="article|review")
            type_request.paginate.assert_called_once_with(per_page=10, n_max=10)

    def test_search_works_handles_errors(self, client):
        """Test that search handles errors gracefully."""
        with patch("openalex_neo4j.openalex_client.Works") as mock_works:
            mock_works.return_value.search.return_value.paginate.side_effect = Exception("API Error")

            works = client.search_works("test query", limit=10)

            # Should return empty list on error
            assert works == []

    def test_count_works_filters_by_type(self, client):
        """count_works applies repeated work types as an OpenAlex OR filter."""
        with patch("openalex_neo4j.openalex_client.Works") as mock_works:
            search_request = mock_works.return_value.search.return_value
            type_request = Mock()
            type_request.get.return_value.meta = {"count": 42}
            search_request.filter.return_value = type_request

            count = client.count_works("test query", work_types=["article", "review"])

            assert count == 42
            search_request.filter.assert_called_once_with(type="article|review")
            type_request.get.assert_called_once()

    def test_fetch_works_by_ids(self, client):
        """Test fetching works by IDs."""
        mock_work_data = {
            "id": "https://openalex.org/W123",
            "title": "Test Paper",
        }

        with patch("openalex_neo4j.openalex_client.Works") as mock_works:
            mock_works.return_value.filter.return_value.paginate.return_value = [[mock_work_data]]

            works = client.fetch_works_by_ids(["W123", "W456"])

            assert len(works) == 1
            assert works[0].id == "W123"

    def test_fetch_works_by_ids_empty(self, client):
        """Test fetching with empty ID list."""
        works = client.fetch_works_by_ids([])
        assert works == []

    def test_fetch_authors_by_ids(self, client):
        """Test fetching authors by IDs."""
        mock_author_data = {
            "id": "https://openalex.org/A123",
            "display_name": "Jane Doe",
            "works_count": 10,
        }

        with patch("openalex_neo4j.openalex_client.Authors") as mock_authors:
            mock_authors.return_value.filter.return_value.paginate.return_value = [[mock_author_data]]

            authors = client.fetch_authors_by_ids(["A123"])

            assert len(authors) == 1
            assert authors[0].id == "A123"
            assert authors[0].display_name == "Jane Doe"

    def test_fetch_authors_by_ids_empty(self, client):
        """Test fetching authors with empty ID list."""
        authors = client.fetch_authors_by_ids([])
        assert authors == []

    def test_fetch_institutions_by_ids(self, client):
        """Test fetching institutions by IDs."""
        mock_inst_data = {
            "id": "https://openalex.org/I123",
            "display_name": "MIT",
            "country_code": "US",
        }

        with patch("openalex_neo4j.openalex_client.Institutions") as mock_institutions:
            mock_institutions.return_value.filter.return_value.paginate.return_value = [[mock_inst_data]]

            institutions = client.fetch_institutions_by_ids(["I123"])

            assert len(institutions) == 1
            assert institutions[0].id == "I123"

    def test_fetch_sources_by_ids(self, client):
        """Test fetching sources by IDs."""
        mock_source_data = {
            "id": "https://openalex.org/S123",
            "display_name": "Nature",
        }

        with patch("openalex_neo4j.openalex_client.Sources") as mock_sources:
            mock_sources.return_value.filter.return_value.paginate.return_value = [[mock_source_data]]

            sources = client.fetch_sources_by_ids(["S123"])

            assert len(sources) == 1
            assert sources[0].id == "S123"

    def test_fetch_topics_by_ids(self, client):
        """Test fetching topics by IDs."""
        mock_topic_data = {
            "id": "https://openalex.org/T123",
            "display_name": "Machine Learning",
        }

        with patch("openalex_neo4j.openalex_client.Topics") as mock_topics:
            mock_topics.return_value.filter.return_value.paginate.return_value = [[mock_topic_data]]

            topics = client.fetch_topics_by_ids(["T123"])

            assert len(topics) == 1
            assert topics[0].id == "T123"

    def test_fetch_publishers_by_ids(self, client):
        """Test fetching publishers by IDs."""
        mock_pub_data = {
            "id": "https://openalex.org/P123",
            "display_name": "Springer",
        }

        with patch("openalex_neo4j.openalex_client.Publishers") as mock_publishers:
            mock_publishers.return_value.filter.return_value.paginate.return_value = [[mock_pub_data]]

            publishers = client.fetch_publishers_by_ids(["P123"])

            assert len(publishers) == 1
            assert publishers[0].id == "P123"

    def test_fetch_funders_by_ids(self, client):
        """Test fetching funders by IDs."""
        mock_funder_data = {
            "id": "https://openalex.org/F123",
            "display_name": "NSF",
        }

        with patch("openalex_neo4j.openalex_client.Funders") as mock_funders:
            mock_funders.return_value.filter.return_value.paginate.return_value = [[mock_funder_data]]

            funders = client.fetch_funders_by_ids(["F123"])

            assert len(funders) == 1
            assert funders[0].id == "F123"

    def test_batch_fetching(self, client):
        """Test that large ID lists are fetched in batches."""
        # Create 100 IDs (should require 2 batches at batch_size=50)
        work_ids = [f"W{i}" for i in range(100)]

        with patch("openalex_neo4j.openalex_client.Works") as mock_works:
            mock_works.return_value.filter.return_value.paginate.return_value = [[]]

            client.fetch_works_by_ids(work_ids)

            # Should be called twice (2 batches)
            assert mock_works.return_value.filter.return_value.paginate.call_count == 2

    def test_fetch_authors_by_ids_requests_full_batch_size(self, client):
        """Batch author fetches request up to 50 results instead of the default 25."""
        author_ids = [f"A{i}" for i in range(50)]

        with patch("openalex_neo4j.openalex_client.Authors") as mock_authors:
            mock_authors.return_value.filter.return_value.paginate.return_value = [[]]

            client.fetch_authors_by_ids(author_ids)

            mock_authors.return_value.filter.return_value.paginate.assert_called_once_with(
                per_page=50,
                n_max=50,
            )
