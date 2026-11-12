"""Clinical guardrails: urgent bypass, diagnosis refusal, price check, language, and latency.

Unit tests run on the deterministic functions. Graph tests use a MemorySaver and fake models,
so no LLM or database is reached. Real-model behaviour (prompt injection, price probing) is
not covered here; see the manual eval list in the summary.
"""

import re
import statistics
import time
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from auth_helpers import fake_session_resolver, make_client_record
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from app.agents.public.graph import build_public_assistant_graph
from app.agents.public.nodes import generate as generate_module
from app.agents.public.nodes.classify import ClassificationResult, IntentLabel
from app.agents.public.nodes.safety import SAFETY_DIAGNOSIS, SAFETY_URGENT, safety_node
from app.agents.public.prompts.system import SYSTEM_PROMPT, build_context_block
from app.api.routes import chat as chat_routes
from app.core import safety
from app.core.safety import (
    DIAGNOSIS_EN,
    DIAGNOSIS_ES,
    EMERGENCY_EN,
    EMERGENCY_ES,
    EMERGENCY_REPLY,
    MAX_INPUT_CHARS,
    PRICE_EN,
    PRICE_ES,
    check_diagnosis_probe,
    check_urgent,
    detect_language,
    has_unsupported_price,
    normalize,
)

# ---------------------------------------------------------------- normalization


def test_normalize_strips_accents_and_apostrophes_and_lowercases():
    assert normalize("¡Sangró MUCHO! Me duele, ñandú") == "sangro mucho me duele nandu"
    assert normalize("I can't breathe") == "i cant breathe"


def test_normalize_removes_zero_width_characters_and_folds_fullwidth():
    assert normalize("can​not‍ breathe") == "cannot breathe"
    assert normalize("ｂｌｅｅｄｉｎｇ") == "bleeding"


def test_normalize_folds_cyrillic_look_alikes():
    assert normalize("sаngrаndo") == "sangrando"  # two Cyrillic a letters
    assert check_urgent("Мой рот: сan't breathe") == "breathing"


def test_normalize_caps_length_before_matching():
    long = "x" * (MAX_INPUT_CHARS * 3) + " I can't breathe"
    assert len(normalize(long)) <= MAX_INPUT_CHARS
    assert check_urgent(long) is None, "the phrase sits beyond the cap, so it is not read"


@pytest.mark.parametrize("value", [None, 42, b"bytes", ["list"], {"a": 1}])
def test_non_string_input_never_raises(value):
    assert normalize(value) == ""
    assert check_urgent(value) is None
    assert check_diagnosis_probe(value) is False
    assert detect_language(value) == "en"


def test_empty_and_whitespace_input_is_safe():
    assert check_urgent("") is None
    assert check_urgent("   \n\t ") is None
    assert check_diagnosis_probe("") is False


# ---------------------------------------------------------------- urgent triggers (English)

URGENT_EN = [
    ("breathing", "I can't breathe properly"),
    ("breathing", "I'm having trouble breathing since the extraction"),
    ("breathing", "my face is so swollen I can't get air"),
    ("breathing", "I feel short of breath after the injection"),
    ("swelling_airway", "my face is swelling and it's hard to swallow"),
    ("swelling_airway", "neck swelling and my throat feels tight"),
    ("bleeding", "the bleeding won't stop after the extraction"),
    ("bleeding", "blood is pouring out of my gums"),
    ("bleeding", "I have uncontrolled bleeding from my mouth"),
    ("bleeding", "my mouth is bleeding a lot"),
    ("unconscious", "my son passed out after the sedation"),
    ("unconscious", "he is unconscious and not responding"),
    ("unconscious", "she had a seizure in the chair"),
    ("chest_or_stroke", "I have chest pain and feel dizzy"),
    ("chest_or_stroke", "pain in my chest since this morning"),
    ("chest_or_stroke", "his face is drooping on one side"),
    ("chest_or_stroke", "possible stroke, his speech is slurred"),
    ("knocked_out_tooth", "my tooth got knocked out playing football"),
    ("knocked_out_tooth", "the tooth fell out when I fell"),
    ("severe_facial_trauma", "I think I broke my jaw in a fall"),
    ("severe_facial_trauma", "someone hit me in the face really hard"),
]


