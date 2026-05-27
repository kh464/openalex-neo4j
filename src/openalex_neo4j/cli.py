"""Command-line interface for OpenAlex to Neo4j import tool."""

import json
import logging
import os
import sys
from pathlib import Path

import click
from dotenv import load_dotenv

from .neo4j_client import Neo4jClient
from .openalex_client import OpenAlexClient
from .rate_limiter import TokenBucket
from .importer import OpenAlexImporter
from .search import HybridSearcher, format_results_table
from .session_manager import SessionManager
from .datasource import merge_record

# Load environment variables from .env file
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@click.group()
def cli():
    """OpenAlex to Neo4j - Import and query scholarly data."""
    pass


# ---------------------------------------------------------------------------
# Helper: shared Neo4j connection
# ---------------------------------------------------------------------------

def _get_neo4j_client(neo4j_uri, neo4j_username, neo4j_password) -> Neo4jClient:
    """Create and connect a Neo4j client."""
    client = Neo4jClient(neo4j_uri, neo4j_username, neo4j_password)
    client.connect()
    return client


def _common_neo4j_options(f):
    """Decorator adding standard Neo4j connection options to a command."""
    f = click.option("--neo4j-uri",
        default=lambda: os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        help="Neo4j connection URI (env: NEO4J_URI)")(f)
    f = click.option("--neo4j-username",
        default=lambda: os.getenv("NEO4J_USERNAME", "neo4j"),
        help="Neo4j username (env: NEO4J_USERNAME)")(f)
    f = click.option("--neo4j-password",
        default=lambda: os.getenv("NEO4J_PASSWORD"),
        help="Neo4j password (env: NEO4J_PASSWORD)")(f)
    return f


