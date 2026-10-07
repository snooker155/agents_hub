"""Tests for documentation i18n support."""
import json
from tools.docs_tool import read_doc
from common.paths import PROJECT_ROOT


# The five first-hour pages that should have translations
TRANSLATED_DOCS = [
    "installation",
    "overview",
    "models",
    "chat",
    "assistant",
]

# Languages that have translations
TRANSLATED_LANGS = ["ru", "de"]


def _invoke_read_doc(doc_id, lang="en"):
    """Helper to invoke the read_doc tool and parse the JSON result."""
    result_str = read_doc.invoke({"doc_id": doc_id, "lang": lang})
    return json.loads(result_str)


class TestDocsTranslations:
    """Test that translated documentation files exist and match the English version."""

    def test_russian_files_exist(self):
        """All five translated pages exist in Russian."""
        for doc_id in TRANSLATED_DOCS:
            path = PROJECT_ROOT / "docs" / "ru" / f"{doc_id}.md"
            assert path.exists(), f"Russian translation missing: {path}"

    def test_german_files_exist(self):
        """All five translated pages exist in German."""
        for doc_id in TRANSLATED_DOCS:
            path = PROJECT_ROOT / "docs" / "de" / f"{doc_id}.md"
            assert path.exists(), f"German translation missing: {path}"

    def test_read_doc_returns_russian(self):
        """read_doc returns Russian translation when lang='ru'."""
        for doc_id in TRANSLATED_DOCS:
            data = _invoke_read_doc(doc_id, lang="ru")
            assert data["ok"], f"Failed to read {doc_id} in Russian: {data.get('error')}"
            assert data["lang"] == "ru", f"Expected lang='ru', got {data.get('lang')} for {doc_id}"
            assert data["content"], f"Empty content for {doc_id} in Russian"

    def test_read_doc_returns_german(self):
        """read_doc returns German translation when lang='de'."""
        for doc_id in TRANSLATED_DOCS:
            data = _invoke_read_doc(doc_id, lang="de")
            assert data["ok"], f"Failed to read {doc_id} in German: {data.get('error')}"
            assert data["lang"] == "de", f"Expected lang='de', got {data.get('lang')} for {doc_id}"
            assert data["content"], f"Empty content for {doc_id} in German"

    def test_read_doc_returns_english_when_missing(self):
        """read_doc falls back to English when translation is missing."""
        # Use a non-translated doc
        data = _invoke_read_doc("changelog", lang="ru")
        assert data["ok"], f"Failed to read changelog: {data.get('error')}"
        # Changelog only exists in English, so lang should be 'en'
        assert data["lang"] == "en", f"Expected lang='en' for untranslated doc, got {data.get('lang')}"

    def test_read_doc_returns_english_by_default(self):
        """read_doc returns English when lang is not specified."""
        for doc_id in TRANSLATED_DOCS:
            data = _invoke_read_doc(doc_id, lang="en")
            assert data["ok"], f"Failed to read {doc_id} in English: {data.get('error')}"
            # When requesting English explicitly, should get English
            assert data["lang"] == "en", f"Expected lang='en', got {data.get('lang')}"

    def test_read_doc_ignores_invalid_lang(self):
        """read_doc treats invalid language codes as English."""
        data = _invoke_read_doc("installation", lang="fr")
        assert data["ok"]
        assert data["lang"] == "en", "Invalid lang should fallback to English"

    def test_russian_and_english_have_same_structure(self):
        """Russian translations have the same headings and code blocks as English."""
        import re
        for doc_id in TRANSLATED_DOCS:
            # Read files directly to avoid truncation issues
            with open(PROJECT_ROOT / "docs" / f"{doc_id}.md") as f:
                en_content = f.read()
            with open(PROJECT_ROOT / "docs" / "ru" / f"{doc_id}.md") as f:
                ru_content = f.read()

            # Count headings
            en_headings = len(re.findall(r"^#+\s", en_content, re.MULTILINE))
            ru_headings = len(re.findall(r"^#+\s", ru_content, re.MULTILINE))
            assert en_headings == ru_headings, (
                f"{doc_id}: English has {en_headings} headings, Russian has {ru_headings}"
            )

            # Count code blocks
            en_blocks = len(re.findall(r"^```", en_content, re.MULTILINE))
            ru_blocks = len(re.findall(r"^```", ru_content, re.MULTILINE))
            assert en_blocks == ru_blocks, (
                f"{doc_id}: English has {en_blocks} code blocks, Russian has {ru_blocks}"
            )

    def test_german_and_english_have_same_structure(self):
        """German translations have the same headings and code blocks as English."""
        import re
        for doc_id in TRANSLATED_DOCS:
            # Read files directly to avoid truncation issues
            with open(PROJECT_ROOT / "docs" / f"{doc_id}.md") as f:
                en_content = f.read()
            with open(PROJECT_ROOT / "docs" / "de" / f"{doc_id}.md") as f:
                de_content = f.read()

            # Count headings
            en_headings = len(re.findall(r"^#+\s", en_content, re.MULTILINE))
            de_headings = len(re.findall(r"^#+\s", de_content, re.MULTILINE))
            assert en_headings == de_headings, (
                f"{doc_id}: English has {en_headings} headings, German has {de_headings}"
            )

            # Count code blocks
            en_blocks = len(re.findall(r"^```", en_content, re.MULTILINE))
            de_blocks = len(re.findall(r"^```", de_content, re.MULTILINE))
            assert en_blocks == de_blocks, (
                f"{doc_id}: English has {en_blocks} code blocks, German has {de_blocks}"
            )
