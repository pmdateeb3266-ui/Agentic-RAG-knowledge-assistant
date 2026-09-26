import re
import unicodedata

from config import PREFERRED_OUTPUT_LANGUAGE

# v4: query planner for deterministic structured filters/aggregates plus semantic RAG.

DASH_TRANSLATION = str.maketrans({
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "―": "-", "−": "-",
})


def normalize_identifier_text(text):
    return unicodedata.normalize("NFKC", text or "").translate(DASH_TRANSLATION).strip()


def norm(text):
    return normalize_identifier_text(text).casefold().strip()


def detect_language(question):
    if re.search(r"[\u0600-\u06FF]", question):
        return "ar"
    if re.search(r"[\u0900-\u097F]", question):
        return "hi"
    if re.search(r"[\u0C80-\u0CFF]", question):
        return "kn"
    if re.search(r"[\u0C00-\u0C7F]", question):
        return "te"
    lowered = norm(question)
    if any(x in f" {lowered} " for x in [" wie ", " und ", " der ", " die ", " das ", "erwäh", "ä", "ö", "ü", "ß"]):
        return "de"
    return "en"


def detect_requested_output_language(question, detected_language):
    """Honor an explicit response-language request independently of input script."""
    text = norm(question)
    language_patterns = [
        ("en", [r"\bin english\b", r"\banswer (?:me )?in english\b", r"\benglish answer\b"]),
        ("te", [r"\bin telugu\b", r"\btelugu answer\b", r"తెలుగులో"]),
        ("hi", [r"\bin hindi\b", r"\bhindi answer\b", r"हिंदी में"]),
        ("kn", [r"\bin kannada\b", r"\bkannada answer\b", r"ಕನ್ನಡದಲ್ಲಿ"]),
        ("de", [r"\bin german\b", r"\bauf deutsch\b"]),
        ("ar", [r"\bin arabic\b", r"\bبالعربية\b"]),
    ]
    for language, patterns in language_patterns:
        if any(re.search(pattern, text, flags=re.I | re.U) for pattern in patterns):
            return language, True
    if PREFERRED_OUTPUT_LANGUAGE in {"en", "te", "hi", "kn", "de", "ar"}:
        return PREFERRED_OUTPUT_LANGUAGE, False
    return detected_language, False


COUNT_MARKERS = {
    "en": ["how many", "how many times", "number of", "count", "how often"],
    "de": ["wie viele", "wie oft", "anzahl"],
    "hi": ["कितने", "कितनी", "कितनी बार", "गिनती"],
    "kn": ["ಎಷ್ಟು", "ಎಷ್ಟು ಬಾರಿ", "ಎಣಿಕೆ"],
    "te": ["ఎన్ని", "ఎన్ని సార్లు", "ఎన్నిసార్లు", "ఎంత మంది"],
    "ar": ["كم", "كم مرة", "عدد"],
}
LIST_MARKERS = {
    "en": ["show", "list", "give me", "find", "who", "which", "all"],
    "de": ["zeige", "liste", "finde", "wer", "alle"],
    "hi": ["दिखाओ", "सूची", "कौन", "सभी"],
    "kn": ["ತೋರಿಸಿ", "ಪಟ್ಟಿ", "ಯಾರು", "ಎಲ್ಲಾ"],
    "te": ["చూపించు", "జాబితా", "ఎవరు", "అందరూ", "అన్ని"],
    "ar": ["اعرض", "قائمة", "من", "كل"],
}
SUMMARY_MARKERS = ["summarize", "summary", "summarise", "overview", "సారాంశం", "सारांश", "ಸಾರಾಂಶ", "ملخص", "zusammenfassung"]
COMPARE_MARKERS = ["compare", "comparison", "difference between", " vs ", " versus ", "పోల్చ", "तुलना", "ಹೋಲಿಸಿ", "قارن", "vergleiche"]

# These words describe a query or a class of records; they are not personal
# names. Keep this vocabulary independent of particular values so phrases
# such as "People age between 21 and 37" and "Residents of house 10/221"
# work for any age range or house identifier.
GENERIC_QUERY_WORDS = {
    "address", "addresses", "all", "article", "articles", "booth", "booths",
    "a", "an", "biggest", "category", "categories", "clause", "clauses", "complete", "content",
    "data", "date", "dates", "deadline", "deadlines", "detail", "details",
    "document", "documents", "entity", "entities", "figure", "figures",
    "file", "files", "heading", "headings", "house", "houses", "information",
    "every", "key", "keyword", "keywords", "largest", "location", "locations", "mention", "mentions",
    "overview", "pdf", "pdfs", "people", "person", "persons", "point", "points", "polling",
    "record", "records", "reference", "references", "resident", "residents",
    "populated", "section", "sections", "summary", "table", "tables", "text", "the", "theme",
    "themes", "topic", "topics", "voter", "voters",
}


