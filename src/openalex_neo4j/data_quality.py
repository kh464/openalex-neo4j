"""Data quality validation and cleaning pipeline."""
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


# --- 数据类 ---

@dataclass
class RuleViolation:
    """A single quality rule violation."""
    rule_name: str
    entity_id: str
    entity_type: str
    severity: str            # "error" | "warning" | "info"
    message: str
    field: str | None = None
    value: Any | None = None


@dataclass
class QualityReport:
    """Quality check report for a session's data."""
    session_id: str
    total_entities: int = 0
    violations: list[RuleViolation] = field(default_factory=list)
    created_at: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()

    @property
    def error_count(self) -> int:
        return sum(1 for v in self.violations if v.severity == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for v in self.violations if v.severity == "warning")

    @property
    def info_count(self) -> int:
        return sum(1 for v in self.violations if v.severity == "info")

    @property
    def summary(self) -> dict:
        return {
            "errors": self.error_count,
            "warnings": self.warning_count,
            "infos": self.info_count,
        }

    def by_entity_type(self) -> dict[str, list[RuleViolation]]:
        result: dict[str, list[RuleViolation]] = {}
        for v in self.violations:
            result.setdefault(v.entity_type, []).append(v)
        return result

    def by_severity(self) -> dict[str, list[RuleViolation]]:
        result: dict[str, list[RuleViolation]] = {}
        for v in self.violations:
            result.setdefault(v.severity, []).append(v)
        return result


# --- 校验规则基类 ---

class QualityRule(ABC):
    """Base class for a single quality validation rule."""

    name: str = ""
    description: str = ""
    severity: str = "info"
    applies_to: list[str] = []

    @abstractmethod
    def check(self, entity: Any) -> RuleViolation | None:
        """Check a single entity. Return a RuleViolation or None."""
        ...


# --- 预置规则实现 ---

class MissingTitleRule(QualityRule):
    name = "missing_title"
    description = "Work title is missing"
    severity = "error"
    applies_to = ["Work"]

    def check(self, entity: Any) -> RuleViolation | None:
        if not entity.title or not entity.title.strip():
            return RuleViolation(
                rule_name=self.name,
                entity_id=entity.id,
                entity_type="Work",
                severity=self.severity,
                message="Title is missing or empty",
                field="title",
                value=entity.title,
            )
        return None


class OutlierYearRule(QualityRule):
    name = "outlier_year"
    description = "Publication year is outside reasonable range"
    severity = "warning"
    applies_to = ["Work"]

    def __init__(self, min_year: int = 1900, max_year: int | None = None):
        self.min_year = min_year
        self.max_year = max_year or (datetime.now().year + 2)

    def check(self, entity: Any) -> RuleViolation | None:
        if entity.publication_year is not None:
            if entity.publication_year < self.min_year or entity.publication_year > self.max_year:
                return RuleViolation(
                    rule_name=self.name,
                    entity_id=entity.id,
                    entity_type="Work",
                    severity=self.severity,
                    message=f"Publication year {entity.publication_year} outside "
                            f"range [{self.min_year}, {self.max_year}]",
                    field="publication_year",
                    value=entity.publication_year,
                )
        return None


class MissingAbstractRule(QualityRule):
    name = "missing_abstract"
    description = "Work has no abstract"
    severity = "info"
    applies_to = ["Work"]

    def check(self, entity: Any) -> RuleViolation | None:
        if not entity.abstract:
            return RuleViolation(
                rule_name=self.name,
                entity_id=entity.id,
                entity_type="Work",
                severity=self.severity,
                message="Abstract is missing",
                field="abstract",
                value=None,
            )
        return None


class MissingDisplayNameRule(QualityRule):
    name = "missing_display_name"
    description = "Entity display_name is missing"
    severity = "error"
    applies_to = ["Author", "Institution", "Source", "Topic", "Publisher", "Funder"]

    def check(self, entity: Any) -> RuleViolation | None:
        if not entity.display_name or not entity.display_name.strip():
            return RuleViolation(
                rule_name=self.name,
                entity_id=entity.id,
                entity_type=type(entity).__name__,
                severity=self.severity,
                message="Display name is missing",
                field="display_name",
                value=entity.display_name,
            )
        return None


class EmptyEntityRule(QualityRule):
    name = "empty_entity"
    description = "Entity has only an ID, all other fields are empty"
    severity = "warning"
    applies_to = ["Work", "Author", "Institution", "Source", "Topic", "Publisher", "Funder"]

    # Fields that are allowed to be empty/NULL and not considered
    # "meaningful content" for empty-entity detection
    OPTIONAL_FIELDS = {"cited_by_count", "works_count", "embedding", "is_oa"}

    def check(self, entity: Any) -> RuleViolation | None:
        entity_type = type(entity).__name__

        # Collect all meaningful field values
        meaningful = []
        for field_name in entity.__dataclass_fields__:
            if field_name in ("id", "import_sessions", "first_imported_at", "last_imported_at",
                              *self.OPTIONAL_FIELDS):
                continue
            val = getattr(entity, field_name)
            if val is not None and val != [] and val != "":
                meaningful.append(field_name)

        # If only id has a value, this is an empty shell
        if len(meaningful) == 0:
            return RuleViolation(
                rule_name=self.name,
                entity_id=entity.id,
                entity_type=entity_type,
                severity=self.severity,
                message=f"{entity_type} has no meaningful data beyond ID",
                field=None,
                value=None,
            )
        return None


