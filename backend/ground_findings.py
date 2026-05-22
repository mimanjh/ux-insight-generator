"""
ground_findings.py — the "augmented generation" half of the RAG pipeline.

By the time this runs, analyze_screenshot.py has already produced the UX
findings. This module grounds each finding in a real NNG article:

  1. Build a search query from each finding (theme + title + observations).
  2. Retrieve the top candidate articles for each (retrieval.py).
  3. Make ONE Claude call that, per finding, picks at most one candidate
     that genuinely supports it — or declines with no citation.
  4. Attach the chosen citation back onto each finding.

Why a second model call instead of letting the first one cite from
memory? Because a model citing from memory invents plausible-sounding
articles that do not exist. RAG fixes that: Claude may only choose from
the candidate list we hand it, and we validate its answer against that
list. That constraint — "cite only what you were given, and decline if
nothing fits" — is the whole point of grounding.

Failure is non-fatal: if the index is missing or the call errors, every
finding still gets `citation: None` and the analysis returns normally.
"""

import json
import logging

from dotenv import load_dotenv
from anthropic import Anthropic

from backend.retrieval import IndexUnavailable, retrieve_batch

logger = logging.getLogger("uvicorn.error")

# Text-only reasoning over short snippets — no image. Same model family as
# the critique step for consistent judgment quality.
GROUNDING_MODEL = "claude-sonnet-4-5"
MAX_TOKENS = 1024

# How many candidate articles to retrieve and offer per finding. A small
# number keeps the prompt focused; the model still has a real choice.
CANDIDATES_PER_FINDING = 4

TOOL = {
    "name": "attach_citations",
    "description": (
        "Report which NNG article (if any) supports each UX finding. "
        "Call this exactly once, with one entry per finding index."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "citations": {
                "type": "array",
                "description": (
                    "One entry per finding, in any order. Every finding "
                    "index must appear exactly once."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "finding_index": {
                            "type": "integer",
                            "description": "0-based index of the finding.",
                        },
                        "article_id": {
                            "type": ["string", "null"],
                            "description": (
                                "The id of the chosen candidate article, "
                                "or null if no candidate genuinely supports "
                                "this finding. Must be one of the ids listed "
                                "as a candidate for THIS finding."
                            ),
                        },
                        "relevance_note": {
                            "type": ["string", "null"],
                            "description": (
                                "One sentence on how the article supports "
                                "this specific finding. null when article_id "
                                "is null."
                            ),
                        },
                    },
                    "required": [
                        "finding_index",
                        "article_id",
                        "relevance_note",
                    ],
                },
            }
        },
        "required": ["citations"],
    },
}

PROMPT_HEADER = """You are grounding UX critique findings in published UX research from the Nielsen Norman Group (NNG).

For each finding below, you are given a short list of candidate NNG articles retrieved by semantic similarity. Decide whether one of them genuinely supports the UX principle behind the finding.

Rules:
- Choose AT MOST ONE article per finding, and ONLY from that finding's own candidate list.
- Cite an article only if it genuinely backs the finding's reasoning. Loose topical overlap is not enough — the article should support *why this is a UX problem*.
- If no candidate is a strong match, set article_id to null. An honest "no citation" is better than a weak or misleading one.
- Never return an article_id that is not in the finding's candidate list.
- relevance_note: one sentence connecting the article to THIS finding. Use null when article_id is null.

Call the attach_citations tool exactly once, with an entry for every finding index shown below.

"""


def _finding_query(finding: dict) -> str:
    """Build the retrieval query string for one finding.

    We concatenate the theme and the finding's own words. This must stay
    roughly comparable to what build_index.py embeds for articles
    (title + summary) — both are short topical descriptions of a UX issue.
    """
    parts = [
        finding.get("theme", ""),
        finding.get("title", ""),
        finding.get("what_i_see", ""),
        finding.get("why_it_matters", ""),
    ]
    return " ".join(p for p in parts if p).strip()


def _build_prompt(findings: list[dict], candidates: list[list[dict]]) -> str:
    """Render the findings + their retrieved candidates into prompt text."""
    blocks = [PROMPT_HEADER]
    for i, (finding, cands) in enumerate(zip(findings, candidates)):
        blocks.append(f"--- FINDING {i} ---")
        blocks.append(f"theme: {finding.get('theme', '')}")
        blocks.append(f"title: {finding.get('title', '')}")
        blocks.append(f"what_i_see: {finding.get('what_i_see', '')}")
        blocks.append(f"why_it_matters: {finding.get('why_it_matters', '')}")
        blocks.append("Candidate articles:")
        for c in cands:
            blocks.append(f"  [{c['id']}] {c['title']}")
            blocks.append(f"      {c['summary']}")
        blocks.append("")
    return "\n".join(blocks)


def ground_findings(analysis: dict) -> dict:
    """Attach a grounded NNG citation to each finding in `analysis`.

    Mutates and returns `analysis`. Every finding gains a `citation` key:
    either None, or {article_id, title, url, relevance_note}.

    Never raises: any failure (no index, API error) degrades to all-None
    citations so the surrounding analysis still succeeds.
    """
    findings: list[dict] = analysis.get("findings", [])

    # Default every finding to no citation. If anything below fails we
    # return early and the schema is still consistent for the frontend.
    for f in findings:
        f["citation"] = None

    if not findings:
        return analysis

    # --- Retrieve ---
    queries = [_finding_query(f) for f in findings]
    try:
        candidates = retrieve_batch(queries, k=CANDIDATES_PER_FINDING)
    except IndexUnavailable as e:
        logger.warning("RAG skipped — %s", e)
        return analysis
    except Exception as e:
        logger.warning("RAG retrieval failed (%s) — skipping citations", e)
        return analysis

    # --- Augmented generation: one Claude call to choose citations ---
    prompt = _build_prompt(findings, candidates)
    try:
        load_dotenv(override=True)
        client = Anthropic()
        response = client.messages.create(
            model=GROUNDING_MODEL,
            max_tokens=MAX_TOKENS,
            tools=[TOOL],
            tool_choice={"type": "tool", "name": "attach_citations"},
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        logger.warning("RAG grounding call failed (%s) — skipping", e)
        return analysis

    tool_input = None
    for block in response.content:
        if block.type == "tool_use" and block.name == "attach_citations":
            tool_input = block.input
            break
    if tool_input is None:
        logger.warning("RAG grounding returned no tool_use — skipping")
        return analysis

    # --- Validate and attach ---
    # Trust nothing: the model can return an out-of-range index or an
    # article_id we never offered. Drop anything that does not check out —
    # that validation is what stops a hallucinated citation from shipping.
    for entry in tool_input.get("citations", []):
        idx = entry.get("finding_index")
        article_id = entry.get("article_id")
        if not isinstance(idx, int) or not (0 <= idx < len(findings)):
            continue
        if not article_id:
            continue  # model declined — finding keeps citation=None

        offered = {c["id"]: c for c in candidates[idx]}
        chosen = offered.get(article_id)
        if chosen is None:
            logger.warning(
                "RAG dropped hallucinated citation %r for finding %d",
                article_id,
                idx,
            )
            continue

        findings[idx]["citation"] = {
            "article_id": chosen["id"],
            "title": chosen["title"],
            "url": chosen["url"],
            "relevance_note": entry.get("relevance_note"),
        }

    cited = sum(1 for f in findings if f["citation"])
    logger.info("RAG grounded %d/%d findings with a citation", cited, len(findings))
    return analysis
