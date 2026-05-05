"""Neo4j client for database operations."""

import logging
from typing import Any

from neo4j import GraphDatabase, Driver, Session

logger = logging.getLogger(__name__)


def to_camel_case_label(text: str | None) -> str | None:
    """Convert hyphenated text to CamelCase for Neo4j labels.

    Args:
        text: Hyphenated string like "journal-article"

    Returns:
        CamelCase string like "JournalArticle", or None if input is None

    Examples:
        >>> to_camel_case_label("journal-article")
        "JournalArticle"
        >>> to_camel_case_label("book-chapter")
        "BookChapter"
    """
    if not text:
        return None

    # Split on hyphens and capitalize each part
    parts = text.split('-')
    return ''.join(part.capitalize() for part in parts)


class Neo4jClient:
    """Client for Neo4j database operations."""

    # Entity types that need constraints
    ENTITY_TYPES = [
        "Work",
        "Author",
        "Institution",
        "Source",
        "Topic",
        "Publisher",
        "Funder",
    ]

    def __init__(self, uri: str, username: str, password: str):
        """Initialize Neo4j client.

        Args:
            uri: Neo4j connection URI (e.g., bolt://localhost:7687)
            username: Neo4j username
            password: Neo4j password
        """
        self.uri = uri
        self.username = username
        self.password = password
        self._driver: Driver | None = None

    def __enter__(self) -> "Neo4jClient":
        """Context manager entry."""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit."""
        self.close()

    def connect(self) -> None:
        """Establish connection to Neo4j."""
        logger.info(f"Connecting to Neo4j at {self.uri}")
        self._driver = GraphDatabase.driver(
            self.uri,
            auth=(self.username, self.password)
        )
        # Verify connectivity
        self._driver.verify_connectivity()
        logger.info("Successfully connected to Neo4j")

    def close(self) -> None:
        """Close connection to Neo4j."""
        if self._driver:
            self._driver.close()
            logger.info("Closed Neo4j connection")

    @property
    def driver(self) -> Driver:
        """Get the Neo4j driver.

        Returns:
            Neo4j driver instance

        Raises:
            RuntimeError: If not connected
        """
        if not self._driver:
            raise RuntimeError("Not connected to Neo4j. Call connect() first.")
        return self._driver

    def create_constraints(self) -> None:
        """Create uniqueness constraints for all entity types."""
        logger.info("Creating constraints for entity types")

        with self.driver.session() as session:
            for entity_type in self.ENTITY_TYPES:
                constraint_name = f"{entity_type.lower()}_id_unique"
                query = f"""
                CREATE CONSTRAINT {constraint_name} IF NOT EXISTS
                FOR (n:{entity_type})
                REQUIRE n.id IS UNIQUE
                """
                try:
                    session.run(query)
                    logger.debug(f"Created constraint for {entity_type}")
                except Exception as e:
                    logger.warning(f"Failed to create constraint for {entity_type}: {e}")

        logger.info("Finished creating constraints")

    def create_indexes(self, include_vector: bool = False) -> None:
        """Create indexes for common search fields.

        Creates both text indexes (for full-text search) and regular indexes
        (for exact matches) on frequently queried fields. Optionally creates
        vector index for semantic search.

        Args:
            include_vector: If True, create vector index for embeddings
        """
        logger.info("Creating indexes for search fields")

        with self.driver.session() as session:
            # Fulltext index for Work (title + abstract) - uses Lucene syntax
            try:
                query = """
                CREATE FULLTEXT INDEX work_fulltext IF NOT EXISTS
                FOR (n:Work)
                ON EACH [n.title, n.abstract]
                """
                session.run(query)
                logger.info("Created fulltext index for Work (title + abstract)")
            except Exception as e:
                logger.warning(f"Failed to create fulltext index: {e}")

            # Text indexes for simple string matching
            text_indexes = [
                ("work_title_text", "Work", "title"),
                ("author_name_text", "Author", "display_name"),
                ("institution_name_text", "Institution", "display_name"),
                ("source_name_text", "Source", "display_name"),
                ("topic_name_text", "Topic", "display_name"),
            ]

            for index_name, label, property_name in text_indexes:
                query = f"""
                CREATE TEXT INDEX {index_name} IF NOT EXISTS
                FOR (n:{label})
                ON (n.{property_name})
                """
                try:
                    session.run(query)
                    logger.debug(f"Created text index: {index_name}")
                except Exception as e:
                    logger.warning(f"Failed to create text index {index_name}: {e}")

            # Regular indexes for exact match and range queries
            regular_indexes = [
                ("work_doi", "Work", "doi"),
                ("work_year", "Work", "publication_year"),
                ("work_type", "Work", "type"),
                ("work_oa", "Work", "is_oa"),
                ("author_orcid", "Author", "orcid"),
                ("institution_ror", "Institution", "ror"),
                ("institution_country", "Institution", "country_code"),
                ("source_issn_l", "Source", "issn_l"),
            ]

            for index_name, label, property_name in regular_indexes:
                query = f"""
                CREATE INDEX {index_name} IF NOT EXISTS
                FOR (n:{label})
                ON (n.{property_name})
                """
                try:
                    session.run(query)
                    logger.debug(f"Created index: {index_name}")
                except Exception as e:
                    logger.warning(f"Failed to create index {index_name}: {e}")

            # Vector index for semantic search (if requested)
            if include_vector:
                try:
                    # Check Neo4j version supports vector indexes (5.11+)
                    query = """
                    CREATE VECTOR INDEX work_embedding_vector IF NOT EXISTS
                    FOR (n:Work)
                    ON (n.embedding)
                    OPTIONS {indexConfig: {
                        `vector.dimensions`: 384,
                        `vector.similarity_function`: 'cosine'
                    }}
                    """
                    session.run(query)
                    logger.info("Created vector index for semantic search")
                except Exception as e:
                    logger.warning(f"Failed to create vector index (requires Neo4j 5.11+): {e}")

        logger.info("Finished creating indexes")

    def batch_create_nodes(
        self,
        label: str,
        nodes: list[dict[str, Any]],
        batch_size: int = 500,
        dynamic_label: bool = False,
        current_session: str | None = None,
    ) -> int:
        """Create nodes in batches using UNWIND and MERGE.

        Args:
            label: Node label (e.g., "Work", "Author")
            nodes: List of node properties dictionaries
            batch_size: Number of nodes per batch
            dynamic_label: If True, use item._label field from node dict as additional
                dynamic label using Neo4j's dynamic label syntax: SET n:$(item._label)
            current_session: If provided, enables session tracking.
                Each node dict should include:
                  - current_session: session ID
                  - current_timestamp: ISO timestamp string
                  - import_sessions: list[str]
                  - first_imported_at: str or None
                  - last_imported_at: str or None

        Returns:
            Total number of nodes created/updated
        """
        if not nodes:
            return 0

        logger.info(f"Creating {len(nodes)} {label} nodes in batches of {batch_size}")
        total_created = 0

        if current_session and label != "ImportSession":
            # With session tracking: use ON CREATE / ON MATCH to merge import_sessions
            if dynamic_label:
                query = f"""
                UNWIND $batch AS item
                MERGE (n:{label} {{id: item.id}})
                ON CREATE SET
                  n += item {{.*, _label: null, current_session: null, current_timestamp: null,
                              import_sessions: null, first_imported_at: null, last_imported_at: null}},
                  n.import_sessions = [item.current_session],
                  n.first_imported_at = item.current_timestamp,
                  n.last_imported_at = item.current_timestamp
                ON MATCH SET
                  n += item {{.*, _label: null, current_session: null, current_timestamp: null,
                              import_sessions: null, first_imported_at: null, last_imported_at: null}},
                  n.import_sessions =
                    CASE WHEN item.current_session IN coalesce(n.import_sessions, [])
                    THEN n.import_sessions
                    ELSE coalesce(n.import_sessions, []) + item.current_session
                    END,
                  n.last_imported_at = item.current_timestamp
                SET n:$(item._label)
                RETURN count(n) as count
                """
            else:
                query = f"""
                UNWIND $batch AS item
                MERGE (n:{label} {{id: item.id}})
                ON CREATE SET
                  n += item {{.*, current_session: null, current_timestamp: null,
                              import_sessions: null, first_imported_at: null, last_imported_at: null}},
                  n.import_sessions = [item.current_session],
                  n.first_imported_at = item.current_timestamp,
                  n.last_imported_at = item.current_timestamp
                ON MATCH SET
                  n += item {{.*, current_session: null, current_timestamp: null,
                              import_sessions: null, first_imported_at: null, last_imported_at: null}},
                  n.import_sessions =
                    CASE WHEN item.current_session IN coalesce(n.import_sessions, [])
                    THEN n.import_sessions
                    ELSE coalesce(n.import_sessions, []) + item.current_session
                    END,
                  n.last_imported_at = item.current_timestamp
                RETURN count(n) as count
                """
        else:
            # Original behavior (no session tracking)
            if dynamic_label:
                query = f"""
                UNWIND $batch AS item
                MERGE (n:{label} {{id: item.id}})
                SET n += item {{.*, _label: null}},
                    n:$(item._label)
                RETURN count(n) as count
                """
            else:
                query = f"""
                UNWIND $batch AS item
                MERGE (n:{label} {{id: item.id}})
                SET n += item
                RETURN count(n) as count
                """

        with self.driver.session() as session:
            for i in range(0, len(nodes), batch_size):
                batch = nodes[i:i + batch_size]
                try:
                    result = session.run(query, batch=batch)
                    count = result.single()["count"]
                    total_created += count
                    logger.debug(f"Batch {i // batch_size + 1}: Created/updated {count} {label} nodes")
                except Exception as e:
                    logger.error(f"Failed to create batch of {label} nodes: {e}")

        logger.info(f"Finished creating {total_created} {label} nodes")
        return total_created

    def batch_create_relationships(
        self,
        rel_type: str,
        source_label: str,
        target_label: str,
        relationships: list[dict[str, Any]],
        batch_size: int = 500
    ) -> int:
        """Create relationships in batches using UNWIND and MERGE.

        Args:
            rel_type: Relationship type (e.g., "AUTHORED", "CITES")
            source_label: Source node label
            target_label: Target node label
            relationships: List of dicts with 'source_id' and 'target_id'
            batch_size: Number of relationships per batch

        Returns:
            Total number of relationships created
        """
        if not relationships:
            return 0

        logger.info(
            f"Creating {len(relationships)} {rel_type} relationships "
            f"in batches of {batch_size}"
        )
        total_created = 0

        query = f"""
        UNWIND $batch AS rel
        MATCH (a:{source_label} {{id: rel.source_id}})
        MATCH (b:{target_label} {{id: rel.target_id}})
        MERGE (a)-[r:{rel_type}]->(b)
        RETURN count(r) as count
        """

        with self.driver.session() as session:
            for i in range(0, len(relationships), batch_size):
                batch = relationships[i:i + batch_size]
                try:
                    result = session.run(query, batch=batch)
                    count = result.single()["count"]
                    total_created += count
                    logger.debug(
                        f"Batch {i // batch_size + 1}: "
                        f"Created {count} {rel_type} relationships"
                    )
                except Exception as e:
                    logger.error(f"Failed to create batch of {rel_type} relationships: {e}")

        logger.info(f"Finished creating {total_created} {rel_type} relationships")
        return total_created

    def get_node_count(self, label: str) -> int:
        """Get count of nodes with given label.

        Args:
            label: Node label

        Returns:
            Number of nodes
        """
        query = f"MATCH (n:{label}) RETURN count(n) as count"
        with self.driver.session() as session:
            result = session.run(query)
            return result.single()["count"]

    def get_relationship_count(self, rel_type: str) -> int:
        """Get count of relationships of given type.

        Args:
            rel_type: Relationship type

        Returns:
            Number of relationships
        """
        query = f"MATCH ()-[r:{rel_type}]->() RETURN count(r) as count"
        with self.driver.session() as session:
            result = session.run(query)
            return result.single()["count"]

    def clear_database(self) -> None:
        """Clear all nodes and relationships from the database.

        WARNING: This will delete ALL data in the database!
        """
        logger.warning("Clearing entire database")
        with self.driver.session() as session:
            session.run("MATCH (n) DETACH DELETE n")
        logger.info("Database cleared")

    def get_node_by_id(self, label: str, node_id: str) -> dict[str, Any] | None:
        """Get a node by its ID.

        Args:
            label: Node label
            node_id: Node ID

        Returns:
            Node properties as dict, or None if not found
        """
        query = f"MATCH (n:{label} {{id: $id}}) RETURN n"
        with self.driver.session() as session:
            result = session.run(query, id=node_id)
            record = result.single()
            if record:
                return dict(record["n"])
            return None

    def get_relationships(
        self,
        rel_type: str,
        source_label: str | None = None,
        target_label: str | None = None,
        limit: int = 100
    ) -> list[dict[str, Any]]:
        """Get relationships of given type.

        Args:
            rel_type: Relationship type
            source_label: Optional source node label filter
            target_label: Optional target node label filter
            limit: Maximum number of relationships to return

        Returns:
            List of relationship dicts with source_id and target_id
        """
        source_pattern = f":{source_label}" if source_label else ""
        target_pattern = f":{target_label}" if target_label else ""
        query = f"""
        MATCH (a{source_pattern})-[r:{rel_type}]->(b{target_pattern})
        RETURN a.id as source_id, b.id as target_id
        LIMIT $limit
        """
        with self.driver.session() as session:
            result = session.run(query, limit=limit)
            return [
                {"source_id": record["source_id"], "target_id": record["target_id"]}
                for record in result
            ]
