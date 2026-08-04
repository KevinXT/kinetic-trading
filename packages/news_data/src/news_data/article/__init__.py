"""Article-text research records, normalization, and offline corpus import."""

from news_data.article.corpus import (
    SAFE_REJECTION_SCHEMA_VERSION,
    ArticleCorpusProvider,
    ImportRejection,
    LocalArticleCorpusProvider,
    LocalCorpusImportResult,
    SafeImportRejectionV1,
    build_safe_import_rejection,
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
    "SAFE_REJECTION_SCHEMA_VERSION",
    "SCHEMA_VERSION",
    "ArticleCorpusProvider",
    "ArticleTextRecordV1",
    "ImportRejection",
    "LocalArticleCorpusProvider",
    "LocalCorpusImportResult",
    "SafeImportRejectionV1",
    "build_safe_import_rejection",
    "normalize_text_for_hash",
    "normalize_url",
    "sha256_hex",
    "stable_article_id",
]
