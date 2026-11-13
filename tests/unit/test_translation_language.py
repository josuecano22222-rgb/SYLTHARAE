"""Language detection, and the user's override of it.

The corpus this installation holds is English, Arabic, Hebrew, Persian and
Croatian. Detection here is a script/stop-word heuristic on the text the
analyst selected - it decides which engine pair to ask for and which memory
rows to look in - and it is always overridable: a short selection ("Yes.")
carries no evidence of its language, so the analyst's declared language wins.
"""

from services.translation.language import (
    TRANSLATION_LANGUAGES,
    detect_language,
    resolve_languages,
)


def test_scripts_are_recognised_by_their_letters():
    assert detect_language("هل وصلت الرسالة؟") == "ar"
    assert detect_language("האם ההודעה הגיעה?") == "he"
    assert detect_language("آیا پیام رسید؟") == "fa"
    assert detect_language("The report was delivered yesterday.") == "en"


def test_croatian_is_separated_from_english_by_its_stop_words():
    assert detect_language("Molim vas da pošaljete izvješće što je prije moguće.") == "hr"
    assert detect_language("Please send the report as soon as possible.") == "en"


def test_a_short_selection_without_evidence_is_reported_as_unknown():
    language, confident = detect_language("42", with_confidence=True)
    assert language is None
    assert confident is False


def test_the_user_override_wins_and_is_recorded_as_an_override():
    source, detected, overridden = resolve_languages(
        "The report was delivered yesterday.", target_lang="he", source_lang="hr")
    assert source == "hr"
    assert detected == "en"
    assert overridden is True

    source, detected, overridden = resolve_languages(
        "The report was delivered yesterday.", target_lang="he")
    assert source == "en"
    assert detected == "en"
    assert overridden is False


def test_the_translation_vocabulary_covers_the_corpus_and_the_interface():
    """Content languages, not UI languages: Croatian is in the corpus and not
    in the sidebar, and the four interface languages must all be offerable."""
    assert set(TRANSLATION_LANGUAGES) == {"en", "ar", "he", "fa", "hr"}
    from settings.languages import SUPPORTED_LANGUAGES

    assert set(SUPPORTED_LANGUAGES) <= set(TRANSLATION_LANGUAGES)