@cli.command(name="import")
@click.option(
    "--query", "-q",
    required=False,
    help="OpenAlex search query (e.g., 'artificial intelligence' — required unless --resume or --list-cache)",
)
@click.option(
    "--limit", "-l",
    default=None,
    type=int,
    help="Maximum number of works to fetch (default: all matching)",
)
@click.option(
    "--neo4j-uri",
    default=lambda: os.getenv("NEO4J_URI", "bolt://localhost:7687"),
    help="Neo4j connection URI (env: NEO4J_URI)",
)
@click.option(
    "--neo4j-username",
    default=lambda: os.getenv("NEO4J_USERNAME", "neo4j"),
    help="Neo4j username (env: NEO4J_USERNAME)",
)
@click.option(
    "--neo4j-password",
    default=lambda: os.getenv("NEO4J_PASSWORD"),
    help="Neo4j password (env: NEO4J_PASSWORD)",
)
@click.option(
    "--email",
    default=lambda: os.getenv("OPENALEX_EMAIL"),
    help="Email for OpenAlex polite pool (env: OPENALEX_EMAIL)",
)
@click.option(
    "--expand-depth",
    default=1,
    type=int,
    help="Levels of relationship expansion (default: 1)",
)
@click.option(
    "--skip-abstracts",
    is_flag=True,
    help="Skip storing abstracts (faster import, less storage)",
)
@click.option(
    "--generate-embeddings",
    is_flag=True,
    help="Generate embeddings for semantic search (requires sentence-transformers)",
)
@click.option(
    "--verbose", "-v",
    is_flag=True,
    help="Enable verbose logging",
)
@click.option(
    "--tag", "-t",
    default=None,
    help="Optional tag/alias for this import session",
)
@click.option(
    "--node-tag",
    "node_tags",
    multiple=True,
    help="Custom tag to attach to every imported node (can repeat)",
)
@click.option(
    "--skip-constraints",
    is_flag=True,
    help="Skip creating constraints and indexes (faster when Neo4j already set up)",
)
@click.option(
    "--clean",
    type=click.Choice(["off", "report", "auto"]),
    default="off",
    help="Data cleaning level: off (default), report, or auto",
)
@click.option(
    "--quality-report", "--qr",
    is_flag=True,
    help="Print quality report after import",
)
@click.option(
    "--from-year",
    type=int,
    default=None,
    help="Start publication year (inclusive), e.g. 2020",
)
@click.option(
    "--to-year",
    type=int,
    default=None,
    help="End publication year (inclusive), e.g. 2024",
)
@click.option(
    "--type",
    "work_types",
    multiple=True,
    help="OpenAlex work type filter, e.g. article or review (can repeat)",
)
@click.option(
    "--cache-dir",
    default=None,
    type=click.Path(file_okay=False),
    help="Local cache directory (default: ~/.openalex-neo4j/cache/)",
)
@click.option(
    "--keep-cache",
    is_flag=True,
    help="Keep local cache after import (for debugging/resume)",
)
@click.option(
    "--resume",
    default=None,
    help="Resume import from cached session ID (skips API fetch)",
)
@click.option(
    "--list-cache",
    is_flag=True,
    help="List cached import sessions",
)
@click.option(
    "--fetch-only",
    is_flag=True,
    help="Only fetch data to local cache, skip Neo4j import",
)
def import_data(
    query: str,
    limit: int | None,
    neo4j_uri: str,
    neo4j_username: str,
    neo4j_password: str | None,
    email: str | None,
    expand_depth: int,
    skip_abstracts: bool,
    generate_embeddings: bool,
    verbose: bool,
    tag: str | None,
    node_tags: tuple[str, ...],
    skip_constraints: bool,
    clean: str,
    quality_report: bool,
    from_year: int | None,
    to_year: int | None,
    work_types: tuple[str, ...],
    cache_dir: str | None,
    keep_cache: bool,
    resume: str | None,
    list_cache: bool,
    fetch_only: bool,
) -> None:
    """Import OpenAlex scholarly data into Neo4j.

    Search OpenAlex using a natural language query and import the results
    along with related entities (authors, institutions, citations, etc.)
    into a Neo4j graph database.

    Example:

        openalex-neo4j import --query "machine learning" --limit 50

    """

    def _get_cache_root() -> Path:
        """Resolve the cache root directory."""
        return Path(cache_dir) if cache_dir else Path.home() / ".openalex-neo4j" / "cache"

    # Handle --list-cache
    if list_cache:
        cache_root = _get_cache_root()
        if cache_root.exists():
            for d in sorted(cache_root.iterdir()):
                if d.is_dir():
                    manifest_file = d / "manifest.json"
                    if manifest_file.exists():
                        m = json.loads(manifest_file.read_text())
                        click.echo(
                            f"{d.name}  "
                            f"query={m.get('query')}  "
                            f"works={m.get('entity_counts', {}).get('Work', 0)}"
                        )
        else:
            click.echo(f"No cache directory found at {cache_root}")
        return

    # Handle --resume
    if resume:
        click.echo(f"Resuming import from cache session: {resume}")

    # Set logging level
    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Validate inputs
    if not neo4j_password:
        click.echo("Error: Neo4j password is required (use --neo4j-password or NEO4J_PASSWORD env var)", err=True)
        sys.exit(1)

    if not query and not resume and not list_cache:
        click.echo("Error: --query is required unless using --resume or --list-cache", err=True)
        sys.exit(1)

    if limit is not None and limit <= 0:
        click.echo("Error: --limit must be positive", err=True)
        sys.exit(1)

    if expand_depth < 1:
        click.echo("Error: --expand-depth must be at least 1", err=True)
        sys.exit(1)

    # Display configuration
    click.echo("=" * 70)
    click.echo("OpenAlex to Neo4j Import")
    click.echo("=" * 70)
    if resume:
        click.echo(f"Query: (from cache — session {resume})")
    else:
        click.echo(f"Query: {query}")
    click.echo(f"Limit: {'all' if limit is None else limit} works")
    if from_year:
        click.echo(f"From year: {from_year}")
    if to_year:
        click.echo(f"To year: {to_year}")
    if work_types:
        click.echo(f"Work types: {', '.join(work_types)}")
    click.echo(f"Expand depth: {expand_depth}")
    click.echo(f"Neo4j URI: {neo4j_uri}")
    click.echo(f"Neo4j username: {neo4j_username}")
    click.echo(f"OpenAlex email: {email or '(not set - using anonymous pool)'}")
    if node_tags:
        click.echo(f"Node tags: {', '.join(node_tags)}")
    if keep_cache:
        click.echo(f"Cache: {_get_cache_root()} (keep)")
    else:
        click.echo(f"Cache dir: {_get_cache_root()}")
    click.echo("=" * 70)
    click.echo()

    try:
        # ─── Fetch-only mode: skip Neo4j entirely ───
        if fetch_only:
            click.echo("Fetch-only mode: will not write to Neo4j")
            click.echo()
            openalex_client = OpenAlexClient(email, rate_limiter=TokenBucket())
            importer = OpenAlexImporter(None, openalex_client)  # type: ignore[arg-type]
            counts = importer.import_from_query(
                query, limit, expand_depth,
                skip_abstracts=skip_abstracts,
                generate_embeddings=generate_embeddings,
                tag=tag,
                from_year=from_year,
                to_year=to_year,
                work_types=list(work_types),
                cache_dir=_get_cache_root(),
                keep_cache=True,
                fetch_only=True,
                node_tags=list(node_tags),
            )
            click.echo()
            click.echo("=" * 70)
            click.echo("Fetch Complete!")
            click.echo("=" * 70)
            click.echo(f"  Cache:       {counts.get('cache_dir')}")
            click.echo(f"  Session ID:  {counts.get('session_id')}")
            click.echo()
            click.echo("  Use --resume to import this cache into Neo4j later:")
            click.echo(f"    uv run openalex-neo4j import --resume {counts.get('session_id')}")
            click.echo("=" * 70)
            return

        # Initialize clients
        click.echo("Connecting to Neo4j...")
        neo4j_client = Neo4jClient(neo4j_uri, neo4j_username, neo4j_password)
        neo4j_client.connect()

        click.echo("Initializing OpenAlex client...")
        openalex_client = OpenAlexClient(email, rate_limiter=TokenBucket())

        click.echo("Starting import...")
        click.echo()

        # Initialize session manager for tracking
        session_manager = SessionManager(neo4j_client)

        # Create importer with session tracking
        importer = OpenAlexImporter(neo4j_client, openalex_client, session_manager=session_manager)

        # Handle --resume (skip API fetch, import from cache)
        if resume:
            counts = importer.import_from_cache(
                resume,
                _get_cache_root(),
                node_tags=list(node_tags),
            )
            click.echo(f"Resumed import from cache session: {resume}")
            # Skip quality check for resumed imports — the cache dicts are
            # already flushed, so importer.works etc. won't be populated.
            neo4j_client.close()
            return

        # Normal import from OpenAlex API
        counts = importer.import_from_query(
            query, limit, expand_depth,
            skip_abstracts=skip_abstracts,
            generate_embeddings=generate_embeddings,
            tag=tag,
            skip_constraints=skip_constraints,
            from_year=from_year,
            to_year=to_year,
            work_types=list(work_types),
            cache_dir=_get_cache_root(),
            keep_cache=keep_cache,
            node_tags=list(node_tags),
        )

        # Quality check and cleaning
        if clean != "off" or quality_report:
            from .data_quality import DataQualityPipeline, clean_entity_fields

            pipeline = DataQualityPipeline()

            # Build entities dict from importer collections
            entities = {}
            if importer.works:
                entities["Work"] = list(importer.works.values())
            if importer.authors:
                entities["Author"] = list(importer.authors.values())
            if importer.institutions:
                entities["Institution"] = list(importer.institutions.values())
            if importer.sources:
                entities["Source"] = list(importer.sources.values())
            if importer.topics:
                entities["Topic"] = list(importer.topics.values())

            # Auto-clean if requested
            if clean == "auto":
                total_changes = 0
                for entity_list in entities.values():
                    for entity in entity_list:
                        changes = clean_entity_fields(entity)
                        total_changes += len(changes)
                if total_changes > 0:
                    click.echo(f"Auto-cleaned {total_changes} fields")

            # Run quality check
            report = pipeline.run(
                importer.current_session or "unknown",
                entities,
            )

            # Store quality summary in session
            if importer.session_manager and importer.current_session:
                importer.session_manager.complete_session(
                    importer.current_session,
                    stats=counts,
                    quality_summary=report.summary,
                )

            # Print summary if requested
            if quality_report:
                click.echo()
                click.echo("=" * 70)
                click.echo("Quality Report")
                click.echo("=" * 70)
                click.echo(f"  Total entities checked: {report.total_entities}")
                click.echo(f"  Errors:   {report.error_count}")
                click.echo(f"  Warnings: {report.warning_count}")
                click.echo(f"  Infos:    {report.info_count}")
                if report.violations:
                    click.echo()
                    click.echo("  Top violations:")
                    for v in report.violations[:10]:
                        click.echo(f"    [{v.severity.upper()}] {v.entity_type}:{v.entity_id} - {v.message}")
                    if len(report.violations) > 10:
                        click.echo(f"    ... and {len(report.violations) - 10} more")
                click.echo("=" * 70)
                click.echo()

        # Display results
        click.echo()
        click.echo("=" * 70)
        click.echo("Import Complete!")
        click.echo("=" * 70)
        click.echo()
        click.echo("Nodes created:")
        click.echo(f"  Works: {counts.get('works', 0)}")
        click.echo(f"  Authors: {counts.get('authors', 0)}")
        click.echo(f"  Institutions: {counts.get('institutions', 0)}")
        click.echo(f"  Sources: {counts.get('sources', 0)}")
        click.echo(f"  Topics: {counts.get('topics', 0)}")
        click.echo(f"  Publishers: {counts.get('publishers', 0)}")
        click.echo(f"  Funders: {counts.get('funders', 0)}")
        click.echo()
        click.echo("Relationships created:")
        click.echo(f"  AUTHORED: {counts.get('authored', 0)}")
        click.echo(f"  AFFILIATED_WITH: {counts.get('affiliated_with', 0)}")
        click.echo(f"  PUBLISHED_IN: {counts.get('published_in', 0)}")
        click.echo(f"  CITES: {counts.get('cites', 0)}")
        click.echo(f"  HAS_TOPIC: {counts.get('has_topic', 0)}")
        click.echo(f"  FUNDED_BY: {counts.get('funded_by', 0)}")
        click.echo(f"  PUBLISHED_BY: {counts.get('published_by', 0)}")
        click.echo("=" * 70)

        # Display session ID
        if importer.current_session:
            click.echo()
            click.echo(f"Session ID: {importer.current_session}")
            click.echo(f"Use 'openalex-neo4j session show {importer.current_session}' for details")

        # Clean up
        neo4j_client.close()

    except KeyboardInterrupt:
        click.echo("\nImport cancelled by user", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"\nError: {e}", err=True)
        logger.exception("Import failed")
        sys.exit(1)


