"""Manages import session metadata in Neo4j and local storage."""
import json
import logging
from datetime import datetime
from pathlib import Path

from .models import ImportSession
from .neo4j_client import Neo4jClient

logger = logging.getLogger(__name__)


class SessionManager:
    """Manages import session metadata in Neo4j and local storage.

    Session metadata is stored in two places:
      - Neo4j: :ImportSession node for in-database queries
      - Local: ~/.openalex-neo4j/sessions.json for fast CLI listing

    The local file is the source of truth for stats, quality_summary,
    and tag (fields not stored in Neo4j).
    """

    SESSIONS_DIR = Path.home() / ".openalex-neo4j"
    SESSIONS_FILE = SESSIONS_DIR / "sessions.json"

    SESSIONS_DIR = Path.home() / ".openalex-neo4j"
    SESSIONS_FILE = SESSIONS_DIR / "sessions.json"

    def __init__(self, neo4j_client: Neo4jClient):
        self.neo4j = neo4j_client
        self._local_sessions: dict[str, dict] = {}
        self._counter: int = 0
        self._ensure_dir()
        self._load_local()

    # --- 内部辅助 ---

    def _ensure_dir(self) -> None:
        """Create sessions directory if it doesn't exist."""
        self.SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

    def _load_local(self) -> None:
        """Load local session metadata from JSON file."""
        if self.SESSIONS_FILE.exists():
            try:
                with open(self.SESSIONS_FILE, "r", encoding="utf-8") as f:
                    self._local_sessions = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"Failed to load sessions file: {e}")
                self._local_sessions = {}
        else:
            self._local_sessions = {}

    def _save_local(self) -> None:
        """Save local session metadata to JSON file."""
        try:
            with open(self.SESSIONS_FILE, "w", encoding="utf-8") as f:
                json.dump(self._local_sessions, f, indent=2, ensure_ascii=False)
        except OSError as e:
            logger.error(f"Failed to save sessions file: {e}")

    def _next_id(self) -> str:
        """Generate a unique session ID based on current timestamp."""
        self._counter += 1
        now = datetime.now()
        return now.strftime("%Y%m%d_%H%M%S") + f"_{self._counter:04d}"

    # --- Session 生命周期管理 ---

    def create_session(self, query: str, limit: int = 100,
                       expand_depth: int = 1,
                       tag: str | None = None) -> ImportSession:
        """Create a new import session.

        Generates a session ID, creates an :ImportSession node in Neo4j,
        and writes metadata to local storage.

        Returns:
            ImportSession with id, created_at, etc. populated.
        """
        session_id = self._next_id()
        now = datetime.now()
        session = ImportSession(
            id=session_id,
            query=query,
            limit=limit,
            expand_depth=expand_depth,
            tag=tag,
            created_at=now,
            status="running",
        )

        # Write to local storage
        self._local_sessions[session_id] = {
            "query": query,
            "limit": limit,
            "expand_depth": expand_depth,
            "tag": tag,
            "created_at": now.isoformat(),
            "status": "running",
            "stats": None,
            "quality_summary": None,
        }
        self._save_local()

        # Write :ImportSession node to Neo4j
        try:
            node = session.to_node_dict()
            self.neo4j.batch_create_nodes("ImportSession", [node])
        except Exception as e:
            logger.warning(f"Failed to write ImportSession node to Neo4j: {e}")

        logger.info(f"Created import session: {session_id} (query='{query}')")
        return session

    def complete_session(self, session_id: str, stats: dict[str, int] | None = None,
                         quality_summary: dict | None = None) -> None:
        """Mark a session as completed and update statistics.

        Args:
            session_id: The session ID to update.
            stats: Node/relationship counts from the import.
            quality_summary: Quality check results, e.g. {"errors": 0, "warnings": 2}.
        """
        if session_id not in self._local_sessions:
            logger.warning(f"Session {session_id} not found in local storage")
            return

        self._local_sessions[session_id]["status"] = "completed"
        if stats is not None:
            self._local_sessions[session_id]["stats"] = stats
        if quality_summary is not None:
            self._local_sessions[session_id]["quality_summary"] = quality_summary
        self._save_local()

        # Update ImportSession node in Neo4j
        try:
            with self.neo4j.driver.session() as session:
                session.run(
                    "MATCH (s:ImportSession {id: $id}) SET s.status = $status",
                    id=session_id, status="completed",
                )
        except Exception as e:
            logger.warning(f"Failed to update ImportSession node in Neo4j: {e}")

        logger.info(f"Completed import session: {session_id}")

    def fail_session(self, session_id: str) -> None:
        """Mark a session as failed."""
        if session_id not in self._local_sessions:
            return
        self._local_sessions[session_id]["status"] = "failed"
        self._save_local()
        try:
            with self.neo4j.driver.session() as session:
                session.run(
                    "MATCH (s:ImportSession {id: $id}) SET s.status = $status",
                    id=session_id, status="failed",
                )
        except Exception:
            pass
        logger.info(f"Failed import session: {session_id}")

    def tag_session(self, session_id: str, tag: str) -> None:
        """Set a human-readable tag/alias for a session."""
        if session_id not in self._local_sessions:
            raise KeyError(f"Session {session_id} not found")
        self._local_sessions[session_id]["tag"] = tag
        self._save_local()

    # --- 查询 ---

    def get_session(self, session_id: str) -> ImportSession | None:
        """Get a session by ID. Checks local storage first, falls back to Neo4j."""
        local = self._local_sessions.get(session_id)
        if local:
            return ImportSession(
                id=session_id,
                query=local.get("query", ""),
                limit=local.get("limit", 100),
                expand_depth=local.get("expand_depth", 1),
                tag=local.get("tag"),
                created_at=datetime.fromisoformat(local["created_at"]) if local.get("created_at") else None,
                status=local.get("status", "unknown"),
                stats=local.get("stats"),
                quality_summary=local.get("quality_summary"),
            )
        # Fallback: query Neo4j
        try:
            with self.neo4j.driver.session() as ns:
                result = ns.run(
                    "MATCH (s:ImportSession {id: $id}) RETURN s",
                    id=session_id,
                )
                record = result.single()
                if record:
                    props = dict(record["s"])
                    return ImportSession(
                        id=props["id"],
                        query=props.get("query", ""),
                        limit=props.get("limit", 100),
                        expand_depth=props.get("expand_depth", 1),
                        tag=props.get("tag"),
                        created_at=datetime.fromisoformat(props["created_at"]) if props.get("created_at") else None,
                        status=props.get("status", "unknown"),
                    )
        except Exception:
            pass
        return None

    def list_sessions(self, limit: int = 20) -> list[ImportSession]:
        """List recent sessions, newest first."""
        all_sessions = []
        for sid, data in self._local_sessions.items():
            all_sessions.append(ImportSession(
                id=sid,
                query=data.get("query", ""),
                limit=data.get("limit", 100),
                expand_depth=data.get("expand_depth", 1),
                tag=data.get("tag"),
                created_at=datetime.fromisoformat(data["created_at"]) if data.get("created_at") else None,
                status=data.get("status", "unknown"),
                stats=data.get("stats"),
                quality_summary=data.get("quality_summary"),
            ))

        all_sessions.sort(key=lambda s: (s.created_at or datetime.min, s.id), reverse=True)
        return all_sessions[:limit]

    # --- 删除 ---

    def clear_all_sessions(self) -> None:
        """Clear all session metadata from local storage.

        Call this when the Neo4j database is cleared (e.g. clear_database)
        so that `sessions` / `session list` doesn't show stale entries.
        """
        self._local_sessions.clear()
        if self.SESSIONS_FILE.exists():
            try:
                self.SESSIONS_FILE.unlink()
            except OSError as e:
                logger.warning(f"Failed to remove sessions file: {e}")

    def get_session_node_counts(self, session_id: str) -> dict[str, int]:
        """Count nodes associated with a session, grouped by label.

        Uses the import_sessions array property on nodes.
        """
        counts: dict[str, int] = {}
        try:
            with self.neo4j.driver.session() as session:
                result = session.run("""
                    MATCH (n)
                    WHERE $session_id IN n.import_sessions
                    RETURN labels(n) as labels, count(n) as count
                """, session_id=session_id)
                for record in result:
                    # Use the first label (most specific) as the group key
                    label = record["labels"][0] if record["labels"] else "Unknown"
                    counts[label] = record["count"]
        except Exception as e:
            logger.error(f"Failed to count session nodes: {e}")
        return counts

    def delete_session(self, session_id: str) -> dict[str, int]:
        """Delete all data associated with a session.

        Strategy (strict deletion):
          1. Delete nodes whose import_sessions == [session_id] (isolated nodes).
             DETACH DELETE cascades to their relationships.
          2. Remove session_id from import_sessions of shared nodes.
          3. Delete the ImportSession node itself.

        Returns:
            Dict with counts of deleted and updated nodes.
        """
        result: dict[str, int] = {"deleted": 0, "updated": 0}

        try:
            with self.neo4j.driver.session() as session:
                # Step 1: Delete isolated nodes (only belong to this session)
                step1 = session.run("""
                    MATCH (n)
                    WHERE n.import_sessions = [$session_id]
                    DETACH DELETE n
                    RETURN count(n) as deleted
                """, session_id=session_id)
                result["deleted"] = step1.single()["deleted"]

                # Step 2: Delete the ImportSession node
                session.run(
                    "MATCH (s:ImportSession {id: $id}) DELETE s",
                    id=session_id,
                )

                # Step 3: Remove session_id from shared nodes
                step3 = session.run("""
                    MATCH (n)
                    WHERE $session_id IN n.import_sessions
                    SET n.import_sessions = [s IN n.import_sessions WHERE s <> $session_id]
                    RETURN count(n) as updated
                """, session_id=session_id)
                result["updated"] = step3.single()["updated"]

        except Exception as e:
            logger.error(f"Failed to delete session {session_id}: {e}")
            raise

        # Clean up local storage
        self._local_sessions.pop(session_id, None)
        self._save_local()

        logger.info(f"Deleted session {session_id}: {result['deleted']} nodes removed, "
                    f"{result['updated']} nodes updated")
        return result
