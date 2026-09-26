from language_utils import english_gender, english_relationship, romanize


def no_evidence_message(language):
    return {
        "en": "No sufficiently relevant evidence was found in the selected documents. No answer was generated.",
        "te": "ఎంచుకున్న పత్రాల్లో తగినంత సంబంధిత ఆధారం కనుగొనబడలేదు. సమాధానం రూపొందించబడలేదు.",
        "hi": "चयनित दस्तावेज़ों में पर्याप्त प्रासंगिक प्रमाण नहीं मिला। कोई उत्तर तैयार नहीं किया गया।",
        "kn": "ಆಯ್ದ ದಾಖಲೆಗಳಲ್ಲಿ ಸಾಕಷ್ಟು ಸಂಬಂಧಿತ ಸಾಕ್ಷ್ಯ ಕಂಡುಬಂದಿಲ್ಲ. ಉತ್ತರವನ್ನು ರಚಿಸಲಾಗಿಲ್ಲ.",
        "de": "In den ausgewählten Dokumenten wurden keine ausreichend relevanten Belege gefunden. Es wurde keine Antwort erzeugt.",
        "ar": "لم يتم العثور على أدلة ذات صلة كافية في المستندات المحددة. لم يتم إنشاء إجابة.",
    }.get(language, "No sufficiently relevant evidence was found in the selected documents. No answer was generated.")


def _filter_text(flt):
    field, op = flt.get("field"), flt.get("op")
    if field == "age" and op == "between":
        return f"age between {flt['min']} and {flt['max']} inclusive"
    if field == "age":
        symbols = {"eq": "=", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}
        return f"age {symbols.get(op, op)} {flt.get('value')}"
    if field == "name":
        return f"name matches '{flt.get('value')}' in {flt.get('scope', 'all_name_fields')}"
    return f"{field} = {flt.get('value')}"


def verified_facts_text(plan, facts):
    if not facts:
        return ""
    if plan.get("intent") == "STRUCTURED_QUERY":
        lines = [
            "Deterministic structured query executed over all extracted records.",
            "Filters: " + ("; ".join(_filter_text(x) for x in facts.get("filters", [])) or "none"),
            f"Verified matching records: {facts.get('matched_records', 0)}",
            "Pages with matches: " + (", ".join(map(str, facts.get("pages", []))) or "none"),
        ]
        scope = facts.get("document_scope") or {}
        if scope.get("error"):
            lines.append(f"Document scope error: {scope['error']}")
        elif scope.get("resolved_source"):
            lines.append(f"Document scope: {scope['resolved_source']}")
        else:
            lines.append("Document scope: all indexed documents")
        agg = facts.get("aggregation")
        if agg:
            lines.append(f"Aggregation: {agg}")
            lines.append(f"Verified aggregate value: {facts.get('value')}")
        if facts.get("groups") is not None:
            lines.append(f"Verified groups: {facts.get('groups')}")
        if facts.get("document_counts"):
            lines.append(f"Verified counts by source document: {facts.get('document_counts')}")
        lines.append("The LLM must not recalculate or estimate these values from semantic evidence.")
        return "\n".join(lines)
    return ""