def _looks_like_generic_query_target(value):
    """Reject commands/content requests that are not plausible name targets."""
    cleaned = norm(value)
    words = re.findall(r"[a-z]+", cleaned)
    if not words:
        return True
    if any(char.isdigit() for char in cleaned):
        return True
    if words[0] in GENERIC_QUERY_WORDS:
        return True
    return all(word in GENERIC_QUERY_WORDS for word in words)


def extract_structured_entity(question):
    """Identify document-level entities that require deterministic execution."""
    text = norm(question).replace("\u200c", "").replace("\u200d", "")
    booth_patterns = [
        r"\bpolling\s+(?:booths?|stations?|centers?|centres?)\b",
        r"\bbooths?\b",
        r"పోలింగ్\s*కేంద్ర",
        r"मतदान\s*(?:केंद्र|केन्द्र)",
        r"(?:ಮತಗಟ್ಟೆ|ಮತದಾನ\s*ಕೇಂದ್ರ)",
        r"\b(?:wahllokale?|wahlbezirke?)\b",
        r"(?:مركز|محطة|مراكز|محطات)\s*الاقتراع",
    ]
    if any(re.search(pattern, text, flags=re.I | re.U) for pattern in booth_patterns):
        voter_subject = re.search(
            r"\b(?:voters?|people|persons?|age|house|epic|serial)\b|ఓటరు|ఓటర్లు|मतदाता|ಮತದಾರ",
            text,
            flags=re.I | re.U,
        )
        header_metric = re.search(r"\b(?:header|official)\b.*\bvoters?\b|\bvoter\s+totals?\b|ఓటర్ల\s*సంఖ్య", text, flags=re.I | re.U)
        if not voter_subject or header_metric:
            return "polling_booth"

    document_noun = re.search(r"\b(?:documents?|pdfs?|files?)\b", text, flags=re.I)
    document_metadata = re.search(
        r"\b(?:document\s+names?|pdf\s+names?|file\s+names?|"
        r"page\s+counts?|pages\s+per|number\s+of\s+pages|total\s+pages|most\s+pages|fewest\s+pages|"
        r"record\s+counts?|records\s+per|most\s+(?:structured\s+)?records|fewest\s+(?:structured\s+)?records|"
        r"ocr\s+languages?|language\s+hints?|index(?:ed|ing)?\s+dates?)\b",
        text,
        flags=re.I,
    )
    explicit_document_count_or_list = re.search(
        # The document noun must be the thing being counted/listed.  A PDF
        # filename in "how many voters are in x.pdf" is only a scope and must
        # not turn the voter query into a document-metadata query.
        r"\b(?:how\s+many|count|number\s+of)\s+(?:indexed\s+)?(?:documents?|pdfs?|files?)\b|"
        r"\b(?:list|show|display)\s+(?:all\s+)?(?:indexed\s+)?(?:documents?|pdfs?|files?)(?:\s+names?)?\b",
        text,
        flags=re.I,
    )
    implicit_index_metadata = re.search(
        r"\b(?:total|how\s+many|number\s+of)\s+pages?\b.*\bindexed\b",
        text,
        flags=re.I,
    )
    if (document_noun and (document_metadata or explicit_document_count_or_list)) or implicit_index_metadata:
        return "document"
    return None


def extract_polling_booth_scope(question):
    text = norm(question).replace("\u200c", "").replace("\u200d", "")
    for pattern in [
        r"\b(?:polling\s+)?(?:booth|station|centre|center)\s*(?:number|no\.?)?\s*[:#-]?\s*(\d{1,6})\b",
        r"పోలింగ్\s*కేంద్రం\s*(?:సంఖ్య)?\s*[:#-]?\s*(\d{1,6})",
    ]:
        match = re.search(pattern, text, flags=re.I | re.U)
        if match:
            return match.group(1)
    return None


