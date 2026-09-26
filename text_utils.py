import re
import unicodedata

try:
    from unidecode import unidecode
except ImportError:
    unidecode = None

from config import MIN_NATIVE_TEXT_LENGTH


COMMON_LATIN_EXTENDED = set(
    "äöüÄÖÜß"
    "áàâãåÁÀÂÃÅ"
    "éèêëÉÈÊË"
    "íìîïÍÌÎÏ"
    "óòôõÓÒÔÕ"
    "úùûÚÙÛ"
    "çÇñÑ"
)


def normalize_unicode(text):
    return unicodedata.normalize("NFKC", text or "").strip()


def native_text_is_good(text):
    """Reject broken/legacy-encoded PDF text before it reaches the index."""
    if not text:
        return False

    text = text.strip()
    if len(text) < MIN_NATIVE_TEXT_LENGTH:
        return False

    total = max(1, len(text))
    if text.count("\ufffd"):
        return False

    control_count = 0
    strange_symbol_count = 0
    suspicious_latin_count = 0
    letter_count = 0

    for char in text:
        category = unicodedata.category(char)

        if category.startswith("C"):
            control_count += 1

        if category.startswith("L"):
            letter_count += 1
            code = ord(char)
            if 0x0100 <= code <= 0x024F and char not in COMMON_LATIN_EXTENDED:
                suspicious_latin_count += 1

        if category.startswith("S") and char not in {"€", "$", "£", "₹", "%", "+", "="}:
            strange_symbol_count += 1

    if control_count / total > 0.01:
        return False
    if strange_symbol_count / total > 0.08:
        return False
    if letter_count and suspicious_latin_count / letter_count > 0.03:
        return False

    gibberish_patterns = [
        r"d3D",
        r"73MV",
        r"\[\w-\]",
        r"[A-Z]{2}\d{5,}",
    ]
    if sum(len(re.findall(pattern, text)) for pattern in gibberish_patterns) >= 5:
        return False

    return True


def char_script(char):
    code = ord(char)
    if 0x0041 <= code <= 0x024F:
        return "latin"
    if 0x0600 <= code <= 0x06FF:
        return "arabic"
    if 0x0900 <= code <= 0x097F:
        return "devanagari"
    if 0x0C00 <= code <= 0x0C7F:
        return "telugu"
    if 0x0C80 <= code <= 0x0CFF:
        return "kannada"
    return "other"


def detect_script(text):
    counts = {}
    for char in normalize_unicode(text):
        if not char.isalpha():
            continue
        script = char_script(char)
        counts[script] = counts.get(script, 0) + 1
    if not counts:
        return "other"
    return max(counts, key=counts.get)


def phonetic_key(text):
    """Conservative deterministic key for cross-script personal-name matching."""
    if unidecode is None:
        raise RuntimeError(
            "Cross-script name matching requires Unidecode.\n"
            "Install it with:\n\n    pip install Unidecode\n"
        )

    value = unidecode(normalize_unicode(text)).casefold()
    value = re.sub(r"[^a-z0-9]+", "", value)
    if not value:
        return ""

    for old, new in (
        ("ph", "f"),
        ("gh", "g"),
        ("kh", "k"),
        ("bh", "b"),
        ("dh", "d"),
        ("th", "t"),
        ("ck", "k"),
        ("q", "k"),
    ):
        value = value.replace(old, new)

    if len(value) >= 5 and value.endswith("e"):
        value = value[:-1]

    value = value.replace("ou", "au")
    value = value.replace("ow", "au")
    value = value.replace("aw", "au")
    value = re.sub(r"aa+", "a", value)
    value = re.sub(r"ee+", "i", value)
    value = re.sub(r"ii+", "i", value)
    value = re.sub(r"oo+", "u", value)
    value = re.sub(r"uu+", "u", value)
    # Unidecode may double an Indic consonant even when common Latin spelling
    # uses one (for example కళావతి -> kllavti, Kalavathi -> kalavati).
    value = re.sub(r"([bcdfghjklmnpqrstvwxyz])\1+", r"\1", value)
    return value


def unicode_words(text):
    text = normalize_unicode(text)
    # Dots and dashes separate initials from the personal name. Keeping them
    # inside a token collapses `P. Haseena` into incompatible phonetic keys.
    return re.findall(r"[\w\u0600-\u06FF\u0900-\u097F\u0C00-\u0C7F\u0C80-\u0CFF'’]+", text, flags=re.UNICODE)


def name_token_keys(text):
    keys = []
    for token in unicode_words(text):
        key = phonetic_key(token)
        if key:
            keys.append(key)
    return keys


def _levenshtein(a, b):
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    previous = list(range(len(b) + 1))
    for i, char_a in enumerate(a, start=1):
        current = [i]
        for j, char_b in enumerate(b, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[j] + 1,
                    previous[j - 1] + (char_a != char_b),
                )
            )
        previous = current
    return previous[-1]


def _consonant_key(value):
    """Reduce a Latin phonetic key to its consonant sequence."""
    return re.sub(r"[aeiou]", "", value or "")


def _nasal_consonant_variant(left, right):
    """Accept one n/m transliteration variation, but no general fuzzy swap."""
    if len(left) != len(right):
        return False
    differences = [(a, b) for a, b in zip(left, right) if a != b]
    return len(differences) == 1 and set(differences[0]) == {"m", "n"}


