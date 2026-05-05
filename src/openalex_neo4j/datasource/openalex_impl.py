"""OpenAlex itself as a data source (for re-fetching to get missing fields)."""
import logging

import pyalex
from pyalex import Works

from .base import DataSource, DataRecord
from ..models import Work

logger = logging.getLogger(__name__)


class OpenAlexSource(DataSource):
    """Use OpenAlex API as a data source for enrichment.

    Useful for re-fetching a work by ID to get fields that were
    missed or stripped in the initial import (e.g., abstracts).
    """

    def __init__(self, email: str | None = None):
        if email:
            pyalex.config.email = email

    @property
    def name(self) -> str:
        return "openalex"

    def fetch_by_openalex_id(self, openalex_id: str) -> DataRecord | None:
        """Re-fetch a work from OpenAlex by ID."""
        try:
            works = Works().filter(openalex_id=f"https://openalex.org/{openalex_id}").get()
            if not works:
                return None
            data = works[0]
            work = Work.from_openalex(data)
            return self._work_to_record(work, data)
        except Exception as e:
            logger.warning(f"OpenAlex fetch failed for {openalex_id}: {e}")
            return None

    def fetch_by_doi(self, doi: str) -> DataRecord | None:
        try:
            results = Works().filter(doi=doi).get()
            if not results:
                return None
            data = results[0]
            work = Work.from_openalex(data)
            return self._work_to_record(work, data)
        except Exception as e:
            logger.warning(f"OpenAlex fetch by DOI failed for {doi}: {e}")
            return None

    def _work_to_record(self, work: Work, raw_data: dict) -> DataRecord:
        return DataRecord(
            source_name=self.name,
            source_confidence=1.0,
            openalex_id=work.id,
            external_ids={"doi": work.doi} if work.doi else {},
            raw_data=raw_data,
            title=work.title,
            abstract=work.abstract,
            publication_date=work.publication_date,
            doi=work.doi,
        )

    def confidence(self, record: DataRecord) -> float:
        return 1.0  # OpenAlex is the source of truth

    def to_openalex_id(self, record: DataRecord) -> str | None:
        return record.openalex_id
