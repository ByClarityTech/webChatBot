# PRD status: done and not done

Draft for review. Last checked against the code on 5 Nov 2026, branch `feature/admin-auth`.

Source: *PRD: AI Website Chatbot for Dental & Orthodontic Practices*, v1.1 (26 Oct 2026). The PRD has 37 functional requirements (FR-01 to FR-37), all P0, plus non-functional requirements (NFR). This file lists what the code does today and gives a full requirement for everything not done, so each item can be planned and built on its own.

## How the PRD maps to this product

This product serves many businesses from one install. Where the PRD says "practice", read "client".

| PRD term | In this code |
|---|---|
| Practice | Client (`clients` table, picked by slug in the widget: `/embed?client=<slug>`) |
| Practice admin | Admin user of that client |
| Staff agent | Support user of that client |
| (not in PRD) | Super Admin: manages every client, must pick a client on client-scoped pages |
| Setup wizard S-01 to S-07, Settings D-05 | Admin pages under `/admin` (Settings, Calendar, Documents, FAQs, Users, Clients) |

Two differences from the PRD are already decided by how the product is built, and are noted again where they matter:

- Clients and users are created by a Super Admin, not by self sign-up (affects FR-01).
- The LLM is OpenAI, Groq or Gemini, with OpenAI embeddings. The PRD says Claude with a BAA and a keyword fallback when there's no AI key. This needs a decision (see "Needs a decision").

## Summary

| Status | Count | FRs |
|---|---|---|
| Done | 6 | FR-03, FR-04, FR-06, FR-07, FR-08, FR-10 |
| Partial | 8 | FR-05, FR-09, FR-12, FR-14, FR-15, FR-17, FR-32, FR-36 |
| Not started | 23 | FR-01, FR-02, FR-11, FR-13, FR-16, FR-18 to FR-31, FR-33, FR-34, FR-35, FR-37 |

FR-10 (built-in calendar) is code complete with 323 unit and 39 integration tests passing. It's not committed yet and the manual browser check is still open.

## Done

| FR | What exists | Where |
|---|---|---|
| FR-03 Upload documents | Upload, parse and index per client. PDF, Word (.docx) and text, plus .pptx, .html and .md. 10 MB limit. Delete and reindex. | `backend/app/api/routes/admin/documents.py`, `backend/app/services/document_worker.py`, worker |
| FR-04 Patient data warning | Banner on the Documents page: do not upload personal, patient or confidential records. | `frontend/components/admin/document-table.tsx` |
| FR-06 Add, edit, delete FAQs | Per client, with draft and approved status. | `backend/app/api/routes/admin/faqs.py`, `frontend/components/admin/faq-manager.tsx` |
| FR-07 Up to 4 default questions | Fifth is refused by the API and by a database check. Order is stored. Chat preview and search on the FAQs page. | `backend/app/db/models/faq.py`, migration `0006_faqs` |
| FR-08 Approving FAQs builds the knowledge base | Approved FAQs are indexed and ranked first when the bot answers. | `backend/app/agents/public/nodes/retrieve.py`, `worker/app/public_pipeline.py` |
| FR-10 Calendar | Per client: Google Calendar, built-in calendar (business hours minus bookings) or no online booking. The booking option is hidden when there is no usable calendar. Overlapping bookings are blocked in the database. Minimum notice per client. | `backend/app/booking/`, `backend/app/api/routes/admin/calendar.py`, migration `0009_built_in_calendar` |

Also done, from the NFR list:

- Login throttling per email and IP (`backend/app/api/routes/auth.py`). The exact limit should be checked against "5 failed attempts in 15 minutes".
- CSRF protection for the admin app by Origin and Referer check (`backend/app/core/csrf.py`).
- Health check with Postgres, Qdrant and worker status (`backend/app/api/routes/health.py`).
- Role-based access for Super Admin, Admin and Support, with per-client scoping.
- Audit log of admin actions (users, clients, FAQs, calendar). Audit of conversation views and exports is not done, because conversations aren't stored yet.
- Automated tests (pytest, unit and integration). The PRD's list of covered areas (live chat, metrics) can't be met until those features exist.