def extract_entity_request(question, entity, aggregation):
    """Return requested fields, entity-specific filters, and an operation."""
    text = norm(question).replace("\u200c", "").replace("\u200d", "")
    fields = []
    entity_filters = []
    operation = None

    if entity == "polling_booth":
        field_markers = [
            ("address", ["address", "addresses", "location", "locations", "చిరునామా", "पता", "ವಿಳಾಸ", "adresse", "عنوان"]),
            ("name", ["booth name", "booth names", "station name", "centre name", "center name", "కేంద్రం పేరు", "केंद्र का नाम", "ಕೇಂದ್ರದ ಹೆಸರು"]),
            ("classification", ["classification", "classifications", "category", "categories", "type", "types", "వర్గీకరణ", "वर्गीकरण", "ವರ್ಗೀಕರಣ"]),
            ("reservation_status", ["reservation", "reserved", "status", "రిజర్వేషన్", "आरक्षण", "ಮೀಸಲಾತಿ"]),
            ("constituency_number", ["constituency", "నియోజకవర్గం", "निर्वाचन क्षेत्र", "ಕ್ಷೇತ್ರ"]),
            ("part_number", ["part number", "part numbers", "భాగం సంఖ్య", "भाग संख्या", "ಭಾಗ ಸಂಖ್ಯೆ"]),
            ("header_voter_counts", ["header voter", "official voter", "voter totals", "ఓటర్ల సంఖ్య"]),
            ("sources", ["source document", "source documents", "pdf", "pdfs", "file", "files"]),
        ]
        for field, markers in field_markers:
            if any(marker in text for marker in markers):
                fields.append(field)
        if re.search(r"\b(?:number|numbers|numbers?\s+and)\b", text) or aggregation == "count":
            fields.append("number")
        if re.search(r"\b(?:details?|all\s+information|complete\s+information)\b", text):
            fields = [
                "number", "name", "classification", "reservation_status", "address",
                "constituency_number", "part_number", "header_voter_counts", "sources",
            ]
        booth_number = extract_polling_booth_scope(question)
        if booth_number:
            entity_filters.append({"field": "number", "op": "eq", "value": booth_number})
        if not fields:
            fields = ["number", "name", "classification", "address", "sources"]

    elif entity == "document":
        fields = ["source"]
        if re.search(r"\b(?:pages?|page\s+count|number\s+of\s+pages)\b", text):
            fields.append("page_count")
        if re.search(r"\b(?:records?|record\s+count|voters?)\b", text):
            fields.append("record_count")
        if re.search(r"\b(?:ocr\s+language|language\s+hint)\b", text):
            fields.append("language_hint")
        if re.search(r"\b(?:created|uploaded|when\s+indexed|indexed\s+on|index\s+date)\b", text):
            fields.append("created_at")

        if re.search(r"\b(?:most|highest|largest|maximum)\b.*\bpages?\b", text):
            operation = "max_page_count"
        elif re.search(r"\b(?:fewest|lowest|smallest|minimum|least)\b.*\bpages?\b", text):
            operation = "min_page_count"
        elif re.search(r"\b(?:most|highest|largest|maximum)\b.*\brecords?\b", text):
            operation = "max_record_count"
        elif re.search(r"\b(?:fewest|lowest|smallest|minimum|least)\b.*\brecords?\b", text):
            operation = "min_record_count"
        elif re.search(r"\b(?:total|how\s+many|number\s+of)\b.*\bpages?\b", text):
            operation = "sum_page_count"
        elif re.search(r"\btotal\b.*\brecords?\b.*\b(?:documents?|pdfs?|files?)\b", text):
            operation = "sum_record_count"
        elif "page_count" in fields and re.search(r"\b(?:for\s+each|per|each)\b|\bpage\s+counts?\b", text):
            operation = "list"
        elif "record_count" in fields and re.search(r"\b(?:for\s+each|per|each)\b|\brecord\s+counts?\b", text):
            operation = "list"
        elif aggregation == "count":
            operation = "count"
        else:
            operation = "list"

    return list(dict.fromkeys(fields)), entity_filters, operation


def _contains_any(text, values):
    padded = f" {text} "
    for raw in values:
        value = norm(raw)
        if not value:
            continue
        # Word-like Latin markers use boundaries so "man" does not match "many".
        if re.fullmatch(r"[a-z0-9\s.-]+", value):
            pattern = r"(?<![a-z0-9])" + re.escape(value).replace(r"\ ", r"\s+") + r"(?![a-z0-9])"
            if re.search(pattern, text, flags=re.I):
                return True
        elif value in text:
            return True
    return False


