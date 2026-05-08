"""Data serializer for local JSONL cache during import."""

import json
import logging
import shutil
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class DataSerializer:
    """Serialize entity data to local JSONL files and read them back.

    Writing: each entity type gets its own ``{label}.jsonl`` file, one JSON
    object per line.  Reading: loads all entities of a type (or all types)
    into memory as ``list[dict]``.

    Directory layout::

        {cache_dir}/{session_id}/
        ├── manifest.json
        ├── works.jsonl
        ├── authors.jsonl
        └── ...
    """

    LABELS = ["Work", "Author", "Institution", "Source",
              "Topic", "Publisher", "Funder"]

    def __init__(self, cache_dir: Path, session_id: str):
        """Initialize serializer.

        Args:
            cache_dir: Root cache directory (e.g. ``~/.openalex-neo4j/cache``).
            session_id: Current import session ID.
        """
        self.data_dir = cache_dir / session_id
        self.data_dir.mkdir(parents=True, exist_ok=True)

    # ──────── Write ────────

    def append(self, label: str, node_dict: dict[str, Any]) -> None:
        """Append a single entity record to the JSONL file for *label*."""
        file_path = self.data_dir / f"{label.lower()}.jsonl"
        with open(file_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(node_dict, ensure_ascii=False) + "\n")

    def append_batch(self, label: str, nodes: list[dict[str, Any]]) -> None:
        """Append multiple entity records to the JSONL file for *label*."""
        if not nodes:
            return
        file_path = self.data_dir / f"{label.lower()}.jsonl"
        with open(file_path, "a", encoding="utf-8") as f:
            for node in nodes:
                f.write(json.dumps(node, ensure_ascii=False) + "\n")

    def write_manifest(self, metadata: dict[str, Any]) -> None:
        """Write session manifest as JSON."""
        file_path = self.data_dir / "manifest.json"
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)

    # ──────── Read ────────

    def read(self, label: str) -> list[dict[str, Any]]:
        """Read all entity records for *label* from disk."""
        file_path = self.data_dir / f"{label.lower()}.jsonl"
        if not file_path.exists():
            return []
        records: list[dict[str, Any]] = []
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if stripped:
                    records.append(json.loads(stripped))
        return records

    def read_all(self) -> dict[str, list[dict[str, Any]]]:
        """Read all entity types and return a dict keyed by label."""
        return {label: self.read(label) for label in self.LABELS
                if self.data_dir.joinpath(f"{label.lower()}.jsonl").exists()}

    def read_manifest(self) -> dict[str, Any] | None:
        """Read the session manifest, or ``None`` if absent."""
        file_path = self.data_dir / "manifest.json"
        if not file_path.exists():
            return None
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)

    # ──────── Query ────────

    def count(self, label: str) -> int:
        """Count entity records for *label* without loading them fully."""
        file_path = self.data_dir / f"{label.lower()}.jsonl"
        if not file_path.exists():
            return 0
        n = 0
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    n += 1
        return n

    # ──────── Cleanup ────────

    def cleanup(self) -> None:
        """Delete the entire session cache directory."""
        if self.data_dir.exists():
            shutil.rmtree(self.data_dir)
            logger.info(f"Removed cache directory: {self.data_dir}")
