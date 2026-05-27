"""Import orchestration for OpenAlex data into Neo4j."""

import json
import logging
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
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

        # DataSerializer for local JSONL cache (used in cache-backed flow)
        self.serializer: "DataSerializer | None" = None  # noqa: F821

        # Storage for collected entities (deduplicated by ID)
        # NOTE: kept for backward compatibility; new cache flow bypasses these
        self.works: dict[str, Work] = {}
        self.authors: dict[str, Author] = {}
        self.institutions: dict[str, Institution] = {}
        self.sources: dict[str, Source] = {}
        self.topics: dict[str, Topic] = {}
        self.publishers: dict[str, Publisher] = {}
        self.funders: dict[str, Funder] = {}
        self.node_tags: list[str] = []

    def import_from_query(
        self,
        query: str,
        limit: int | None = None,
        expand_depth: int = 1,
        skip_abstracts: bool = False,
        generate_embeddings: bool = False,
        tag: str | None = None,
        skip_constraints: bool = False,
        from_year: int | None = None,
        to_year: int | None = None,
        work_types: list[str] | tuple[str, ...] | None = None,
        cache_dir: str | Path | None = None,
        keep_cache: bool = False,
        fetch_only: bool = False,
        node_tags: list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, int]:
        """Import data starting from a search query, using local JSONL cache.

        Args:
            query: Search query string
            limit: Maximum number of initial works to fetch, or ``None`` for all.
            expand_depth: How many levels to expand relationships
                1 = Direct relationships only
                2 = Include citations of citations, etc.
            skip_abstracts: If True, don't store abstracts (faster import)
            generate_embeddings: If True, generate embeddings for semantic search
                (requires sentence-transformers)
            tag: Optional human-readable tag for the import session
            skip_constraints: If True, skip creating constraints and indexes
            from_year: Optional start year for filtering works
            to_year: Optional end year for filtering works
            work_types: Optional OpenAlex work types used to filter initial
                works, e.g. ``["article", "review"]``.
            cache_dir: Local cache directory (default: ~/.openalex-neo4j/cache/)
            keep_cache: If True, keep cache after import (for debugging/resume)
            fetch_only: If True, skip Neo4j import phase entirely (cache-only).
                Implies keep_cache=True.
            node_tags: Optional custom tags stored on every imported node as
                the ``import_tags`` property.

        Returns:
            Dictionary with counts of imported entities
        """
        from .serializer import DataSerializer

        limit_str = "all" if limit is None else str(limit)
        logger.info(
            f"Starting import: query='{query}', limit={limit_str}, depth={expand_depth}, "
            f"skip_abstracts={skip_abstracts}, generate_embeddings={generate_embeddings}"
            f"{f', from_year={from_year}' if from_year else ''}"
            f"{f', to_year={to_year}' if to_year else ''}"
            f"{f', work_types={list(work_types)}' if work_types else ''}"
        )
        self.node_tags = self._normalize_tags(node_tags)
        normalized_work_types = self._normalize_work_types(work_types)

        # ─── Initialize session and cache ───
        if self.session_manager:
            session_obj = self.session_manager.create_session(
                query=query,
                limit=limit,
                expand_depth=expand_depth,
                tag=tag,
            )
            self.current_session = session_obj.id
        else:
            self.current_session = f"S{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        cache_root = Path(cache_dir) if cache_dir else Path.home() / ".openalex-neo4j" / "cache"
        self.serializer = DataSerializer(cache_root, self.current_session)
        logger.info(f"Cache directory: {self.serializer.data_dir}")

        # ─── Fetch phase: API → JSONL ───
        initial_works = self.openalex.search_works(
            query, limit, from_year=from_year, to_year=to_year,
            work_types=normalized_work_types,
        )
        self._save_works_batch(initial_works)

        for depth in range(1, expand_depth + 1):
            logger.info(f"Expanding relationships at depth {depth}")
            self._expand_and_save_relationships()

        # ─── Post-fetch processing ───
        if skip_abstracts:
            logger.info("Skipping abstracts as requested")
            all_works = self.serializer.read("Work")
            for w in all_works:
                w["abstract"] = None
            work_path = self.serializer.data_dir / "work.jsonl"
            with open(work_path, "w", encoding="utf-8") as f:
                for w in all_works:
                    f.write(json.dumps(w, ensure_ascii=False) + "\n")

        if generate_embeddings:
            try:
                from .embeddings import generate_work_embedding
            except ImportError:
                logger.error(
                    "sentence-transformers not installed. "
                    "Install with: uv sync --extra embeddings"
                )
            else:
                logger.info("Generating embeddings for cached works")
                all_works = self.serializer.read("Work")
                embedded_count = 0
                for w in all_works:
                    if w.get("title"):
                        embedding = generate_work_embedding(w["title"], w.get("abstract"))
                        if embedding:
                            w["embedding"] = embedding
                            embedded_count += 1
                if embedded_count > 0:
                    work_path = self.serializer.data_dir / "work.jsonl"
                    with open(work_path, "w", encoding="utf-8") as f:
                        for w in all_works:
                            f.write(json.dumps(w, ensure_ascii=False) + "\n")
                logger.info(f"Generated embeddings for {embedded_count} works")

        # ─── Write manifest ───
        entity_counts = {}
        for label in DataSerializer.LABELS:
            entity_counts[label] = self.serializer.count(label)
        self.serializer.write_manifest({
            "session_id": self.current_session,
            "query": query,
            "source": "openalex-api",
            "created_at": datetime.now().isoformat(),
            "parameters": {
                "limit": limit,
                "expand_depth": expand_depth,
                "skip_abstracts": skip_abstracts,
                "generate_embeddings": generate_embeddings,
                "from_year": from_year,
                "to_year": to_year,
                "work_types": normalized_work_types,
                "node_tags": self.node_tags,
            },
            "entity_counts": entity_counts,
        })

        # ─── Import phase: JSONL → Neo4j ───
        if fetch_only:
            logger.info(f"Fetch-only mode: cache saved at {self.serializer.data_dir}")
            return {
                "session_id": self.current_session,
                "cache_dir": str(self.serializer.data_dir),
                "fetch_only": True,
            }

        if not skip_constraints:
            self.neo4j.create_constraints()
            self.neo4j.create_indexes(include_vector=generate_embeddings)

        logger.info("Importing nodes to Neo4j (streaming mode)")
        node_counts, entity_ids = self._import_nodes_streaming()

        logger.info("Importing relationships to Neo4j")
        rel_counts = self._import_relationships_streaming(entity_ids)

        counts = {**node_counts, **rel_counts}

        # ─── Complete session ───
        if self.session_manager and self.current_session:
            self.session_manager.complete_session(self.current_session, stats=counts)

        # ─── Cleanup ───
        if not keep_cache:
            self.serializer.cleanup()

        logger.info(f"Import complete: {counts}")
        return counts

    def _add_works(self, works: list[Work]) -> None:
        """Add works to collection (deduplicates by ID)."""
        for work in works:
            if work.id not in self.works:
                self.works[work.id] = work

    # Fields used for relationship expansion — saved in cache but
    # stripped before Neo4j node creation so they don't become properties.
    _REL_FIELDS = {
        "author_ids", "institution_ids", "source_id", "topic_ids",
        "funder_ids", "referenced_work_ids", "publisher_id",
    }

    @staticmethod
    def _normalize_tags(node_tags: list[str] | tuple[str, ...] | None) -> list[str]:
        """Normalize custom node tags, preserving order while deduplicating."""
        if not node_tags:
            return []

        normalized: list[str] = []
        for raw_tag in node_tags:
            if raw_tag is None:
                continue
            tag = raw_tag.strip()
            if tag and tag not in normalized:
                normalized.append(tag)
        return normalized

    @staticmethod
    def _normalize_work_types(work_types: list[str] | tuple[str, ...] | None) -> list[str]:
        """Normalize OpenAlex work type filters, preserving order."""
        if not work_types:
            return []

        normalized: list[str] = []
        for raw_type in work_types:
            if raw_type is None:
                continue
            work_type = raw_type.strip().lower()
            if work_type and work_type not in normalized:
                normalized.append(work_type)
        return normalized

    def _merge_import_tags(self, existing_tags: Any) -> list[str]:
        """Merge active node tags with any existing cached node tags."""
        merged: list[str] = []

        if isinstance(existing_tags, list):
            for tag in existing_tags:
                if isinstance(tag, str):
                    clean = tag.strip()
                    if clean and clean not in merged:
                        merged.append(clean)
        elif isinstance(existing_tags, str):
            clean = existing_tags.strip()
            if clean:
                merged.append(clean)

        for tag in self.node_tags:
            if tag not in merged:
                merged.append(tag)

        return merged

    def _attach_import_metadata(
        self,
        node: dict[str, Any],
        timestamp: str | None = None,
    ) -> None:
        """Attach session metadata and custom node tags to a node dict."""
        if self.current_session:
            node["current_session"] = self.current_session
            node["current_timestamp"] = timestamp or datetime.now().isoformat()

        merged_tags = self._merge_import_tags(node.get("import_tags"))
        if merged_tags:
            node["import_tags"] = merged_tags

    def _save_works_batch(self, works: list[Work]) -> None:
        """Serialize a batch of Work objects to the local JSONL cache."""
        nodes = [w.to_node_dict(current_session=self.current_session) for w in works]
        ts = datetime.now().isoformat()
        for i, work in enumerate(works):
            # Inject relationship fields needed by _expand_and_save_relationships
            nodes[i].update({
                "author_ids": work.author_ids,
                "institution_ids": work.institution_ids,
                "source_id": work.source_id,
                "topic_ids": work.topic_ids,
                "funder_ids": work.funder_ids,
                "referenced_work_ids": work.referenced_work_ids,
            })
            self._attach_import_metadata(nodes[i], ts)
        self.serializer.append_batch("Work", nodes)

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

    def _expand_and_save_relationships(self) -> None:
        """Fetch related entities for cached works and save to JSONL cache.

        Entity types are fetched in parallel via ``ThreadPoolExecutor``.
        The ``TokenBucket`` rate limiter inside ``OpenAlexClient`` prevents
        the parallel requests from exceeding the polite-pool limit.
        """
        cached_works = self.serializer.read("Work")

        # Collect all IDs we need to fetch
        author_ids = set()
        institution_ids = set()
        source_ids = set()
        topic_ids = set()
        funder_ids = set()
        referenced_work_ids = set()

        for w in cached_works:
            for aid in w.get("author_ids", []):
                author_ids.add(aid)
            for iid in w.get("institution_ids", []):
                institution_ids.add(iid)
            sid = w.get("source_id")
            if sid:
                source_ids.add(sid)
            for tid in w.get("topic_ids", []):
                topic_ids.add(tid)
            for fid in w.get("funder_ids", []):
                funder_ids.add(fid)
            for ref in w.get("referenced_work_ids", []):
                referenced_work_ids.add(ref)

        # Remove IDs we already have cached
        cached_author_ids = {a["id"] for a in self.serializer.read("Author")}
        author_ids -= cached_author_ids

        cached_inst_ids = {i["id"] for i in self.serializer.read("Institution")}
        institution_ids -= cached_inst_ids

        cached_source_ids = {s["id"] for s in self.serializer.read("Source")}
        source_ids -= cached_source_ids

        cached_topic_ids = {t["id"] for t in self.serializer.read("Topic")}
        topic_ids -= cached_topic_ids

        cached_funder_ids = {f["id"] for f in self.serializer.read("Funder")}
        funder_ids -= cached_funder_ids

        cached_work_ids = {w["id"] for w in cached_works}
        referenced_work_ids -= cached_work_ids

        ts = datetime.now().isoformat()

        def _save(nodes: list[dict], label: str) -> None:
            for node in nodes:
                self._attach_import_metadata(node, ts)
            self.serializer.append_batch(label, nodes)

        # ─── Parallel fetch ───
        tasks: list[tuple[str, Any]] = []

        if author_ids:
            def fetch_authors():
                entities = self.openalex.fetch_authors_by_ids(list(author_ids))
                _save([e.to_node_dict() for e in entities], "Author")
            tasks.append(("Author", fetch_authors))

        if institution_ids:
            def fetch_institutions():
                entities = self.openalex.fetch_institutions_by_ids(list(institution_ids))
                _save([e.to_node_dict() for e in entities], "Institution")
            tasks.append(("Institution", fetch_institutions))

        if source_ids:
            def fetch_sources_with_publishers():
                entities = self.openalex.fetch_sources_by_ids(list(source_ids))
                nodes = [e.to_node_dict() for e in entities]
                for i, e in enumerate(entities):
                    nodes[i]["publisher_id"] = e.publisher_id
                _save(nodes, "Source")

                # Fetch publishers for these sources
                cached_pub_ids = {p["id"] for p in self.serializer.read("Publisher")}
                pub_ids_to_fetch = {
                    e.publisher_id for e in entities
                    if e.publisher_id and e.publisher_id not in cached_pub_ids
                }
                if pub_ids_to_fetch:
                    pubs = self.openalex.fetch_publishers_by_ids(list(pub_ids_to_fetch))
                    _save([p.to_node_dict() for p in pubs], "Publisher")
            tasks.append(("Source", fetch_sources_with_publishers))

        if topic_ids:
            def fetch_topics():
                entities = self.openalex.fetch_topics_by_ids(list(topic_ids))
                _save([e.to_node_dict() for e in entities], "Topic")
            tasks.append(("Topic", fetch_topics))

        if funder_ids:
            def fetch_funders():
                entities = self.openalex.fetch_funders_by_ids(list(funder_ids))
                _save([e.to_node_dict() for e in entities], "Funder")
            tasks.append(("Funder", fetch_funders))

        if referenced_work_ids:
            def fetch_referenced():
                entities = self.openalex.fetch_works_by_ids(list(referenced_work_ids))
                # Use _save_works_batch for Work to include relationship fields
                self._save_works_batch(entities)
            tasks.append(("ReferencedWork", fetch_referenced))

        if not tasks:
            return

        with ThreadPoolExecutor(max_workers=5) as executor:
            future_map = {executor.submit(fn): name for name, fn in tasks}
            for future in as_completed(future_map):
                name = future_map[future]
                try:
                    future.result()
                    logger.info(f"Completed fetch: {name}")
                except Exception as e:
                    logger.error(f"Failed to fetch {name}: {e}")

    def _enrich_nodes_with_session(
        self, nodes: list[dict[str, Any]], timestamp: str | None = None,
    ) -> None:
        """Add current_session and current_timestamp to node dicts for batch_create_nodes.

        The Cypher session-tracking query references item.current_session and
        item.current_timestamp. These are not added by to_node_dict() so we
        inject them here.
        """
        ts = timestamp or datetime.now().isoformat()
        for node in nodes:
            self._attach_import_metadata(node, ts)

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

    def _import_nodes_from_dict(self, entities: dict[str, list[dict]]) -> dict[str, int]:
        """Create Neo4j nodes from dict data (read from JSONL cache).

        Args:
            entities: Dict keyed by label, each value is a list of node dicts.

        Returns:
            Dictionary with node counts.
        """
        counts = {}
        label_map = {
            "Work": ("works", True),
            "Author": ("authors", False),
            "Institution": ("institutions", False),
            "Source": ("sources", False),
            "Topic": ("topics", False),
            "Publisher": ("publishers", False),
            "Funder": ("funders", False),
        }
        ts = datetime.now().isoformat()
        for label, (count_key, dynamic) in label_map.items():
            nodes = entities.get(label, [])
            if not nodes:
                continue
            # Strip relationship-only fields so they don't end up as
            # node properties in Neo4j.
            clean_nodes = []
            for node in nodes:
                clean = {k: v for k, v in node.items()
                         if k not in self._REL_FIELDS}
                self._attach_import_metadata(clean, ts)
                clean_nodes.append(clean)
            counts[count_key] = self.neo4j.batch_create_nodes(
                label, clean_nodes, dynamic_label=dynamic,
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

    def _import_relationships_from_dict(self, entities: dict[str, list[dict]]) -> dict[str, int]:
        """Create Neo4j relationships from dict data (read from JSONL cache).

        Args:
            entities: Dict keyed by label, each value is a list of node dicts.

        Returns:
            Dictionary with relationship counts.
        """
        works = {w["id"]: w for w in entities.get("Work", [])}
        authors = {a["id"]: a for a in entities.get("Author", [])}
        sources = {s["id"]: s for s in entities.get("Source", [])}
        institutions = {i["id"]: i for i in entities.get("Institution", [])}
        topics = {t["id"]: t for t in entities.get("Topic", [])}
        funders = {f["id"]: f for f in entities.get("Funder", [])}
        publishers = {p["id"]: p for p in entities.get("Publisher", [])}

        counts = {}

        # AUTHORED relationships (Author -> Work)
        authored = []
        for w_id, w in works.items():
            for a_id in w.get("author_ids", []):
                if a_id in authors:
                    authored.append({"source_id": a_id, "target_id": w_id})
        if authored:
            counts["authored"] = self.neo4j.batch_create_relationships(
                "AUTHORED", "Author", "Work", authored,
            )

        # AFFILIATED_WITH relationships (Author -> Institution)
        affiliated = []
        for w_id, w in works.items():
            for a_id in w.get("author_ids", []):
                for i_id in w.get("institution_ids", []):
                    if a_id in authors and i_id in institutions:
                        affiliated.append({"source_id": a_id, "target_id": i_id})
        if affiliated:
            unique_affiliated = {(r["source_id"], r["target_id"]): r for r in affiliated}
            counts["affiliated_with"] = self.neo4j.batch_create_relationships(
                "AFFILIATED_WITH", "Author", "Institution", list(unique_affiliated.values()),
            )

        # PUBLISHED_IN relationships (Work -> Source)
        published_in = []
        for w_id, w in works.items():
            s_id = w.get("source_id")
            if s_id and s_id in sources:
                published_in.append({"source_id": w_id, "target_id": s_id})
        if published_in:
            counts["published_in"] = self.neo4j.batch_create_relationships(
                "PUBLISHED_IN", "Work", "Source", published_in,
            )

        # CITES relationships (Work -> Work)
        cites = []
        for w_id, w in works.items():
            for ref_id in w.get("referenced_work_ids", []):
                if ref_id in works:
                    cites.append({"source_id": w_id, "target_id": ref_id})
        if cites:
            counts["cites"] = self.neo4j.batch_create_relationships(
                "CITES", "Work", "Work", cites,
            )

        # HAS_TOPIC relationships (Work -> Topic)
        has_topic = []
        for w_id, w in works.items():
            for t_id in w.get("topic_ids", []):
                if t_id in topics:
                    has_topic.append({"source_id": w_id, "target_id": t_id})
        if has_topic:
            counts["has_topic"] = self.neo4j.batch_create_relationships(
                "HAS_TOPIC", "Work", "Topic", has_topic,
            )

        # FUNDED_BY relationships (Work -> Funder)
        funded_by = []
        for w_id, w in works.items():
            for f_id in w.get("funder_ids", []):
                if f_id in funders:
                    funded_by.append({"source_id": w_id, "target_id": f_id})
        if funded_by:
            counts["funded_by"] = self.neo4j.batch_create_relationships(
                "FUNDED_BY", "Work", "Funder", funded_by,
            )

        # PUBLISHED_BY relationships (Source -> Publisher)
        published_by = []
        for s_id, s in sources.items():
            p_id = s.get("publisher_id")
            if p_id and p_id in publishers:
                published_by.append({"source_id": s_id, "target_id": p_id})
        if published_by:
            counts["published_by"] = self.neo4j.batch_create_relationships(
                "PUBLISHED_BY", "Source", "Publisher", published_by,
            )

        return counts

    def _import_nodes_streaming(
        self,
    ) -> tuple[dict[str, int], dict[str, set[str]]]:
        """Create Neo4j nodes by reading one entity type at a time from JSONL.

        Unlike ``_import_nodes_from_dict`` (which requires all entity data in
        memory at once), this method reads, writes, and releases each type
        sequentially, reducing peak memory from ~588 MB to ~250 MB for a
        100 K-work import.

        Returns:
            A tuple of (node_counts, entity_ids) where *entity_ids* maps each
            label to the set of entity IDs that were created.  The ID sets are
            used by ``_import_relationships_streaming`` to avoid re-reading
            every entity type from disk.
        """
        counts: dict[str, int] = {}
        entity_ids: dict[str, set[str]] = {}

        label_map = [
            ("Work", "works", True),
            ("Author", "authors", False),
            ("Institution", "institutions", False),
            ("Source", "sources", False),
            ("Topic", "topics", False),
            ("Publisher", "publishers", False),
            ("Funder", "funders", False),
        ]

        ts = datetime.now().isoformat()

        for label, count_key, dynamic in label_map:
            nodes = self.serializer.read(label)
            if not nodes:
                entity_ids[label] = set()
                continue

            # Strip relationship-only fields and inject session fields
            clean_nodes: list[dict[str, Any]] = []
            ids_for_relations: set[str] = set()
            for node in nodes:
                ids_for_relations.add(node["id"])
                clean = {k: v for k, v in node.items()
                         if k not in self._REL_FIELDS}
                self._attach_import_metadata(clean, ts)
                clean_nodes.append(clean)

            # Write to Neo4j
            counts[count_key] = self.neo4j.batch_create_nodes(
                label, clean_nodes, dynamic_label=dynamic,
                current_session=self.current_session,
            )
            # nodes / clean_nodes released for GC here
            entity_ids[label] = ids_for_relations

        return counts, entity_ids

    def _import_relationships_streaming(
        self,
        entity_ids: dict[str, set[str]],
    ) -> dict[str, int]:
        """Create Neo4j relationships using streamed node ID sets.

        Reads only the Work dicts from JSONL (the only entity type that
        carries relationship fields).  Membership checks for other entity
        types use the lightweight ID sets collected during the node-creation
        phase.

        Args:
            entity_ids: Mapping of label -> set of entity IDs created during
                the node-creation phase.

        Returns:
            Dictionary with relationship counts.
        """
        works_list = self.serializer.read("Work")
        works = {w["id"]: w for w in works_list}

        authors = entity_ids.get("Author", set())
        institutions = entity_ids.get("Institution", set())
        sources = entity_ids.get("Source", set())
        topics = entity_ids.get("Topic", set())
        funders = entity_ids.get("Funder", set())
        publishers = entity_ids.get("Publisher", set())

        counts: dict[str, int] = {}

        # AUTHORED relationships (Author -> Work)
        authored = []
        for w_id, w in works.items():
            for a_id in w.get("author_ids", []):
                if a_id in authors:
                    authored.append({"source_id": a_id, "target_id": w_id})
        if authored:
            counts["authored"] = self.neo4j.batch_create_relationships(
                "AUTHORED", "Author", "Work", authored,
            )

        # AFFILIATED_WITH relationships (Author -> Institution)
        affiliated = []
        for w_id, w in works.items():
            for a_id in w.get("author_ids", []):
                for i_id in w.get("institution_ids", []):
                    if a_id in authors and i_id in institutions:
                        affiliated.append({"source_id": a_id, "target_id": i_id})
        if affiliated:
            unique_affiliated = {(r["source_id"], r["target_id"]): r for r in affiliated}
            counts["affiliated_with"] = self.neo4j.batch_create_relationships(
                "AFFILIATED_WITH", "Author", "Institution", list(unique_affiliated.values()),
            )

        # PUBLISHED_IN relationships (Work -> Source)
        published_in = []
        for w_id, w in works.items():
            s_id = w.get("source_id")
            if s_id and s_id in sources:
                published_in.append({"source_id": w_id, "target_id": s_id})
        if published_in:
            counts["published_in"] = self.neo4j.batch_create_relationships(
                "PUBLISHED_IN", "Work", "Source", published_in,
            )

        # CITES relationships (Work -> Work)
        cites = []
        for w_id, w in works.items():
            for ref_id in w.get("referenced_work_ids", []):
                if ref_id in works:
                    cites.append({"source_id": w_id, "target_id": ref_id})
        if cites:
            counts["cites"] = self.neo4j.batch_create_relationships(
                "CITES", "Work", "Work", cites,
            )

        # HAS_TOPIC relationships (Work -> Topic)
        has_topic = []
        for w_id, w in works.items():
            for t_id in w.get("topic_ids", []):
                if t_id in topics:
                    has_topic.append({"source_id": w_id, "target_id": t_id})
        if has_topic:
            counts["has_topic"] = self.neo4j.batch_create_relationships(
                "HAS_TOPIC", "Work", "Topic", has_topic,
            )

        # FUNDED_BY relationships (Work -> Funder)
        funded_by = []
        for w_id, w in works.items():
            for f_id in w.get("funder_ids", []):
                if f_id in funders:
                    funded_by.append({"source_id": w_id, "target_id": f_id})
        if funded_by:
            counts["funded_by"] = self.neo4j.batch_create_relationships(
                "FUNDED_BY", "Work", "Funder", funded_by,
            )

        # PUBLISHED_BY relationships (Source -> Publisher)
        published_by = []
        source_list = self.serializer.read("Source")
        for s in source_list:
            p_id = s.get("publisher_id")
            if p_id and p_id in publishers:
                published_by.append({"source_id": s["id"], "target_id": p_id})
        if published_by:
            counts["published_by"] = self.neo4j.batch_create_relationships(
                "PUBLISHED_BY", "Source", "Publisher", published_by,
            )

        return counts

    def import_from_cache(
        self,
        session_id: str,
        cache_dir: str | Path,
        node_tags: list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, int]:
        """Resume import from an existing JSONL cache (skip API fetch).

        Args:
            session_id: Import session ID (also used as cache subdirectory).
            cache_dir: Root cache directory containing the session subdirectory.

        Returns:
            Dictionary with counts of imported entities.
        """
        from .serializer import DataSerializer

        self.node_tags = self._normalize_tags(node_tags)
        cache_root = Path(cache_dir) if cache_dir else Path.home() / ".openalex-neo4j" / "cache"
        self.serializer = DataSerializer(cache_root, session_id)
        self.current_session = session_id

        manifest = self.serializer.read_manifest()
        if not manifest:
            raise ValueError(f"Cache for session {session_id} not found at {self.serializer.data_dir}")

        generate_embeddings = manifest.get("parameters", {}).get("generate_embeddings", False)
        if not self.node_tags:
            self.node_tags = self._normalize_tags(
                manifest.get("parameters", {}).get("node_tags", []),
            )

        self.neo4j.create_constraints()
        self.neo4j.create_indexes(include_vector=generate_embeddings)

        node_counts, entity_ids = self._import_nodes_streaming()
        rel_counts = self._import_relationships_streaming(entity_ids)
        counts = {**node_counts, **rel_counts}

        if self.session_manager and self.current_session:
            self.session_manager.complete_session(self.current_session, stats=counts)

        logger.info(f"Import from cache complete: {counts}")
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