# ---------------------------------------------------------------------------
# count command
# ---------------------------------------------------------------------------

@cli.command(name="count")
@click.option(
    "--query", "-q",
    required=True,
    help="OpenAlex search query to count",
)
@click.option(
    "--from-year",
    type=int,
    default=None,
    help="Filter: start publication year (inclusive)",
)
@click.option(
    "--to-year",
    type=int,
    default=None,
    help="Filter: end publication year (inclusive)",
)
@click.option(
    "--type",
    "work_types",
    multiple=True,
    help="OpenAlex work type filter, e.g. article or review (can repeat)",
)
@click.option(
    "--email",
    default=lambda: os.getenv("OPENALEX_EMAIL"),
    help="Email for OpenAlex polite pool (env: OPENALEX_EMAIL)",
)
def count_command(
    query: str,
    from_year: int | None,
    to_year: int | None,
    work_types: tuple[str, ...],
    email: str | None,
) -> None:
    """Count how many works match a search query in OpenAlex.

    Makes a single API call and displays the total count without
    fetching any work data.
    """
    try:
        client = OpenAlexClient(email, rate_limiter=TokenBucket())
        total = client.count_works(
            query, from_year=from_year, to_year=to_year, work_types=list(work_types),
        )

        click.echo("=" * 70)
        click.echo("OpenAlex Query Count")
        click.echo("=" * 70)
        click.echo(f"  Query:    {query}")
        if from_year:
            click.echo(f"  From:     {from_year}")
        if to_year:
            click.echo(f"  To:       {to_year}")
        if work_types:
            click.echo(f"  Types:    {', '.join(work_types)}")
        click.echo(f"  Matching: {total:,}")
        click.echo("=" * 70)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        logger.exception("Count failed")
        sys.exit(1)


