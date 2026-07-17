"""Article-text research records, normalization, and offline corpus import."""

from news_data.article.corpus import (
    ArticleCorpusProvider,
    ImportRejection,
    LocalArticleCorpusProvider,
    LocalCorpusImportResult,
)
from news_data.article.models import (
    CONTENT_SOURCES,
    CONTENT_STATUSES,
    SCHEMA_VERSION,
    ArticleTextRecordV1,
)
from news_data.article.normalize import (
    NORMALIZER_VERSION,
    normalize_text_for_hash,
    normalize_url,
    sha256_hex,
    stable_article_id,
)

__all__ = [
    "CONTENT_SOURCES",
    "CONTENT_STATUSES",
    "NORMALIZER_VERSION",
    "SCHEMA_VERSION",
    "ArticleCorpusProvider",
    "ArticleTextRecordV1",
    "ImportRejection",
    "LocalArticleCorpusProvider",
    "LocalCorpusImportResult",
    "normalize_text_for_hash",
    "normalize_url",
    "sha256_hex",
    "stable_article_id",
]