## Partial

| FR | What exists | What's missing |
|---|---|---|
| FR-05 Draft FAQs by LLM | Generated from uploaded documents. | Generation from crawled pages, which needs FR-02. |
| FR-12 Publish | Per-client iframe URL. A Super Admin can suspend a client, which makes the widget show "not available". | One-line script snippet, Live/Paused toggle for the client's own admin, install guides, verify installation, test page. See requirement R-17. |
| FR-14 Default questions in chat | The widget shows the client's default questions as chips and a free-text box. | Showing them after the welcome choice (FR-13), which doesn't exist yet. |
| FR-15 Grounded answers | Answers come from the client's documents and FAQs only. | Spanish replies, explicit no-diagnosis and no-invented-prices rules, urgent symptoms card. See R-08. |
| FR-17 Buying intent | A booking intent node opens the booking form. | Place a call now button; CTA switches per client (FR-09). See R-07. |
| FR-32 Recheck at confirm | The server rechecks the slot and returns 409 if it's taken. | The widget showing the next open times without losing the visitor's details. See R-07. |
| FR-36 One staff agent | Support users have their own login. | Inbox, live chat, callbacks for that user. See R-12. |
| FR-09 CTAs | Server-wide booking on/off switch only. | Per-client toggles and fields. See R-06. |

Calendar extras from screen S-05 that are not built: choosing which Google calendar to use (the primary one is used), appointment type name, and a booking window in days ahead.

---

## Requirements for what's not done

Numbers follow the task list used in this project: tasks 1 (per-client booking) and 2 (built-in calendar) are done, so the list starts at R-03. Each requirement below is written to be built and tested on its own. They are grouped in the suggested build order (A to E). "Depends on" lists what must exist first. Field limits and wording come from the PRD unless marked *proposed*, which means the PRD doesn't say and the value needs confirming.

### A. Foundations

#### R-03 Allowed domains and chat rate limit (NFR security)

**Goal.** A client's widget works only on that client's websites, and the public chat endpoint can't be used to run up LLM costs.

**Requirements.**
1. Each client has a list of allowed domains (for example `smilecare.com`, `www.smilecare.com`). Store hostnames only, lowercased, without scheme, path or port. Maximum 20 per client (*proposed*).
2. The Admin manages the list on the client's Settings page. A Super Admin can manage it for any client. Every add or remove is audited.
3. The widget refuses to run on any other site. Use both:
   - the `Content-Security-Policy: frame-ancestors` header on `/embed`, built from the client's list plus the app's own origin, so browsers refuse to frame it elsewhere;
   - a server check on `/api/v1/widget/config` and `/api/v1/chat`: the Origin or Referer hostname must be on the list. Otherwise return 403 "This assistant is not available on this website".
4. With an empty list, the widget runs only on the app's own origin (the test page). This keeps local development working.
5. Chat is limited to 30 requests per minute per IP. Past the limit, return 429 with a `Retry-After` header and the message "You're sending messages too quickly. Please wait a moment."
6. The widget shows the 403 and 429 messages in the chat instead of failing silently.

**Acceptance.**
- Framing `/embed?client=x` on an unlisted site is blocked by the browser; the API returns 403 for that Origin.
- The 31st chat request in a minute from one IP gets 429.
- Admin of client A can't see or change client B's domains.

**Depends on.** Nothing. Needed before R-17 (go live needs at least one domain).

#### R-04 Conversation records, chat end rule and retention (FR-25 data, FR-27, NFR retention)

**Goal.** Every chat is stored per client so it can be shown, measured and deleted on schedule.

