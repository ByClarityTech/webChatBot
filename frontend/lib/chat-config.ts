export const chatConfig = {
  title: process.env.NEXT_PUBLIC_CHAT_TITLE?.trim() || "",
  subtitle: process.env.NEXT_PUBLIC_CHAT_SUBTITLE?.trim() || "",
  greeting:
    process.env.NEXT_PUBLIC_CHAT_GREETING?.trim() ||
    "Hi! How can I help you today?",
  inputPlaceholder: process.env.NEXT_PUBLIC_CHAT_INPUT_PLACEHOLDER?.trim() || "Ask a question...",
  // Default questions are per client: the starred FAQs, loaded from /api/v1/widget/config.
};