def deterministic_summary(plan, facts):
    if not facts or plan.get("intent") != "STRUCTURED_QUERY":
        return ""
    language = plan.get("output_language") or plan.get("language") or "en"
    if facts.get("entity") == "polling_booth":
        translations = {
            "en": {"count": "Verified polling booths", "booth": "Polling booth", "source": "Source document", "name": "Name", "classification": "Classification", "reservation_status": "Reservation status", "address": "Address", "constituency_number": "Constituency number", "part_number": "Part number", "header_voter_counts": "Header voter totals", "male": "Male", "female": "Female", "total": "Total", "original": "original", "romanized": "romanized", "missing": "Header not identified for", "basis": "Result computed deterministically from stored first-page polling-centre headers."},
            "te": {"count": "ధృవీకరించిన పోలింగ్ కేంద్రాలు", "booth": "పోలింగ్ కేంద్రం", "source": "మూల పత్రం", "name": "పేరు", "classification": "వర్గీకరణ", "reservation_status": "రిజర్వేషన్ స్థితి", "address": "చిరునామా", "constituency_number": "నియోజకవర్గ సంఖ్య", "part_number": "భాగం సంఖ్య", "header_voter_counts": "శీర్షికలోని ఓటర్ల సంఖ్య", "male": "పురుషులు", "female": "మహిళలు", "total": "మొత్తం", "original": "అసలు", "romanized": "రోమనైజ్డ్", "missing": "శీర్షిక గుర్తించబడని పత్రాలు", "basis": "నిల్వ చేసిన మొదటి పేజీ పోలింగ్ కేంద్ర శీర్షికల నుండి ఫలితం నిర్దిష్టంగా లెక్కించబడింది."},
            "hi": {"count": "सत्यापित मतदान केंद्र", "booth": "मतदान केंद्र", "source": "स्रोत दस्तावेज़", "name": "नाम", "classification": "वर्गीकरण", "reservation_status": "आरक्षण स्थिति", "address": "पता", "constituency_number": "निर्वाचन क्षेत्र संख्या", "part_number": "भाग संख्या", "header_voter_counts": "शीर्षक में मतदाता संख्या", "male": "पुरुष", "female": "महिला", "total": "कुल", "original": "मूल", "romanized": "रोमनकृत", "missing": "जिन दस्तावेज़ों में शीर्षक नहीं मिला", "basis": "परिणाम संग्रहित प्रथम-पृष्ठ मतदान-केंद्र शीर्षकों से निश्चित रूप से निकाला गया है।"},
            "kn": {"count": "ಪರಿಶೀಲಿಸಿದ ಮತಗಟ್ಟೆಗಳು", "booth": "ಮತಗಟ್ಟೆ", "source": "ಮೂಲ ದಾಖಲೆ", "name": "ಹೆಸರು", "classification": "ವರ್ಗೀಕರಣ", "reservation_status": "ಮೀಸಲಾತಿ ಸ್ಥಿತಿ", "address": "ವಿಳಾಸ", "constituency_number": "ಕ್ಷೇತ್ರ ಸಂಖ್ಯೆ", "part_number": "ಭಾಗ ಸಂಖ್ಯೆ", "header_voter_counts": "ಶೀರ್ಷಿಕೆಯ ಮತದಾರರ ಎಣಿಕೆ", "male": "ಪುರುಷರು", "female": "ಮಹಿಳೆಯರು", "total": "ಒಟ್ಟು", "original": "ಮೂಲ", "romanized": "ರೋಮನೀಕೃತ", "missing": "ಶೀರ್ಷಿಕೆ ಗುರುತಿಸದ ದಾಖಲೆಗಳು", "basis": "ಸಂಗ್ರಹಿಸಿದ ಮೊದಲ ಪುಟದ ಮತಗಟ್ಟೆ ಶೀರ್ಷಿಕೆಗಳಿಂದ ಫಲಿತಾಂಶವನ್ನು ನಿರ್ಧಾರಾತ್ಮಕವಾಗಿ ಲೆಕ್ಕಿಸಲಾಗಿದೆ."},
            "de": {"count": "Verifizierte Wahllokale", "booth": "Wahllokal", "source": "Quelldokument", "name": "Name", "classification": "Klassifizierung", "reservation_status": "Reservierungsstatus", "address": "Adresse", "constituency_number": "Wahlkreisnummer", "part_number": "Teilnummer", "header_voter_counts": "Wählerzahlen im Kopf", "male": "Männlich", "female": "Weiblich", "total": "Gesamt", "original": "Original", "romanized": "romanisiert", "missing": "Kopfzeile nicht erkannt für", "basis": "Das Ergebnis wurde deterministisch aus den gespeicherten Kopfzeilen der ersten Seiten berechnet."},
            "ar": {"count": "مراكز الاقتراع المتحقق منها", "booth": "مركز الاقتراع", "source": "المستند المصدر", "name": "الاسم", "classification": "التصنيف", "reservation_status": "حالة الحجز", "address": "العنوان", "constituency_number": "رقم الدائرة", "part_number": "رقم الجزء", "header_voter_counts": "أعداد الناخبين في العنوان", "male": "ذكور", "female": "إناث", "total": "المجموع", "original": "الأصل", "romanized": "بالأحرف اللاتينية", "missing": "لم يُتعرف على العنوان في", "basis": "تم حساب النتيجة بصورة حتمية من عناوين مراكز الاقتراع المخزنة في الصفحات الأولى."},
        }
        booth_labels = translations.get(language, translations["en"])
        requested = set(facts.get("requested_fields") or ["number", "sources"])
        lines = [f"{booth_labels['count']}: {facts.get('value', 0)}"]
        for booth in facts.get("polling_booths", []):
            lines.append(f"{booth_labels['booth']} {booth.get('number')}")
            for field in ["name", "classification", "reservation_status", "address", "constituency_number", "part_number"]:
                if field not in requested or booth.get(field) in {None, ""}:
                    continue
                value = str(booth[field])
                if language == "en" and field in {"name", "classification", "reservation_status", "address"}:
                    lines.append(f"  {booth_labels[field]} ({booth_labels['original']}): {value}")
                    romanized = romanize(value)
                    if romanized:
                        lines.append(f"  {booth_labels[field]} ({booth_labels['romanized']}): {romanized}")
                else:
                    lines.append(f"  {booth_labels[field]}: {value}")
            if "header_voter_counts" in requested and booth.get("header_voter_counts"):
                counts = booth["header_voter_counts"]
                lines.append(
                    f"  {booth_labels['header_voter_counts']}: "
                    f"{booth_labels['male']}={counts.get('male')}, "
                    f"{booth_labels['female']}={counts.get('female')}, "
                    f"{booth_labels['total']}={counts.get('total')}"
                )
            sources = ", ".join(booth.get("sources", []))
            lines.append(f"  {booth_labels['source']}: {sources}")
        unidentified = facts.get("unidentified_documents") or []
        if unidentified:
            lines.append(f"{booth_labels['missing']}: " + ", ".join(unidentified))
        lines.append(booth_labels["basis"])
        return "\n".join(lines)

    if facts.get("entity") == "document":
        translations = {
            "en": {"count": "Verified documents", "total_pages": "Verified total pages", "total_records": "Verified total records", "source": "Document", "page_count": "Pages", "record_count": "Structured records", "language_hint": "OCR language", "created_at": "Indexed at", "basis": "Result computed deterministically from the document index."},
            "te": {"count": "ధృవీకరించిన పత్రాలు", "total_pages": "ధృవీకరించిన మొత్తం పేజీలు", "total_records": "ధృవీకరించిన మొత్తం రికార్డులు", "source": "పత్రం", "page_count": "పేజీలు", "record_count": "నిర్మిత రికార్డులు", "language_hint": "OCR భాష", "created_at": "సూచిక చేసిన సమయం", "basis": "పత్ర సూచిక నుండి ఫలితం నిర్దిష్టంగా లెక్కించబడింది."},
            "hi": {"count": "सत्यापित दस्तावेज़", "total_pages": "सत्यापित कुल पृष्ठ", "total_records": "सत्यापित कुल रिकॉर्ड", "source": "दस्तावेज़", "page_count": "पृष्ठ", "record_count": "संरचित रिकॉर्ड", "language_hint": "OCR भाषा", "created_at": "अनुक्रमण समय", "basis": "परिणाम दस्तावेज़ अनुक्रमणिका से निश्चित रूप से निकाला गया है।"},
            "kn": {"count": "ಪರಿಶೀಲಿಸಿದ ದಾಖಲೆಗಳು", "total_pages": "ಪರಿಶೀಲಿಸಿದ ಒಟ್ಟು ಪುಟಗಳು", "total_records": "ಪರಿಶೀಲಿಸಿದ ಒಟ್ಟು ದಾಖಲೆಗಳು", "source": "ದಾಖಲೆ", "page_count": "ಪುಟಗಳು", "record_count": "ರಚನಾತ್ಮಕ ದಾಖಲೆಗಳು", "language_hint": "OCR ಭಾಷೆ", "created_at": "ಸೂಚ್ಯಂಕಿತ ಸಮಯ", "basis": "ದಾಖಲೆ ಸೂಚ್ಯಂಕದಿಂದ ಫಲಿತಾಂಶವನ್ನು ನಿರ್ಧಾರಾತ್ಮಕವಾಗಿ ಲೆಕ್ಕಿಸಲಾಗಿದೆ."},
            "de": {"count": "Verifizierte Dokumente", "total_pages": "Verifizierte Gesamtseiten", "total_records": "Verifizierte Gesamtdatensätze", "source": "Dokument", "page_count": "Seiten", "record_count": "Strukturierte Datensätze", "language_hint": "OCR-Sprache", "created_at": "Indexiert am", "basis": "Das Ergebnis wurde deterministisch aus dem Dokumentindex berechnet."},
            "ar": {"count": "المستندات المتحقق منها", "total_pages": "إجمالي الصفحات المتحقق منه", "total_records": "إجمالي السجلات المتحقق منه", "source": "المستند", "page_count": "الصفحات", "record_count": "السجلات المنظمة", "language_hint": "لغة OCR", "created_at": "وقت الفهرسة", "basis": "تم حساب النتيجة بصورة حتمية من فهرس المستندات."},
        }
        labels = translations.get(language, translations["en"])
        operation = facts.get("entity_operation") or "list"
        if operation == "sum_page_count":
            heading = f"{labels['total_pages']}: {facts.get('value', 0)}"
        elif operation == "sum_record_count":
            heading = f"{labels['total_records']}: {facts.get('value', 0)}"
        else:
            heading = f"{labels['count']}: {len(facts.get('document_metadata', []))}"
        lines = [heading]
        requested = set(facts.get("requested_fields") or ["source"])
        for item in facts.get("document_metadata", []):
            lines.append(f"{labels['source']}: {item.get('source')}")
            for field in ["page_count", "record_count", "language_hint", "created_at"]:
                if field in requested:
                    lines.append(f"  {labels[field]}: {item.get(field)}")
        lines.append(labels["basis"])
        return "\n".join(lines)
    translations = {
        "en": ["Structured filters", "none", "Document scope", "all indexed documents", "Verified count", "Verified matching records", "Grouped counts", "Counts by source document", "Quality review", "records with warnings", "without warnings", "Pages with matches", "Result computed deterministically from structured OCR records."],
        "te": ["నిర్మిత ఫిల్టర్లు", "ఏవీ లేవు", "పత్ర పరిధి", "సూచికలోని అన్ని పత్రాలు", "ధృవీకరించిన సంఖ్య", "ధృవీకరించిన సరిపోలికలు", "వర్గాల వారీ సంఖ్య", "మూల పత్రాల వారీ సంఖ్య", "నాణ్యత సమీక్ష", "హెచ్చరికలున్న రికార్డులు", "హెచ్చరికలు లేనివి", "సరిపోలికలున్న పేజీలు", "ఫలితం నిర్మిత OCR రికార్డుల నుండి నిర్దిష్టంగా లెక్కించబడింది."],
        "hi": ["संरचित फ़िल्टर", "कोई नहीं", "दस्तावेज़ क्षेत्र", "सभी अनुक्रमित दस्तावेज़", "सत्यापित संख्या", "सत्यापित मिलान", "समूहवार संख्या", "स्रोत दस्तावेज़ के अनुसार संख्या", "गुणवत्ता समीक्षा", "चेतावनी वाले रिकॉर्ड", "बिना चेतावनी", "मिलान वाले पृष्ठ", "परिणाम संरचित OCR रिकॉर्ड से निश्चित रूप से निकाला गया है।"],
        "kn": ["ರಚನಾತ್ಮಕ ಫಿಲ್ಟರ್‌ಗಳು", "ಯಾವುದೂ ಇಲ್ಲ", "ದಾಖಲೆ ವ್ಯಾಪ್ತಿ", "ಸೂಚ್ಯಂಕಿತ ಎಲ್ಲಾ ದಾಖಲೆಗಳು", "ಪರಿಶೀಲಿಸಿದ ಎಣಿಕೆ", "ಪರಿಶೀಲಿಸಿದ ಹೊಂದಾಣಿಕೆಗಳು", "ಗುಂಪುವಾರು ಎಣಿಕೆ", "ಮೂಲ ದಾಖಲೆಯವಾರು ಎಣಿಕೆ", "ಗುಣಮಟ್ಟ ಪರಿಶೀಲನೆ", "ಎಚ್ಚರಿಕೆಗಳಿರುವ ದಾಖಲೆಗಳು", "ಎಚ್ಚರಿಕೆಗಳಿಲ್ಲದವು", "ಹೊಂದಾಣಿಕೆಯ ಪುಟಗಳು", "ಫಲಿತಾಂಶವನ್ನು ರಚನಾತ್ಮಕ OCR ದಾಖಲೆಗಳಿಂದ ನಿರ್ಧಾರಾತ್ಮಕವಾಗಿ ಲೆಕ್ಕಿಸಲಾಗಿದೆ."],
        "de": ["Strukturierte Filter", "keine", "Dokumentbereich", "alle indexierten Dokumente", "Verifizierte Anzahl", "Verifizierte Treffer", "Gruppierte Anzahlen", "Anzahl nach Quelldokument", "Qualitätsprüfung", "Datensätze mit Warnungen", "ohne Warnungen", "Seiten mit Treffern", "Das Ergebnis wurde deterministisch aus strukturierten OCR-Datensätzen berechnet."],
        "ar": ["عوامل التصفية المنظمة", "لا يوجد", "نطاق المستند", "جميع المستندات المفهرسة", "العدد المتحقق منه", "النتائج المتحقق منها", "الأعداد حسب المجموعة", "الأعداد حسب المستند المصدر", "مراجعة الجودة", "سجلات تحتوي على تحذيرات", "بلا تحذيرات", "الصفحات التي تحتوي على نتائج", "تم حساب النتيجة بصورة حتمية من سجلات OCR المنظمة."],
    }
    t = translations.get(language, translations["en"])
    metric_labels = {
        "en": ("Verified average age", "Verified maximum age", "Verified minimum age", "not available"),
        "te": ("ధృవీకరించిన సగటు వయస్సు", "ధృవీకరించిన గరిష్ఠ వయస్సు", "ధృవీకరించిన కనిష్ఠ వయస్సు", "అందుబాటులో లేదు"),
        "hi": ("सत्यापित औसत आयु", "सत्यापित अधिकतम आयु", "सत्यापित न्यूनतम आयु", "उपलब्ध नहीं"),
        "kn": ("ಪರಿಶೀಲಿಸಿದ ಸರಾಸರಿ ವಯಸ್ಸು", "ಪರಿಶೀಲಿಸಿದ ಗರಿಷ್ಠ ವಯಸ್ಸು", "ಪರಿಶೀಲಿಸಿದ ಕನಿಷ್ಠ ವಯಸ್ಸು", "ಲಭ್ಯವಿಲ್ಲ"),
        "de": ("Verifiziertes Durchschnittsalter", "Verifiziertes Höchstalter", "Verifiziertes Mindestalter", "nicht verfügbar"),
        "ar": ("متوسط العمر المتحقق منه", "أقصى عمر متحقق منه", "أدنى عمر متحقق منه", "غير متاح"),
    }.get(language, ("Verified average age", "Verified maximum age", "Verified minimum age", "not available"))
    filters = "; ".join(_filter_text(x) for x in facts.get("filters", [])) or t[1]
    matched = facts.get("matched_records", 0)
    agg = facts.get("aggregation")
    lines = [f"{t[0]}: {filters}"]
    scope = facts.get("document_scope") or {}
    if scope.get("error"):
        lines.append(f"{t[2]}: {scope['error']}")
    elif scope.get("resolved_source"):
        lines.append(f"{t[2]}: {scope['resolved_source']}")
    else:
        lines.append(f"{t[2]}: {t[3]}")
    booth_scope = facts.get("booth_scope") or {}
    if booth_scope:
        booth_scope_labels = {
            "en": "Polling booth scope", "te": "పోలింగ్ కేంద్ర పరిధి", "hi": "मतदान केंद्र क्षेत्र",
            "kn": "ಮತಗಟ್ಟೆ ವ್ಯಾಪ್ತಿ", "de": "Wahllokalbereich", "ar": "نطاق مركز الاقتراع",
        }
        scope_value = booth_scope.get("number")
        if booth_scope.get("error"):
            scope_value = booth_scope["error"]
        lines.append(f"{booth_scope_labels.get(language, booth_scope_labels['en'])}: {scope_value}")
    if agg == "count":
        lines.append(f"{t[4]}: {facts.get('value', matched)}")
    elif agg == "avg_age":
        value = facts.get("value")
        lines.append(f"{metric_labels[0]}: " + (f"{value:.2f}" if isinstance(value, (int, float)) else metric_labels[3]))
    elif agg == "max_age":
        lines.append(f"{metric_labels[1]}: {facts.get('value') if facts.get('value') is not None else metric_labels[3]}")
    elif agg == "min_age":
        lines.append(f"{metric_labels[2]}: {facts.get('value') if facts.get('value') is not None else metric_labels[3]}")
    else:
        lines.append(f"{t[5]}: {matched}")
    if facts.get("groups") is not None:
        lines.append(f"{t[6]}: " + ", ".join(f"{k}={v}" for k, v in facts["groups"].items()))
    if facts.get("document_counts"):
        lines.append(f"{t[7]}: " + ", ".join(f"{k}={v}" for k, v in facts["document_counts"].items()))
    household = facts.get("household_lookup") or {}
    if household:
        household_label = {
            "en": "Household lookup", "te": "కుటుంబ శోధన", "hi": "परिवार खोज",
            "kn": "ಕುಟುಂಬ ಹುಡುಕಾಟ", "de": "Haushaltssuche", "ar": "بحث الأسرة",
        }.get(language, "Household lookup")
        none_label = {
            "en": "no matching person or household found", "te": "సరిపోలే వ్యక్తి లేదా కుటుంబం కనుగొనబడలేదు",
            "hi": "कोई मेल खाता व्यक्ति या परिवार नहीं मिला", "kn": "ಹೊಂದಾಣಿಕೆಯ ವ್ಯಕ್ತಿ ಅಥವಾ ಕುಟುಂಬ ಕಂಡುಬಂದಿಲ್ಲ",
            "de": "keine passende Person oder kein passender Haushalt gefunden", "ar": "لم يتم العثور على شخص أو أسرة مطابقة",
        }.get(language, "no matching person or household found")
        locations = ", ".join(
            f"{item['house_number']} ({item['source']})" for item in household.get("households", [])
        ) or none_label
        lines.append(f"{household_label} — {household.get('target')}: {locations}")
    quality = facts.get("quality") or {}
    if quality:
        lines.append(
            f"{t[8]}: {quality.get('records_with_warnings', 0)} {t[9]}; "
            f"{quality.get('records_without_warnings', 0)} {t[10]}."
        )
    lines.append(f"{t[11]}: " + (", ".join(map(str, facts.get("pages", []))) or t[1]))
    lines.append(t[12])
    return "\n".join(lines)


