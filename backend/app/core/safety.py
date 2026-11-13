"""Deterministic safety checks that run before the language model sees a message.

Three layers, in the order a request meets them:

1. check_urgent: acute emergencies get a fixed, bilingual reply and never reach the model.
2. check_diagnosis_probe: questions asking for a diagnosis get a fixed refusal.
3. has_unsupported_price: a reply that quotes an amount absent from the retrieved context is
   replaced with a referral (checked after the model answers).

Every pattern runs on normalized text (lowercase, accents and apostrophes removed, invisible
and look-alike characters folded, punctuation turned into spaces). Negated phrases such as
"I am not short of breath" still match; a false positive is preferred to a missed emergency.

Nothing here logs message text. The text can contain personal health details.
"""

import logging
import re
import unicodedata

logger = logging.getLogger(__name__)

MAX_INPUT_CHARS = 4000

# Zero-width spaces and marks, soft hyphens, bidi controls, and the byte-order mark.
_INVISIBLE = re.compile("[­​-‏‪-‮⁠-⁤⁦-⁯﻿]")
# Cyrillic and Greek letters that look like Latin ones, folded so "сangrando" matches "sangrando".
_HOMOGLYPHS = str.maketrans(
    {
        "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "х": "x", "у": "y",
        "і": "i", "ј": "j", "ѕ": "s", "ԁ": "d", "ɡ": "g", "ο": "o", "α": "a",
        "ε": "e", "ν": "v", "ι": "i", "κ": "k", "τ": "t",
    }
)  # fmt: skip


def normalize(text: object) -> str:
    """Lowercase ASCII words separated by single spaces. Never raises; non-strings give ''."""
    if not isinstance(text, str):
        return ""
    text = _INVISIBLE.sub("", text[:MAX_INPUT_CHARS])
    # NFKC folds fullwidth and compatibility forms; then fold look-alikes and drop accents.
    text = unicodedata.normalize("NFKC", text).casefold().translate(_HOMOGLYPHS)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.replace("'", "").replace("’", "").replace("`", "")
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return " ".join(text.split())


# ---------------------------------------------------------------- urgent categories

_URGENT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "breathing",
        re.compile(
            r"\b(cant|cannot|can not|unable to|trouble|difficulty|hard to|struggling to)"
            r" (breathe|breath\w*|respirar|get enough air|get air|catch my breath)\b"
            r"|\bshort of breath\b|\bgasping\b"
            r"|\bno puedo respirar\b|\bme cuesta (mucho )?respirar\b"
            r"|\bdificultad para respirar\b|\bme falta (el )?aire\b|\bno me entra (el )?aire\b"
            r"|\bme ahogo\b|\bme estoy ahogando\b"
        ),
    ),
    (
        "unconscious",
        re.compile(
            r"\bunconscious\b|\bpassed out\b|\bpassing out\b|\bfainted\b|\bseizure\w*"
            r"|\bnot responding\b|\bcollapsed\b"
            r"|\binconsciente\b|\bdesmay\w*|\bconvulsi\w*|\bno responde\b"
        ),
    ),
    (
        "chest_or_stroke",
        re.compile(
            r"\bchest pain\b|\bpain (in|on) (my |the )?chest\b|\bheart attack\b|\bstroke\b"
            r"|\bface (is )?droop\w*|\bslurred speech\b"
            r"|\bcant (move|feel) (my )?(arm|leg|face|side)\b"
            r"|\bdolor (de |en el )?pecho\b|\bderrame\b|\binfarto\b|\bhabla arrastrad\w*"
            r"|\bcara (caida|torcida)\b"
        ),
    ),
    (
        "knocked_out_tooth",
        re.compile(
            r"\bknocked (out|loose)\b|\btooth (came|fell|popped|got|was) out\b"
            r"|\bdiente (se )?(cayo|salio|salto)\b"
            r"|\bse me (cayo|salio|solto) (el |un |una |la |mi )?(diente|muela)\b"
            r"|\bdiente (fuera|arrancado|arrancada)\b"
        ),
    ),
    (
        "severe_facial_trauma",
        re.compile(
            r"\b(broke|broken|fractured) (my )?(jaw|face|cheek|nose)\b|\bjaw fracture\b"
            r"|\bhit (me|my) (in the )?face (hard|really hard|very hard)\b"
            r"|\bsevere(ly)? (injur|hurt)\w* (my )?(face|mouth|jaw)\b"
            r"|\bme rompi (la )?(mandibula|quijada|cara|nariz)\b"
            r"|\b(mandibula|quijada) (rota|fracturad\w*)\b"
            r"|\bgolpe (muy )?fuerte en (la )?cara\b|\blesion grave (en|de) (la )?(cara|boca)\b"
        ),
    ),
)

