"""Tests for research-only theme-code classification."""

from kinetic.processing.news.themes.classification import (
    GENERIC_NOISE,
    INDUSTRY_CORE,
    MANUFACTURING,
    POLICY_GEOPOLITICS,
    REQUIRES_RECORD_REVIEW,
    SUPPLY_CHAIN,
    TECHNOLOGY_INFRASTRUCTURE,
    classify_theme,
)


def test_generic_noise_flagged_and_reviewable() -> None:
    for code in ("TAX_ECON_PRICE", "EPU_ECONOMY_HISTORIC", "LEADER", "TAX_FNCACT_PRESIDENT"):
        c = classify_theme(code)
        assert c.category == GENERIC_NOISE
        assert c.generic_noise_flag is True
        assert c.manual_review_required is True


def test_supply_chain_and_manufacturing_and_ict() -> None:
    assert classify_theme("WB_698_TRADE").category == SUPPLY_CHAIN
    assert classify_theme("WB_1281_MANUFACTURING").category == MANUFACTURING
    assert (
        classify_theme("WB_133_INFORMATION_AND_COMMUNICATION_TECHNOLOGIES").category
        == TECHNOLOGY_INFRASTRUCTURE
    )


def test_policy_geopolitics() -> None:
    assert classify_theme("ARMEDCONFLICT").category == POLICY_GEOPOLITICS
    assert classify_theme("ECON_SANCTION").category == POLICY_GEOPOLITICS


def test_industry_core_hits_chip_tokens() -> None:
    c = classify_theme("ECON_SEMICONDUCTOR")
    assert c.category == INDUSTRY_CORE
    assert c.generic_noise_flag is False
    assert c.manual_review_required is False


def test_unknown_requires_record_review() -> None:
    c = classify_theme("WB_2024_SOMETHING_OBSCURE")
    assert c.category == REQUIRES_RECORD_REVIEW
    assert c.manual_review_required is True


def test_empty_code_is_review() -> None:
    c = classify_theme("")
    assert c.category == REQUIRES_RECORD_REVIEW


def test_to_dict_shape() -> None:
    d = classify_theme("WB_698_TRADE").to_dict()
    assert set(d) == {
        "classification",
        "classification_confidence",
        "classification_reasoning",
        "generic_noise_flag",
        "manual_review_required",
        "matched_rule",
    }


def test_matched_rule_names_the_firing_rule() -> None:
    # Supply-chain token, generic prefix, industry-core token, and no-rule cases
    # each surface a concrete, non-empty matched_rule (Phase 6 transparency).
    assert classify_theme("WB_698_TRADE").matched_rule.startswith("supply_chain_token:")
    assert classify_theme("TAX_FNCACT_PRESIDENT").matched_rule.startswith("generic_prefix:")
    assert classify_theme("TAX_ECON_PRICE").matched_rule == "generic_exact:TAX_ECON_PRICE"
    assert classify_theme("WB_SEMICONDUCTOR_X").matched_rule.startswith("industry_core_token:")
    assert classify_theme("ZZZ_UNKNOWN_9").matched_rule == "none"
