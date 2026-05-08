"""Data models for OpenAlex entities."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class ImportSession:
    """Represents a single import session (one run of import command)."""
    id: str
    query: str
    limit: int | None = None
    expand_depth: int = 1
    tag: str | None = None
    created_at: datetime | None = None
    status: str = "completed"                 # "completed" | "failed" | "running"
    stats: dict[str, int] | None = None       # node/relationship counts from the import
    quality_summary: dict | None = None        # {"errors": N, "warnings": N, "infos": N}

    def to_node_dict(self) -> dict[str, Any]:
        """Convert to Neo4j node properties.

        Omits stats/quality_summary (stored in local JSON only).
        """
        return {
            "id": self.id,
            "query": self.query,
            "limit": self.limit,
            "expand_depth": self.expand_depth,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "status": self.status,
            "tag": self.tag,
        }


def extract_openalex_id(url: str | None) -> str | None:
    """Extract OpenAlex ID from URL.

    Args:
        url: OpenAlex URL like 'https://openalex.org/W123456'

    Returns:
        ID like 'W123456' or None if url is None/invalid
    """
    if not url:
        return None
    if isinstance(url, str) and '/' in url:
        return url.split('/')[-1]
    return url


@dataclass
class Work:
    """Represents a scholarly work."""
    id: str
    title: str | None = None
    publication_year: int | None = None
    publication_date: str | None = None
    doi: str | None = None
    type: str | None = None
    cited_by_count: int = 0
    is_oa: bool = False
    abstract: str | None = None
    embedding: list[float] | None = None
    author_ids: list[str] = field(default_factory=list)
    institution_ids: list[str] = field(default_factory=list)
    source_id: str | None = None
    topic_ids: list[str] = field(default_factory=list)
    funder_ids: list[str] = field(default_factory=list)
    referenced_work_ids: list[str] = field(default_factory=list)
    # Session tracking fields
    import_sessions: list[str] = field(default_factory=list)
    first_imported_at: str | None = None
    last_imported_at: str | None = None

    @classmethod
    def from_openalex(cls, data: dict[str, Any]) -> "Work":
        """Create Work from OpenAlex API response."""
        work_id = extract_openalex_id(data.get("id"))
        if not work_id:
            raise ValueError("Work must have an id")

        # Extract author IDs
        author_ids = []
        for authorship in data.get("authorships", []):
            if author := authorship.get("author"):
                # Handle both dict and string formats
                if isinstance(author, dict):
                    if author_id := extract_openalex_id(author.get("id")):
                        author_ids.append(author_id)
                elif isinstance(author, str):
                    if author_id := extract_openalex_id(author):
                        author_ids.append(author_id)

        # Extract institution IDs from authorships
        institution_ids = []
        for authorship in data.get("authorships", []):
            for institution in authorship.get("institutions", []):
                # Handle both dict and string formats
                if isinstance(institution, dict):
                    if inst_id := extract_openalex_id(institution.get("id")):
                        institution_ids.append(inst_id)
                elif isinstance(institution, str):
                    if inst_id := extract_openalex_id(institution):
                        institution_ids.append(inst_id)

        # Extract source ID
        source_id = None
        if primary_location := data.get("primary_location"):
            if source := primary_location.get("source"):
                # Handle both dict and string formats
                if isinstance(source, dict):
                    source_id = extract_openalex_id(source.get("id"))
                elif isinstance(source, str):
                    source_id = extract_openalex_id(source)

        # Extract topic IDs
        topic_ids = []
        for topic in data.get("topics", []):
            # Handle both dict and string formats
            if isinstance(topic, dict):
                if topic_id := extract_openalex_id(topic.get("id")):
                    topic_ids.append(topic_id)
            elif isinstance(topic, str):
                if topic_id := extract_openalex_id(topic):
                    topic_ids.append(topic_id)

        # Extract funder IDs
        funder_ids = []
        for grant in data.get("grants", []):
            if funder := grant.get("funder"):
                # Handle both dict and string formats
                if isinstance(funder, dict):
                    if funder_id := extract_openalex_id(funder.get("id")):
                        funder_ids.append(funder_id)
                elif isinstance(funder, str):
                    if funder_id := extract_openalex_id(funder):
                        funder_ids.append(funder_id)

        # Extract referenced work IDs
        referenced_work_ids = [
            extract_openalex_id(ref)
            for ref in data.get("referenced_works", [])
            if extract_openalex_id(ref)
        ]

        # Get abstract
        abstract = None
        if abstract_inverted := data.get("abstract_inverted_index"):
            # Reconstruct abstract from inverted index
            words = [""] * (max(max(positions) for positions in abstract_inverted.values()) + 1)
            for word, positions in abstract_inverted.items():
                for pos in positions:
                    words[pos] = word
            abstract = " ".join(words)

        return cls(
            id=work_id,
            title=data.get("title"),
            publication_year=data.get("publication_year"),
            publication_date=data.get("publication_date"),
            doi=data.get("doi"),
            type=data.get("type"),
            cited_by_count=data.get("cited_by_count", 0),
            is_oa=data.get("open_access", {}).get("is_oa", False),
            abstract=abstract,
            author_ids=author_ids,
            institution_ids=list(set(institution_ids)),  # Deduplicate
            source_id=source_id,
            topic_ids=topic_ids,
            funder_ids=funder_ids,
            referenced_work_ids=referenced_work_ids,
        )

    def to_node_dict(self, current_session: str | None = None) -> dict[str, Any]:
        """Convert to dictionary for Neo4j node creation.

        Args:
            current_session: If provided, include session tracking fields.
        """
        from .neo4j_client import to_camel_case_label

        node_dict = {
            "id": self.id,
            "title": self.title,
            "publication_year": self.publication_year,
            "publication_date": self.publication_date,
            "doi": self.doi,
            "type": self.type,
            "cited_by_count": self.cited_by_count,
            "is_oa": self.is_oa,
            "abstract": self.abstract,
        }
        # Add CamelCase type as dynamic label field (or "Work" as default)
        if self.type:
            node_dict["_label"] = to_camel_case_label(self.type)
        else:
            node_dict["_label"] = "Work"
        # Only include embedding if it exists
        if self.embedding:
            node_dict["embedding"] = self.embedding
        # Session tracking fields
        if current_session:
            node_dict["import_sessions"] = self.import_sessions or [current_session]
            node_dict["first_imported_at"] = self.first_imported_at
            node_dict["last_imported_at"] = self.last_imported_at
        return node_dict


@dataclass
class Author:
    """Represents an author."""
    id: str
    display_name: str | None = None
    orcid: str | None = None
    works_count: int = 0
    cited_by_count: int = 0
    # Session tracking fields
    import_sessions: list[str] = field(default_factory=list)
    first_imported_at: str | None = None
    last_imported_at: str | None = None

    @classmethod
    def from_openalex(cls, data: dict[str, Any]) -> "Author":
        """Create Author from OpenAlex API response."""
        author_id = extract_openalex_id(data.get("id"))
        if not author_id:
            raise ValueError("Author must have an id")

        return cls(
            id=author_id,
            display_name=data.get("display_name"),
            orcid=data.get("orcid"),
            works_count=data.get("works_count", 0),
            cited_by_count=data.get("cited_by_count", 0),
        )

    def to_node_dict(self, current_session: str | None = None) -> dict[str, Any]:
        """Convert to dictionary for Neo4j node creation."""
        node_dict = {
            "id": self.id,
            "display_name": self.display_name,
            "orcid": self.orcid,
            "works_count": self.works_count,
            "cited_by_count": self.cited_by_count,
        }
        if current_session:
            node_dict["import_sessions"] = self.import_sessions or [current_session]
            node_dict["first_imported_at"] = self.first_imported_at
            node_dict["last_imported_at"] = self.last_imported_at
        return node_dict


@dataclass
class Institution:
    """Represents an institution."""
    id: str
    display_name: str | None = None
    ror: str | None = None
    country_code: str | None = None
    type: str | None = None
    works_count: int = 0
    # Session tracking fields
    import_sessions: list[str] = field(default_factory=list)
    first_imported_at: str | None = None
    last_imported_at: str | None = None

    @classmethod
    def from_openalex(cls, data: dict[str, Any]) -> "Institution":
        """Create Institution from OpenAlex API response."""
        inst_id = extract_openalex_id(data.get("id"))
        if not inst_id:
            raise ValueError("Institution must have an id")

        return cls(
            id=inst_id,
            display_name=data.get("display_name"),
            ror=data.get("ror"),
            country_code=data.get("country_code"),
            type=data.get("type"),
            works_count=data.get("works_count", 0),
        )

    def to_node_dict(self, current_session: str | None = None) -> dict[str, Any]:
        """Convert to dictionary for Neo4j node creation."""
        node_dict = {
            "id": self.id,
            "display_name": self.display_name,
            "ror": self.ror,
            "country_code": self.country_code,
            "type": self.type,
            "works_count": self.works_count,
        }
        if current_session:
            node_dict["import_sessions"] = self.import_sessions or [current_session]
            node_dict["first_imported_at"] = self.first_imported_at
            node_dict["last_imported_at"] = self.last_imported_at
        return node_dict


@dataclass
class Source:
    """Represents a publication source (journal, conference, etc)."""
    id: str
    display_name: str | None = None
    issn_l: str | None = None
    issn: list[str] = field(default_factory=list)
    type: str | None = None
    publisher_id: str | None = None
    works_count: int = 0
    # Session tracking fields
    import_sessions: list[str] = field(default_factory=list)
    first_imported_at: str | None = None
    last_imported_at: str | None = None

    @classmethod
    def from_openalex(cls, data: dict[str, Any]) -> "Source":
        """Create Source from OpenAlex API response."""
        source_id = extract_openalex_id(data.get("id"))
        if not source_id:
            raise ValueError("Source must have an id")

        publisher_id = None
        if publisher := data.get("host_organization"):
            publisher_id = extract_openalex_id(publisher)

        return cls(
            id=source_id,
            display_name=data.get("display_name"),
            issn_l=data.get("issn_l"),
            issn=data.get("issn", []),
            type=data.get("type"),
            publisher_id=publisher_id,
            works_count=data.get("works_count", 0),
        )

    def to_node_dict(self, current_session: str | None = None) -> dict[str, Any]:
        """Convert to dictionary for Neo4j node creation."""
        node_dict = {
            "id": self.id,
            "display_name": self.display_name,
            "issn_l": self.issn_l,
            "issn": self.issn,
            "type": self.type,
            "works_count": self.works_count,
        }
        if current_session:
            node_dict["import_sessions"] = self.import_sessions or [current_session]
            node_dict["first_imported_at"] = self.first_imported_at
            node_dict["last_imported_at"] = self.last_imported_at
        return node_dict


@dataclass
class Topic:
    """Represents a research topic."""
    id: str
    display_name: str | None = None
    description: str | None = None
    keywords: list[str] = field(default_factory=list)
    # Session tracking fields
    import_sessions: list[str] = field(default_factory=list)
    first_imported_at: str | None = None
    last_imported_at: str | None = None

    @classmethod
    def from_openalex(cls, data: dict[str, Any]) -> "Topic":
        """Create Topic from OpenAlex API response."""
        topic_id = extract_openalex_id(data.get("id"))
        if not topic_id:
            raise ValueError("Topic must have an id")

        return cls(
            id=topic_id,
            display_name=data.get("display_name"),
            description=data.get("description"),
            keywords=data.get("keywords", []),
        )

    def to_node_dict(self, current_session: str | None = None) -> dict[str, Any]:
        """Convert to dictionary for Neo4j node creation."""
        node_dict = {
            "id": self.id,
            "display_name": self.display_name,
            "description": self.description,
            "keywords": self.keywords,
        }
        if current_session:
            node_dict["import_sessions"] = self.import_sessions or [current_session]
            node_dict["first_imported_at"] = self.first_imported_at
            node_dict["last_imported_at"] = self.last_imported_at
        return node_dict


@dataclass
class Publisher:
    """Represents a publisher."""
    id: str
    display_name: str | None = None
    country_codes: list[str] = field(default_factory=list)
    works_count: int = 0
    # Session tracking fields
    import_sessions: list[str] = field(default_factory=list)
    first_imported_at: str | None = None
    last_imported_at: str | None = None

    @classmethod
    def from_openalex(cls, data: dict[str, Any]) -> "Publisher":
        """Create Publisher from OpenAlex API response."""
        pub_id = extract_openalex_id(data.get("id"))
        if not pub_id:
            raise ValueError("Publisher must have an id")

        return cls(
            id=pub_id,
            display_name=data.get("display_name"),
            country_codes=data.get("country_codes", []),
            works_count=data.get("works_count", 0),
        )

    def to_node_dict(self, current_session: str | None = None) -> dict[str, Any]:
        """Convert to dictionary for Neo4j node creation."""
        node_dict = {
            "id": self.id,
            "display_name": self.display_name,
            "country_codes": self.country_codes,
            "works_count": self.works_count,
        }
        if current_session:
            node_dict["import_sessions"] = self.import_sessions or [current_session]
            node_dict["first_imported_at"] = self.first_imported_at
            node_dict["last_imported_at"] = self.last_imported_at
        return node_dict


@dataclass
class Funder:
    """Represents a funding organization."""
    id: str
    display_name: str | None = None
    country_code: str | None = None
    description: str | None = None
    # Session tracking fields
    import_sessions: list[str] = field(default_factory=list)
    first_imported_at: str | None = None
    last_imported_at: str | None = None

    @classmethod
    def from_openalex(cls, data: dict[str, Any]) -> "Funder":
        """Create Funder from OpenAlex API response."""
        funder_id = extract_openalex_id(data.get("id"))
        if not funder_id:
            raise ValueError("Funder must have an id")

        return cls(
            id=funder_id,
            display_name=data.get("display_name"),
            country_code=data.get("country_code"),
            description=data.get("description"),
        )

    def to_node_dict(self, current_session: str | None = None) -> dict[str, Any]:
        """Convert to dictionary for Neo4j node creation."""
        node_dict = {
            "id": self.id,
            "display_name": self.display_name,
            "country_code": self.country_code,
            "description": self.description,
        }
        if current_session:
            node_dict["import_sessions"] = self.import_sessions or [current_session]
            node_dict["first_imported_at"] = self.first_imported_at
            node_dict["last_imported_at"] = self.last_imported_at
        return node_dict