class InvalidWorkTypeRule(QualityRule):
    name = "invalid_work_type"
    description = "Work type is not a recognized OpenAlex type"
    severity = "warning"
    applies_to = ["Work"]

    VALID_TYPES = {
        "article", "book-chapter", "dataset", "dissertation", "book",
        "editorial", "erratum", "grant", "letter", "note", "paragraph",
        "reference-entry", "report", "review", "standard", "other",
    }

    def check(self, entity: Any) -> RuleViolation | None:
        if entity.type and entity.type not in self.VALID_TYPES:
            return RuleViolation(
                rule_name=self.name,
                entity_id=entity.id,
                entity_type="Work",
                severity=self.severity,
                message=f"Unknown work type: '{entity.type}'",
                field="type",
                value=entity.type,
            )
        return None


class ShortTitleRule(QualityRule):
    name = "short_title"
    description = "Title is suspiciously short (possibly a placeholder)"
    severity = "info"
    applies_to = ["Work"]

    def __init__(self, min_length: int = 10):
        self.min_length = min_length

    def check(self, entity: Any) -> RuleViolation | None:
        if entity.title and len(entity.title.strip()) < self.min_length:
            return RuleViolation(
                rule_name=self.name,
                entity_id=entity.id,
                entity_type="Work",
                severity=self.severity,
                message=f"Title is only {len(entity.title.strip())} characters (min {self.min_length})",
                field="title",
                value=entity.title,
            )
        return None


# --- 规则注册表 ---

class RuleCatalog:
    """Registry of all available quality rules."""

    def __init__(self):
        self._rules: dict[str, QualityRule] = {}

    def register(self, rule: QualityRule) -> None:
        """Register a single rule."""
        self._rules[rule.name] = rule

    def register_defaults(self) -> None:
        """Register all built-in rules."""
        self.register(MissingTitleRule())
        self.register(OutlierYearRule())
        self.register(MissingAbstractRule())
        self.register(MissingDisplayNameRule())
        self.register(EmptyEntityRule())
        self.register(InvalidWorkTypeRule())
        self.register(ShortTitleRule())

    def get(self, name: str) -> QualityRule | None:
        return self._rules.get(name)

    def get_for_entity(self, entity_type: str) -> list[QualityRule]:
        """Get all rules that apply to a given entity type."""
        return [r for r in self._rules.values() if entity_type in r.applies_to]

    def list(self) -> list[QualityRule]:
        return list(self._rules.values())


# --- 清洗管道 ---

class DataQualityPipeline:
    """Runs quality checks on a collection of entities and produces reports."""

    def __init__(self, catalog: RuleCatalog | None = None):
        self.catalog = catalog or RuleCatalog()
        if not self.catalog.list():
            self.catalog.register_defaults()

    def run(self, session_id: str, entities: dict[str, list[Any]]) -> QualityReport:
        """Run all applicable rules on a collection of entities.

        Args:
            session_id: The import session ID.
            entities: Dict mapping entity type names to lists of entities.
                Example: {"Work": [work1, work2], "Author": [author1]}

        Returns:
            QualityReport with all violations.
        """
        report = QualityReport(session_id=session_id)
        total = 0

        for entity_type, entity_list in entities.items():
            rules = self.catalog.get_for_entity(entity_type)
            total += len(entity_list)

            for entity in entity_list:
                for rule in rules:
                    try:
                        violation = rule.check(entity)
                        if violation:
                            report.violations.append(violation)
                    except Exception as e:
                        logger.warning(f"Rule {rule.name} failed on {entity.id}: {e}")

        report.total_entities = total
        return report


# --- 数据清洗函数 ---

def clean_entity_fields(entity: Any) -> dict[str, Any]:
    """Auto-fix common data quality issues on an entity.

    Modifies the entity in-place. Returns a dict of changes made.

    Fixes:
      - Strip whitespace from string fields
      - Convert empty strings to None
      - Set outlier years to None
    """
    changes = {}

    if hasattr(entity, "title") and isinstance(entity.title, str):
        stripped = entity.title.strip()
        if stripped == "":
            entity.title = None
            changes["title"] = "empty string -> None"
        elif stripped != entity.title:
            entity.title = stripped
            changes["title"] = "stripped whitespace"

    if hasattr(entity, "display_name") and isinstance(entity.display_name, str):
        stripped = entity.display_name.strip()
        if stripped == "":
            entity.display_name = None
            changes["display_name"] = "empty string -> None"
        elif stripped != entity.display_name:
            entity.display_name = stripped
            changes["display_name"] = "stripped whitespace"

    if hasattr(entity, "doi") and isinstance(entity.doi, str):
        # Normalize DOI: remove URL prefix if present
        doi = entity.doi.strip()
        for prefix in ["https://doi.org/", "http://doi.org/", "doi:"]:
            if doi.startswith(prefix):
                doi = doi[len(prefix):]
                entity.doi = doi
                changes["doi"] = f"normalized from URL"
                break

    if hasattr(entity, "publication_year") and entity.publication_year is not None:
        year = entity.publication_year
        max_year = datetime.now().year + 2
        if year < 1900 or year > max_year:
            entity.publication_year = None
            changes["publication_year"] = f"{year} -> None (outlier)"

    return changes