def extract_identifiers(question):
    q = normalize_identifier_text(question)
    values = set()
    for value in re.findall(r"\b[A-Za-z]{1,8}\d{5,}\b", q):
        values.add(value)
    for value in re.findall(r"(?<!\w)\d+(?:[-/][A-Za-z0-9]+)+(?!\w)", q):
        values.add(value)
    return sorted(values, key=len, reverse=True)


def _capture_value_after(question, patterns, value_pattern):
    q = normalize_identifier_text(question)
    for phrase in patterns:
        m = re.search(rf"(?:{phrase})\s*(?:is|=|:|no\.?|number)?\s*({value_pattern})", q, flags=re.I | re.U)
        if m:
            return normalize_identifier_text(m.group(1).strip(" ,.;:!?"))
    return None


FIELD_PATTERNS = {
    "house_number": [r"house(?:\s*(?:number|no\.?|#))?", r"hausnummer", r"మకాన\s*(?:నంబర్|సంఖ్య)", r"ఇంటి\s*(?:నంబర్|సంఖ్య)", r"मकान\s*(?:नंबर|संख्या)", r"ಮನೆ\s*(?:ನಂಬರ್|ಸಂಖ್ಯೆ)", r"رقم\s*المنزل"],
    "epic_id": [r"epic(?:\s*(?:id|number|no\.?))?", r"voter\s*id"],
    "serial_number": [r"serial\s*(?:number|no\.?|#)", r"क्रम\s*(?:संख्या|नंबर)", r"ಕ್ರಮ\s*(?:ಸಂಖ್ಯೆ|ನಂಬರ್)", r"క్రమ\s*(?:సంఖ్య|నంబర్)", r"الرقم\s*التسلسلي"],
}


def extract_exact_field_filters(question):
    filters = []
    specs = [
        ("house_number", r"[A-Za-z0-9]+(?:[-/][A-Za-z0-9]+)*"),
        ("epic_id", r"[A-Za-z]{1,8}\s*\d{5,}|[A-Za-z0-9/-]{6,}"),
        ("serial_number", r"\d+[A-Za-z]?"),
    ]
    for field, pattern in specs:
        value = _capture_value_after(question, FIELD_PATTERNS[field], pattern)
        if value:
            # House numbers in the supported voter tables always contain a
            # digit. This prevents pronouns in phrases such as "same house
            # number as that of Asif" from becoming house_number = "that".
            if field == "house_number" and not re.search(r"\d", value):
                continue
            if field == "epic_id":
                value = re.sub(r"\s+", "", value)
            filters.append({"field": field, "op": "eq", "value": value})
    return filters


