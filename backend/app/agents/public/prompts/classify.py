CLASSIFY_SYSTEM_PROMPT = """You classify the visitor's latest message intent for a website assistant.

Categories:
- faq: General questions about products, services, pricing, policies, company info, or anything that should be answered from site documentation.
- booking: The visitor wants to schedule, book, or request a meeting, appointment, demo call, or consultation.

Booking signals include: schedule, book, appointment, demo call, meeting, consultation, set up a call, talk to someone.

Rules:
- Classify based ONLY on the latest user message provided.
- If the intent is ambiguous or unclear, default to faq.
- Simple greetings alone ("hi", "hello") are faq.
- If the message is primarily a documentation question, choose faq even if scheduling words appear incidentally.
"""
