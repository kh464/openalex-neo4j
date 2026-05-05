"""Abstract base class for data source adapters."""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class DataRecord:
    """Standard record format that all data source adapters must output.

    Fields are categorized as:
      - REQUIRED: source_name, source_confidence, external_ids, raw_data
      - Work fields: title, abstract, publication_date, doi, authors, source_display_name
      - Author fields: display_name, orcid
    """
    # --- 标识字段（REQUIRED） ---
    source_name: str
    source_confidence: float            # 0.0 ~ 1.0
    external_ids: dict[str, str]        # e.g. {"doi": "10.1234/abc"}
    raw_data: dict[str, Any]            # original API response JSON

    # --- 映射字段 ---
    openalex_id: str | None = None

    # --- Work 字段 ---
    title: str | None = None
    abstract: str | None = None
    publication_date: str | None = None  # ISO format
    doi: str | None = None
    authors: list[dict] | None = None    # see format below
    source_display_name: str | None = None

    # --- Author 字段 ---
    display_name: str | None = None
    orcid: str | None = None

    def __post_init__(self):
        """Validate required fields."""
        if not self.source_name:
            raise ValueError("source_name is required")
        if not 0.0 <= self.source_confidence <= 1.0:
            raise ValueError(f"source_confidence must be between 0 and 1, got {self.source_confidence}")


# Author entry format for DataRecord.authors list:
# [
#   {
#       "name": "John Doe",                          # str, required
#       "orcid": "0000-0000-0000-0000",              # str, optional
#       "position": "first",                         # str: "first"|"last"|"corresponding"|"middle", optional
#       "affiliations": ["Massachusetts Institute of Technology"],  # list[str], optional
#   },
# ]


class DataSource(ABC):
    """Abstract base class for a data source adapter.

    Subclasses must implement:
      - name (property)
      - fetch_by_doi()
      - fetch_by_openalex_id()
      - confidence()
      - to_openalex_id()
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier for this data source, e.g. 'crossref'."""
        ...

    @abstractmethod
    def fetch_by_doi(self, doi: str) -> DataRecord | None:
        """Fetch a record by DOI. Returns None if not found."""
        ...

    @abstractmethod
    def fetch_by_openalex_id(self, openalex_id: str) -> DataRecord | None:
        """Fetch a record by OpenAlex ID. Returns None if not found."""
        ...

    def fetch_by_title(self, title: str, year: int | None = None) -> DataRecord | None:
        """Fetch by title (fuzzy match). Default implementation returns None.

        Subclasses that support title search should override this.
        """
        return None

    @abstractmethod
    def confidence(self, record: DataRecord) -> float:
        """Return how confident we are in this record, 0.0 ~ 1.0.

        The confidence should reflect how the record was matched:
          - DOI exact match: 0.95
          - Title exact match: 0.8
          - Title fuzzy match: 0.6
        """
        ...

    @abstractmethod
    def to_openalex_id(self, record: DataRecord) -> str | None:
        """Map a DataRecord back to an OpenAlex ID, if possible.

        For example, lookup the DOI in OpenAlex to find the corresponding Wxxxxxx ID.
        """
        ...

    def batch_fetch(self, queries: list[dict]) -> list[DataRecord | None]:
        """Batch fetch multiple records.

        Each query dict must have one of: 'doi', 'openalex_id', 'title'.
        Default implementation calls individual methods sequentially.
        Subclasses may override for parallel fetching.

        Args:
            queries: [{"doi": "10.xxx/yyy"}, {"openalex_id": "W123"}, {"title": "paper title", "year": 2023}]
        """
        results = []
        for q in queries:
            if "doi" in q:
                results.append(self.fetch_by_doi(q["doi"]))
            elif "openalex_id" in q:
                results.append(self.fetch_by_openalex_id(q["openalex_id"]))
            elif "title" in q:
                results.append(self.fetch_by_title(q["title"], q.get("year")))
            else:
                results.append(None)
        return results


# --- 合并策略 ---

FIELD_MAP = {
    "title": "title",
    "abstract": "abstract",
    "publication_date": "publication_date",
    "doi": "doi",
    "source_display_name": "source_display_name",
    "display_name": "display_name",
    "orcid": "orcid",
}


def merge_record(target: dict, source: DataRecord, strategy: str = "fill_null") -> dict[str, Any]:
    """Merge a DataRecord into a target node dict.

    Args:
        target: Existing node properties dict.
        source: DataRecord from a data source.
        strategy: "fill_null" (only fill None fields) or "overwrite" (replace all).

    Returns:
        Dict of changes made: {field_name: (old_value, new_value)}.
    """
    if strategy not in ("fill_null", "overwrite"):
        raise ValueError(f"Unknown merge strategy: '{strategy}'")

    changes: dict[str, Any] = {}

    for target_field, source_field in FIELD_MAP.items():
        source_val = getattr(source, source_field, None)
        if source_val is None:
            continue

        # Only merge if source has confidence >= 0.5
        if source.source_confidence < 0.5:
            continue

        if target_field not in target or target[target_field] is None:
            target[target_field] = source_val
            changes[target_field] = (None, source_val)
        elif strategy == "overwrite" and source.source_confidence > 0.9:
            old_val = target[target_field]
            target[target_field] = source_val
            changes[target_field] = (old_val, source_val)

    return changes