def extract_age_filter(question):
    text = norm(question)
    # Require an age marker or an obvious age phrase to avoid treating every number as age.
    age_context = any(x in text for x in ["age", "aged", "years old", "older", "younger", "above", "below", "under", "over", "alter", "उम्र", "आयु", "ವಯಸ್ಸು", "వయస్సు", "العمر"])
    if not age_context:
        return None

    # between/from X and/to Y: inclusive by normal human interpretation.
    patterns = [
        r"(?:age(?:d)?\s*)?(?:between|from)\s*(\d{1,3})\s*(?:and|to|-)\s*(\d{1,3})",
        r"(?:between|from)\s*(?:the\s+)?ages?\s*(?:of\s*)?(\d{1,3})\s*(?:and|to|through|-)\s*(\d{1,3})",
        r"(?:ages?|aged)\s*(\d{1,3})\s*(?:to|through|-)\s*(\d{1,3})",
        r"(?:age\s*)?(?:bracket|range)\s*(?:of\s*)?(\d{1,3})\s*(?:and|to|through|-)\s*(\d{1,3})",
        r"(?:वय(?:स|స్సు)|వయస్సు|ವಯಸ್ಸು|उम्र|العمر)[^\d]{0,12}(\d{1,3})\s*(?:-|to|and)\s*(\d{1,3})",
    ]
    q = normalize_identifier_text(question)
    for pattern in patterns:
        m = re.search(pattern, q, flags=re.I | re.U)
        if m:
            lo, hi = int(m.group(1)), int(m.group(2))
            if lo > hi:
                lo, hi = hi, lo
            return {"field": "age", "op": "between", "min": lo, "max": hi}

    m = re.search(r"(?:age(?:d)?\s*)?(\d{1,3})\s*(?:and\s+above|or\s+above|and\s+older|or\s+older|and\s+over)", q, flags=re.I)
    if m:
        return {"field": "age", "op": "gte", "value": int(m.group(1))}
    m = re.search(r"(?:age(?:d)?\s*)?(\d{1,3})\s*(?:and\s+below|or\s+below|and\s+younger|or\s+younger)", q, flags=re.I)
    if m:
        return {"field": "age", "op": "lte", "value": int(m.group(1))}

    # phrase before number
    relation_patterns = [
        (r"(?:at\s+least|minimum|not\s+less\s+than)\s*(?:age\s*)?(\d{1,3})", "gte"),
        (r"(?:older\s+than|above|over|greater\s+than|more\s+than)\s*(?:age\s*)?(\d{1,3})", "gt"),
        (r"(?:at\s+most|maximum|not\s+more\s+than)\s*(?:age\s*)?(\d{1,3})", "lte"),
        (r"(?:younger\s+than|below|under|less\s+than)\s*(?:age\s*)?(\d{1,3})", "lt"),
        (r"(?:age(?:d)?\s*)?(?:at\s+least|minimum(?:\s+age)?|not\s+less\s+than)\s*(\d{1,3})", "gte"),
        (r"(?:age(?:d)?\s*)?(?:older\s+than|above|over|greater\s+than|more\s+than)\s*(\d{1,3})", "gt"),
        (r"(?:age(?:d)?\s*)?(?:at\s+most|maximum(?:\s+age)?|not\s+more\s+than)\s*(\d{1,3})", "lte"),
        (r"(?:age(?:d)?\s*)?(?:younger\s+than|below|under|less\s+than)\s*(\d{1,3})", "lt"),
    ]
    for pattern, op in relation_patterns:
        m = re.search(pattern, q, flags=re.I)
        if m:
            return {"field": "age", "op": op, "value": int(m.group(1))}

    # simple exact age, e.g. "age 86" or "aged 86"
    m = re.search(r"(?:age|aged|alter|उम्र|आयु|ವಯಸ್ಸು|వయస్సు|العمر)\s*(?:is|=|:)?\s*(\d{1,3})", q, flags=re.I | re.U)
    if m:
        return {"field": "age", "op": "eq", "value": int(m.group(1))}
    return None


def extract_gender_filter(question):
    text = norm(question)
    male = ["male", "males", "men", "man", "पुरुष", "ಪುರುಷ", "పురుష", "మగ", "ذكر", "männlich"]
    female = ["female", "females", "women", "woman", "महिला", "स्त्री", "ಮಹಿಳೆ", "ಸ್ತ್ರೀ", "మహిళ", "స్త్రీ", "أنثى", "weiblich"]
    has_female = _contains_any(text, female)
    has_male = _contains_any(text, male)
    # "male and female" asks for a comparison/group, not an intersection or
    # a female-only filter.
    if has_female and has_male:
        return None
    if has_female:
        return {"field": "gender", "op": "eq", "value": "female"}
    if has_male:
        return {"field": "gender", "op": "eq", "value": "male"}
    return None