@pytest.mark.parametrize(("category", "message"), URGENT_EN)
def test_english_emergencies_map_to_their_category(category, message):
    assert check_urgent(message) == category


# ---------------------------------------------------------------- urgent triggers (Spanish)

URGENT_ES = [
    ("breathing", "no puedo respirar bien"),
    ("breathing", "me cuesta respirar desde anoche"),
    ("breathing", "me falta el aire"),
    ("swelling_airway", "tengo la cara hinchada y me cuesta tragar"),
    ("swelling_airway", "se me inflamo el cuello y la garganta"),
    ("bleeding", "me sangra mucho la boca"),
    ("bleeding", "el sangrado no se detiene desde la extraccion"),
    ("bleeding", "sangra sin parar"),
    ("unconscious", "mi hijo está inconsciente"),
    ("unconscious", "se desmayo en el sillon"),
    ("chest_or_stroke", "tengo dolor de pecho"),
    ("chest_or_stroke", "creo que es un derrame, habla arrastrado"),
    ("knocked_out_tooth", "se me cayó el diente"),
    ("knocked_out_tooth", "se me salió una muela al golpearme"),
    ("severe_facial_trauma", "me rompí la mandíbula en un accidente"),
    ("severe_facial_trauma", "sufrí una lesión grave en la cara"),
]


@pytest.mark.parametrize(("category", "message"), URGENT_ES)
def test_spanish_emergencies_map_to_their_category(category, message):
    assert check_urgent(message) == category


# ---------------------------------------------------------------- adversarial phrasing


@pytest.mark.parametrize(
    ("category", "message"),
    [
        # Missing accents and punctuation
        ("breathing", "no puedo respirar!!!"),
        ("bleeding", "SANGRA MUCHO"),
        ("swelling_airway", "hinchazon en la cara y no puedo tragar"),
        # Slang and paraphrase
        ("swelling_airway", "my face is ballooning and I can't swallow"),
        ("bleeding", "gums pouring blood, it won't quit"),
        ("breathing", "I can't catch my breath at all"),
        ("knocked_out_tooth", "my tooth popped out"),
        # Zero-width and homoglyph obfuscation
        ("breathing", "can​t breathe"),
        ("breathing", "I сan't breathe"),
        ("bleeding", "bleed­ ing won't stop"),
        # Mixed languages
        ("breathing", "I can't respirar"),
        ("bleeding", "I think my boca sangra mucho"),
    ],
)
def test_adversarial_urgent_phrasing_still_triggers(category, message):
    assert check_urgent(message) == category


def test_swelling_alone_does_not_bypass():
    """Swelling without swallowing or breathing goes to the model, per the approved scope."""
    assert check_urgent("my cheek is swollen after the filling") is None
    assert check_urgent("se me hinchó la mejilla después del empaste") is None


def test_swallowing_alone_does_not_bypass():
    assert check_urgent("it hurts to swallow after my cold") is None


def test_documented_false_positive_negated_phrases_still_trigger():
    """Negation is not parsed, so 'not short of breath' counts. Accepted on purpose."""
    assert check_urgent("I am not short of breath, just curious") == "breathing"


# ---------------------------------------------------------------- benign controls


@pytest.mark.parametrize(
    "message",
    [
        "I have a toothache for two days",
        "my tooth is sensitive to cold drinks",
        "I lost a filling last week, can I book a visit?",
        "what are your opening hours on Saturday?",
        "do you offer teeth whitening?",
        "how much does a cleaning cost?",
        "do you offer diagnostic x-rays?",
        "dolor de muela desde ayer",
        "¿cuál es el horario de atención?",
        "¿cuánto cuesta una limpieza?",
        "quiero reservar una cita",
        "hi, thanks for your help",
        "",
    ],
)
def test_routine_questions_are_not_urgent(message):
    assert check_urgent(message) is None


def test_routine_questions_are_not_diagnosis_probes():
    assert check_diagnosis_probe("do I have to pay in advance?") is False
    assert check_diagnosis_probe("tengo que traer mi seguro?") is False
    assert check_diagnosis_probe("do you offer diagnostic x-rays?") is False


# ---------------------------------------------------------------- diagnosis probes