@cli.command(name="search")
@click.option(
    "--query", "-q",
    required=True,
    help="Search query (natural language)",
)
@click.option(
    "--limit", "-l",
    default=10,
    type=int,
    help="Number of results to return (default: 10)",
)
@click.option(
    "--neo4j-uri",
    default=lambda: os.getenv("NEO4J_URI", "bolt://localhost:7687"),
    help="Neo4j connection URI (env: NEO4J_URI)",
)
@click.option(
    "--neo4j-username",
    default=lambda: os.getenv("NEO4J_USERNAME", "neo4j"),
    help="Neo4j username (env: NEO4J_USERNAME)",
)
@click.option(
    "--neo4j-password",
    default=lambda: os.getenv("NEO4J_PASSWORD"),
    help="Neo4j password (env: NEO4J_PASSWORD)",
)
@click.option(
    "--vector-weight",
    default=0.5,
    type=float,
    help="Weight for vector search (0-1, default: 0.5)",
)
@click.option(
    "--fulltext-weight",
    default=0.5,
    type=float,
    help="Weight for fulltext search (0-1, default: 0.5)",
)
@click.option(
    "--rrf-k",
    default=60,
    type=int,
    help="RRF constant for rank fusion (default: 60)",
)
@click.option(
    "--verbose", "-v",
    is_flag=True,
    help="Enable verbose logging",
)
def search(
    query: str,
    limit: int,
    neo4j_uri: str,
    neo4j_username: str,
    neo4j_password: str | None,
    vector_weight: float,
    fulltext_weight: float,
    rrf_k: int,
    verbose: bool,
) -> None:
    """Search the knowledge graph using hybrid retrieval.

    Combines vector similarity search and fulltext search using reciprocal
    rank fusion (RRF) to find the most relevant papers. Returns detailed
    information including authors, institutions, topics, and citations.

    Example:

        openalex-neo4j search --query "neural networks for computer vision" --limit 10

    """
    # Set logging level
    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    else:
        # For search, only show warnings by default
        logging.getLogger().setLevel(logging.WARNING)

    # Validate inputs
    if not neo4j_password:
        click.echo("Error: Neo4j password is required (use --neo4j-password or NEO4J_PASSWORD env var)", err=True)
        sys.exit(1)

    if limit <= 0:
        click.echo("Error: --limit must be positive", err=True)
        sys.exit(1)

    if not (0 <= vector_weight <= 1):
        click.echo("Error: --vector-weight must be between 0 and 1", err=True)
        sys.exit(1)

    if not (0 <= fulltext_weight <= 1):
        click.echo("Error: --fulltext-weight must be between 0 and 1", err=True)
        sys.exit(1)

    try:
        # Connect to Neo4j
        neo4j_client = Neo4jClient(neo4j_uri, neo4j_username, neo4j_password)
        neo4j_client.connect()

        # Create searcher
        searcher = HybridSearcher(neo4j_client.driver)

        # Display search parameters
        click.echo("=" * 120)
        click.echo("Hybrid Search - OpenAlex Knowledge Graph")
        click.echo("=" * 120)
        click.echo(f"Query: {query}")
        click.echo(f"Vector weight: {vector_weight}, Fulltext weight: {fulltext_weight}, RRF k: {rrf_k}")
        click.echo("=" * 120)
        click.echo()

        # Perform search
        results = searcher.search(
            query=query,
            limit=limit,
            vector_weight=vector_weight,
            fulltext_weight=fulltext_weight,
            k=rrf_k
        )

        # Display results
        if not results:
            click.echo("No results found.")
        else:
            click.echo(f"Found {len(results)} results:\n")
            table = format_results_table(results)
            click.echo(table)

        # Clean up
        neo4j_client.close()

    except KeyboardInterrupt:
        click.echo("\nSearch cancelled by user", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"\nError: {e}", err=True)
        logger.exception("Search failed")
        sys.exit(1)