LABELS = {
    "en": {
        "total": "Total matches", "showing": "Showing", "of": "of", "page": "Page",
        "source": "Source document", "serial": "Serial", "house": "House number",
        "name": "Voter name", "relationship": "Relationship", "related": "Related name",
        "gender": "Gender", "age": "Age", "epic": "EPIC", "oldest": "Oldest people",
        "youngest": "Youngest people", "more": "More results are available",
        "male": "Male", "female": "Female", "unknown": "Unknown",
        "household": "Household lookup", "largest_household": "Largest household",
        "residents": "residents", "none": "none",
    },
    "te": {"total": "మొత్తం సరిపోలికలు", "showing": "చూపిస్తున్నవి", "of": "లో", "page": "పేజీ", "source": "మూల పత్రం", "serial": "క్రమ సంఖ్య", "house": "ఇంటి నంబర్", "name": "ఓటరు పేరు", "relationship": "సంబంధం", "related": "సంబంధిత పేరు", "gender": "లింగం", "age": "వయస్సు", "epic": "EPIC", "oldest": "అత్యధిక వయస్సు గల వ్యక్తులు", "youngest": "అతి తక్కువ వయస్సు గల వ్యక్తులు", "more": "మరిన్ని ఫలితాలు ఉన్నాయి", "male": "పురుషులు", "female": "మహిళలు", "unknown": "తెలియదు", "household": "కుటుంబ శోధన", "largest_household": "అత్యధిక సభ్యులున్న ఇల్లు", "residents": "నివాసితులు", "none": "ఏదీ లేదు"},
    "hi": {"total": "कुल मिलान", "showing": "दिखाए जा रहे हैं", "of": "में से", "page": "पृष्ठ", "source": "स्रोत दस्तावेज़", "serial": "क्रम संख्या", "house": "मकान संख्या", "name": "मतदाता का नाम", "relationship": "संबंध", "related": "संबंधित नाम", "gender": "लिंग", "age": "आयु", "epic": "EPIC", "oldest": "सबसे अधिक आयु वाले व्यक्ति", "youngest": "सबसे कम आयु वाले व्यक्ति", "more": "और परिणाम उपलब्ध हैं", "male": "पुरुष", "female": "महिला", "unknown": "अज्ञात", "household": "परिवार खोज", "largest_household": "सबसे बड़ा परिवार", "residents": "निवासी", "none": "कोई नहीं"},
    "kn": {"total": "ಒಟ್ಟು ಹೊಂದಾಣಿಕೆಗಳು", "showing": "ತೋರಿಸಲಾಗುತ್ತಿದೆ", "of": "ರಲ್ಲಿ", "page": "ಪುಟ", "source": "ಮೂಲ ದಾಖಲೆ", "serial": "ಕ್ರಮ ಸಂಖ್ಯೆ", "house": "ಮನೆ ಸಂಖ್ಯೆ", "name": "ಮತದಾರರ ಹೆಸರು", "relationship": "ಸಂಬಂಧ", "related": "ಸಂಬಂಧಿತ ಹೆಸರು", "gender": "ಲಿಂಗ", "age": "ವಯಸ್ಸು", "epic": "EPIC", "oldest": "ಅತಿ ಹಿರಿಯ ವ್ಯಕ್ತಿಗಳು", "youngest": "ಅತಿ ಕಿರಿಯ ವ್ಯಕ್ತಿಗಳು", "more": "ಇನ್ನಷ್ಟು ಫಲಿತಾಂಶಗಳು ಲಭ್ಯವಿವೆ", "male": "ಪುರುಷ", "female": "ಮಹಿಳೆ", "unknown": "ತಿಳಿದಿಲ್ಲ", "household": "ಕುಟುಂಬ ಹುಡುಕಾಟ", "largest_household": "ಅತಿ ದೊಡ್ಡ ಕುಟುಂಬ", "residents": "ನಿವಾಸಿಗಳು", "none": "ಯಾವುದೂ ಇಲ್ಲ"},
    "de": {"total": "Treffer insgesamt", "showing": "Angezeigt", "of": "von", "page": "Seite", "source": "Quelldokument", "serial": "Laufende Nummer", "house": "Hausnummer", "name": "Wählername", "relationship": "Beziehung", "related": "Zugehöriger Name", "gender": "Geschlecht", "age": "Alter", "epic": "EPIC", "oldest": "Älteste Personen", "youngest": "Jüngste Personen", "more": "Weitere Ergebnisse sind verfügbar", "male": "Männlich", "female": "Weiblich", "unknown": "Unbekannt", "household": "Haushaltssuche", "largest_household": "Größter Haushalt", "residents": "Bewohner", "none": "keine"},
    "ar": {"total": "إجمالي النتائج", "showing": "المعروض", "of": "من", "page": "الصفحة", "source": "المستند المصدر", "serial": "الرقم التسلسلي", "house": "رقم المنزل", "name": "اسم الناخب", "relationship": "صلة القرابة", "related": "الاسم المرتبط", "gender": "الجنس", "age": "العمر", "epic": "EPIC", "oldest": "الأكبر سنًا", "youngest": "الأصغر سنًا", "more": "توجد نتائج إضافية", "male": "ذكر", "female": "أنثى", "unknown": "غير معروف", "household": "بحث الأسرة", "largest_household": "أكبر أسرة", "residents": "مقيمون", "none": "لا يوجد"},
}