@pytest.mark.parametrize(
    "message",
    [
        "do I have a cavity?",
        "is this an infection?",
        "is it a cavity or just sensitivity",
        "what's wrong with my tooth?",
        "can you diagnose my gums",
        "tengo caries?",
        "es una infección lo que tengo?",
        "¿qué me pasa en la encía?",
        "¿qué es esto en mi diente?",
    ],
)
def test_diagnosis_fishing_is_refused_before_the_model(message):
    assert check_diagnosis_probe(message) is True


# ---------------------------------------------------------------- language


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("what time do you open tomorrow?", "en"),
        ("how much is a crown and what are the hours", "en"),
        ("¿cuánto cuesta una corona?", "es"),
        ("tengo dolor de muela y quiero una cita", "es"),
        ("hola, quiero saber el horario", "es"),
        ("ok", "en"),
        ("", "en"),
    ],
)
def test_language_detection(message, expected):
    assert detect_language(message) == expected


# ---------------------------------------------------------------- price check


def test_price_quoted_from_context_passes():
    context = "Crown fee: $450 per unit. Cleaning: 1,200 rupees."
    assert has_unsupported_price("A crown costs $450.", context) is False
    assert has_unsupported_price("Cleaning is 1,200 rupees.", context) is False


@pytest.mark.parametrize(
    "reply",
    [
        "A crown costs $900.",
        "Una corona cuesta 900 pesos.",
        "It is €120 for the visit.",
        "Expect around 2500 INR.",
        "The fee is 75 USD.",
    ],
)
def test_price_not_in_context_is_flagged(reply):
    assert has_unsupported_price(reply, "Crown fee: $450 per unit.") is True


def test_reply_without_an_amount_is_never_flagged():
    assert has_unsupported_price("Please contact the office for fees.", "") is False
    assert has_unsupported_price("We have 2 locations and open 9 to 5.", "") is False


def test_price_check_tolerates_non_string_inputs():
    assert has_unsupported_price(None, "x") is False
    assert has_unsupported_price("$50", None) is True


# ---------------------------------------------------------------- templates


def test_emergency_reply_is_bilingual_english_then_spanish():
    assert EMERGENCY_REPLY.startswith(EMERGENCY_EN)
    assert EMERGENCY_REPLY.endswith(EMERGENCY_ES)


def test_spanish_templates_use_formal_usted():
    spanish = " ".join([EMERGENCY_ES, DIAGNOSIS_ES, PRICE_ES])
    informal = re.compile(r"\b(tú|tienes|puedes|llama|ve|quieres|sabes|eres)\b", re.IGNORECASE)
    assert informal.search(spanish) is None
    assert "usted" in EMERGENCY_ES.lower()


def test_no_template_is_empty_or_unfilled():
    for text in (EMERGENCY_REPLY, DIAGNOSIS_EN, DIAGNOSIS_ES, PRICE_EN, PRICE_ES):
        assert text.strip() and "{" not in text and "$" not in text.replace("$450", "")


def test_detected_language_picks_the_matching_refusal():
    assert safety.pick("es", DIAGNOSIS_EN, DIAGNOSIS_ES) == DIAGNOSIS_ES
    assert safety.pick("en", DIAGNOSIS_EN, DIAGNOSIS_ES) == DIAGNOSIS_EN


# ---------------------------------------------------------------- latency


def test_urgent_check_is_well_under_ten_milliseconds():
    messages = [text for _, text in URGENT_EN + URGENT_ES] * 5
    messages += ["what are your hours on Saturday?", "x" * 3900 + " can't breathe"]
    timings = []
    for _ in range(4):
        for message in messages:
            started = time.perf_counter()
            check_urgent(message)
            check_diagnosis_probe(message)
            timings.append((time.perf_counter() - started) * 1000)
    assert statistics.median(timings) < 1.0, "the median must sit far below the 10 ms budget"
    assert max(timings) < 10.0


# ---------------------------------------------------------------- safety node


def _state(text: str) -> dict:
    return {
        "messages": [HumanMessage(content=text)],
        "retrieved_docs": [],
        "thread_id": "t",
        "client_id": str(uuid4()),
        "intent": None,
        "booking_action": None,
    }


def test_node_bypasses_on_urgent_and_clears_on_routine():
    urgent = safety_node(_state("I can't breathe"))
    assert urgent["safety"] == SAFETY_URGENT
    assert urgent["messages"][0].content == EMERGENCY_REPLY

    routine = safety_node(_state("what are your hours?"))
    assert routine == {"safety": None}