# Keep backwards compatibility with old command name
def main():
    """Entry point for CLI - redirects to group."""
    cli()


# ---------------------------------------------------------------------------
# clear command
# ---------------------------------------------------------------------------

@cli.command(name="clear")
@_common_neo4j_options
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt")
def clear_data(neo4j_uri, neo4j_username, neo4j_password, yes):
    """Clear ALL data from the Neo4j database.

    WARNING: This will permanently delete all nodes and relationships.
    """
    if not neo4j_password:
        click.echo("Error: Neo4j password is required", err=True)
        sys.exit(1)

    if not yes:
        click.echo()
        click.echo("!" * 70)
        click.echo("WARNING: This will permanently delete ALL data in the database!")
        click.echo("!" * 70)
        click.echo()
        click.confirm("Are you sure you want to continue?", abort=True)

    click.echo("Connecting to Neo4j...")
    neo4j_client = _get_neo4j_client(neo4j_uri, neo4j_username, neo4j_password)

    click.echo("Clearing database...")
    neo4j_client.clear_database()
    neo4j_client.close()

    # Also clear local session metadata so `sessions` doesn't show stale entries
    from .session_manager import SessionManager
    manager = SessionManager(neo4j_client=None)  # type: ignore[arg-type]
    manager.clear_all_sessions()

    click.echo("Database cleared successfully.")


# ---------------------------------------------------------------------------
# stats command
# ---------------------------------------------------------------------------

@cli.command(name="stats")
@_common_neo4j_options
def stats_command(neo4j_uri, neo4j_username, neo4j_password):
    """Show statistics about imported data in the Neo4j database."""
    if not neo4j_password:
        click.echo("Error: Neo4j password is required", err=True)
        sys.exit(1)

    neo4j_client = _get_neo4j_client(neo4j_uri, neo4j_username, neo4j_password)

    click.echo("=" * 70)
    click.echo("Database Statistics")
    click.echo("=" * 70)

    labels = ["Work", "Author", "Institution", "Source", "Topic", "Publisher", "Funder", "ImportSession"]
    for label in labels:
        try:
            count = neo4j_client.get_node_count(label)
            click.echo(f"  {label}: {count}")
        except Exception:
            click.echo(f"  {label}: (error)")

    click.echo()
    click.echo("Relationships:")
    rels = ["AUTHORED", "AFFILIATED_WITH", "PUBLISHED_IN", "CITES", "HAS_TOPIC", "FUNDED_BY", "PUBLISHED_BY"]
    for rel in rels:
        try:
            count = neo4j_client.get_relationship_count(rel)
            click.echo(f"  {rel}: {count}")
        except Exception:
            click.echo(f"  {rel}: (error)")

    click.echo("=" * 70)
    neo4j_client.close()


# ---------------------------------------------------------------------------
# export command
# ---------------------------------------------------------------------------

@cli.command(name="export")
@_common_neo4j_options
@click.option("--node-tag", required=True, help="Export only nodes containing this import tag")
@click.option("--label", "labels", multiple=True, help="Restrict export to one or more labels")
@click.option("--output", required=True, type=click.Path(dir_okay=False, path_type=Path),
              help="Output JSONL file path")
def export_command(neo4j_uri, neo4j_username, neo4j_password, node_tag, labels, output):
    """Export tagged Neo4j nodes to a JSONL file."""
    if not neo4j_password:
        click.echo("Error: Neo4j password is required", err=True)
        sys.exit(1)

    neo4j_client = _get_neo4j_client(neo4j_uri, neo4j_username, neo4j_password)

    query = """
        MATCH (n)
        WHERE $node_tag IN coalesce(n.import_tags, [])
          AND ($labels = [] OR any(label IN labels(n) WHERE label IN $labels))
        RETURN labels(n) as labels, properties(n) as props
        ORDER BY coalesce(n.id, ''), head(labels(n))
    """

    exported = 0
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with neo4j_client.driver.session() as session:
            result = session.run(query, node_tag=node_tag, labels=list(labels))
            with output.open("w", encoding="utf-8") as f:
                for record in result:
                    row = dict(record["props"])
                    row["_labels"] = record["labels"]
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
                    exported += 1
    finally:
        neo4j_client.close()

    click.echo(f"Exported {exported} nodes to {output}")