**Requirements.**
1. Store a conversation per widget session: client, session id, started at, last activity at, ended at, end reason (closed, idle), language, type (anonymous or known), test flag.
2. Store each message: conversation, sender (patient, AI, staff), text, created at, and for AI answers the sources used and whether the no-answer path was taken.
3. A chat ends when the visitor closes the widget or after 30 minutes with no activity. A message after that starts a new conversation in the same browser session. Every chat-based metric uses this rule.
4. Chats from the test page and admin previews are flagged as test and excluded from metrics.
5. Retention setting per client: 30 days, 90 days, 6 months, 1 year (default), 2 years, 7 years. A daily job deletes conversations and messages older than the setting, including LangGraph checkpoint data for those threads.
6. Changing retention is Admin only and audited.
7. The chat thread id from the widget must belong to the same client (already enforced for bookings); apply the same check to chat.

**Acceptance.**
- Sending messages creates one conversation; 31 minutes idle then a message creates a second.
- Closing the widget sets end reason "closed".
- With retention 30 days, a 31-day-old conversation is gone after the job runs.

**Depends on.** Nothing. Needed by R-05, R-09 to R-15.

**Note.** Stored chats can contain health information. Encryption at rest and access audit need a decision first (see "Needs a decision").

### B. Visitor chat

#### R-05 Welcome choice, share details and consent (FR-13, FR-31, NFR consent, W-02, W-03)

**Requirements.**
1. When the chat opens, show the greeting and two buttons: Share my details and Just start chatting.
2. Share details form: name (required), mobile phone (required, US format), Are you a current patient? Yes/No (required), consent checkbox (required, unticked by default) with the consent text, Continue, and a Skip link.
3. Store the consent text shown, the date and time, and the session, with the conversation. The conversation becomes known.
4. Just start chatting and Skip open the chat as anonymous.
5. Booking and callback cards ask "Are you a current patient?" and consent only if not already answered.
6. Consent wording comes from the PRD's proposed text with the client name filled in. It is pending legal review, so the text should be configurable without a code change.

**Acceptance.** Known chats show name, phone, current-patient answer and the stored consent record; anonymous chats store none of these.

**Depends on.** R-04.

#### R-06 CTA settings per client (FR-09, S-04)

**Requirements.**
1. Per-client toggles: Book a calendar slot, Place a call, SMS/iMessage option, Callback form.
2. Practice phone number, required when Place a call or SMS is on, US format.
3. Book a calendar slot can be on only when the client has a usable calendar (FR-10 rules).
4. Callback form fields: name and phone always on; email, preferred time and reason optional.
5. The widget shows only the CTAs that are on. Text us is shown on mobile only.
6. Changes are Admin only and audited.

**Acceptance.** Turning Place a call off removes the Call now button from every widget view on the next page load.

**Depends on.** Nothing.

#### R-07 Buying intent, suggested times and taken slots (FR-17, FR-32, FR-33, user flow 3)

**Requirements.**
1. On buying intent (asks to book, asks about cost, consultations or availability), the reply shows Book a slot and Call now, for the CTAs that are on.
2. With booking on, the reply also suggests up to 3 next open times as buttons, in the client's timezone with the timezone name, for example "Tue 3:00 PM (Asia/Kolkata)".
3. Tapping a time opens the booking card with that time selected. Book a slot opens it with no time selected.
4. A plain booking request ("Can I book?") is answered with the next open times and is not logged as a gap.
5. If there are no open times in the booking window, the bot says so and offers a callback.
6. On Confirm, if the slot was taken (409), the card stays open with everything the visitor typed, says the time was just taken, and shows the next open times.
7. Booking window per client in days ahead (*proposed* default 30). Only dates inside it can be picked.
8. Optional email and reason on the booking card; pre-fill name and phone for known chats.
9. After booking: date, time, address and an Add to my calendar link (an .ics file) for both calendar modes.
10. A booking makes the chat known. It counts in metrics only when confirmed.

**Acceptance.** Two visitors pick the same time; the second sees "just taken", keeps their details and books another time without retyping.

**Depends on.** R-06 for CTA switches.

#### R-08 Answer quality and safety (FR-15, FR-16, NFR AI safety, NFR performance)