def _token_equivalent(candidate_token, target_token):
    candidate_key = phonetic_key(candidate_token)
    target_key = phonetic_key(target_token)
    if not candidate_key or not target_key:
        return False
    if candidate_key == target_key:
        return True

    # Indic consonant initials transliterate with an inherent vowel: Telugu
    # `పి` becomes `pi`, while the user writes the Latin initial `P`.
    # Apply this only to a single-letter Latin target token.
    if len(target_key) == 1 and target_key.isalpha() and len(candidate_key) <= 2:
        if candidate_key.startswith(target_key) and detect_script(candidate_token) != detect_script(target_token):
            return True

    # Indic electoral rolls commonly prefix names with dotted initials. OCR
    # and transliteration can collapse `కె.అసీఫ్` into `keasif` and
    # `పి.యమ్.డి.ఆసిఫ్` into `piymddiasif`. Match the final name only when the
    # original token visibly contains a dot and the extra prefix is short.
    if "." in candidate_token and candidate_key.endswith(target_key):
        prefix = candidate_key[:-len(target_key)] if target_key else candidate_key
        if 0 < len(prefix) <= 8:
            return True

    # An undotted leading character is part of the name, not an initial:
    # Vasif must not be treated as Asif merely because the edit distance is 1.
    if "." not in candidate_token and candidate_key.endswith(target_key) and candidate_key != target_key:
        return False

    # OCR sometimes drops one vowel sign in Indic names. Permit one phonetic
    # edit only for cross-script matching on reasonably long tokens. This is
    # intentionally narrower than generic fuzzy search.
    if detect_script(candidate_token) != detect_script(target_token):
        if max(len(candidate_key), len(target_key)) >= 5:
            if _levenshtein(candidate_key, target_key) <= 1:
                candidate_consonants = _consonant_key(candidate_key)
                target_consonants = _consonant_key(target_key)
                # A missing/extra vowel is a common transliteration variation.
                # A changed consonant usually creates a different personal
                # name (for example Naseem/Nasir/Nazim), so do not fuzzy-match it.
                if (
                    candidate_consonants == target_consonants
                    or _nasal_consonant_variant(candidate_consonants, target_consonants)
                ):
                    return True

        # Longer Indic names can differ by more than one Latin vowel even
        # when they are the same name. Example: Telugu `నసీంబాను` becomes
        # `nsinbanu`, while a user types `Naseembanu` -> `nasimbanu`.
        # Require both long full keys and nearly identical consonant frames;
        # this does not let a short prefix such as `Naseem` match
        # `Naseembanu`.
        if min(len(candidate_key), len(target_key)) >= 6:
            candidate_consonants = _consonant_key(candidate_key)
            target_consonants = _consonant_key(target_key)
            if abs(len(candidate_key) - len(target_key)) <= 2:
                return (
                    candidate_consonants == target_consonants
                    or _nasal_consonant_variant(candidate_consonants, target_consonants)
                )
    return False


def count_target_in_name(name, target):
    """Count a name/partial name in one field using conservative cross-script matching."""
    target_tokens = unicode_words(target)
    name_tokens = unicode_words(name)
    if not target_tokens or not name_tokens:
        return 0

    size = len(target_tokens)
    count = 0
    for index in range(0, len(name_tokens) - size + 1):
        window = name_tokens[index:index + size]
        if all(_token_equivalent(candidate, wanted) for candidate, wanted in zip(window, target_tokens)):
            count += 1
    return count


def clean_ocr_text(text):
    text = normalize_unicode(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" |\t\r\n")


def clean_numeric(text):
    value = re.sub(r"\D+", "", text or "")
    return value


def clean_identifier(text):
    value = re.sub(r"[^A-Za-z0-9/-]+", "", text or "")
    return value.upper()


def canonical_gender(text):
    """Map OCR/multilingual gender labels to male/female when confidently recognizable."""
    value = normalize_unicode(text).casefold().strip()
    value = re.sub(r"[\s.,:;|]+", "", value)
    if not value:
        return "unknown"

    male_aliases = {
        "m", "male", "man", "men", "పు", "పురుష", "పురుషుడు", "మగ", "पुरुष", "ಪು", "ಪುರುಷ", "ذكر", "männlich", "mannlich"
    }
    female_aliases = {
        "f", "female", "woman", "women", "స్త్రీ", "మహిళ", "स्त्री", "महिला", "ಸ್ತ್ರೀ", "ಮಹಿಳೆ", "أنثى", "انثى", "weiblich"
    }
    if value in male_aliases or any(value.startswith(x) for x in ["పురుష", "पुरुष", "ಪುರುಷ"]):
        return "male"
    if value in female_aliases or any(value.startswith(x) for x in ["స్త్రీ", "మహిళ", "महिला", "ಸ್ತ್ರೀ", "ಮಹಿಳ"]):
        return "female"

    # Conservative transliteration fallback.
    try:
        key = phonetic_key(value)
    except Exception:
        key = ""
    if key in {"pu", "purush", "purushudu", "maga", "male", "man", "dhkr", "mannlich"}:
        return "male"
    if key in {"stri", "mahila", "female", "woman", "antha", "weiblich"}:
        return "female"
    return "unknown"
