"""
Input guardrails for TravelMate AI.

Every user message passes through check_input() before any agent runs.
The checks are deliberately deterministic - regex and keyword rules, no
LLM call - for three reasons:

  * cost: the Groq free tier allows 8,000 tokens/minute across the whole
    request, and spending any of it to decide whether to spend the rest
    is poor economics,
  * latency: blocking a bad request should be instant, not a round trip,
  * reliability: a refusal that depends on a model call fails open when
    the model is rate limited.

Relevance is the one check that needs conversation context. "make it 5
days" contains no travel words at all, yet it is a perfectly good
follow-up, so is_followup relaxes the topic rule for threads that already
have history.
"""

import re
from dataclasses import dataclass

MIN_CHARS = 3
MAX_CHARS = 2000


@dataclass
class Verdict:
    """Outcome of the guardrail pass."""

    allowed: bool
    category: str  # ok | empty | too_short | too_long | injection | unsafe | off_topic
    message: str = ""  # user-facing refusal text, empty when allowed


# --- 1. prompt-injection / instruction-override attempts -------------------
INJECTION_PATTERNS = [
    r"\bignore\s+(all\s+|any\s+)?(previous|prior|above|earlier)\b",
    r"\bdisregard\s+(all\s+|any\s+)?(previous|prior|above|earlier|instructions)\b",
    r"\b(reveal|show|print|repeat|output)\s+(me\s+)?(your\s+)?(system\s+prompt|instructions|prompt)\b",
    r"\byou\s+are\s+now\s+(a|an)\b",
    r"\bact\s+as\s+(a|an)\s+(?!travel|trip)",
    r"\bpretend\s+(you\s+are|to\s+be)\b",
    r"\bdeveloper\s+mode\b",
    r"\bjailbreak\b",
    r"<\s*/?\s*(system|assistant)\s*>",
    r"\bforget\s+(everything|all)\b",
]

# --- 2. unsafe travel requests --------------------------------------------
# Scoped to travel-adjacent wrongdoing. Single words like "visa" or
# "border" are legitimate questions, so each pattern pairs a subject with
# an illicit intent.
UNSAFE_PATTERNS = [
    (r"\b(smuggl\w*|traffick\w*)\b", "smuggling or trafficking"),
    (r"\b(fake|forged|forge|counterfeit|fraudulent)\s+(passport|visa|id|identity|ticket|boarding\s*pass)\b",
     "document fraud"),
    (r"\b(evade|avoid|bypass|sneak\s+past|get\s+around)\s+(customs|immigration|border|security\s+check)\b",
     "evading border or security controls"),
    (r"\b(cross|enter)\w*\s+(the\s+)?border\s+(illegally|undetected|without\s+(a\s+)?visa)\b",
     "illegal border crossing"),
    (r"\bhow\s+to\s+(carry|bring|hide)\b.{0,30}\b(drugs|narcotics|cocaine|heroin|weapon|gun|explosive)\b",
     "transporting prohibited items"),
    (r"\b(child\s+sex|sex\s+tourism|underage)\b", "exploitation"),
    (r"\b(hide|conceal)\b.{0,20}\b(from\s+)?(airport\s+)?(security|scanner|x-?ray)\b",
     "concealing items from security screening"),
]

# --- 3. relevance ---------------------------------------------------------
# Matched as prefixes, so "hostel" also catches "hostels".
TRAVEL_TERMS = [
    "travel", "trip", "flight", "fly", "flew", "flying", "airline", "airport",
    "hotel", "hostel", "stay", "accommodation", "resort", "airbnb", "lodge",
    "itinerar", "plan", "visit", "tour", "sightsee", "holiday", "vacation",
    "destination", "journey", "booking", "book", "depart", "arriv", "layover",
    "visa", "passport", "backpack", "cruise", "weekend", "getaway", "honeymoon",
    "budget", "day", "night", "week", "month", "beach", "mountain", "city",
    "countr", "train", "bus", "car", "rental", "border", "crossing", "immigration",
    "customs", "terminal", "baggage", "luggage", "currency", "sim", "roam",
]