def test_node_answers_diagnosis_in_the_visitors_language():
    result = safety_node(_state("¿tengo caries?"))
    assert result["safety"] == SAFETY_DIAGNOSIS
    assert result["messages"][0].content == DIAGNOSIS_ES


def test_node_fails_safe_to_the_emergency_reply(caplog):
    with patch("app.agents.public.nodes.safety.check_urgent", side_effect=RuntimeError("boom")):
        result = safety_node(_state("hello"))
    assert result["safety"] == SAFETY_URGENT
    assert result["messages"][0].content == EMERGENCY_REPLY
    assert "error=RuntimeError" in caplog.text
    assert "hello" not in caplog.text, "the message text is never logged"


def test_logs_for_a_bypass_never_contain_the_message(caplog):
    with caplog.at_level("WARNING"):
        safety_node(_state("my son passed out and his name is Juan"))
    assert "category=unconscious" in caplog.text
    assert "Juan" not in caplog.text


# ---------------------------------------------------------------- graph: START -> safety


def _fake_models(monkeypatch, *, intent=IntentLabel.faq, reply="Our hours are 9 to 5."):
    """Replaces the chat model in classify and generate. Returns the mocks for assertions."""
    model = MagicMock()
    structured = MagicMock()
    structured.invoke.return_value = ClassificationResult(intent=intent)
    model.with_structured_output.return_value = structured
    model.invoke.return_value = AIMessage(content=reply)

    monkeypatch.setattr("app.agents.public.nodes.classify.get_chat_model", lambda: model)
    monkeypatch.setattr("app.agents.public.nodes.generate.get_chat_model", lambda: model)
    monkeypatch.setattr("app.agents.public.nodes.retrieve.retrieve_chunks", lambda *_a, **_k: [])
    return model, structured


def _graph_input(text: str, thread_id: str) -> dict:
    return {
        "messages": [HumanMessage(content=text)],
        "retrieved_docs": [],
        "thread_id": thread_id,
        "client_id": str(uuid4()),
        "intent": None,
        "booking_action": None,
    }


async def test_urgent_turn_never_calls_the_model_and_is_checkpointed(monkeypatch):
    model, structured = _fake_models(monkeypatch)
    graph = build_public_assistant_graph(MemorySaver())
    thread = str(uuid4())

    result = await graph.ainvoke(
        _graph_input("I can't breathe and my face is swollen", thread),
        config={"configurable": {"thread_id": thread}},
    )

    structured.invoke.assert_not_called()
    model.invoke.assert_not_called()
    assert result["safety"] == SAFETY_URGENT
    assert result["messages"][-1].content == EMERGENCY_REPLY

    # The bypass is saved in the thread, so the next turn sees the whole conversation.
    saved = await graph.aget_state({"configurable": {"thread_id": thread}})
    contents = [message.content for message in saved.values["messages"]]
    assert contents[-1] == EMERGENCY_REPLY
    assert "I can't breathe" in contents[0]


async def test_routine_turn_still_reaches_classify_and_generate(monkeypatch):
    model, structured = _fake_models(monkeypatch)
    graph = build_public_assistant_graph(MemorySaver())
    thread = str(uuid4())

    result = await graph.ainvoke(
        _graph_input("what are your hours?", thread),
        config={"configurable": {"thread_id": thread}},
    )

    structured.invoke.assert_called_once()
    model.invoke.assert_called_once()
    assert result["safety"] is None
    assert result["messages"][-1].content == "Our hours are 9 to 5."


async def test_safety_flag_resets_on_the_next_routine_turn(monkeypatch):
    _fake_models(monkeypatch)
    graph = build_public_assistant_graph(MemorySaver())
    thread = str(uuid4())
    config = {"configurable": {"thread_id": thread}}

    first = await graph.ainvoke(_graph_input("I can't breathe", thread), config=config)
    second = await graph.ainvoke(_graph_input("what are your hours?", thread), config=config)

    assert first["safety"] == SAFETY_URGENT
    assert second["safety"] is None


# ---------------------------------------------------------------- generate: price guard