_BLEEDING = re.compile(r"\b(bleed\w*|blood)\b|\bsangr\w*")
_BLEEDING_SEVERE = re.compile(
    r"\b(wont|will not|doesnt|does not|not|never|cant) stop\b|\buncontrolled\b|\bheavy\b"
    r"|\ba lot\b|\bpouring\b|\bgushing\b|\bsoaking\b|\bnon ?stop\b|\bsevere\b|\bcontinuously\b"
    r"|\bmucho\b|\bsin parar\b|\bno para\b|\bno (se )?detiene\b|\bincontrolable\b"
    r"|\ba chorros\b|\babundante\w*|\bcontinu\w*|\bgrave\b|\bfuerte\b"
)

_SWELLING = re.compile(
    r"\bswell\w*|\bswollen\b|\bballoon\w*|\bpuff\w*|\bface is (huge|big|enormous)\b"
    r"|\bhinch\w*|\binflam\w*|\bse me infl\w*"
)
_AIRWAY = re.compile(
    r"\bswallow\w*|\bthroat\b|\bneck\b|\bbreath\w*|\bairway\b|\bchok\w*|\btongue\b|\bgag\w*"
    r"|\btrag\w*|\bgarganta\b|\bcuello\b|\brespir\w*|\baire\b|\bahog\w*|\blengua\b"
)
# Swelling of the face, jaw or gums is urgent on its own. Teeth are left out on purpose:
# a swollen tooth is common and is routed to the office, not the emergency reply.
_FACE_SITE = re.compile(
    r"\b(face|facial|jaw|jowl|cheek|gum|gums|lip|lips|chin|cara|mandibula|quijada|mejilla|"
    r"mejillas|encia|encias|labio|labios|barbilla|menton)\b"
)


def check_urgent(text: object) -> str | None:
    """The emergency category a message falls into, or None. Never raises on odd input."""
    cleaned = normalize(text)
    if not cleaned:
        return None
    for category, pattern in _URGENT_PATTERNS:
        if pattern.search(cleaned):
            return category
    # Swelling with a breathing or swallowing word is the airway case.
    if _SWELLING.search(cleaned) and _AIRWAY.search(cleaned):
        return "swelling_airway"
    # Facial, jaw or gum swelling is urgent without any airway word.
    if _SWELLING.search(cleaned) and _FACE_SITE.search(cleaned):
        return "facial_swelling"
    if _BLEEDING.search(cleaned) and _BLEEDING_SEVERE.search(cleaned):
        return "bleeding"
    return None


# ---------------------------------------------------------------- diagnosis probes

_DIAGNOSIS_PROBE = re.compile(
    r"\b(do i|do you think i|could i|might i) have (a |an |any )?"
    r"(cavity|cavities|infection|abscess|gum disease|periodontitis|gingivitis|fracture|"
    r"cracked tooth|tumou?r|cyst|oral cancer)\b"
    r"|\bis (this|it|that|my) (a |an )?(cavity|infection|abscess|gum disease|cyst|tumou?r|"
    r"cancer|fracture|crack)\b"
    r"|\bwhat('?s| is) wrong with (my|this|the) (tooth|teeth|gum|gums|jaw|mouth)\b"
    r"|\bdiagnos(e|es|is|ed|ing)\b|\bdiagnostico\b"
    r"|\b(tengo|es|son|podria tener) (una |un )?(caries|infeccion|absceso|gingivitis|"
    r"periodontitis|fractura|quiste|tumor|cancer)\b"
    r"|\bque me pasa (en|con) (el|la|mi)\b|\bque es esto\b"
)


def check_diagnosis_probe(text: object) -> bool:
    cleaned = normalize(text)
    return bool(cleaned) and _DIAGNOSIS_PROBE.search(cleaned) is not None


# ---------------------------------------------------------------- language

