export const chatConfig = {
  title: process.env.NEXT_PUBLIC_CHAT_TITLE?.trim() || "",
  subtitle: process.env.NEXT_PUBLIC_CHAT_SUBTITLE?.trim() || "",
  greeting:
    process.env.NEXT_PUBLIC_CHAT_GREETING?.trim() ||
    "Hi! How can I help you today?",
  inputPlaceholder: process.env.NEXT_PUBLIC_CHAT_INPUT_PLACEHOLDER?.trim() || "Ask a question...",
  suggestedQuestions: [
    "What business problems can Clarity Tech Labs help solve?",
    "What services does Clarity Tech Labs offer?",
    "Which industries has Clarity Tech Labs worked with?",
  ],
};