def _labels(language):
    # English is the safe fallback while original-script field values are preserved.
    return LABELS.get(language, LABELS["en"])


def _record_lines(result, number, labels):
    metadata = result["metadata"]
    # The document string is useful for semantic evidence, but structured output
    # is rendered from complete fields encoded in that canonical string.
    parts = {}
    for segment in result["document"].split(" | "):
        if ": " in segment:
            key, value = segment.split(": ", 1)
            parts[key] = value
    language = labels.get("_language", "en")
    source = str(metadata.get("source", ""))
    voter_name = parts.get("Voter Name", "")
    related_name = parts.get("Related Name", "")
    relationship = parts.get("Relationship", "")
    gender = parts.get("Gender", "")

    lines = [
        f"{number}. {labels['source']}: {metadata.get('source', '')}",
        f"   {labels['page']}: {metadata.get('page', '')}",
        f"   {labels['serial']}: {parts.get('Serial', '')}",
        f"   {labels['house']}: {parts.get('House No', '')}",
    ]
    if language == "en":
        lines.append(f"   {labels['name']} (original): {voter_name}")
        voter_romanized = romanize(voter_name)
        if voter_romanized:
            lines.append(f"   {labels['name']} (romanized): {voter_romanized}")
        relationship_english = english_relationship(relationship)
        relationship_display = f"{relationship_english} (original: {relationship})" if relationship_english != relationship else relationship_english
        lines.append(f"   {labels['relationship']}: {relationship_display}")
        lines.append(f"   {labels['related']} (original): {related_name}")
        related_romanized = romanize(related_name)
        if related_romanized:
            lines.append(f"   {labels['related']} (romanized): {related_romanized}")
        gender_english = english_gender(source, gender)
        gender_display = f"{gender_english} (original OCR: {gender})" if gender_english != gender else gender_english
        lines.append(f"   {labels['gender']}: {gender_display}")
    else:
        lines.extend([
            f"   {labels['name']}: {voter_name}",
            f"   {labels['relationship']}: {relationship}",
            f"   {labels['related']}: {related_name}",
            f"   {labels['gender']}: {gender}",
        ])
    lines.extend([
        f"   {labels['age']}: {parts.get('Age', '')}",
        f"   {labels['epic']}: {parts.get('EPIC', '')}",
    ])
    warnings = result.get("quality_warnings") or []
    notices = result.get("quality_notices") or []
    lines.append(f"   OCR confidence: {float(metadata.get('ocr_confidence') or 0):.1f}")
    if warnings:
        lines.append("   Quality warning: " + "; ".join(warnings))
    else:
        lines.append("   Quality: no automatic validation warnings")
    if notices:
        lines.append("   Source note: " + "; ".join(notices))
    return lines


