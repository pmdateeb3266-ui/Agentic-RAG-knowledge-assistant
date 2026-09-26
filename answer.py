import re

import ollama

from config import LLM_MODEL, MAX_CONTEXT_CHARACTERS, MAX_OUTPUT_TOKENS, OLLAMA_NUM_CTX
from evidence_processor import deterministic_summary, render_structured_records, verified_facts_text
from query_analyzer import analyze_query
from retriever import retrieve
from result_exporter import export_csv


question = input("\nAsk a question about your documents: ")
plan = analyze_query(question)
bundle = retrieve(question, plan=plan)
results = bundle["results"]
facts = bundle["facts"]

if not results and not facts:
    print("\nI could not find relevant evidence.")
    raise SystemExit

# Exact/list/ranked structured answers are complete, deterministic renderings.
# Qwen is intentionally bypassed so it cannot select, translate, or omit records.
if plan["intent"] == "STRUCTURED_QUERY":
    if plan.get("export_format") == "csv":
        print("\n" + "=" * 70)
        print("ANSWER")
        print("=" * 70)
        print(deterministic_summary(plan, facts))
        print(f"\nCSV exported: {export_csv(question, results, plan.get('output_language', 'en'))}")
        print(f"Exported records: {len(results)}")
        raise SystemExit
    print("\n" + "=" * 70)
    print("ANSWER")
    print("=" * 70)
    print(render_structured_records(plan, facts, results) if results else deterministic_summary(plan, facts))
    print("\n" + "=" * 70)
    print("VERIFIED PIPELINE RESULT")
    print("=" * 70)
    print(deterministic_summary(plan, facts))
    raise SystemExit


def build_context(items):
    blocks = []
    selected = []
    total = 0

    for number, result in enumerate(items, start=1):
        metadata = result["metadata"]
        block = f"""
[S{number}]
Document: {metadata.get('source', '')}
Page: {metadata.get('page', '')}
Scope: {result.get('scope', '')}
Retrieval: {result.get('method', '')}
Extraction: {metadata.get('extraction_method', 'unknown')}
OCR confidence: {metadata.get('ocr_confidence', '')}
Match fields: {', '.join(result.get('match_fields', []))}

EVIDENCE:
{result['document']}
"""
        if total + len(block) > MAX_CONTEXT_CHARACTERS:
            break
        blocks.append(block)
        selected.append((number, result))
        total += len(block)

    return "\n".join(blocks), selected


evidence, selected_results = build_context(results)
verified = verified_facts_text(plan, facts)

prompt = f"""
/no_think

You are a careful multilingual document research assistant.
Answer only from the verified facts and evidence supplied below.

QUERY PLAN:
Input language: {plan['language']}
Required output language: {plan.get('output_language', plan['language'])}
Intent: {plan['intent']}
Target: {plan.get('target') or 'not identified'}
Structured scope: {plan.get('structured_scope')}
Filters: {plan.get('filters')}
Aggregation: {plan.get('aggregation') or 'none'}
Group by: {plan.get('group_by') or 'none'}
Document scope: {plan.get('document_scope')}

VERIFIED FACTS:
{verified or 'No deterministic aggregate facts were computed.'}

STRICT RULES:
1. Never use outside knowledge.
2. Never invent missing information.
3. Answer in the required output language shown above. An explicit language request always wins.
4. Cite document-derived factual details using [S1], [S2], etc.
5. Never cite a source that does not support the claim.
6. Preserve names in their original script and preserve serial numbers, house numbers, ages and EPIC IDs exactly as extracted. Never invent a translated name.
7. OCR can contain errors. If OCR is uncertain, say so rather than silently correcting it.
8. For STRUCTURED_QUERY, all filters and aggregates were computed deterministically over the full structured dataset. Never recalculate or estimate them from the limited LLM context.
9. Range filters such as age between/above/below are exact deterministic filters, not semantic similarity.
10. If the user asks for details, summarize only the verified matching records supplied below.
11. For semantic questions, explain only what the retrieved evidence supports.
12. Keep the answer concise and useful.

USER QUESTION:
{question}

DOCUMENT EVIDENCE:
{evidence or 'No evidence snippets were required.'}

Return only the final answer, not reasoning.
"""


def extract_content(response):
    message = getattr(response, "message", None)
    if message is not None:
        content = getattr(message, "content", None)
        if content is not None:
            return str(content)
    if isinstance(response, dict):
        message = response.get("message", {})
        if isinstance(message, dict):
            return str(message.get("content", "") or "")
    return ""


def clean_answer(text):
    text = (text or "").strip()
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    return text.strip()


def call_model(prompt_text, retry=False):
    kwargs = {
        "model": LLM_MODEL,
        "messages": [{"role": "user", "content": prompt_text}],
        "options": {
            "temperature": 0,
            "num_ctx": OLLAMA_NUM_CTX,
            "num_predict": MAX_OUTPUT_TOKENS * (2 if retry else 1),
        },
    }
    try:
        response = ollama.chat(**kwargs, think=False)
    except TypeError:
        response = ollama.chat(**kwargs)
    return clean_answer(extract_content(response))


print(f"\nQuery intent: {plan['intent']}")
if plan.get("target"):
    print(f"Detected target: {plan['target']} ({plan.get('target_confidence') or 'unknown'} confidence)")
print(f"Retrieved {len(results)} evidence item(s).")
print(f"Sending {len(selected_results)} item(s) to {LLM_MODEL}.")
print("\nGenerating answer...\n")

answer = ""
try:
    answer = call_model(prompt)
    if not answer:
        answer = call_model(
            prompt + "\nIMPORTANT: Your previous response was empty. Return a short final answer only.",
            retry=True,
        )
except Exception as error:
    print("Ollama generation error:")
    print(error)

summary = deterministic_summary(plan, facts)

print("=" * 70)
print("ANSWER")
print("=" * 70)
if answer:
    print(answer)
elif summary:
    print(summary)
else:
    print("Retrieval succeeded, but the local LLM returned no usable final answer after retry.")

print("\n" + "=" * 70)
print("VERIFIED PIPELINE RESULT")
print("=" * 70)
print(summary or "No deterministic aggregate result for this query.")

print("\n" + "=" * 70)
print("SOURCES SENT TO LLM")
print("=" * 70)
for source_number, result in selected_results:
    metadata = result["metadata"]
    print(
        f"[S{source_number}] {metadata.get('source', '')} - page {metadata.get('page', '')} "
        f"- {result.get('scope', '')} - {result.get('method', '')}"
    )
