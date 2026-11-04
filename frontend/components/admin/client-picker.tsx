"use client";

import { useEffect, useState } from "react";

import { useAdminUser } from "@/components/admin/admin-session";
import { ManagedClient, listClients } from "@/lib/admin-api";

export type ClientScope = {
  isSuperAdmin: boolean;
  clients: ManagedClient[];
  clientId: string;
  setClientId: (clientId: string) => void;
  /** A Super Admin who has not chosen a client yet: load nothing. */
  needsClient: boolean;
  /** The client_id to send: the chosen one for a Super Admin, undefined for others. */
  scope: string | undefined;
  /** `path` with ?client_id= added when a Super Admin chose a client. */
  withClient: (path: string) => string;
};

/**
 * Which client an admin page works on. Admins and Support users always work on their own
 * client (nothing to choose); a Super Admin picks one. `initialClientId` lets a page start
 * on a client named in its URL, such as after returning from Google.
 */
export function useClientScope(initialClientId = ""): ClientScope {
  const actor = useAdminUser();
  const isSuperAdmin = actor.user_type === "super_admin";
  const [clients, setClients] = useState<ManagedClient[]>([]);
  const [clientId, setClientId] = useState(isSuperAdmin ? initialClientId : "");

  useEffect(() => {
    if (!isSuperAdmin) {
      return;
    }
    listClients()
      .then(setClients)
      .catch(() => setClients([]));
  }, [isSuperAdmin]);

  const scope = isSuperAdmin && clientId ? clientId : undefined;
  return {
    isSuperAdmin,
    clients,
    clientId,
    setClientId,
    needsClient: isSuperAdmin && !clientId,
    scope,
    withClient: (path) => {
      if (!scope) {
        return path;
      }
      const separator = path.includes("?") ? "&" : "?";
      return `${path}${separator}client_id=${encodeURIComponent(scope)}`;
    },
  };
}

/** The Super Admin's client dropdown; renders nothing for other users. */
export function ClientPicker({ scope, id }: { scope: ClientScope; id: string }) {
  if (!scope.isSuperAdmin) {
    return null;
  }
  return (
    <div>
      <label htmlFor={id} className="block text-xs font-medium text-slate-600">
        Client
      </label>
      <select
        id={id}
        value={scope.clientId}
        onChange={(event) => scope.setClientId(event.target.value)}
        className="mt-1 rounded-lg border border-slate-300 px-3 py-2 text-sm"
      >
        <option value="">Choose a client</option>
        {scope.clients.map((client) => (
          <option key={client.id} value={client.id}>
            {client.name}
            {client.status === "suspended" ? " (suspended)" : ""}
          </option>
        ))}
      </select>
    </div>
  );
}
