"""Tests for CLI commands."""
from unittest.mock import Mock, patch, MagicMock

import pytest
from click.testing import CliRunner

from openalex_neo4j.cli import cli
from openalex_neo4j.datasource.base import DataRecord


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def mock_neo4j_client():
    """Create a mock Neo4j client with a full driver.session() context manager.

    Returns (client, session_mock) so tests can configure session.run() data.
    """
    client = Mock()
    client.close = Mock()
    session_mock = Mock()
    session_mock.run.return_value = []

    cm = MagicMock()
    cm.__enter__.return_value = session_mock
    cm.__exit__.return_value = None
    client.driver.session.return_value = cm

    return client, session_mock


@pytest.fixture
def mock_datasource():
    """Create a mock DataSource that returns a record for any query."""
    ds = Mock()
    ds.name = "openalex"
    ds.confidence.return_value = 1.0

    record = DataRecord(
        source_name="openalex",
        source_confidence=1.0,
        external_ids={"doi": "10.1234/abc"},
        raw_data={},
        openalex_id="W123",
        title="Fetched Title",
        abstract="Fetched abstract",
        doi="10.1234/abc",
    )
    ds.fetch_by_doi.return_value = record
    ds.fetch_by_openalex_id.return_value = record
    return ds


# ---------------------------------------------------------------------------
# Basic CLI help
# ---------------------------------------------------------------------------

class TestCliBasic:
    def test_help(self, runner):
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "enrich" in result.output


# ---------------------------------------------------------------------------
# enrich command
# ---------------------------------------------------------------------------

