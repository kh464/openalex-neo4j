"""Tests for data source adapters."""
from unittest.mock import Mock, patch

import pytest

from openalex_neo4j.datasource.base import DataRecord, merge_record
from openalex_neo4j.datasource.openalex_impl import OpenAlexSource


SAMPLE_WORK_DATA = {
    "id": "https://openalex.org/W123",
    "title": "Test Paper",
    "publication_year": 2023,
    "publication_date": "2023-01-15",
    "doi": "https://doi.org/10.1234/abc",
    "type": "article",
    "cited_by_count": 10,
    "open_access": {"is_oa": True},
    "abstract_inverted_index": {"This": [0], "is": [1], "a": [2], "test": [3]},
}


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

    def test_to_openalex_id_returns_openalex_id(self):
        source = OpenAlexSource()
        record = DataRecord(
            source_name="openalex", source_confidence=1.0,
            external_ids={}, raw_data={}, openalex_id="W123",
        )
        assert source.to_openalex_id(record) == "W123"

    def test_to_openalex_id_none(self):
        source = OpenAlexSource()
        record = DataRecord(source_name="openalex", source_confidence=1.0, external_ids={}, raw_data={})
        assert source.to_openalex_id(record) is None

    # --- fetch_by_openalex_id ---

    @patch("openalex_neo4j.datasource.openalex_impl.Works")
    def test_fetch_by_openalex_id_found(self, mock_works_cls):
        """fetch_by_openalex_id returns a DataRecord when work is found."""
        mock_filter = Mock()
        mock_works_cls.return_value.filter.return_value = mock_filter
        mock_filter.get.return_value = [SAMPLE_WORK_DATA]

        source = OpenAlexSource()
        record = source.fetch_by_openalex_id("W123")

        assert record is not None
        assert record.openalex_id == "W123"
        assert record.title == "Test Paper"
        assert record.doi == "https://doi.org/10.1234/abc"
        assert record.publication_date == "2023-01-15"
        assert record.source_name == "openalex"
        assert record.source_confidence == 1.0
        mock_works_cls.return_value.filter.assert_called_once_with(
            openalex_id="https://openalex.org/W123"
        )

    @patch("openalex_neo4j.datasource.openalex_impl.Works")
    def test_fetch_by_openalex_id_not_found(self, mock_works_cls):
        """fetch_by_openalex_id returns None when no results."""
        mock_filter = Mock()
        mock_works_cls.return_value.filter.return_value = mock_filter
        mock_filter.get.return_value = []

        source = OpenAlexSource()
        record = source.fetch_by_openalex_id("W999")
        assert record is None

    @patch("openalex_neo4j.datasource.openalex_impl.Works")
    def test_fetch_by_openalex_id_api_error(self, mock_works_cls):
        """fetch_by_openalex_id returns None on API exception."""
        mock_works_cls.return_value.filter.side_effect = Exception("API Error")

        source = OpenAlexSource()
        record = source.fetch_by_openalex_id("W123")
        assert record is None

    @patch("openalex_neo4j.datasource.openalex_impl.Works")
    def test_fetch_by_openalex_id_with_abstract(self, mock_works_cls):
        """fetch_by_openalex_id reconstructs abstract from inverted index."""
        data = {**SAMPLE_WORK_DATA, "abstract_inverted_index": {
            "Hello": [0], "World": [1],
        }}
        mock_filter = Mock()
        mock_works_cls.return_value.filter.return_value = mock_filter
        mock_filter.get.return_value = [data]

        source = OpenAlexSource()
        record = source.fetch_by_openalex_id("W123")
        assert record is not None
        assert record.abstract == "Hello World"

    # --- fetch_by_doi ---

    @patch("openalex_neo4j.datasource.openalex_impl.Works")
    def test_fetch_by_doi_found(self, mock_works_cls):
        """fetch_by_doi returns a DataRecord when DOI is found."""
        mock_filter = Mock()
        mock_works_cls.return_value.filter.return_value = mock_filter
        mock_filter.get.return_value = [SAMPLE_WORK_DATA]

        source = OpenAlexSource()
        record = source.fetch_by_doi("10.1234/abc")

        assert record is not None
        assert record.openalex_id == "W123"
        assert record.doi == "https://doi.org/10.1234/abc"
        assert record.title == "Test Paper"
        assert record.source_confidence == 1.0
        mock_works_cls.return_value.filter.assert_called_once_with(doi="10.1234/abc")

    @patch("openalex_neo4j.datasource.openalex_impl.Works")
    def test_fetch_by_doi_not_found(self, mock_works_cls):
        """fetch_by_doi returns None when no results."""
        mock_filter = Mock()
        mock_works_cls.return_value.filter.return_value = mock_filter
        mock_filter.get.return_value = []

        source = OpenAlexSource()
        record = source.fetch_by_doi("10.9999/unknown")
        assert record is None

    @patch("openalex_neo4j.datasource.openalex_impl.Works")
    def test_fetch_by_doi_api_error(self, mock_works_cls):
        """fetch_by_doi returns None on API exception."""
        mock_works_cls.return_value.filter.side_effect = Exception("API Error")

        source = OpenAlexSource()
        record = source.fetch_by_doi("10.1234/abc")
        assert record is None
