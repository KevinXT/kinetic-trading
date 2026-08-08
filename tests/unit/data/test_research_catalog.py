"""Tests for the feature catalog config and loader."""

from kinetic.data.catalog.features import CATEGORIES, load_feature_catalog

CATALOG_PATH = "configs/research/news_market_feature_catalog.yaml"


def test_catalog_loads_and_is_versioned() -> None:
    catalog = load_feature_catalog(CATALOG_PATH)
    assert catalog.catalog_version == "news-market-catalog-v2"
    assert len(catalog.features) > 30


def test_every_feature_has_valid_category_and_status() -> None:
    catalog = load_feature_catalog(CATALOG_PATH)
    for feature in catalog.features:
        assert feature.category in CATEGORIES
        assert feature.implementation_status in {
            "core_v1",
            "v1_optional",
            "future",
            "unsupported",
        }


def test_catalog_names_are_unique() -> None:
    catalog = load_feature_catalog(CATALOG_PATH)
    names = [f.name for f in catalog.features]
    assert len(names) == len(set(names))


def test_lag_only_features_declare_no_current_inclusion() -> None:
    catalog = load_feature_catalog(CATALOG_PATH)
    for name in ("news_attention_zscore_7", "news_attention_zscore_30"):
        feature = catalog.by_name(name)
        assert feature is not None
        assert feature.includes_current is False
        assert feature.category == "input"


def test_targets_and_inputs_are_disjoint_categories() -> None:
    catalog = load_feature_catalog(CATALOG_PATH)
    inputs = catalog.names_for_category("input")
    targets = catalog.names_for_category("target")
    assert inputs.isdisjoint(targets)


def test_catalog_to_dict_is_sorted() -> None:
    catalog = load_feature_catalog(CATALOG_PATH)
    payload = catalog.to_dict()
    names = [f["name"] for f in payload["features"]]
    assert names == sorted(names)


def test_hypothesis_metadata_distinguishes_confirmatory_and_exploratory() -> None:
    catalog = load_feature_catalog(CATALOG_PATH)
    h1 = catalog.by_name("news_attention_zscore_30")
    h4 = catalog.by_name("inter_attn30_x_prior_vol")
    assert h1 is not None and h1.hypothesis_class == "confirmatory"
    assert h1.hypothesis_id == "H1"
    assert h4 is not None and h4.hypothesis_class == "exploratory"
    assert h4.hypothesis_id == "H4"


def test_current_catalog_has_no_unreleased_v1_schema_names() -> None:
    names = {feature.name for feature in load_feature_catalog(CATALOG_PATH).features}
    stale = {
        "news_canonical_article_count",
        "next_session_return",
        "fwd_5_cum_return",
        "fwd_5_cum_abnormal_return",
    }
    assert names.isdisjoint(stale)
    assert "news_title_deduplicated_article_count" in names
    assert "target_session_return" in names