class TestEnrichCommand:
    """Tests for `openalex-neo4j enrich`."""

    @patch("openalex_neo4j.cli._get_neo4j_client")
    @patch("openalex_neo4j.datasource.get_datasource")
    def test_enrich_no_works(
        self, mock_get_ds, mock_get_neo4j, runner,
        mock_neo4j_client, mock_datasource,
    ):
        """No works to enrich prints message and exits cleanly."""
        client, session_mock = mock_neo4j_client
        session_mock.run.return_value = []  # no works
        mock_get_neo4j.return_value = client
        mock_get_ds.return_value = mock_datasource

        result = runner.invoke(cli, [
            "enrich", "--neo4j-password", "pw",
        ])
        assert result.exit_code == 0
        assert "No works found to enrich" in result.output

    @patch("openalex_neo4j.cli._get_neo4j_client")
    @patch("openalex_neo4j.datasource.get_datasource")
    def test_enrich_dry_run(
        self, mock_get_ds, mock_get_neo4j, runner,
        mock_neo4j_client, mock_datasource,
    ):
        """Dry-run shows changes without writing to Neo4j."""
        client, session_mock = mock_neo4j_client
        session_mock.run.return_value = [
            {"id": "W1", "title": "Old Title", "doi": "10.1234/abc"},
        ]
        mock_get_neo4j.return_value = client
        mock_get_ds.return_value = mock_datasource

        result = runner.invoke(cli, [
            "enrich", "--neo4j-password", "pw", "--dry-run",
        ])
        assert result.exit_code == 0
        assert "DRY RUN" in result.output
        assert "Dry-run complete" in result.output
        assert "W1" in result.output
        # dry_run path only does the initial query, no write queries
        assert session_mock.run.call_count == 1

    @patch("openalex_neo4j.cli._get_neo4j_client")
    @patch("openalex_neo4j.datasource.get_datasource")
    def test_enrich_actual_write(
        self, mock_get_ds, mock_get_neo4j, runner,
        mock_neo4j_client, mock_datasource,
    ):
        """Non-dry-run writes changes to Neo4j."""
        client, session_mock = mock_neo4j_client
        session_mock.run.return_value = [
            {"id": "W1", "title": "Old Title", "doi": "10.1234/abc"},
        ]
        mock_get_neo4j.return_value = client
        mock_get_ds.return_value = mock_datasource

        result = runner.invoke(cli, [
            "enrich", "--neo4j-password", "pw",
        ])
        assert result.exit_code == 0
        assert "Enriched" in result.output
        # First call: query works; subsequent calls: write changes
        assert session_mock.run.call_count >= 2  # initial query + writes

    @patch("openalex_neo4j.cli._get_neo4j_client")
    @patch("openalex_neo4j.datasource.get_datasource")
    def test_enrich_with_session_filter(
        self, mock_get_ds, mock_get_neo4j, runner,
        mock_neo4j_client, mock_datasource,
    ):
        """--session filters works by import session."""
        client, session_mock = mock_neo4j_client
        session_mock.run.return_value = [
            {"id": "W1", "title": "Paper", "doi": "10.1234/abc"},
        ]
        mock_get_neo4j.return_value = client
        mock_get_ds.return_value = mock_datasource

        result = runner.invoke(cli, [
            "enrich", "--neo4j-password", "pw", "--session", "S001",
        ])
        assert result.exit_code == 0
        # Verify the first query included the session filter
        first_call_kwargs = session_mock.run.call_args_list[0][1]
        assert first_call_kwargs.get("session_id") == "S001"

    @patch("openalex_neo4j.cli._get_neo4j_client")
    @patch("openalex_neo4j.datasource.get_datasource")
    def test_enrich_limit(
        self, mock_get_ds, mock_get_neo4j, runner,
        mock_neo4j_client, mock_datasource,
    ):
        """--limit caps number of works enriched."""
        client, session_mock = mock_neo4j_client
        session_mock.run.return_value = [
            {"id": f"W{i}", "title": f"Paper {i}", "doi": f"10.1234/{i}"}
            for i in range(10)
        ]
        mock_get_neo4j.return_value = client
        mock_get_ds.return_value = mock_datasource

        result = runner.invoke(cli, [
            "enrich", "--neo4j-password", "pw", "--limit", "3", "--dry-run",
        ])
        assert result.exit_code == 0
        assert "Found 3 works to enrich" in result.output

    @patch("openalex_neo4j.cli._get_neo4j_client")
    @patch("openalex_neo4j.datasource.list_datasources")
    @patch("openalex_neo4j.datasource.get_datasource")
    def test_enrich_unknown_datasource(
        self, mock_get_ds, mock_list, mock_get_neo4j, runner,
        mock_neo4j_client,
    ):
        """Unknown datasource prints error and exits."""
        client, _ = mock_neo4j_client
        mock_get_neo4j.return_value = client
        mock_get_ds.side_effect = KeyError("unknown")
        mock_list.return_value = ["openalex"]

        result = runner.invoke(cli, [
            "enrich", "--neo4j-password", "pw",
            "--datasource", "unknown",
        ])
        assert result.exit_code == 1
        assert "Unknown datasource" in result.output

    def test_enrich_no_password(self, runner):
        """Missing password prints error."""
        result = runner.invoke(cli, [
            "enrich", "--neo4j-password", "",
        ])
        assert result.exit_code == 1
        assert "password is required" in result.output

    @patch("openalex_neo4j.cli._get_neo4j_client")
    @patch("openalex_neo4j.datasource.get_datasource")
    def test_enrich_overwrite_strategy(
        self, mock_get_ds, mock_get_neo4j, runner,
        mock_neo4j_client, mock_datasource,
    ):
        """--strategy overwrite is accepted and displayed."""
        client, session_mock = mock_neo4j_client
        session_mock.run.return_value = [
            {"id": "W1", "title": "Old Title", "doi": "10.1234/abc"},
        ]
        mock_get_neo4j.return_value = client
        mock_get_ds.return_value = mock_datasource

        result = runner.invoke(cli, [
            "enrich", "--neo4j-password", "pw",
            "--strategy", "overwrite", "--dry-run",
        ])
        assert result.exit_code == 0
        assert "Strategy: overwrite" in result.output

    @patch("openalex_neo4j.cli._get_neo4j_client")
    @patch("openalex_neo4j.datasource.get_datasource")
    def test_enrich_multiple_datasources(
        self, mock_get_ds, mock_get_neo4j, runner,
        mock_neo4j_client,
    ):
        """Multiple --datasource options are tried in order."""
        client, session_mock = mock_neo4j_client
        session_mock.run.return_value = [
            {"id": "W1", "title": "Paper", "doi": None},  # no doi, falls to id
        ]
        mock_get_neo4j.return_value = client

        ds1 = Mock()
        ds1.name = "crossref"
        ds1.fetch_by_doi.return_value = None
        ds1.fetch_by_openalex_id.return_value = None

        ds2 = Mock()
        ds2.name = "openalex"
        ds2.fetch_by_doi.return_value = None
        ds2.fetch_by_openalex_id.return_value = DataRecord(
            source_name="openalex", source_confidence=1.0,
            external_ids={}, raw_data={},
            title="Fetched Title",
        )

        def get_ds_side_effect(name, **config):
            return {"crossref": ds1, "openalex": ds2}[name]

        mock_get_ds.side_effect = get_ds_side_effect

        result = runner.invoke(cli, [
            "enrich", "--neo4j-password", "pw",
            "--datasource", "crossref",
            "--datasource", "openalex",
            "--dry-run",
        ])
        assert result.exit_code == 0
        # ds2 should have been tried since ds1 returned None
        ds2.fetch_by_openalex_id.assert_called_once_with("W1")


