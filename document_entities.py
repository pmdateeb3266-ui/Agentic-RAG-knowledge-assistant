import re
import unicodedata


def _normalized_header(text):
    value = unicodedata.normalize("NFKC", text or "")
    value = value.replace("\u200c", "").replace("\u200d", "")
    return re.sub(r"\s+", " ", value).strip()


def extract_polling_booth_number(page_text):
    """Extract an explicitly labelled polling-booth number from a page header."""
    text = _normalized_header(page_text)
    patterns = [
        r"పోలింగ్\s*కేంద్రం\s*సంఖ్య\s*[:#-]?\s*(\d{1,6})",
        r"polling\s*(?:booth|station|centre|center)\s*(?:number|no\.?)?\s*[:#-]?\s*(\d{1,6})",
        r"मतदान\s*(?:केंद्र|केन्द्र)\s*(?:संख्या|नंबर)?\s*[:#-]?\s*(\d{1,6})",
        r"(?:ಮತಗಟ್ಟೆ|ಮತದಾನ\s*ಕೇಂದ್ರ)\s*(?:ಸಂಖ್ಯೆ|ನಂಬರ್)?\s*[:#-]?\s*(\d{1,6})",
        r"(?:wahllokal|wahlbezirk)\s*(?:nummer|nr\.?)?\s*[:#-]?\s*(\d{1,6})",
        r"(?:مركز|محطة)\s*الاقتراع\s*(?:رقم)?\s*[:#-]?\s*(\d{1,6})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I | re.U)
        if match:
            return match.group(1)
    return None


def _capture(text, start_pattern, end_patterns):
    endings = "|".join(f"(?:{pattern})" for pattern in end_patterns)
    match = re.search(
        rf"(?:{start_pattern})\s*[:#-]?\s*(.+?)(?=\s*(?:{endings})|$)",
        text,
        flags=re.I | re.U,
    )
    return match.group(1).strip(" ,.;:-") if match else None


def _capture_number(text, patterns):
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I | re.U)
        if match:
            return match.group(1)
    return None


def extract_polling_booth_metadata(page_text):
    """Extract labelled booth metadata from stored first-page OCR."""
    text = _normalized_header(page_text)
    number = extract_polling_booth_number(text)
    if not number:
        return None

    data = {"number": number}
    fields = {
        "name": _capture(
            text,
            r"(?:పోలింగ్\s*కేంద్రం\s*పేరు|polling\s*(?:booth|station|centre|center)\s*name)",
            [r"పోలింగ్\s*కేంద్రం\s*వర్గీకరణ", r"classification", r"category"],
        ),
        "classification": _capture(
            text,
            r"(?:పోలింగ్\s*కేంద్రం\s*వర్గీకరణ|classification|category)",
            [r"పోలింగ్\s*కేంద్రం\s*రిజర్వేషన్", r"reservation", r"status", r"address"],
        ),
        "reservation_status": _capture(
            text,
            r"(?:పోలింగ్\s*కేంద్రం\s*రిజర్వేషన్\s*పరి(?:స్థితి|స్టితి)|reservation\s*status)",
            [r"పోలింగ్\s*కేంద్ర\s*చిరునామా", r"address"],
        ),
        "address": _capture(
            text,
            r"(?:పోలింగ్\s*కేంద్ర\s*చిరునామా|polling\s*(?:booth|station|centre|center)\s*address|address)",
            [r"ఓటర్ల\s*సంఖ్య", r"number\s*of\s*voters", r"voters?\s*total", r"పుట\s*సంఖ్య", r"page\s*number"],
        ),
    }
    for key, value in fields.items():
        if value:
            data[key] = value

    constituency_number = _capture_number(text, [
        r"శాసనసభ\s*నియోజకవర్గం\s*సంఖ్య\s*[:#-]?\s*(\d{1,6})",
        r"assembly\s*constituency\s*(?:number|no\.?)\s*[:#-]?\s*(\d{1,6})",
    ])
    part_number = _capture_number(text, [
        r"భాగం\s*సంఖ్య\s*[:#-]?\s*(\d{1,6})",
        r"part\s*(?:number|no\.?)\s*[:#-]?\s*(\d{1,6})",
    ])
    if constituency_number:
        data["constituency_number"] = constituency_number
    if part_number:
        data["part_number"] = part_number

    voter_match = re.search(
        r"(?:ఓటర్ల\s*సంఖ్య|number\s*of\s*voters|voters?\s*total)\s*[:#-]?\s*(\d+)\s+(\d+)\s+(\d+)",
        text,
        flags=re.I | re.U,
    )
    if voter_match:
        data["header_voter_counts"] = {
            "male": int(voter_match.group(1)),
            "female": int(voter_match.group(2)),
            "total": int(voter_match.group(3)),
        }
    return data


def polling_booths_from_pages(page_rows, allowed_sources=None):
    """Return distinct verified booths and any scoped sources lacking a header."""
    allowed = set(allowed_sources) if allowed_sources is not None else None
    by_number = {}
    inspected_sources = set()
    unidentified_sources = set()

    for row in page_rows:
        source = str(row["source"])
        if allowed is not None and source not in allowed:
            continue
        inspected_sources.add(source)
        metadata = extract_polling_booth_metadata(row["page_text"])
        if not metadata:
            unidentified_sources.add(source)
            continue
        number = metadata["number"]
        if number not in by_number:
            by_number[number] = {**metadata, "sources": set()}
        current = by_number[number]
        current["sources"].add(source)
        for key, value in metadata.items():
            if key not in current or not current[key]:
                current[key] = value

    booths = [
        {**metadata, "sources": sorted(metadata["sources"])}
        for number, metadata in sorted(
            by_number.items(),
            key=lambda item: (int(item[0]) if item[0].isdigit() else 10**9, item[0]),
        )
    ]
    return booths, sorted(unidentified_sources), sorted(inspected_sources)


def semantic_source_filter(document_scope, current_document=""):
    """Build an exact Chroma source filter for selected/named documents."""
    scope = document_scope or {}
    sources = []
    if scope.get("mode") == "specific":
        sources = [str(value) for value in (scope.get("sources") or [scope.get("source")]) if value]
    elif scope.get("mode") == "current" and current_document:
        sources = [str(current_document)]
    if len(sources) == 1:
        return {"source": sources[0]}, sources
    if len(sources) > 1:
        return {"source": {"$in": sources}}, sources
    return None, []