# Questions clearly aimed at something other than travel.
OFF_TOPIC_PATTERNS = [
    r"\bwrite\s+(me\s+)?(a\s+)?(python|java|javascript|c\+\+|sql|code|program|script|function)\b",
    r"\b(debug|refactor|compile)\s+(this|my)\b",
    r"\bwho\s+(is|was)\s+the\s+(president|prime\s+minister|ceo)\b",
    r"\b(solve|calculate)\b.{0,20}\b(equation|integral|derivative)\b",
    r"\b(medical|legal|tax|investment)\s+advice\b",
    r"\bwrite\s+(me\s+)?(an?\s+)?(essay|poem|song|story|email)\s+about\b",
]


def _looks_like_travel(text):
    """
    True when the message is recognisably about travel.

    Checked three ways, cheapest first: a travel term, a bare IATA code
    ("DEL to KTM"), then the project's own gazetteer - reusing
    find_location_mentions means every country, alias and city the flight
    tool already knows counts as travel intent, without a second word list.
    """
    lowered = text.lower()
    words = re.findall(r"[a-z]+", lowered)

    if any(w.startswith(stem) for w in words for stem in TRAVEL_TERMS):
        return True

    if len(re.findall(r"\b[A-Z]{3}\b", text)) >= 1:
        return True

    try:
        from tools.flight_tool import find_location_mentions

        if find_location_mentions(text):
            return True
    except Exception:
        # Never fail the request because the gazetteer could not load.
        pass

    return False


def _matches(patterns, text):
    for pattern in patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return pattern
    return None


def check_input(user_input, is_followup=False):
    """
    Validate a user message before any agent runs.

    is_followup: True when the thread already has history, which relaxes
    the relevance rule so short follow-ups ("make it 5 days") pass.
    """
    text = (user_input or "").strip()

    # --- structural -------------------------------------------------------
    if not text:
        return Verdict(False, "empty", "Please tell me where you'd like to travel.")

    if len(text) < MIN_CHARS:
        return Verdict(
            False,
            "too_short",
            "That's a bit short for me to work with - try naming a destination, "
            "for example \"5 days in Nepal from Delhi\".",
        )

    if len(text) > MAX_CHARS:
        return Verdict(
            False,
            "too_long",
            f"That request is {len(text)} characters; please keep it under {MAX_CHARS}.",
        )

    # --- safety -----------------------------------------------------------
    if _matches(INJECTION_PATTERNS, text):
        return Verdict(
            False,
            "injection",
            "I can only help with travel planning, and I can't take instructions "
            "that change how I work. Tell me about the trip you have in mind instead.",
        )

    for pattern, label in UNSAFE_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return Verdict(
                False,
                "unsafe",
                f"I can't help with that - it involves {label}. I'm happy to plan "
                "legitimate travel: flights, hotels and itineraries.",
            )

    # --- relevance --------------------------------------------------------
    if _matches(OFF_TOPIC_PATTERNS, text):
        return Verdict(
            False,
            "off_topic",
            "I'm a travel planner, so that's outside what I can do. Ask me about "
            "flights, hotels or an itinerary and I'll help.",
        )

    if not is_followup and not _looks_like_travel(text):
            return Verdict(
                False,
                "off_topic",
                "I plan trips - flights, hotels and itineraries. Try something like "
                "\"Plan a 5 day trip from Delhi to Kathmandu\".",
            )

    return Verdict(True, "ok")


# ===========================================================================
# Model-based layer
#
# The deterministic rules above are cheap and cannot be rate limited, but
# they only catch phrasings someone thought to write down. This layer adds
# an LLM classifier for what they miss - obfuscation, paraphrase, other
# languages, intent spread across a sentence.
#
# It runs as a SECOND pass, and by default only when the rules are not
# already confident, because the Groq free tier allows 8,000 tokens per
# minute across the whole request. A clear travel request still costs
# nothing; a suspicious-looking one pays ~200 tokens for a real judgement.
# ===========================================================================

import os

# always -> classify every accepted request
# auto   -> classify only when SOFT_SIGNALS fire (default)
# off    -> deterministic rules only
GUARDRAIL_MODE = os.getenv("GUARDRAIL_MODE", "auto").strip().lower()