# ---------------------------------------------------------------------------
# session group
# ---------------------------------------------------------------------------

@cli.group(name="session")
def session_group():
    """Manage import sessions."""
    pass


@session_group.command(name="list")
@_common_neo4j_options
@click.option("--limit", default=20, type=int, help="Number of sessions to show")
def session_list(neo4j_uri, neo4j_username, neo4j_password, limit):
    """List all import sessions."""
    if not neo4j_password:
        click.echo("Error: Neo4j password is required", err=True)
        sys.exit(1)

    neo4j_client = _get_neo4j_client(neo4j_uri, neo4j_username, neo4j_password)
    manager = SessionManager(neo4j_client)
    sessions = manager.list_sessions(limit=limit)

    if not sessions:
        click.echo("No import sessions found.")
        neo4j_client.close()
        return

    click.echo("-" * 90)
    click.echo(f"{'Session ID':<20} {'Query':<30} {'Status':<12} {'Stats':<20}")
    click.echo("-" * 90)
    for s in sessions:
        stats_str = ""
        if s.stats:
            stats_str = f"works={s.stats.get('works', 0)}"
        tag_str = f" [{s.tag}]" if s.tag else ""
        click.echo(
            f"{s.id:<20} {s.query[:28]:<30} {s.status:<12} {stats_str:<20}"
            f"{tag_str}"
        )
    click.echo("-" * 90)

    neo4j_client.close()


@session_group.command(name="show")
@_common_neo4j_options
@click.argument("session_id")
def session_show(neo4j_uri, neo4j_username, neo4j_password, session_id):
    """Show details for a specific import session."""
    if not neo4j_password:
        click.echo("Error: Neo4j password is required", err=True)
        sys.exit(1)

    neo4j_client = _get_neo4j_client(neo4j_uri, neo4j_username, neo4j_password)
    manager = SessionManager(neo4j_client)
    session = manager.get_session(session_id)

    if not session:
        click.echo(f"Session '{session_id}' not found.", err=True)
        neo4j_client.close()
        sys.exit(1)

    click.echo("=" * 70)
    click.echo(f"Session: {session.id}")
    click.echo("=" * 70)
    click.echo(f"  Query:         {session.query}")
    click.echo(f"  Status:        {session.status}")
    click.echo(f"  Limit:         {session.limit}")
    click.echo(f"  Expand depth:  {session.expand_depth}")
    click.echo(f"  Tag:           {session.tag or '(none)'}")
    click.echo(f"  Created at:    {session.created_at}")
    if session.stats:
        click.echo()
        click.echo("  Import Stats:")
        for key, val in sorted(session.stats.items()):
            click.echo(f"    {key}: {val}")

    # Query current node counts for this session
    click.echo()
    click.echo("  Current nodes in this session (from graph):")
    try:
        node_counts = manager.get_session_node_counts(session_id)
        for label, count in sorted(node_counts.items()):
            click.echo(f"    {label}: {count}")
    except Exception as e:
        click.echo(f"    (could not query: {e})")

    click.echo("=" * 70)
    neo4j_client.close()


@session_group.command(name="delete")
@_common_neo4j_options
@click.argument("session_id")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt")
def session_delete(neo4j_uri, neo4j_username, neo4j_password, session_id, yes):
    """Delete all data associated with a session.

    This removes nodes that were exclusively created by this session,
    and unlinks shared nodes from this session's tracking.
    """
    if not neo4j_password:
        click.echo("Error: Neo4j password is required", err=True)
        sys.exit(1)

    if not yes:
        click.echo()
        click.echo(f"WARNING: This will delete all data for session '{session_id}'!")
        click.echo("This action cannot be undone.")
        click.echo()
        click.confirm("Are you sure you want to continue?", abort=True)

    neo4j_client = _get_neo4j_client(neo4j_uri, neo4j_username, neo4j_password)
    manager = SessionManager(neo4j_client)

    try:
        result = manager.delete_session(session_id)
        click.echo(f"Session '{session_id}' deleted.")
        click.echo(f"  Nodes removed:  {result.get('deleted', 0)}")
        click.echo(f"  Nodes updated:  {result.get('updated', 0)}")
    except Exception as e:
        click.echo(f"Error deleting session: {e}", err=True)
        sys.exit(1)
    finally:
        neo4j_client.close()


