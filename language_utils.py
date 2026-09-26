from config import DOCUMENT_GENDER_ALIASES
from text_utils import canonical_gender

try:
    from unidecode import unidecode
except ImportError:
    unidecode = None


def romanize(value):
    value = (value or "").strip()
    if not value or unidecode is None:
        return ""
    result = " ".join(unidecode(value).split()).strip()
    return "" if not result or result.casefold() == value.casefold() else result


def english_relationship(value):
    raw = (value or "").strip()
    compact = raw.replace(" ", "").casefold()
    mapping = {
        "తం": "Father", "తండ్రి": "Father", "త": "Father",
        "భ": "Husband", "భర్త": "Husband", "తల్లి": "Mother", "మ": "Mother",
        "wife": "Wife", "husband": "Husband", "father": "Father", "mother": "Mother",
    }
    return mapping.get(compact, raw or "Unknown")


def english_gender(source, value):
    raw = (value or "").strip()
    normalized = DOCUMENT_GENDER_ALIASES.get(source, {}).get(raw, canonical_gender(raw))
    return normalized.title() if normalized in {"male", "female"} else (raw or "Unknown")
