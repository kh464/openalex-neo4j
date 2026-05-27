"""OpenAlex client for fetching scholarly data."""

import logging
from typing import Any

import pyalex
from pyalex import Works, Authors, Institutions, Sources, Topics, Publishers, Funders

from .models import Work, Author, Institution, Source, Topic, Publisher, Funder
from .rate_limiter import TokenBucket

logger = logging.getLogger(__name__)


class OpenAlexClient:
    """Client for fetching data from OpenAlex API.

    Supports optional rate limiting via a shared ``TokenBucket`` so that
    concurrent callers respect the polite-pool limit (~10 req/s).
    """

    def __init__(self, email: str | None = None,
                 rate_limiter: TokenBucket | None = None):
        """Initialize OpenAlex client.

        Args:
            email: Email for polite pool access (recommended).
            rate_limiter: Optional ``TokenBucket`` for rate limiting.  When
                ``None`` (the default), no throttling is applied.
        """
        if email:
            pyalex.config.email = email
            logger.info(f"Configured OpenAlex with email: {email}")
        else:
            logger.warning("No email configured for OpenAlex polite pool")
        self.rate_limiter = rate_limiter

    def _rate_limit(self) -> None:
        """Block until a rate-limiter token is available (no-op if unset)."""
        if self.rate_limiter:
            self.rate_limiter.acquire()

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

    def search_works(
        self, query: str, limit: int | None = None,
        from_year: int | None = None, to_year: int | None = None,
        work_types: list[str] | tuple[str, ...] | None = None,
    ) -> list[Work]:
        """Search for works matching query, optionally filtered by year/type.

        Args:
            query: Search query string
            limit: Maximum number of works to return, or ``None`` for all.
            from_year: Optional start year (inclusive), passed as from_publication_date
            to_year: Optional end year (inclusive), passed as to_publication_date
            work_types: Optional OpenAlex work types (OR filter), e.g.
                ``["article", "review"]``.

        Returns:
            List of Work objects
        """
        normalized_work_types = self._normalize_work_types(work_types)
        logger.info(
            f"Searching for works: '{query}' (limit={'all' if limit is None else limit}"
            f"{f', from={from_year}' if from_year else ''}"
            f"{f', to={to_year}' if to_year else ''}"
            f"{f', types={normalized_work_types}' if normalized_work_types else ''})"
        )

        works = []
        try:
            # Build the query chain with optional date filters
            request = Works().search(query)
            if from_year:
                request = request.filter(from_publication_date=f"{from_year}-01-01")
            if to_year:
                request = request.filter(to_publication_date=f"{to_year}-12-31")
            if normalized_work_types:
                request = request.filter(type="|".join(normalized_work_types))

            per_page = 200 if limit is None else min(200, limit)
            # Pass n_max explicitly so PyAlex's default 10,000-result cap
            # does not apply when limit is omitted or set above 10,000.
            pager = request.paginate(per_page=per_page, n_max=limit)

            for page in pager:
                self._rate_limit()
                for work_data in page:
                    try:
                        work = Work.from_openalex(work_data)
                        works.append(work)

                        if limit is not None and len(works) >= limit:
                            break
                    except Exception as e:
                        logger.warning(f"Failed to parse work: {e}")

                if limit is not None and len(works) >= limit:
                    break

        except Exception as e:
            logger.error(f"Failed to search works: {e}")

        logger.info(f"Found {len(works)} works")
        return works

    def count_works(
        self, query: str,
        from_year: int | None = None, to_year: int | None = None,
        work_types: list[str] | tuple[str, ...] | None = None,
    ) -> int:
        """Get total count of works matching a search query (no data fetch).

        Args:
            query: Search query string
            from_year: Optional start year (inclusive)
            to_year: Optional end year (inclusive)
            work_types: Optional OpenAlex work types (OR filter), e.g.
                ``["article", "review"]``.

        Returns:
            Total matching work count from OpenAlex meta, or 0 on error.
        """
        normalized_work_types = self._normalize_work_types(work_types)
        logger.info(f"Counting works for: '{query}'")
        try:
            request = Works().search(query)
            if from_year:
                request = request.filter(from_publication_date=f"{from_year}-01-01")
            if to_year:
                request = request.filter(to_publication_date=f"{to_year}-12-31")
            if normalized_work_types:
                request = request.filter(type="|".join(normalized_work_types))
            self._rate_limit()
            result = request.get()
            count = result.meta["count"]
            logger.info(f"Count result: {count:,}")
            return count
        except Exception as e:
            logger.error(f"Failed to count works: {e}")
            return 0

    def fetch_works_by_ids(self, work_ids: list[str]) -> list[Work]:
        """Fetch works by their OpenAlex IDs.

        Args:
            work_ids: List of work IDs (e.g., ['W123', 'W456'])

        Returns:
            List of Work objects
        """
        if not work_ids:
            return []

        logger.info(f"Fetching {len(work_ids)} works by ID")
        works = []

        # Fetch in batches to avoid hitting API limits
        batch_size = 50
        for i in range(0, len(work_ids), batch_size):
            batch_ids = work_ids[i:i + batch_size]
            try:
                # Filter by multiple IDs using OR
                filter_str = "|".join(f"https://openalex.org/{wid}" for wid in batch_ids)
                self._rate_limit()
                results = Works().filter(openalex_id=filter_str).get()

                for work_data in results:
                    try:
                        work = Work.from_openalex(work_data)
                        works.append(work)
                    except Exception as e:
                        logger.warning(f"Failed to parse work: {e}")

            except Exception as e:
                logger.error(f"Failed to fetch works batch: {e}")

        logger.info(f"Fetched {len(works)} works")
        return works

    def fetch_authors_by_ids(self, author_ids: list[str]) -> list[Author]:
        """Fetch authors by their OpenAlex IDs.

        Args:
            author_ids: List of author IDs (e.g., ['A123', 'A456'])

        Returns:
            List of Author objects
        """
        if not author_ids:
            return []

        logger.info(f"Fetching {len(author_ids)} authors by ID")
        authors = []

        batch_size = 50
        for i in range(0, len(author_ids), batch_size):
            batch_ids = author_ids[i:i + batch_size]
            try:
                filter_str = "|".join(f"https://openalex.org/{aid}" for aid in batch_ids)
                self._rate_limit()
                results = Authors().filter(openalex_id=filter_str).get()

                for author_data in results:
                    try:
                        author = Author.from_openalex(author_data)
                        authors.append(author)
                    except Exception as e:
                        logger.warning(f"Failed to parse author: {e}")

            except Exception as e:
                logger.error(f"Failed to fetch authors batch: {e}")

        logger.info(f"Fetched {len(authors)} authors")
        return authors

    def fetch_institutions_by_ids(self, institution_ids: list[str]) -> list[Institution]:
        """Fetch institutions by their OpenAlex IDs.

        Args:
            institution_ids: List of institution IDs (e.g., ['I123', 'I456'])

        Returns:
            List of Institution objects
        """
        if not institution_ids:
            return []

        logger.info(f"Fetching {len(institution_ids)} institutions by ID")
        institutions = []

        batch_size = 50
        for i in range(0, len(institution_ids), batch_size):
            batch_ids = institution_ids[i:i + batch_size]
            try:
                filter_str = "|".join(f"https://openalex.org/{iid}" for iid in batch_ids)
                self._rate_limit()
                results = Institutions().filter(openalex_id=filter_str).get()

                for inst_data in results:
                    try:
                        institution = Institution.from_openalex(inst_data)
                        institutions.append(institution)
                    except Exception as e:
                        logger.warning(f"Failed to parse institution: {e}")

            except Exception as e:
                logger.error(f"Failed to fetch institutions batch: {e}")

        logger.info(f"Fetched {len(institutions)} institutions")
        return institutions

    def fetch_sources_by_ids(self, source_ids: list[str]) -> list[Source]:
        """Fetch sources by their OpenAlex IDs.

        Args:
            source_ids: List of source IDs (e.g., ['S123', 'S456'])

        Returns:
            List of Source objects
        """
        if not source_ids:
            return []

        logger.info(f"Fetching {len(source_ids)} sources by ID")
        sources = []

        batch_size = 50
        for i in range(0, len(source_ids), batch_size):
            batch_ids = source_ids[i:i + batch_size]
            try:
                filter_str = "|".join(f"https://openalex.org/{sid}" for sid in batch_ids)
                self._rate_limit()
                results = Sources().filter(openalex_id=filter_str).get()

                for source_data in results:
                    try:
                        source = Source.from_openalex(source_data)
                        sources.append(source)
                    except Exception as e:
                        logger.warning(f"Failed to parse source: {e}")

            except Exception as e:
                logger.error(f"Failed to fetch sources batch: {e}")

        logger.info(f"Fetched {len(sources)} sources")
        return sources

    def fetch_topics_by_ids(self, topic_ids: list[str]) -> list[Topic]:
        """Fetch topics by their OpenAlex IDs.

        Args:
            topic_ids: List of topic IDs (e.g., ['T123', 'T456'])

        Returns:
            List of Topic objects
        """
        if not topic_ids:
            return []

        logger.info(f"Fetching {len(topic_ids)} topics by ID")
        topics = []

        batch_size = 50
        for i in range(0, len(topic_ids), batch_size):
            batch_ids = topic_ids[i:i + batch_size]
            try:
                filter_str = "|".join(f"https://openalex.org/{tid}" for tid in batch_ids)
                self._rate_limit()
                results = Topics().filter(openalex=filter_str).get()

                for topic_data in results:
                    try:
                        topic = Topic.from_openalex(topic_data)
                        topics.append(topic)
                    except Exception as e:
                        logger.warning(f"Failed to parse topic: {e}")

            except Exception as e:
                logger.error(f"Failed to fetch topics batch: {e}")

        logger.info(f"Fetched {len(topics)} topics")
        return topics

    def fetch_publishers_by_ids(self, publisher_ids: list[str]) -> list[Publisher]:
        """Fetch publishers by their OpenAlex IDs.

        Args:
            publisher_ids: List of publisher IDs (e.g., ['P123', 'P456'])

        Returns:
            List of Publisher objects
        """
        if not publisher_ids:
            return []

        logger.info(f"Fetching {len(publisher_ids)} publishers by ID")
        publishers = []

        batch_size = 50
        for i in range(0, len(publisher_ids), batch_size):
            batch_ids = publisher_ids[i:i + batch_size]
            try:
                filter_str = "|".join(f"https://openalex.org/{pid}" for pid in batch_ids)
                self._rate_limit()
                results = Publishers().filter(openalex_id=filter_str).get()

                for pub_data in results:
                    try:
                        publisher = Publisher.from_openalex(pub_data)
                        publishers.append(publisher)
                    except Exception as e:
                        logger.warning(f"Failed to parse publisher: {e}")

            except Exception as e:
                logger.error(f"Failed to fetch publishers batch: {e}")

        logger.info(f"Fetched {len(publishers)} publishers")
        return publishers

    def fetch_funders_by_ids(self, funder_ids: list[str]) -> list[Funder]:
        """Fetch funders by their OpenAlex IDs.

        Args:
            funder_ids: List of funder IDs (e.g., ['F123', 'F456'])

        Returns:
            List of Funder objects
        """
        if not funder_ids:
            return []

        logger.info(f"Fetching {len(funder_ids)} funders by ID")
        funders = []

        batch_size = 50
        for i in range(0, len(funder_ids), batch_size):
            batch_ids = funder_ids[i:i + batch_size]
            try:
                filter_str = "|".join(f"https://openalex.org/{fid}" for fid in batch_ids)
                self._rate_limit()
                results = Funders().filter(openalex_id=filter_str).get()

                for funder_data in results:
                    try:
                        funder = Funder.from_openalex(funder_data)
                        funders.append(funder)
                    except Exception as e:
                        logger.warning(f"Failed to parse funder: {e}")

            except Exception as e:
                logger.error(f"Failed to fetch funders batch: {e}")

        logger.info(f"Fetched {len(funders)} funders")
        return funders