_SPANISH_WORDS = frozenset(
    "el la los las de que y en un una es por para con mi mis tengo puedo hola gracias "
    "cuanto cuesta donde cuando dolor muela diente quiero necesito hay son al del me se su "
    "tambien pero buenas dias horario".split()
)
_ENGLISH_WORDS = frozenset(
    "the is are my i an to and of for with what how can do does you your it this hours "
    "price cost tooth pain hi hello thanks please when where".split()
)


def detect_language(text: object) -> str:
    """'es' when Spanish words outnumber English ones, else 'en'. Ties go to English."""
    tokens = normalize(text).split()
    spanish = sum(token in _SPANISH_WORDS for token in tokens)
    english = sum(token in _ENGLISH_WORDS for token in tokens)
    return "es" if spanish > english else "en"


def pick(language: str, english: str, spanish: str) -> str:
    return spanish if language == "es" else english


# ---------------------------------------------------------------- price check

_AMOUNT = re.compile(
    r"[$€£₹]\s?\d[\d.,]*"
    r"|\d[\d.,]*\s?(?:usd|eur|mxn|inr|pesos?|dolares?|dollars?|euros?|rupees?|rs)\b",
    re.IGNORECASE,
)
_NUMBER = re.compile(r"\d[\d.,]*")


def _digits(token: str) -> str:
    return re.sub(r"\D", "", token)


def has_unsupported_price(reply: object, context_text: object) -> bool:
    """True when the reply quotes an amount whose digits never appear in the context."""
    if not isinstance(reply, str):
        return False
    amounts = _AMOUNT.findall(unicodedata.normalize("NFKC", reply))
    if not amounts:
        return False
    context = context_text if isinstance(context_text, str) else ""
    context_numbers = {_digits(token) for token in _NUMBER.findall(context)}
    for amount in amounts:
        number = _NUMBER.search(amount)
        digits = _digits(number.group()) if number else ""
        if digits and digits not in context_numbers:
            return True
    return False


# ---------------------------------------------------------------- templates

_EMERGENCY_EN = (
    "This may be a medical emergency. If someone has trouble breathing or swallowing, swelling "
    "of the face, jaw or gums, bleeding that will not stop, chest pain, weakness or slurred "
    "speech, is unconscious or having a seizure, or has a serious injury to the face or mouth, "
    "call 911 or your local emergency number now, or go to the nearest emergency room."
)
_EMERGENCY_ES = (
    "Esto podría ser una emergencia médica. Si usted o alguien cerca de usted tiene dificultad "
    "para respirar o tragar, hinchazón de la cara, la mandíbula o las encías, sangrado que no se "
    "detiene, dolor de pecho, debilidad o habla arrastrada, está inconsciente o tiene "
    "convulsiones, o sufrió una lesión grave en la cara o la boca, llame ahora al 911 o al "
    "número de emergencias de su localidad, o vaya a la sala de urgencias más cercana."
)


def emergency_reply(contact_phone: str | None = None) -> str:
    """The fixed urgent reply in both languages, with the practice's number when one is set.

    Both languages always appear: a wrong guess about the visitor's language must never hide
    the instruction to call 911.
    """
    phone = (contact_phone or "").strip()
    if phone:
        english = (
            f"{_EMERGENCY_EN} You can also call our office directly at {phone}. "
            "If a tooth has been knocked out, call our office right away."
        )
        spanish = (
            f"{_EMERGENCY_ES} También puede llamar directamente a nuestra oficina al {phone}. "
            "Si se le ha caído un diente, llame a nuestra oficina de inmediato."
        )
    else:
        english = f"{_EMERGENCY_EN} If a tooth has been knocked out, call our office right away."
        spanish = (
            f"{_EMERGENCY_ES} Si se le ha caído un diente, llame a nuestra oficina de inmediato."
        )
    return f"{english}\n\n{spanish}"

DIAGNOSIS_EN = (
    "I can't tell you what is causing a symptom or give a diagnosis. Please book a visit with "
    "our office so a dentist can examine you."
)
DIAGNOSIS_ES = (
    "No puedo decirle qué causa un síntoma ni darle un diagnóstico. Le recomendamos reservar "
    "una cita en nuestra oficina para que un dentista lo examine."
)

PRICE_EN = "I can't confirm prices here. Please contact our office for current fees."
PRICE_ES = (
    "No puedo confirmar precios aquí. Por favor, comuníquese con nuestra oficina para conocer "
    "las tarifas actuales."
)
