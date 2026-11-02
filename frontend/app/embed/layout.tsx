export default function EmbedLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="embed-root fixed inset-0 flex flex-col overflow-hidden bg-white">
      {children}
    </div>
  );
}