def extract_name_target(question, language):
    q = normalize_identifier_text(question)
    # quoted names are best
    for pattern in [r'"([^"\n]{1,100})"', r"'([^'\n]{1,100})'", r"“([^”\n]{1,100})”"]:
        m = re.search(pattern, q)
        if m:
            return m.group(1).strip(), "high"

    # explicit markers
    markers = [
        r"(?:voter\s+)?name(?:d)?", r"person\s+named", r"people\s+named",
        r"(?:family|household)\s+members?\s+(?:related\s+to|of|for)",
        r"family\s+of", r"relatives?\s+(?:of|related\s+to)",
        r"(?:people|persons|members)\s+living\s+with", r"नाम", r"ಹೆಸರು", r"పేరు", r"اسم",
    ]
    stop = re.compile(r"\b(?:and|with|are|is|was|were|age|aged|in|house|epic|serial|who|details|above|below|between|over|under|older|younger)\b", re.I)
    for marker in markers:
        m = re.search(rf"{marker}\s+(?:is\s+)?([^,;:?]+)", q, flags=re.I | re.U)
        if m:
            value = stop.split(m.group(1))[0].strip()
            words = value.split()
            if 0 < len(words) <= 4:
                return value, "high"

    # Capture names following imperative search commands before the broader
    # capitalized-name fallback. Otherwise "Find Asif" can be captured as one
    # phrase and rejected because its first word is a command.
    if language in {"en", "de"}:
        command_match = re.search(r"\b(?:find|search\s+for|locate)\s+(?:all\s+)?(.+)$", q, flags=re.I)
        if command_match:
            value = re.split(
                r"\s+(?:where(?:\s+ever)?|wherever|and\s+(?:provide|give|show|include)|"
                r"in\s+(?:the\s+)?(?:voter(?:s|'s|s')?\s+name|related\s+name|all\s+name\s+fields|"
                r"english|telugu|hindi|kannada|german|arabic)|from\s+(?:document|pdf|file))\b",
                command_match.group(1),
                maxsplit=1,
                flags=re.I,
            )[0].strip(" ,;:?!")
            if not _looks_like_generic_query_target(value) and 0 < len(value.split()) <= 6:
                return value, "high"

    # Capitalized personal name in English/German questions.
    if language in {"en", "de"}:
        # A capitalized component following `-` or `/` belongs to an
        # identifier such as house number 9-280-A; it is not a person's name.
        candidates = re.findall(r"(?<![\w/-])([A-Z][A-Za-zÀ-ÖØ-öø-ÿ'’.-]*(?:\s+[A-Z][A-Za-zÀ-ÖØ-öø-ÿ'’.-]*){0,2})", q)
        blocked = {
            "How", "What", "Who", "Where", "When", "Which", "Please", "Give", "Tell", "Show", "Find", "List",
            "All", "Summarize", "Summarise", "Compare", "Export", "Download", "Save", "Display", "Return",
            "Explain", "Describe", "Extract", "Identify",
            "Complete", "Residents", "Resident", "People", "Person", "Persons", "Voters", "Voter",
            "Topics", "Topic", "Key", "Details", "Information", "Records", "Record", "Documents", "Document",
            "English", "Telugu", "Hindi", "Kannada", "German", "Arabic",
            "CSV", "Excel", "PDF", "PDFs", "EPIC", "ID", "Serial", "House",
        }
        for value in candidates:
            if len(re.sub(r"[^A-Za-zÀ-ÖØ-öø-ÿ]", "", value)) < 2:
                continue
            if value.split()[0] not in blocked and not _looks_like_generic_query_target(value):
                return value.strip(), "medium"
    return None, None


def extract_name_scope(question):
    text = norm(question)
    if any(x in text for x in ["father", "mother", "related name", "relative name", "relation name"]):
        return "related_name"
    if any(x in text for x in [
        "voter named", "voters named", "people named", "person named", "voter name",
        "voters name", "voter's name", "voters' name", "names of voters",
    ]):
        return "voter_name"
    return "all_name_fields"


def extract_aggregation(question, language):
    text = norm(question)
    if _contains_any(text, COUNT_MARKERS.get(language, []) + COUNT_MARKERS["en"]):
        return "count"
    if any(x in text for x in ["average age", "avg age", "mean age", "औसत आयु", "సగటు వయస్సు", "ಸರಾಸರಿ ವಯಸ್ಸು", "متوسط العمر"]):
        return "avg_age"
    if any(x in text for x in ["oldest", "maximum age", "max age", "eldest"]):
        return "max_age"
    if any(x in text for x in ["youngest", "minimum age", "min age"]):
        return "min_age"
    return None


def extract_group_by(question):
    text = norm(question)
    if any(x in text for x in ["by gender", "per gender", "each gender", "male and female", "males and females", "men and women"]):
        return "gender"
    if any(x in text for x in ["by age", "per age", "each age"]):
        return "age"
    if any(x in text for x in ["by house", "per house", "each house"]):
        return "house_number"
    return None


def extract_sort_limit(question):
    text = norm(question)
    order_by = None
    direction = None
    if any(x in text for x in ["oldest", "eldest", "maximum age", "max age", "highest age", "descending age"]):
        order_by, direction = "age", "desc"
    elif any(x in text for x in ["youngest", "minimum age", "min age", "lowest age", "ascending age"]):
        order_by, direction = "age", "asc"

    limit = None
    m = re.search(r"\b(?:top|first|show|list)\s+(\d{1,3})\b", normalize_identifier_text(question), flags=re.I)
    if not m:
        m = re.search(r"\b(?:oldest|eldest|youngest)\s+(\d{1,3})\b", normalize_identifier_text(question), flags=re.I)
    if m:
        limit = max(1, min(1000, int(m.group(1))))
    return order_by, direction, limit