@session_group.command(name="tag")
@_common_neo4j_options
@click.argument("session_id")
@click.option("--name", required=True, help="Tag name for the session")
def session_tag(neo4j_uri, neo4j_username, neo4j_password, session_id, name):
    """Set a human-readable tag for a session."""
    if not neo4j_password:
        click.echo("Error: Neo4j password is required", err=True)
        sys.exit(1)

    neo4j_client = _get_neo4j_client(neo4j_uri, neo4j_username, neo4j_password)
    manager = SessionManager(neo4j_client)

    try:
        manager.tag_session(session_id, name)
        click.echo(f"Session '{session_id}' tagged as '{name}'.")
    except KeyError:
        click.echo(f"Session '{session_id}' not found.", err=True)
        sys.exit(1)
    finally:
        neo4j_client.close()


# ---------------------------------------------------------------------------
# sessions shortcut (alias for session list)
# ---------------------------------------------------------------------------

@cli.command(name="sessions")
@_common_neo4j_options
@click.option("--limit", default=20, type=int)
def sessions_shortcut(neo4j_uri, neo4j_username, neo4j_password, limit):
    """List all import sessions (shortcut for 'session list')."""
    ctx = click.get_current_context()
    ctx.invoke(session_list, neo4j_uri=neo4j_uri, neo4j_username=neo4j_username,
               neo4j_password=neo4j_password, limit=limit)


# ---------------------------------------------------------------------------
# prune command
# ---------------------------------------------------------------------------

@cli.command(name="prune")
@_common_neo4j_options
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt")
def prune_data(neo4j_uri, neo4j_username, neo4j_password, yes):
    """Remove orphaned nodes (no import_sessions or empty import_sessions)."""
    if not neo4j_password:
        click.echo("Error: Neo4j password is required", err=True)
        sys.exit(1)

    neo4j_client = _get_neo4j_client(neo4j_uri, neo4j_username, neo4j_password)

    # Count orphaned nodes first
    with neo4j_client.driver.session() as session:
        result = session.run("""
            MATCH (n)
            WHERE n.import_sessions IS NULL
               OR n.import_sessions = []
            RETURN labels(n) as label, count(n) as count
            ORDER BY count DESC
        """)
        orphans = {record["label"][0]: record["count"] for record in result}

    if not orphans:
        click.echo("No orphaned nodes found.")
        neo4j_client.close()
        return

    total = sum(orphans.values())
    click.echo(f"Found {total} orphaned nodes:")
    for label, count in sorted(orphans.items(), key=lambda x: -x[1]):
        click.echo(f"  {label}: {count}")

    if not yes:
        click.echo()
        click.confirm(f"Delete these {total} nodes?", abort=True)

    with neo4j_client.driver.session() as session:
        result = session.run("""
            MATCH (n)
            WHERE n.import_sessions IS NULL
               OR n.import_sessions = []
            DETACH DELETE n
            RETURN count(n) as deleted
        """)
        deleted = result.single()["deleted"]

    click.echo(f"Deleted {deleted} orphaned nodes.")
    neo4j_client.close()


# ---------------------------------------------------------------------------
# report group
# ---------------------------------------------------------------------------

@cli.group(name="report")
def report_group():
    """View quality reports for import sessions."""
    pass


@report_group.command(name="show")
@_common_neo4j_options
@click.argument("session_id")
def report_show(neo4j_uri, neo4j_username, neo4j_password, session_id):
    """Show quality report for a session."""
    if not neo4j_password:
        click.echo("Error: Neo4j password is required", err=True)
        sys.exit(1)

    neo4j_client = _get_neo4j_client(neo4j_uri, neo4j_username, neo4j_password)
    manager = SessionManager(neo4j_client)
    session = manager.get_session(session_id)

    if not session:
        click.echo(f"Session '{session_id}' not found.", err=True)
        neo4j_client.close()
        sys.exit(1)

    if not session.quality_summary:
        click.echo(f"No quality report available for session '{session_id}'.")
        click.echo("Run import with --quality-report or --clean report/auto to generate one.")
        neo4j_client.close()
        return

    click.echo("=" * 70)
    click.echo(f"Quality Report: {session_id}")
    click.echo("=" * 70)
    click.echo(f"  Query:     {session.query}")
    click.echo(f"  Errors:    {session.quality_summary.get('errors', 'N/A')}")
    click.echo(f"  Warnings:  {session.quality_summary.get('warnings', 'N/A')}")
    click.echo(f"  Infos:     {session.quality_summary.get('infos', 'N/A')}")
    click.echo("=" * 70)
    neo4j_client.close()


