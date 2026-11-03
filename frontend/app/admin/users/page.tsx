import { UsersTable } from "@/components/admin/users-table";

export default function AdminUsersPage() {
  return (
    <div className="px-8 py-10">
      <h1 className="text-2xl font-semibold text-slate-900">Users</h1>
      <p className="mt-2 max-w-2xl text-sm text-slate-600">
        Create sign-ins for your team. New users and password resets get a generated password,
        shown once; the user must choose their own the first time they sign in.
      </p>

      <div className="mt-8">
        <UsersTable />
      </div>
    </div>
  );
}