# ---------------------------------------------------------------------------
# import command — cache options
# ---------------------------------------------------------------------------

class TestImportCacheOptions:
    """Tests for `openalex-neo4j import --cache-dir / --keep-cache / --resume / --list-cache`."""

    @patch("openalex_neo4j.cli.Neo4jClient")
    @patch("openalex_neo4j.cli.OpenAlexClient")
    def test_import_cache_dir_and_keep(
        self, mock_oa_client_cls, mock_neo4j_cls, runner, tmp_path,
    ):
        """--cache-dir and --keep-cache are passed through to the importer."""
        mock_neo4j_instance = Mock()
        mock_neo4j_instance.connect = Mock()
        mock_neo4j_instance.close = Mock()
        mock_neo4j_cls.return_value = mock_neo4j_instance

        mock_importer = Mock()
        mock_importer.import_from_query.return_value = {}
        mock_importer.current_session = "S_test"

        with patch("openalex_neo4j.cli.OpenAlexImporter", return_value=mock_importer):
            result = runner.invoke(cli, [
                "import",
                "--query", "test",
                "--limit", "1",
                "--neo4j-password", "pw",
                "--cache-dir", str(tmp_path),
                "--keep-cache",
            ])
        assert result.exit_code == 0
        # Check import_from_query was called with the right kwargs
        _, kwargs = mock_importer.import_from_query.call_args
        assert "cache_dir" in kwargs
        assert "keep_cache" in kwargs
        assert kwargs["keep_cache"] is True

    @patch("openalex_neo4j.cli.Neo4jClient")
    @patch("openalex_neo4j.cli.OpenAlexClient")
    def test_import_resume(
        self, mock_oa_client_cls, mock_neo4j_cls, runner, tmp_path,
    ):
        """--resume calls import_from_cache instead of import_from_query."""
        mock_neo4j_instance = Mock()
        mock_neo4j_instance.connect = Mock()
        mock_neo4j_instance.close = Mock()
        mock_neo4j_cls.return_value = mock_neo4j_instance

        mock_importer = Mock()
        mock_importer.import_from_cache.return_value = {"works": 5}

        with patch("openalex_neo4j.cli.OpenAlexImporter", return_value=mock_importer):
            result = runner.invoke(cli, [
                "import",
                "--query", "test",
                "--limit", "1",
                "--neo4j-password", "pw",
                "--resume", "S20260508_1200",
            ])
        assert result.exit_code == 0
        mock_importer.import_from_cache.assert_called_once()
        assert "Resumed import" in result.output

    def test_import_list_cache_no_dir(self, runner, tmp_path):
        """--list-cache with an empty cache directory."""
        result = runner.invoke(cli, [
            "import",
            "--query", "irrelevant",
            "--limit", "1",
            "--neo4j-password", "pw",
            "--list-cache",
            "--cache-dir", str(tmp_path),
        ])
        assert result.exit_code == 0
        # Should not crash; the cache dir is empty / non-existent

    def test_import_list_cache_with_data(self, runner, tmp_path):
        """--list-cache displays cached sessions."""
        # Create a fake manifest
        cache_session = tmp_path / "S20260508_1200"
        cache_session.mkdir(parents=True)
        manifest = cache_session / "manifest.json"
        manifest.write_text('{"query": "machine learning", "entity_counts": {"Work": 10}}')

        result = runner.invoke(cli, [
            "import",
            "--query", "irrelevant",
            "--limit", "1",
            "--neo4j-password", "pw",
            "--list-cache",
            "--cache-dir", str(tmp_path),
        ])
        assert result.exit_code == 0
        assert "S20260508_1200" in result.output
        assert "machine learning" in result.output
        assert "10" in result.output