def extract_pagination(question):
    q = normalize_identifier_text(question)
    show_all = bool(re.search(r"\b(?:show|list|display|return)\s+all\b|\ball\s+(?:records|results|details)\b", q, re.I))
    match = re.search(r"\bpage\s+(\d+)\b", q, re.I)
    page = max(1, int(match.group(1))) if match else 1
    return {"page": page, "show_all": show_all}


def extract_export_format(question):
    text = norm(question)
    if re.search(r"\b(?:export|download|save)\b.*\b(?:csv|spreadsheet)\b|\bto\s+csv\b", text):
        return "csv"
    return None


def extract_document_scope(question):
    """Extract an explicit PDF scope without treating filenames as entities."""
    q = normalize_identifier_text(question)
    text = norm(q)
    if re.search(r"\b(?:all|every)\s+(?:indexed\s+)?(?:documents|pdfs|files)\b", text):
        return {"mode": "all", "source": None, "explicit": True}
    if re.search(r"\b(?:current|selected|active)\s+(?:document|pdf|file)\b", text):
        return {"mode": "current", "source": None, "explicit": True}
    quoted_filenames = re.findall(r"[\"']([^\"']+\.pdf)[\"']", q, flags=re.I | re.U)
    bare_filenames = re.findall(
        r"(?<![\w.-])([A-Za-z0-9_./\\-]+\.pdf)\b",
        q,
        flags=re.I | re.U,
    )
    filenames = quoted_filenames or bare_filenames
    if filenames:
        # A query currently addresses one source. Multiple named PDFs are left
        # explicit rather than silently choosing the first.
        cleaned = [value.strip(" ,.;:!?\"'") for value in filenames]
        return {"mode": "specific", "source": cleaned[0], "sources": cleaned, "explicit": True}
    return {"mode": "all", "source": None, "explicit": False}


def extract_ranked_operations(question):
    """Return deterministic top/bottom age selections for compound rank queries."""
    text = norm(question)
    has_oldest = any(word in text for word in ["oldest", "eldest"])
    has_youngest = "youngest" in text
    if not (has_oldest and has_youngest):
        return []

    default_limit = 5
    oldest_match = re.search(r"(?:oldest|eldest)\s+(\d{1,3})", text)
    youngest_match = re.search(r"youngest\s+(\d{1,3})", text)
    shared_match = re.search(r"(\d{1,3})\s+(?:oldest|eldest).*?(?:and|&)\s+(?:the\s+)?(?:youngest)", text)
    oldest_limit = int(oldest_match.group(1)) if oldest_match else (int(shared_match.group(1)) if shared_match else default_limit)
    youngest_limit = int(youngest_match.group(1)) if youngest_match else (int(shared_match.group(1)) if shared_match else default_limit)
    return [
        {"name": "oldest", "order_by": "age", "order_direction": "desc", "limit": max(1, min(1000, oldest_limit))},
        {"name": "youngest", "order_by": "age", "order_direction": "asc", "limit": max(1, min(1000, youngest_limit))},
    ]


def extract_household_operation(question, name_target):
    text = norm(question)
    same_house = any(
        phrase in text
        for phrase in [
            "same house", "same household", "people in the house", "others in the house",
            "house number that of", "house number as", "household members",
            "family member", "family members", "family of", "entire family",
            "relative of", "relatives of", "living with",
        ]
    )
    if same_house and name_target:
        return {"name": "same_household", "target": name_target}
    return None


def extract_house_ranking_operation(question):
    """Recognize a generalized request for the most populated household."""
    text = norm(question)
    patterns = [
        r"\bwhich\s+houses?(?:\s+numbers?)?\s+(?:has|have|contains?)\s+(?:the\s+)?(?:most|maximum|highest\s+number\s+of|more)\s+(?:people|persons|residents|voters|members)\b",
        r"\b(?:house|household)\s+with\s+(?:the\s+)?(?:most|maximum|highest\s+number\s+of)\s+(?:people|persons|residents|voters|members)\b",
        r"\b(?:largest|biggest|most\s+populated)\s+(?:household|house|family)\b",
        r"\bwhich\s+(?:house|household)\s+has\s+(?:the\s+)?(?:highest|largest|maximum)\s+(?:population|members)\b",
        r"\b(?:top|first)\s+house(?:hold)?\s+by\s+(?:people|persons|residents|voters|members|population)\b",
    ]
    if any(re.search(pattern, text, flags=re.I) for pattern in patterns):
        return {"name": "largest_household", "group_by": "source_and_house_number", "include_ties": True}
    return None


