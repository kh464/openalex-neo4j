"""Tests for DataSerializer (local JSONL cache)."""

import json
from pathlib import Path

import pytest

from openalex_neo4j.serializer import DataSerializer


class TestDataSerializer:
    """Tests for DataSerializer CRUD operations."""

    @pytest.fixture
    def serializer(self, tmp_path: Path) -> DataSerializer:
        return DataSerializer(tmp_path, "test_session_001")

    def test_init_creates_dir(self, tmp_path: Path):
        """Initialization creates the cache directory."""
        sid = "test_init_dir"
        ds = DataSerializer(tmp_path, sid)
        assert (tmp_path / sid).exists()
        assert (tmp_path / sid).is_dir()

    def test_append_and_read(self, serializer: DataSerializer):
        """Append a single record and read it back."""
        record = {"id": "W1", "title": "Test Work"}
        serializer.append("Work", record)
        result = serializer.read("Work")
        assert len(result) == 1
        assert result[0]["id"] == "W1"
        assert result[0]["title"] == "Test Work"

    def test_append_batch(self, serializer: DataSerializer):
        """Append multiple records at once."""
        records = [
            {"id": "W1", "title": "Work 1"},
            {"id": "W2", "title": "Work 2"},
            {"id": "W3", "title": "Work 3"},
        ]
        serializer.append_batch("Work", records)
        result = serializer.read("Work")
        assert len(result) == 3
        assert [r["id"] for r in result] == ["W1", "W2", "W3"]

    def test_read_empty(self, serializer: DataSerializer):
        """Reading a non-existent label returns an empty list."""
        result = serializer.read("NonExistent")
        assert result == []

    def test_read_all(self, serializer: DataSerializer):
        """read_all returns all entity types."""
        serializer.append("Work", {"id": "W1"})
        serializer.append("Author", {"id": "A1"})
        all_data = serializer.read_all()
        assert "Work" in all_data
        assert "Author" in all_data
        assert len(all_data["Work"]) == 1
        assert len(all_data["Author"]) == 1

    def test_manifest_write_read(self, serializer: DataSerializer):
        """Write and read manifest."""
        meta = {"query": "machine learning", "count": 42}
        serializer.write_manifest(meta)
        result = serializer.read_manifest()
        assert result is not None
        assert result["query"] == "machine learning"
        assert result["count"] == 42

    def test_manifest_read_nonexistent(self, serializer: DataSerializer):
        """Reading a non-existent manifest returns None."""
        assert serializer.read_manifest() is None

    def test_count(self, serializer: DataSerializer):
        """Count records without loading them into memory."""
        assert serializer.count("Work") == 0
        serializer.append("Work", {"id": "W1"})
        serializer.append("Work", {"id": "W2"})
        assert serializer.count("Work") == 2

    def test_cleanup_removes_dir(self, serializer: DataSerializer):
        """cleanup deletes the session cache directory."""
        data_dir = serializer.data_dir
        assert data_dir.exists()
        serializer.cleanup()
        assert not data_dir.exists()

    def test_multiple_sessions_isolated(self, tmp_path: Path):
        """Multiple sessions do not interfere."""
        ds1 = DataSerializer(tmp_path, "session_a")
        ds2 = DataSerializer(tmp_path, "session_b")
        ds1.append("Work", {"id": "W1"})
        ds2.append("Work", {"id": "W2"})
        assert ds1.read("Work")[0]["id"] == "W1"
        assert ds2.read("Work")[0]["id"] == "W2"

    def test_chinese_characters(self, serializer: DataSerializer):
        """Non-ASCII characters are preserved."""
        record = {"id": "W1", "title": "人工智能"}
        serializer.append("Work", record)
        result = serializer.read("Work")
        assert result[0]["title"] == "人工智能"

    def test_append_multiple_batches(self, serializer: DataSerializer):
        """Multiple append_batch calls accumulate correctly."""
        serializer.append_batch("Work", [{"id": "W1"}, {"id": "W2"}])
        serializer.append_batch("Work", [{"id": "W3"}])
        result = serializer.read("Work")
        assert len(result) == 3

    def test_append_batch_empty_is_noop(self, serializer: DataSerializer):
        """append_batch with empty list does nothing."""
        serializer.append_batch("Work", [])
        assert serializer.count("Work") == 0