**Requirements.**
1. Reply in Spanish when the visitor writes in Spanish, using the English knowledge base. English/Spanish switch in the widget header.
2. Never diagnose, give treatment advice or state prices and policies that aren't in the knowledge base. Put this in the system prompt and add tests with adversarial questions.
3. Urgent symptoms (swelling, trouble breathing, heavy bleeding) show a fixed safety card with Call the practice and Call 911. The wording is fixed, never AI-written. Shown even during live chat.
4. After each answer, show up to 3 related questions drawn from the knowledge base as chips (FR-16).
5. Show a source link under an answer (page or document it came from).
6. Answers stream into the bubble as they are generated (Server-Sent Events).
7. Greeting in English and Spanish per client (see R-16).

**Acceptance.** A Spanish question gets a Spanish answer; "my face is swelling" shows the safety card and no AI text; answers start appearing before the full reply is ready.

**Depends on.** R-04 for storing language and sources. The AI provider decision affects item 2 testing.

### C. When the bot can't answer

#### R-09 Gaps log (FR-18 logging, FR-24, FR-29, D-04 Gaps)

**Requirements.**
1. When the AI has no answer, log the question as a gap for the client, with the conversation.
2. Group similar questions into one gap (embedding similarity, threshold *proposed* 0.85) and count times asked.
3. Gaps tab columns: question, times asked (default sort, highest first), last asked, source (AI had no answer, or Send now, review later), ad hoc reply, status (Open, Answered, Dismissed).
4. Add to knowledge base opens question and answer fields, pre-filled with the ad hoc reply. Saving creates an approved FAQ, indexes it and sets the gap to Answered.
5. Dismiss is Admin only.
6. Admin and Support can view and answer.
7. Dashboard Home shows the top 5 open gaps by times asked in the selected date range, with median gap close time.

**Acceptance.** Asking the same unknown question three ways creates one gap with count 3; answering it makes the bot answer next time.

**Depends on.** R-04.

#### R-10 Callbacks (FR-23, FR-37, W callback card, D-03 Callbacks)

**Requirements.**
1. Callback card: name and mobile phone (required, pre-filled for known chats), current patient if not answered, preferred time (morning, afternoon, evening) and email and reason if on in R-06, question pre-filled with the unanswered question, consent checkbox with the callback consent text (required, unticked).
2. Submit shows a confirmation in the chat and creates a callback request.
3. Callbacks tab for Admin and Support: name, phone as a click-to-call link, current patient answer, preferred time, question, source (Callback card, No agent online, Not answered in 3 minutes, filterable), requested at (default sort oldest first), status (New, Called, Done), View chat link opening the conversation in a side panel.
4. Status changes are audited.
5. Rate limit callback submissions per IP (*proposed* 5 per hour).

**Acceptance.** A submitted callback appears in the tab with the right source and the full chat behind View chat.

**Depends on.** R-04, R-05 (consent), R-06 (fields).

#### R-11 No-answer offer and handover rule (FR-18, FR-19)

**Requirements.**
1. With no answer, the bot says so and shows a card: Chat with our team, Call now, Request a callback, Text us (mobile only), for the options that are on.
2. Text us opens the visitor's messaging app (`sms:`) to the client number with the question pre-filled, and shows the "texts are not encrypted" note. Nothing is sent or stored by the product.
3. Chat with our team is offered at most once per session. A returning known patient in a new session can get it again.
4. Outside live chat hours (R-12) or with nobody online, Chat with our team is replaced by the callback, call and text options.

**Acceptance.** After one handover in a session, a second unanswered question shows the card without Chat with our team.

**Depends on.** R-06, R-09, R-10. Live chat itself is R-12.

### D. Staff side

#### R-12 Live chat Inbox (FR-20, FR-35, FR-36, D-03 Live chats)

