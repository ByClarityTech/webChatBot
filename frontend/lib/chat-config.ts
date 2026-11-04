export const chatConfig = {
  greeting:
    process.env.NEXT_PUBLIC_CHAT_GREETING?.trim() ||
    "Hi! How can I help you today?",
  inputPlaceholder: process.env.NEXT_PUBLIC_CHAT_INPUT_PLACEHOLDER?.trim() || "Ask a question...",
  // Question buttons under the greeting; {clientName} is replaced with the client's name.
  suggestedQuestionTemplates: [
    "What business problems can {clientName} help solve?",
    "What services does {clientName} offer?",
    "Which industries has {clientName} worked with?",
  ],
};


