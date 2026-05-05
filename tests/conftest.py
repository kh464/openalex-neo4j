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
