"""Tests for data quality module."""
from openalex_neo4j.data_quality import (
    QualityReport,
    RuleCatalog,
    MissingTitleRule,
    OutlierYearRule,
    MissingAbstractRule,
    EmptyEntityRule,
    InvalidWorkTypeRule,
    ShortTitleRule,
    DataQualityPipeline,
    clean_entity_fields,
)
from openalex_neo4j.models import Work, Author


class TestQualityReport:
    """Tests for QualityReport."""

    def test_empty_report(self):
        report = QualityReport(session_id="S1")
        assert report.error_count == 0
        assert report.warning_count == 0
        assert report.info_count == 0
        assert report.summary == {"errors": 0, "warnings": 0, "infos": 0}

    def test_report_with_violations(self):
        from openalex_neo4j.data_quality import RuleViolation
        report = QualityReport(
            session_id="S1",
            total_entities=10,
            violations=[
                RuleViolation("r1", "W1", "Work", "error", "e1"),
                RuleViolation("r2", "A1", "Author", "warning", "w1"),
                RuleViolation("r3", "W2", "Work", "info", "i1"),
            ],
        )
        assert report.error_count == 1
        assert report.warning_count == 1
        assert report.info_count == 1

    def test_by_entity_type(self):
        from openalex_neo4j.data_quality import RuleViolation
        report = QualityReport(session_id="S1", violations=[
            RuleViolation("r1", "W1", "Work", "error", "e1"),
            RuleViolation("r2", "A1", "Author", "warning", "w1"),
            RuleViolation("r3", "W2", "Work", "info", "i1"),
        ])
        by_type = report.by_entity_type()
        assert len(by_type["Work"]) == 2
        assert len(by_type["Author"]) == 1


class TestQualityRules:
    """Tests for individual quality rules."""

    def test_missing_title_violation(self):
        rule = MissingTitleRule()
        work = Work(id="W1", title=None)
        v = rule.check(work)
        assert v is not None
        assert v.rule_name == "missing_title"

    def test_missing_title_ok(self):
        rule = MissingTitleRule()
        work = Work(id="W1", title="Good Title")
        assert rule.check(work) is None

    def test_missing_title_empty_string(self):
        rule = MissingTitleRule()
        work = Work(id="W1", title="")
        v = rule.check(work)
        assert v is not None

    def test_outlier_year_too_old(self):
        rule = OutlierYearRule(min_year=1900)
        work = Work(id="W1", title="Old", publication_year=1800)
        v = rule.check(work)
        assert v is not None

    def test_outlier_year_too_future(self):
        rule = OutlierYearRule(max_year=2099)
        work = Work(id="W1", title="Future", publication_year=3000)
        v = rule.check(work)
        assert v is not None

    def test_outlier_year_ok(self):
        rule = OutlierYearRule()
        work = Work(id="W1", title="Normal", publication_year=2023)
        assert rule.check(work) is None

    def test_outlier_year_none(self):
        rule = OutlierYearRule()
        work = Work(id="W1", title="No Year", publication_year=None)
        assert rule.check(work) is None

    def test_missing_abstract(self):
        rule = MissingAbstractRule()
        work = Work(id="W1", title="Test", abstract=None)
        assert rule.check(work) is not None

    def test_missing_abstract_ok(self):
        rule = MissingAbstractRule()
        work = Work(id="W1", title="Test", abstract="Has abstract")
        assert rule.check(work) is None

    def test_empty_entity_only_id(self):
        rule = EmptyEntityRule()
        work = Work(id="W1")
        v = rule.check(work)
        assert v is not None
        assert v.rule_name == "empty_entity"

    def test_empty_entity_with_data(self):
        rule = EmptyEntityRule()
        work = Work(id="W1", title="Real Paper", publication_year=2023)
        assert rule.check(work) is None

    def test_invalid_work_type(self):
        rule = InvalidWorkTypeRule()
        work = Work(id="W1", title="Test", type="not-a-real-type")
        v = rule.check(work)
        assert v is not None

    def test_valid_work_type(self):
        rule = InvalidWorkTypeRule()
        work = Work(id="W1", title="Test", type="article")
        assert rule.check(work) is None

    def test_short_title(self):
        rule = ShortTitleRule(min_length=10)
        work = Work(id="W1", title="Short")
        v = rule.check(work)
        assert v is not None

    def test_short_title_ok(self):
        rule = ShortTitleRule(min_length=5)
        work = Work(id="W1", title="Long enough title")
        assert rule.check(work) is None


class TestRuleCatalog:
    """Tests for RuleCatalog."""

    def test_register_defaults(self):
        catalog = RuleCatalog()
        catalog.register_defaults()
        assert len(catalog.list()) >= 7  # at least 7 built-in rules

    def test_get_for_entity(self):
        catalog = RuleCatalog()
        catalog.register_defaults()
        work_rules = catalog.get_for_entity("Work")
        assert len(work_rules) >= 5  # most rules apply to Work
        author_rules = catalog.get_for_entity("Author")
        assert len(author_rules) >= 2  # display_name + empty_entity


class TestDataQualityPipeline:
    """Tests for DataQualityPipeline."""

    def test_run_on_works(self):
        pipeline = DataQualityPipeline()
        entities = {
            "Work": [
                Work(id="W1", title="Good Paper", publication_year=2023),
                Work(id="W2", title=None),                           # missing title
                Work(id="W3", title="Old", publication_year=1800),   # outlier
            ],
        }
        report = pipeline.run("S1", entities)
        assert report.total_entities == 3
        # W2 missing title -> error
        assert report.error_count >= 1
        # W3 outlier year -> warning
        assert report.warning_count >= 1

    def test_run_on_author(self):
        pipeline = DataQualityPipeline()
        entities = {
            "Author": [
                Author(id="A1", display_name="John Doe"),
                Author(id="A2", display_name=None),  # missing name
            ],
        }
        report = pipeline.run("S1", entities)
        assert report.total_entities == 2
        assert report.error_count >= 1


class TestCleanEntityFields:
    """Tests for clean_entity_fields."""

    def test_strip_title_whitespace(self):
        work = Work(id="W1", title="  Hello World  ")
        changes = clean_entity_fields(work)
        assert work.title == "Hello World"
        assert "title" in changes

    def test_empty_title_to_none(self):
        work = Work(id="W1", title="   ")
        changes = clean_entity_fields(work)
        assert work.title is None
        assert "title" in changes

    def test_normalize_doi(self):
        work = Work(id="W1", title="Test", doi="https://doi.org/10.1234/abc")
        clean_entity_fields(work)
        assert work.doi == "10.1234/abc"

    def test_normalize_doi_short(self):
        work = Work(id="W1", title="Test", doi="doi:10.1234/abc")
        clean_entity_fields(work)
        assert work.doi == "10.1234/abc"

    def test_outlier_year_to_none(self):
        work = Work(id="W1", title="Test", publication_year=1800)
        clean_entity_fields(work)
        assert work.publication_year is None

    def test_good_year_unchanged(self):
        work = Work(id="W1", title="Test", publication_year=2023)
        clean_entity_fields(work)
        assert work.publication_year == 2023