def render_structured_records(plan, facts, results):
    """Render exact structured results without allowing an LLM to omit rows."""
    output_language = plan.get("output_language") or plan.get("language") or "en"
    labels = dict(_labels(output_language))
    labels["_language"] = output_language
    total = int(facts.get("matched_records", len(results)))
    pagination = facts.get("pagination") or {}
    page = int(pagination.get("page", 1))
    total_pages = int(pagination.get("total_pages", 1))

    lines = [f"{labels['total']}: {total}"]
    household = facts.get("household_lookup") or {}
    if household:
        locations = ", ".join(
            f"{item['house_number']} ({item['source']})" for item in household.get("households", [])
        ) or labels["none"]
        lines.append(f"{labels['household']} — {household.get('target')}: {locations}")
    largest_households = facts.get("largest_households") or []
    if largest_households:
        winners = "; ".join(
            f"{item['house_number']} ({item['source']}) — {item['resident_count']} {labels['residents']}"
            for item in largest_households
        )
        lines.append(f"{labels['largest_household']}: {winners}")
    quality = facts.get("quality") or {}
    if quality:
        lines.append(
            f"Quality review: {quality.get('records_with_warnings', 0)} record(s) with warnings; "
            f"{quality.get('records_without_warnings', 0)} without automatic warnings"
        )
    groups = facts.get("groups") or {}
    if groups:
        lines.append(" | ".join(f"{labels.get(key, key.title())}: {value}" for key, value in groups.items()))
    operations = plan.get("operations") or []
    ranked_operations = [item for item in operations if item.get("name") in {"oldest", "youngest"}]
    if ranked_operations:
        current = None
        item_number = 0
        for result in results:
            operation = result.get("operation", "")
            if operation != current:
                current = operation
                lines.extend(["", labels.get(operation, operation.title())])
                item_number = 0
            item_number += 1
            lines.extend(_record_lines(result, item_number, labels))
        return "\n".join(lines)

    shown = len(results)
    if shown:
        lines.append(f"{labels['showing']}: {shown} {labels['of']} {total} ({labels['page']} {page}/{total_pages})")
        current_source = None
        source_number = 0
        for result in results:
            source = result["metadata"].get("source", "")
            if source != current_source:
                current_source = source
                source_number = 0
                lines.extend(["", f"{labels['source']}: {source}"])
            source_number += 1
            lines.extend(_record_lines(result, source_number, labels)[1:])

    if total_pages > 1 and not pagination.get("show_all"):
        lines.extend([
            "",
            f"{labels['more']}. Ask the same question with ‘page {min(page + 1, total_pages)}’ or ‘show all’."
        ])
    return "\n".join(lines)