**Requirements.**
1. Support user (and Admin as backup) sets Online or Offline in the Inbox.
2. When a visitor chooses Chat with our team, the chat enters the client's waiting queue: patient, last question, wait time. New arrivals play a sound and update a badge.
3. Accept takes the chat. If two people accept, the first wins and the other sees it was taken. No routing or assignment in v1.
4. The visitor sees "Connecting you to our team", then "You're chatting with [name]". Staff messages are marked as staff.
5. The staff view shows the full history, including the AI part.
6. End chat closes the live part and returns the visitor to the AI.
7. If nobody accepts within 3 minutes, the chat leaves the queue; the bot apologises and offers callback, call and text. A callback from here has source "Not answered in 3 minutes".
8. With nobody online, or outside live chat hours (per client: days, start, end), the visitor is offered callback, call and text instead. Source "No agent online".
9. Real-time transport: WebSocket or SSE for both widget and Inbox (*proposed*: SSE plus POST, to match R-08 streaming).
10. Presence and the 3-minute timer must survive one backend restart (store in Postgres or Redis, not only in memory).

**Acceptance.** Visitor asks for a person, Support accepts within 3 minutes, they exchange messages; with nobody online the visitor gets the fallback card.

**Depends on.** R-04, R-10, R-11.

#### R-13 AI-drafted replies (FR-21, FR-22)

**Requirements.**
1. When the knowledge base covers the visitor's question, show an AI suggested reply card. Review and send sends it unchanged.
2. Editing the draft or typing a reply gives two buttons:
   - Save answer and send: sends, and adds the question and reply to the knowledge base as an approved FAQ.
   - Send now, review later: sends as-is and logs a gap with source "Send now, review later" and the ad hoc reply.
3. Reply limit 2,000 characters (*proposed*).

**Acceptance.** Save answer and send makes the bot answer that question itself in a new chat.

**Depends on.** R-09, R-12.

#### R-14 Email notifications (FR-34)

**Requirements.**
1. Each handover and callback request sends an email to the client's notification addresses (Settings, at least one required) and to the Support user.
2. The email contains no patient name, phone or messages: only that a chat or callback is waiting and a link that opens it in the Inbox after login.
3. SMTP settings in server config. If not configured, log a warning and skip; never fail the visitor's request.
4. Send in the background with retry (*proposed* 3 tries).

**Acceptance.** A callback triggers one email per recipient with no personal data in subject or body.

**Depends on.** R-10, R-12. The email provider may need a BAA (see "Needs a decision").

### E. Dashboard and admin

#### R-15 Conversations dashboard and metrics (FR-25, FR-26, FR-28, FR-29, FR-30, D-01, D-02)

**Requirements.**
1. Conversations list per client: patient (name, or "Anonymous" plus session id), phone (known only), started (default sort newest first), messages, sentiment, outcome, handled by (AI or staff name).
2. Search names, phones and message text. Filters: date range, type, current patient (Yes, No, Not asked), sentiment, outcome, language.
3. Chats with the same phone are grouped under one patient.
4. Detail panel: transcript with timestamps marking AI, staff and patient; AI summary; insights (intent, topics, treatment interest, insurance mentioned); sentiment with a one-line reason; patient details with consent record and earlier chats; lead status (New, Contacted, Booked, Not interested); internal notes; Call button.
5. Summary, insights and sentiment are generated by the LLM when a chat ends, in the background.
6. Outcome is the strongest action: Booked, Callback, Call, Text, Handover, None. Call and Text taps are recorded as events.
7. Export (CSV) is Admin only and every export is audited. Every view of a conversation is audited.
8. Home: date range (7 days, 30 days, custom) in the client timezone; cards for chats, engagement rate, answer rate, known-patient rate, handovers and rate; conversions (bookings, calls, callbacks, texts, conversion rate); needs attention (new callbacks, waiting chats, negative chats); top unanswered questions (R-09). All numbers follow the PRD's Metric definitions; test chats excluded.
9. Visitor counting for engagement rate: one per browser session where the widget loaded on an allowed domain.

**Acceptance.** Metric numbers for a seeded set of chats match the PRD formulas exactly (unit tests per formula).

**Depends on.** R-04, R-05, R-07, R-09 to R-12.

#### R-16 Chat design settings (FR-11, S-06, W-01)