# Words that are perfectly legitimate on their own but often appear in
# requests worth a closer look. These do not block anything; they only
# decide whether the classifier is worth its tokens.
SOFT_SIGNALS = [
    r"\b(passport|visa|customs|immigration|border|checkpoint)\b",
    r"\b(cash|undeclared|declare|duty[\s-]?free|limit)\b",
    r"\b(medicine|medication|pills|substance|powder|liquid)\b",
    r"\b(weapon|knife|ammo|firearm|blade)\b",
    r"\b(without|avoid|skip|hide|conceal|unnoticed|undetected|discreet)\b",
    r"\b(illegal|illegally|unlawful|black\s?market|bribe|bribing)\b",
    r"\b(minor|child|underage|someone\s+else'?s)\b",
    r"\b(ignore|instead\s+of\s+your|your\s+rules|pretend|roleplay|role-play)\b",
]


def needs_model_review(text):
    """True when the classifier is worth calling for this message."""
    if GUARDRAIL_MODE == "off":
        return False
    if GUARDRAIL_MODE == "always":
        return True
    return _matches(SOFT_SIGNALS, text) is not None


CLASSIFIER_SYSTEM = (
    "You screen requests for a travel planning assistant. Decide two "
    "things about the user's message:\n"
    "1. SAFE or UNSAFE. Unsafe means it seeks help with smuggling, "
    "trafficking, document fraud, evading customs/immigration/security, "
    "transporting prohibited items, exploitation, or any other illegal act "
    "- even when phrased innocently, misspelled, obfuscated, or split "
    "across a sentence.\n"
    "2. ON-TOPIC or OFF-TOPIC. On-topic means travel: flights, hotels, "
    "itineraries, destinations, visas, packing, transport, budgets. A short "
    "follow-up to an earlier travel request is on-topic.\n\n"
    "Asking about rules is not unsafe: 'what is the duty-free limit' or "
    "'do I need a visa' are safe and on-topic. Seeking to break those rules "
    "is unsafe. Do not refuse ordinary travel questions."
)


def classify_with_model(text, llm, is_followup=False):
    """
    Second-pass LLM screening. Returns a Verdict.

    llm is injected rather than imported so this module stays independent
    of backend.py - no circular import, and it can be tested with a stub.

    Fails OPEN on any model error: the deterministic rules have already
    cleared this message, and a rate-limited classifier should not take
    the app down. Flip to fail-closed if your risk tolerance differs.
    """
    from pydantic import BaseModel, Field

    class Screening(BaseModel):
        safe: bool = Field(description="False if the request seeks help with anything illegal.")
        on_topic: bool = Field(description="False if the request is not about travel.")
        category: str = Field(description="One of: ok, unsafe, off_topic.")
        reason: str = Field(description="One short sentence, addressed to the user.")

    note = " This is a follow-up in an existing travel conversation." if is_followup else ""

    try:
        result = llm.with_structured_output(Screening).invoke(
            [
                {"role": "system", "content": CLASSIFIER_SYSTEM},
                {"role": "user", "content": f'Message: "{text}"{note}'},
            ]
        )
    except Exception as exc:  # rate limit, network, schema failure
        return Verdict(True, "model_unavailable", f"classifier skipped: {exc}"[:120])

    if not result.safe:
        return Verdict(
            False,
            "unsafe_model",
            f"I can't help with that. {result.reason} I'm happy to plan "
            "legitimate travel: flights, hotels and itineraries.",
        )

    if not result.on_topic and not is_followup:
        return Verdict(
            False,
            "off_topic_model",
            f"I'm a travel planner. {result.reason} Ask me about flights, "
            "hotels or an itinerary and I'll help.",
        )

    return Verdict(True, "ok")


def check_input_full(user_input, llm=None, is_followup=False):
    """
    Both layers, cheapest first.

    1. Deterministic rules. A block here is final - no tokens spent.
    2. The classifier, when GUARDRAIL_MODE and SOFT_SIGNALS call for it.

    Pass llm=None to get the deterministic behaviour only.
    """
    verdict = check_input(user_input, is_followup=is_followup)

    if not verdict.allowed:
        return verdict

    if llm is None or not needs_model_review(user_input or ""):
        return verdict

    return classify_with_model(user_input, llm, is_followup=is_followup)
