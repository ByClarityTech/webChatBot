import { WidgetAdminPanel } from "@/components/admin/widget-admin";

export default function AdminWidgetPage() {
  return (
    <div className="px-8 py-10">
      <h1 className="text-2xl font-semibold text-slate-900">Chat widget</h1>
      <p className="mt-2 max-w-2xl text-sm text-slate-600">
        Set how the chat looks on your website, pause it, and get the code to install it.
      </p>
      <div className="mt-8">
        <WidgetAdminPanel />
      </div>
    </div>
  );
}