**Requirements.**
1. Per client: bot name (required), logo or avatar (PNG or JPG, up to 500 KB), primary colour (default indigo #5856D6; rejected if contrast with white text is below 4.5:1), launcher position (bottom right, bottom left), optional teaser text, greeting in English (required) and Spanish (required, AI suggests a translation).
2. Live preview with desktop and mobile toggle.
3. Teaser hides after the first close and stays hidden for that browser.
4. Widget header: logo, bot name, English/Spanish switch, minimise, close.

**Note.** The current widget uses fixed styling (blue header, 3 template buttons) agreed earlier in this project. This requirement replaces fixed values with per-client settings; the current look becomes the default.

**Depends on.** Nothing. R-17's script launcher uses the position and teaser.

#### R-17 Publish, go live and pause (FR-12, S-07)

**Requirements.**
1. One-line script snippet per client with a Copy button. The script adds a launcher bubble and opens the widget in an iframe. Host page CSS must not affect it.
2. Install guides: WordPress, Wix, Squarespace, Other.
3. Email to web developer: sends the snippet and guide to an address the Admin enters.
4. Verify installation: fetches the client's site and checks the snippet is present.
5. Bot status Live or Paused, set by the Admin. Paused shows nothing on the client's site.
6. Live needs: at least one allowed domain, approved knowledge base (at least one approved FAQ). The PRD's verified email and accepted BAA checks depend on R-19.
7. Test page opening the widget alone; chats there are flagged as test.

**Depends on.** R-03, R-16.

#### R-18 Website crawling (FR-02, S-02 crawl, D-04 Sources and Pending changes)

**Requirements.**
1. Website URL per client; Crawl website crawls up to 30 pages on the same domain, with progress.
2. Crawled pages list: URL, status (Crawled, Failed), include toggle.
3. Crawled pages are indexed like documents and used for FAQ generation (completes FR-05).
4. Monthly re-crawl plus Crawl now. Changes go to Pending changes (page, summary of change, Approve, Reject) and go live only when approved.
5. Respect robots.txt; identify the crawler with a generic user agent.
6. Dashboard Home shows a banner when changes are waiting.

**Depends on.** Nothing, but large. Worker already parses HTML.

#### R-19 Sign-up, email verification and terms (FR-01, S-01)

**Decision needed first.** This product creates clients and users through a Super Admin. The PRD's self sign-up may not apply. If self sign-up is not wanted, the remaining parts are:
1. Email verification for new Admin users (link valid 24 hours, *proposed*).
2. Forgot password by email link (never return the link in the API response).
3. Terms and BAA acceptance recorded per client (who, when, version) before the client can go Live.
4. Password strength rules.

**Depends on.** R-14's email sending.

---

## Needs a decision, not only code

These are flagged for human review. The notes describe options; they are not compliance verdicts.

- **HIPAA scope.** The PRD requires BAAs with each client and every vendor that touches chat data, encryption in transit and at rest, and an audit of who viewed or exported each conversation. Once R-04 stores chats, this matters. Someone qualified should decide what's required before real patient data.
- **AI provider.** Code uses OpenAI, Groq or Gemini, and OpenAI for embeddings. The PRD says Claude with a BAA, falling back to approved-FAQ matching when there's no AI key.
- **Consent wording.** The PRD's text is marked pending attorney review.
- **Self sign-up** (R-19).
- **Texting.** Whether each client's number can receive texts (A2P 10DLC). Text us only opens the visitor's app.
- **Metric and latency targets.** Still open in the PRD.
- **Marketing-text consent checkbox.** Out of v1 per the PRD.

## Not covered by the PRD but open in this repo

- Built-in calendar (FR-10) changes are not committed, and the manual browser check is open.
- Commits `338eab1` and `3fff240` are not pushed.
- ESLint has no v9 flat config, so `eslint` doesn't run.
- The worker port is published in `docker-compose.yml`.
- WCAG 2.1 AA has not been checked for the widget or admin app.
