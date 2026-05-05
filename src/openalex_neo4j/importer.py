"""Import orchestration for OpenAlex data into Neo4j."""

import logging
from collections import defaultdict
from datetime import datetime
from typing import Any

from .models import Work, Author, Institution, Source, Topic, Publisher, Funder
from .neo4j_client import Neo4jClient
from .openalex_client import OpenAlexClient
from .session_manager import SessionManager

logger = logging.getLogger(__name__)


class OpenAlexImporter:
    """Orchestrates import of OpenAlex data into Neo4j."""

    def __init__(
        self,
        neo4j_client: Neo4jClient,
        openalex_client: OpenAlexClient,
        session_manager: SessionManager | None = None,
    ):
        """Initialize importer.

        Args:
            neo4j_client: Neo4j client instance
            openalex_client: OpenAlex client instance
            session_manager: Optional session manager for tracking imports.
                If None, session tracking is disabled.
        """
        self.neo4j = neo4j_client
        self.openalex = openalex_client
        self.session_manager = session_manager

        # Current session ID, set when import_from_query is called
        self.current_session: str | None = None

        # Storage for collected entities (deduplicated by ID)
        self.works: dict[str, Work] = {}
        self.authors: dict[str, Author] = {}
        self.institutions: dict[str, Institution] = {}
        self.sources: dict[str, Source] = {}
        self.topics: dict[str, Topic] = {}
        self.publishers: dict[str, Publisher] = {}
        self.funders: dict[str, Funder] = {}

    def import_from_query(
        self,
        query: str,
        limit: int = 100,
        expand_depth: int = 1,
        skip_abstracts: bool = False,
        generate_embeddings: bool = False,
        tag: str | None = None,
        skip_constraints: bool = False,
    ) -> dict[str, int]:
        """Import data starting from a search query.

        Args:
            query: Search query string
            limit: Maximum number of initial works to fetch
            expand_depth: How many levels to expand relationships
                1 = Direct relationships only
                2 = Include citations of citations, etc.
            skip_abstracts: If True, don't store abstracts (faster import)
            generate_embeddings: If True, generate embeddings for semantic search
                (requires sentence-transformers)
            tag: Optional human-readable tag for the import session
            skip_constraints: If True, skip creating constraints and indexes

        Returns:
            Dictionary with counts of imported entities
        """
        logger.info(
            f"Starting import: query='{query}', limit={limit}, depth={expand_depth}, "
            f"skip_abstracts={skip_abstracts}, generate_embeddings={generate_embeddings}"
        )

        # Step 1: Search for initial works
        initial_works = self.openalex.search_works(query, limit)
        self._add_works(initial_works)

        # Step 2: Expand to related entities
        for depth in range(1, expand_depth + 1):
            logger.info(f"Expanding relationships at depth {depth}")
            self._expand_relationships()

        # Step 2.5: Create import session (if session manager available)
        if self.session_manager:
            session_obj = self.session_manager.create_session(
                query=query,
                limit=limit,
                expand_depth=expand_depth,
                tag=tag,
            )
            self.current_session = session_obj.id
            logger.info(f"Import session: {self.current_session}")

        # Step 3: Optionally skip abstracts if not needed
        if skip_abstracts:
            logger.info("Skipping abstracts as requested")
            for work in self.works.values():
                work.abstract = None

        # Step 4: Generate embeddings if requested
        if generate_embeddings:
            self._generate_embeddings()

        # Step 5: Create constraints and indexes in Neo4j
        if not skip_constraints:
            self.neo4j.create_constraints()
            self.neo4j.create_indexes(include_vector=generate_embeddings)

        # Step 6: Import nodes
        logger.info("Importing nodes to Neo4j")
        node_counts = self._import_nodes()

        # Step 7: Import relationships
        logger.info("Importing relationships to Neo4j")
        rel_counts = self._import_relationships()

        # Combine and return counts
        counts = {**node_counts, **rel_counts}

        # Step 7.5: Complete import session
        if self.session_manager and self.current_session:
            self.session_manager.complete_session(self.current_session, stats=counts)

        logger.info(f"Import complete: {counts}")
        return counts

    def _add_works(self, works: list[Work]) -> None:
        """Add works to collection (deduplicates by ID)."""
        for work in works:
            if work.id not in self.works:
                self.works[work.id] = work

    def _expand_relationships(self) -> None:
        """Fetch and add all related entities for collected works."""
        # Collect all IDs we need to fetch
        author_ids = set()
        institution_ids = set()
        source_ids = set()
        topic_ids = set()
        funder_ids = set()
        referenced_work_ids = set()

        for work in self.works.values():
            author_ids.update(work.author_ids)
            institution_ids.update(work.institution_ids)
            if work.source_id:
                source_ids.add(work.source_id)
            topic_ids.update(work.topic_ids)
            funder_ids.update(work.funder_ids)
            referenced_work_ids.update(work.referenced_work_ids)

        # Remove IDs we already have
        author_ids -= self.authors.keys()
        institution_ids -= self.institutions.keys()
        source_ids -= self.sources.keys()
        topic_ids -= self.topics.keys()
        funder_ids -= self.funders.keys()
        referenced_work_ids -= self.works.keys()

        # Fetch authors
        if author_ids:
            authors = self.openalex.fetch_authors_by_ids(list(author_ids))
            for author in authors:
                self.authors[author.id] = author

        # Fetch institutions
        if institution_ids:
            institutions = self.openalex.fetch_institutions_by_ids(list(institution_ids))
            for inst in institutions:
                self.institutions[inst.id] = inst

        # Fetch sources
        if source_ids:
            sources = self.openalex.fetch_sources_by_ids(list(source_ids))
            for source in sources:
                self.sources[source.id] = source

                # Track publisher IDs from sources
                if source.publisher_id and source.publisher_id not in self.publishers:
                    self.publishers[source.publisher_id] = None  # Placeholder

        # Fetch topics
        if topic_ids:
            topics = self.openalex.fetch_topics_by_ids(list(topic_ids))
            for topic in topics:
                self.topics[topic.id] = topic

        # Fetch funders
        if funder_ids:
            funders = self.openalex.fetch_funders_by_ids(list(funder_ids))
            for funder in funders:
                self.funders[funder.id] = funder

        # Fetch referenced works (citations)
        if referenced_work_ids:
            works = self.openalex.fetch_works_by_ids(list(referenced_work_ids))
            self._add_works(works)

        # Fetch publishers (for sources)
        publisher_ids = [pid for pid, pub in self.publishers.items() if pub is None]
        if publisher_ids:
            publishers = self.openalex.fetch_publishers_by_ids(publisher_ids)
            for pub in publishers:
                self.publishers[pub.id] = pub

    def _enrich_nodes_with_session(
        self, nodes: list[dict[str, Any]], timestamp: str | None = None,
    ) -> None:
        """Add current_session and current_timestamp to node dicts for batch_create_nodes.

        The Cypher session-tracking query references item.current_session and
        item.current_timestamp. These are not added by to_node_dict() so we
        inject them here.
        """
        if not self.current_session:
            return
        ts = timestamp or datetime.now().isoformat()
        for node in nodes:
            node["current_session"] = self.current_session
            node["current_timestamp"] = ts

    def _import_nodes(self) -> dict[str, int]:
        """Import all collected nodes to Neo4j.

        Returns:
            Dictionary with node counts
        """
        counts = {}
        ts = datetime.now().isoformat()

        # Works (with dynamic type labels)
        if self.works:
            work_nodes = [
                w.to_node_dict(current_session=self.current_session)
                for w in self.works.values()
            ]
            self._enrich_nodes_with_session(work_nodes, ts)
            counts["works"] = self.neo4j.batch_create_nodes(
                "Work", work_nodes, dynamic_label=True,
                current_session=self.current_session,
            )

        # Authors
        if self.authors:
            author_nodes = [
                a.to_node_dict(current_session=self.current_session)
                for a in self.authors.values()
            ]
            self._enrich_nodes_with_session(author_nodes, ts)
            counts["authors"] = self.neo4j.batch_create_nodes(
                "Author", author_nodes,
                current_session=self.current_session,
            )

        # Institutions
        if self.institutions:
            inst_nodes = [
                i.to_node_dict(current_session=self.current_session)
                for i in self.institutions.values()
            ]
            self._enrich_nodes_with_session(inst_nodes, ts)
            counts["institutions"] = self.neo4j.batch_create_nodes(
                "Institution", inst_nodes,
                current_session=self.current_session,
            )

        # Sources
        if self.sources:
            source_nodes = [
                s.to_node_dict(current_session=self.current_session)
                for s in self.sources.values()
            ]
            self._enrich_nodes_with_session(source_nodes, ts)
            counts["sources"] = self.neo4j.batch_create_nodes(
                "Source", source_nodes,
                current_session=self.current_session,
            )

        # Topics
        if self.topics:
            topic_nodes = [
                t.to_node_dict(current_session=self.current_session)
                for t in self.topics.values()
            ]
            self._enrich_nodes_with_session(topic_nodes, ts)
            counts["topics"] = self.neo4j.batch_create_nodes(
                "Topic", topic_nodes,
                current_session=self.current_session,
            )

        # Publishers
        if self.publishers:
            pub_nodes = [
                p.to_node_dict(current_session=self.current_session)
                for p in self.publishers.values()
                if p is not None  # Filter out placeholders
            ]
            self._enrich_nodes_with_session(pub_nodes, ts)
            if pub_nodes:
                counts["publishers"] = self.neo4j.batch_create_nodes(
                    "Publisher", pub_nodes,
                    current_session=self.current_session,
                )

        # Funders
        if self.funders:
            funder_nodes = [
                f.to_node_dict(current_session=self.current_session)
                for f in self.funders.values()
            ]
            self._enrich_nodes_with_session(funder_nodes, ts)
            counts["funders"] = self.neo4j.batch_create_nodes(
                "Funder", funder_nodes,
                current_session=self.current_session,
            )

        return counts

    def _import_relationships(self) -> dict[str, int]:
        """Import all relationships to Neo4j.

        Returns:
            Dictionary with relationship counts
        """
        counts = {}

        # AUTHORED relationships (Author -> Work)
        authored_rels = []
        for work in self.works.values():
            for author_id in work.author_ids:
                if author_id in self.authors:
                    authored_rels.append({
                        "source_id": author_id,
                        "target_id": work.id,
                    })

        if authored_rels:
            counts["authored"] = self.neo4j.batch_create_relationships(
                "AUTHORED", "Author", "Work", authored_rels
            )

        # AFFILIATED_WITH relationships (Author -> Institution)
        # Note: We get these from works' authorship data
        affiliated_rels = []
        for work in self.works.values():
            for author_id in work.author_ids:
                for inst_id in work.institution_ids:
                    if author_id in self.authors and inst_id in self.institutions:
                        affiliated_rels.append({
                            "source_id": author_id,
                            "target_id": inst_id,
                        })

        if affiliated_rels:
            # Deduplicate affiliations
            unique_rels = {
                (rel["source_id"], rel["target_id"]): rel
                for rel in affiliated_rels
            }
            counts["affiliated_with"] = self.neo4j.batch_create_relationships(
                "AFFILIATED_WITH", "Author", "Institution", list(unique_rels.values())
            )

        # PUBLISHED_IN relationships (Work -> Source)
        published_rels = []
        for work in self.works.values():
            if work.source_id and work.source_id in self.sources:
                published_rels.append({
                    "source_id": work.id,
                    "target_id": work.source_id,
                })

        if published_rels:
            counts["published_in"] = self.neo4j.batch_create_relationships(
                "PUBLISHED_IN", "Work", "Source", published_rels
            )

        # CITES relationships (Work -> Work)
        cites_rels = []
        for work in self.works.values():
            for ref_id in work.referenced_work_ids:
                if ref_id in self.works:
                    cites_rels.append({
                        "source_id": work.id,
                        "target_id": ref_id,
                    })

        if cites_rels:
            counts["cites"] = self.neo4j.batch_create_relationships(
                "CITES", "Work", "Work", cites_rels
            )

        # HAS_TOPIC relationships (Work -> Topic)
        topic_rels = []
        for work in self.works.values():
            for topic_id in work.topic_ids:
                if topic_id in self.topics:
                    topic_rels.append({
                        "source_id": work.id,
                        "target_id": topic_id,
                    })

        if topic_rels:
            counts["has_topic"] = self.neo4j.batch_create_relationships(
                "HAS_TOPIC", "Work", "Topic", topic_rels
            )

        # FUNDED_BY relationships (Work -> Funder)
        funded_rels = []
        for work in self.works.values():
            for funder_id in work.funder_ids:
                if funder_id in self.funders:
                    funded_rels.append({
                        "source_id": work.id,
                        "target_id": funder_id,
                    })

        if funded_rels:
            counts["funded_by"] = self.neo4j.batch_create_relationships(
                "FUNDED_BY", "Work", "Funder", funded_rels
            )

        # PUBLISHED_BY relationships (Source -> Publisher)
        publisher_rels = []
        for source in self.sources.values():
            if source.publisher_id and source.publisher_id in self.publishers:
                publisher_rels.append({
                    "source_id": source.id,
                    "target_id": source.publisher_id,
                })

        if publisher_rels:
            counts["published_by"] = self.neo4j.batch_create_relationships(
                "PUBLISHED_BY", "Source", "Publisher", publisher_rels
            )

        return counts

    def _generate_embeddings(self) -> None:
        """Generate embeddings for all works with titles/abstracts."""
        try:
            from .embeddings import generate_work_embedding
        except ImportError:
            logger.error(
                "sentence-transformers not installed. "
                "Install with: uv sync --extra embeddings"
            )
            return

        logger.info(f"Generating embeddings for {len(self.works)} works")

        embedded_count = 0
        for work_id, work in self.works.items():
            if work.title:
                embedding = generate_work_embedding(work.title, work.abstract)
                if embedding:
                    work.embedding = embedding
                    embedded_count += 1

                    if embedded_count % 100 == 0:
                        logger.info(f"Generated {embedded_count} embeddings...")

        logger.info(f"Generated embeddings for {embedded_count} works")