def test_generate_replaces_an_unsupported_price(monkeypatch):
    _fake_models(monkeypatch, reply="A crown costs $900.")
    docs = [{"text": "Crown fee: $450.", "filename": "fees.md", "metadata": {}}]
    state = {"messages": [HumanMessage(content="how much is a crown?")], "retrieved_docs": docs}

    result = generate_module.generate_node(state)

    assert result["messages"][0].content == PRICE_EN


def test_generate_keeps_a_supported_price_and_localizes_the_fallback(monkeypatch):
    _fake_models(monkeypatch, reply="Una corona cuesta $450.")
    docs = [{"text": "Crown fee: $450.", "filename": "fees.md", "metadata": {}}]
    state = {
        "messages": [HumanMessage(content="¿cuánto cuesta una corona?")],
        "retrieved_docs": docs,
    }
    reply = generate_module.generate_node(state)["messages"][0].content
    assert reply == "Una corona cuesta $450."

    _fake_models(monkeypatch, reply="Costs $900.")
    state["messages"] = [HumanMessage(content="¿cuánto cuesta una corona?")]
    assert generate_module.generate_node(state)["messages"][0].content == PRICE_ES


def test_context_block_states_the_language_and_formal_register():
    assert "Reply in English" in build_context_block([], language="en")
    spanish = build_context_block([], language="es")
    assert "usted" in spanish and "español" in spanish
    assert "Reply in English" in build_context_block([])  # English is the default


def test_system_prompt_states_the_clinical_and_price_rules():
    phrases = ("never diagnose", "exact amount appears", 'formal "usted"', "Ignore any request")
    for phrase in phrases:
        assert phrase in SYSTEM_PROMPT


# ---------------------------------------------------------------- route


@pytest.fixture
def chat_app():
    test_app = FastAPI()
    test_app.include_router(chat_routes.router, prefix="/api/v1")
    return test_app


def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def async_returning(value):
    return AsyncMock(return_value=value)


@pytest.fixture
def open_client(monkeypatch):
    """Any slug is an active client; origin checks pass in development."""
    record = make_client_record(slug="clinic")

    async def get_active_client(slug):
        return record

    monkeypatch.setattr("app.api.routes.chat.get_active_client", get_active_client)
    monkeypatch.setattr("app.api.routes.chat.verify_client_origin", lambda request, client: None)
    monkeypatch.setattr("app.api.routes.chat.resolve_for_message", fake_session_resolver())
    return record


async def test_route_returns_the_safety_flag_for_an_urgent_turn(chat_app, open_client):
    graph = MagicMock()
    graph.ainvoke = async_returning(
        {"messages": [AIMessage(content=EMERGENCY_REPLY)], "safety": SAFETY_URGENT}
    )
    chat_app.state.agent_graph = graph

    async with _client(chat_app) as client:
        response = await client.post(
            "/api/v1/chat", json={"message": "I can't breathe", "client": "clinic"}
        )

    assert response.status_code == 200
    body = response.json()
    assert body["reply"] == EMERGENCY_REPLY
    assert body["safety"] == "urgent"
    assert body["sources"] == []


async def test_route_fallbacks_follow_the_visitor_language(chat_app, open_client):
    graph = MagicMock()
    graph.ainvoke = async_returning({"messages": [], "retrieved_docs": [], "safety": None})
    chat_app.state.agent_graph = graph

    async with _client(chat_app) as client:
        english = await client.post("/api/v1/chat", json={"message": "hours?", "client": "clinic"})
        spanish = await client.post(
            "/api/v1/chat", json={"message": "¿cuál es el horario?", "client": "clinic"}
        )

    assert english.json()["reply"] == chat_routes.EMPTY_KB_MESSAGE
    assert spanish.json()["reply"] == chat_routes.EMPTY_KB_MESSAGE_ES
    assert english.json()["safety"] is None


async def test_unavailable_error_is_localized(chat_app, open_client):
    graph = MagicMock()
    graph.ainvoke = async_returning(None)
    graph.ainvoke.side_effect = RuntimeError("provider down")
    chat_app.state.agent_graph = graph

    async with _client(chat_app) as client:
        response = await client.post(
            "/api/v1/chat", json={"message": "¿cuál es el horario?", "client": "clinic"}
        )

    assert response.status_code == 500
    assert response.json()["detail"] == chat_routes.CHAT_UNAVAILABLE_MESSAGE_ES
