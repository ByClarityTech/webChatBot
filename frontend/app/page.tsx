import { ChatPanel } from "@/components/chat/chat-panel";

export default function HomePage() {
  return (
    <main className="mx-auto flex min-h-screen max-w-3xl flex-col justify-center px-4 py-10">
      <ChatPanel />
    </main>
  );
}
