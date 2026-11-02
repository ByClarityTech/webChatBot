import { ChatPanel } from "@/components/chat/chat-panel";

export default function EmbedPage() {
  return (
    <div className="flex h-full min-h-0 flex-1 flex-col">
      <ChatPanel embed />
    </div>
  );
}