@report_group.command(name="list")
@_common_neo4j_options
@click.option("--limit", default=20, type=int)
def report_list(neo4j_uri, neo4j_username, neo4j_password, limit):
    """List sessions that have quality reports."""
    if not neo4j_password:
        click.echo("Error: Neo4j password is required", err=True)
        sys.exit(1)

    neo4j_client = _get_neo4j_client(neo4j_uri, neo4j_username, neo4j_password)
    manager = SessionManager(neo4j_client)
    sessions = manager.list_sessions(limit=limit)

    click.echo("-" * 80)
    click.echo(f"{'Session ID':<20} {'Errors':<8} {'Warnings':<10} {'Infos':<8}")
    click.echo("-" * 80)

    count = 0
    for s in sessions:
        if s.quality_summary:
            click.echo(
                f"{s.id:<20} "
                f"{s.quality_summary.get('errors', 0):<8} "
                f"{s.quality_summary.get('warnings', 0):<10} "
                f"{s.quality_summary.get('infos', 0):<8}"
            )
            count += 1

    if count == 0:
        click.echo("No sessions with quality reports found.")
    click.echo("-" * 80)
    neo4j_client.close()


# ---------------------------------------------------------------------------
# enrich command
# ---------------------------------------------------------------------------


@cli.command(name="enrich")
@_common_neo4j_options
@click.option("--session", "session_id", help="Session ID to enrich (omit for all works)")
@click.option("--datasource", "datasource_names", multiple=True,
              default=["openalex"], help="Data source(s) to use (can repeat, tried in order)")
@click.option("--strategy", default="fill_null", type=click.Choice(["fill_null", "overwrite"]),
              help="Merge strategy")
@click.option("--dry-run", is_flag=True, help="Show what would be enriched without writing")
@click.option("--limit", default=None, type=int, help="Max works to enrich")
def enrich_command(neo4j_uri, neo4j_username, neo4j_password,
                   session_id, datasource_names, strategy, dry_run, limit):
    """Enrich works with data from additional sources.

    Fills missing fields (abstract, etc.) by querying other data sources.
    """
    if not neo4j_password:
        click.echo("Error: Neo4j password is required", err=True)
        sys.exit(1)

    neo4j_client = _get_neo4j_client(neo4j_uri, neo4j_username, neo4j_password)

    # Resolve datasources
    from .datasource import get_datasource, list_datasources

    sources = []
    for name in datasource_names:
        try:
            ds = get_datasource(name)
            sources.append(ds)
        except KeyError:
            click.echo(f"Unknown datasource: '{name}'. Available: {list_datasources()}", err=True)
            neo4j_client.close()
            sys.exit(1)

    if not sources:
        click.echo("No datasources specified.", err=True)
        neo4j_client.close()
        sys.exit(1)

    # Find works to enrich
    with neo4j_client.driver.session() as session:
        if session_id:
            result = session.run("""
                MATCH (w:Work)
                WHERE $session_id IN w.import_sessions
                RETURN w.id as id, w.title as title, w.doi as doi
            """, session_id=session_id)
        else:
            result = session.run("""
                MATCH (w:Work)
                RETURN w.id as id, w.title as title, w.doi as doi
            """)

        works = []
        for record in result:
            works.append({
                "id": record["id"],
                "title": record["title"],
                "doi": record["doi"],
            })

    if limit:
        works = works[:limit]

    if not works:
        click.echo("No works found to enrich.")
        neo4j_client.close()
        return

    click.echo(f"Found {len(works)} works to enrich.")
    click.echo(f"Data sources: {', '.join(datasource_names)}")
    click.echo(f"Strategy: {strategy}")
    if dry_run:
        click.echo("DRY RUN: no changes will be written.")

    # Enrich each work
    enriched_count = 0
    total_changes = 0

    for i, work in enumerate(works):
        if (i + 1) % 50 == 0:
            click.echo(f"  Progress: {i + 1}/{len(works)}...")

        # Try each datasource in order until we get a record
        record = None
        used_source = None
        for ds in sources:
            if work.get("doi"):
                record = ds.fetch_by_doi(work["doi"])
                if record:
                    used_source = ds.name
                    break
            if work.get("id"):
                record = ds.fetch_by_openalex_id(work["id"])
                if record:
                    used_source = ds.name
                    break

        if record is None:
            continue

        # Merge into a dict of existing properties
        target = {"title": work.get("title")}
        changes = merge_record(target, record, strategy=strategy)

        if not changes:
            continue

        if not dry_run:
            # Write changes to Neo4j
            with neo4j_client.driver.session() as ws:
                for field, (old_val, new_val) in changes.items():
                    ws.run(
                        f"MATCH (w:Work {{id: $id}}) SET w.{field} = $val",
                        id=work["id"], val=new_val,
                    )

        enriched_count += 1
        total_changes += len(changes)

        if dry_run:
            click.echo(f"  [{used_source}] {work['id']}: {len(changes)} fields to update")

    click.echo()
    if dry_run:
        click.echo(f"Dry-run complete. Would enrich {enriched_count} works ({total_changes} field changes).")
    else:
        click.echo(f"Enriched {enriched_count} works ({total_changes} field changes).")

    neo4j_client.close()


if __name__ == "__main__":
    cli()
