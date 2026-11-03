"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { useAdminUser } from "@/components/admin/admin-session";
import {
  AdminDocument,
  ApiError,
  DocumentStatus,
  ManagedClient,
  deleteDocument,
  listClients,
  listDocuments,
  reindexDocument,
  uploadDocument,
} from "@/lib/admin-api";

// Mirror backend/app/services/document_worker.py; the API checks again.
const ACCEPTED_EXTENSIONS = ".pdf,.docx,.pptx,.html,.htm,.md,.txt";
const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;

const STATUS_LABELS: Record<DocumentStatus, string> = {
  processing: "Processing",
  ready: "Ready",
  failed: "Failed",
};

function statusBadgeClass(status: DocumentStatus): string {
  if (status === "ready") {
    return "bg-green-100 text-green-800";
  }
  if (status === "failed") {
    return "bg-red-100 text-red-800";
  }
  return "bg-amber-100 text-amber-800";
}

function formatSize(bytes: number): string {
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  if (bytes < 1024 * 1024) {
    return `${Math.round(bytes / 1024)} KB`;
  }
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function errorMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError) {
    return err.message;
  }
  return err instanceof Error ? err.message : fallback;
}

export function DocumentTable() {
  const actor = useAdminUser();
  const isSuperAdmin = actor.user_type === "super_admin";

  // Super Admin only: which client's documents to show. Admins always see their own.
  const [clients, setClients] = useState<ManagedClient[]>([]);
  const [clientId, setClientId] = useState("");
  const needsClient = isSuperAdmin && !clientId;

  const [documents, setDocuments] = useState<AdminDocument[]>([]);
  const [totalChunks, setTotalChunks] = useState(0);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [actionId, setActionId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const scope = isSuperAdmin ? clientId : undefined;

  const loadDocuments = useCallback(async () => {
    if (needsClient) {
      setDocuments([]);
      setTotalChunks(0);
      return;
    }
    const data = await listDocuments(scope);
    setDocuments(data.documents);
    setTotalChunks(data.total_chunks);
  }, [needsClient, scope]);

  useEffect(() => {
    if (!isSuperAdmin) {
      return;
    }
    listClients()
      .then(setClients)
      .catch((err) => setError(errorMessage(err, "Failed to load clients.")));
  }, [isSuperAdmin]);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      setLoading(true);
      setError(null);
      try {
        await loadDocuments();
      } catch (err) {
        if (!cancelled) {
          setError(errorMessage(err, "Failed to load documents."));
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, [loadDocuments]);

  async function handleUpload(file: File) {
    setError(null);
    setSuccess(null);
    if (file.size > MAX_UPLOAD_BYTES) {
      setError(`${file.name} is larger than 10 MB.`);
      return;
    }

    setUploading(true);
    try {
      const uploaded = await uploadDocument(file, scope);
      setSuccess(`Indexed ${uploaded.filename} (${uploaded.chunk_count} chunks).`);
    } catch (err) {
      setError(errorMessage(err, "Upload failed."));
    } finally {
      setUploading(false);
      if (fileInputRef.current) {
        fileInputRef.current.value = "";
      }
      // A failed upload is listed too, with its error.
      await loadDocuments().catch(() => undefined);
    }
  }

  function handleFileChange(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (file) {
      void handleUpload(file);
    }
  }

  async function runAction(doc: AdminDocument, action: () => Promise<string>, fallback: string) {
    setActionId(doc.id);
    setError(null);
    setSuccess(null);
    try {
      setSuccess(await action());
    } catch (err) {
      setError(errorMessage(err, fallback));
    } finally {
      setActionId(null);
      await loadDocuments().catch(() => undefined);
    }
  }

  function handleDelete(doc: AdminDocument) {
    if (!window.confirm(`Remove "${doc.filename}" from the knowledge base?`)) {
      return;
    }
    void runAction(
      doc,
      async () => {
        await deleteDocument(doc.id, scope);
        return `Removed ${doc.filename}.`;
      },
      "Delete failed.",
    );
  }

  function handleReindex(doc: AdminDocument) {
    void runAction(
      doc,
      async () => {
        const result = await reindexDocument(doc.id, scope);
        return `Reindexed ${doc.filename} (${result.chunk_count} chunks).`;
      },
      "Reindex failed.",
    );
  }

  return (
    <div className="space-y-6">
      <p className="max-w-2xl rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
        Do not upload personal, patient or confidential records. Everything uploaded here can be
        quoted to website visitors.
      </p>

      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="flex flex-wrap items-end gap-4">
          {isSuperAdmin && (
            <div>
              <label htmlFor="documents-client" className="block text-xs font-medium text-slate-600">
                Client
              </label>
              <select
                id="documents-client"
                value={clientId}
                onChange={(event) => {
                  setClientId(event.target.value);
                  setSuccess(null);
                }}
                className="mt-1 rounded-lg border border-slate-300 px-3 py-2 text-sm"
              >
                <option value="">Choose a client</option>
                {clients.map((client) => (
                  <option key={client.id} value={client.id}>
                    {client.name}
                    {client.status === "suspended" ? " (suspended)" : ""}
                  </option>
                ))}
              </select>
            </div>
          )}
          {!needsClient && (
            <p className="text-sm text-slate-600">
              {documents.length} document{documents.length === 1 ? "" : "s"} · {totalChunks}{" "}
              chunks indexed
            </p>
          )}
        </div>

        {!needsClient && (
          <div className="text-right">
            <label className="inline-flex cursor-pointer items-center rounded-xl bg-slate-900 px-4 py-2.5 text-sm font-medium text-white hover:bg-slate-800">
              {uploading ? "Uploading..." : "Upload document"}
              <input
                ref={fileInputRef}
                type="file"
                accept={ACCEPTED_EXTENSIONS}
                className="hidden"
                disabled={uploading}
                onChange={handleFileChange}
              />
            </label>
            <p className="mt-1 text-xs text-slate-500">
              PDF, Word, PowerPoint, HTML, Markdown or text, up to 10 MB
            </p>
          </div>
        )}
      </div>

      {error && (
        <p className="text-sm text-red-600" role="alert">
          {error}
        </p>
      )}
      {success && <p className="text-sm text-green-700">{success}</p>}

      <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white">
        <table className="min-w-full divide-y divide-slate-200 text-sm">
          <thead className="bg-slate-50">
            <tr>
              <th className="px-4 py-3 text-left font-medium text-slate-700">Filename</th>
              <th className="px-4 py-3 text-left font-medium text-slate-700">Size</th>
              <th className="px-4 py-3 text-left font-medium text-slate-700">Status</th>
              <th className="px-4 py-3 text-left font-medium text-slate-700">Chunks</th>
              <th className="px-4 py-3 text-left font-medium text-slate-700">Uploaded</th>
              <th className="px-4 py-3 text-right font-medium text-slate-700">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {needsClient ? (
              <tr>
                <td colSpan={6} className="px-4 py-8 text-center text-slate-500">
                  Choose a client to see and upload its documents.
                </td>
              </tr>
            ) : loading ? (
              <tr>
                <td colSpan={6} className="px-4 py-8 text-center text-slate-500">
                  Loading documents...
                </td>
              </tr>
            ) : documents.length === 0 ? (
              <tr>
                <td colSpan={6} className="px-4 py-8 text-center text-slate-500">
                  No documents yet. Upload a PDF, Word or Markdown file to get started.
                </td>
              </tr>
            ) : (
              documents.map((doc) => {
                const busy = actionId === doc.id;
                return (
                  <tr key={doc.id}>
                    <td className="px-4 py-3 font-medium text-slate-900">{doc.filename}</td>
                    <td className="px-4 py-3 text-slate-600">{formatSize(doc.size_bytes)}</td>
                    <td className="px-4 py-3">
                      <span
                        className={`inline-flex rounded-full px-2.5 py-0.5 text-xs font-medium ${statusBadgeClass(doc.status)}`}
                      >
                        {STATUS_LABELS[doc.status]}
                      </span>
                      {doc.status === "failed" && doc.error && (
                        <p className="mt-1 max-w-xs truncate text-xs text-red-700" title={doc.error}>
                          {doc.error}
                        </p>
                      )}
                    </td>
                    <td className="px-4 py-3 text-slate-600">{doc.chunk_count}</td>
                    <td className="px-4 py-3 text-slate-600">{formatDate(doc.created_at)}</td>
                    <td className="px-4 py-3 text-right">
                      <div className="inline-flex gap-2">
                        <button
                          type="button"
                          disabled={busy || uploading}
                          onClick={() => handleReindex(doc)}
                          className="rounded-lg border border-slate-300 px-3 py-1.5 text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          {busy ? "Working..." : "Reindex"}
                        </button>
                        <button
                          type="button"
                          disabled={busy || uploading}
                          onClick={() => handleDelete(doc)}
                          className="rounded-lg border border-red-200 px-3 py-1.5 text-red-700 hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          Delete
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
