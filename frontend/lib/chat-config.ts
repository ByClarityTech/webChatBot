export const chatConfig = {
  greeting:
    process.env.NEXT_PUBLIC_CHAT_GREETING?.trim() ||
    "Hi! How can I help you today?",
  inputPlaceholder: process.env.NEXT_PUBLIC_CHAT_INPUT_PLACEHOLDER?.trim() || "Ask a question...",
  // Shown under the greeting only when the practice has not starred any default questions
  // (PRD FR-07). Empty on purpose: practice defaults from /widget/config are the only source
  // of suggested questions, so no template copy reaches visitors.
  fallbackSuggestedQuestions: [] as string[],
};

// The widget shows at most this many question chips (PRD FR-07, FR-14).
export const MAX_SUGGESTED_QUESTIONS = 4;