def analyze_query(question):
    language = detect_language(question)
    output_language, output_language_explicit = detect_requested_output_language(question, language)
    text = norm(question)
    entity = extract_structured_entity(question)
    booth_scope = extract_polling_booth_scope(question)

    filters = [] if entity else extract_exact_field_filters(question)
    if not entity:
        age_filter = extract_age_filter(question)
        if age_filter:
            filters.append(age_filter)
        gender_filter = extract_gender_filter(question)
        if gender_filter:
            filters.append(gender_filter)

    name_target, target_confidence = (None, None) if entity else extract_name_target(question, language)
    # Capitalized identifier labels such as "EPIC AP..." are not names.  Keep
    # an explicitly requested name, but suppress only the weaker capitalization
    # fallback when an exact structured identifier was already recognized.
    if filters and target_confidence == "medium" and any(
        flt.get("field") in {"epic_id", "serial_number", "house_number"} for flt in filters
    ):
        name_target, target_confidence = None, None
    name_scope = extract_name_scope(question)
    if name_target:
        filters.append({"field": "name", "op": "match", "value": name_target, "scope": name_scope})

    aggregation = extract_aggregation(question, language)
    requested_fields, entity_filters, entity_operation = extract_entity_request(question, entity, aggregation)
    group_by = extract_group_by(question)
    order_by, order_direction, limit = extract_sort_limit(question)
    operations = extract_ranked_operations(question)
    house_ranking_operation = extract_house_ranking_operation(question)
    if house_ranking_operation and not operations:
        operations = [house_ranking_operation]
    household_operation = extract_household_operation(question, name_target)
    if household_operation and not operations:
        operations = [household_operation]
        # The name identifies the seed record; it is not a filter on the final
        # household members returned by the second operation.
        filters = [flt for flt in filters if flt.get("field") != "name"]
    pagination = extract_pagination(question)
    document_scope = extract_document_scope(question)
    export_format = extract_export_format(question)
    if export_format:
        pagination["show_all"] = True

    # A compound oldest/youngest request is selection, not max/min aggregation.
    if operations:
        aggregation = None

    identifiers = extract_identifiers(question)

    # Any recognized structured filter/aggregate gets deterministic execution.
    if entity or booth_scope or filters or operations or export_format or aggregation in {"count", "avg_age", "max_age", "min_age"} or group_by:
        intent = "STRUCTURED_QUERY"
        # If user asks "how many" without another filter, structured count is still meaningful: count all records.
        return {
            "question": question,
            "language": language,
            "output_language": output_language,
            "output_language_explicit": output_language_explicit,
            "intent": intent,
            "target": name_target,
            "target_confidence": target_confidence,
            "entity": entity,
            "requested_fields": requested_fields,
            "entity_filters": entity_filters,
            "entity_operation": entity_operation,
            "booth_scope": booth_scope,
            "structured_scope": name_scope,
            "filters": filters,
            "aggregation": aggregation,
            "group_by": group_by,
            "order_by": order_by,
            "order_direction": order_direction,
            "limit": limit,
            "operations": operations,
            "pagination": pagination,
            "document_scope": document_scope,
            "export_format": export_format,
            "exact_identifiers": identifiers,
            "requires_exhaustive": True,
        }

    if _contains_any(text, SUMMARY_MARKERS):
        intent = "DOCUMENT_SUMMARY"
    elif _contains_any(text, COMPARE_MARKERS):
        intent = "COMPARE"
    elif identifiers:
        intent = "EXACT_MATCH"
    else:
        intent = "SEMANTIC"

    return {
        "question": question,
        "language": language,
        "output_language": output_language,
        "output_language_explicit": output_language_explicit,
        "intent": intent,
        "target": name_target,
        "target_confidence": target_confidence,
        "entity": entity,
        "requested_fields": requested_fields,
        "entity_filters": entity_filters,
        "entity_operation": entity_operation,
        "booth_scope": booth_scope,
        "structured_scope": name_scope,
        "filters": [],
        "aggregation": None,
        "group_by": None,
        "order_by": None,
        "order_direction": None,
        "limit": None,
        "operations": [],
        "pagination": pagination,
        "document_scope": document_scope,
        "export_format": export_format,
        "exact_identifiers": identifiers,
        "requires_exhaustive": False,
    }
