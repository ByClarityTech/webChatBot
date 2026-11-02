"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { adminFetch } from "@/lib/admin-api";

export type AdminDocument = {
  filename: string;
  source_id: string;
  chunk_count: number;
  indexed_at: string | null;
};

type DocumentsResponse = {
  documents: AdminDocument[];
  total_chunks: number;
};

function formatIndexedAt(value: string | null): string {
  if (!value) {
    return "—";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString();
}

async function parseError(response: Response): Promise<string> {
  const text = await response.text();
  try {
    const body = JSON.parse(text) as { detail?: string };
    if (typeof body.detail === "string") {
      return body.detail;
    }
  } catch {
    // not JSON
  }
  return text || `Request failed (${response.status})`;
}

export function DocumentTable() {
  const [documents, setDocuments] = useState<AdminDocument[]>([]);
  const [totalChunks, setTotalChunks] = useState(0);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [actionId, setActionId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const loadDocuments = useCallback(async () => {
    setError(null);
    const response = await adminFetch("/api/v1/admin/documents");
    if (!response.ok) {
      throw new Error(await parseError(response));
    }
    const data = (await response.json()) as DocumentsResponse;
    setDocuments(data.documents);
    setTotalChunks(data.total_chunks);
  }, []);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      setLoading(true);
      try {
        await loadDocuments();
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load documents.");
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
    setUploading(true);
    setError(null);
    setSuccess(null);

    const formData = new FormData();
    formData.append("file", file);

    try {
      const response = await adminFetch("/api/v1/admin/documents/upload", {
        method: "POST",
        body: formData,
      });
      if (!response.ok) {
        throw new Error(await parseError(response));
      }
      const payload = (await response.json()) as { filename: string; chunks_indexed: number };
      setSuccess(`Indexed ${payload.filename} (${payload.chunks_indexed} chunks).`);
      await loadDocuments();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed.");
    } finally {
      setUploading(false);
      if (fileInputRef.current) {
        fileInputRef.current.value = "";
      }
    }
  }

  function handleFileChange(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (file) {
      void handleUpload(file);
    }
  }

  async function handleDelete(doc: AdminDocument) {
    const confirmed = window.confirm(`Remove "${doc.filename}" from the knowledge base?`);
    if (!confirmed) {
      return;
    }

    setActionId(doc.source_id);
    setError(null);
    setSuccess(null);

    try {
      const response = await adminFetch(`/api/v1/admin/documents/${doc.source_id}`, {
        method: "DELETE",
      });
      if (!response.ok) {
        throw new Error(await parseError(response));
      }
      setSuccess(`Removed ${doc.filename}.`);
      await loadDocuments();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Delete failed.");
    } finally {
      setActionId(null);
    }
  }

  async function handleReindex(doc: AdminDocument) {
    setActionId(doc.source_id);
    setError(null);
    setSuccess(null);

    try {
      const response = await adminFetch(`/api/v1/admin/documents/${doc.source_id}/reindex`, {
        method: "POST",
      });
      if (!response.ok) {
        throw new Error(await parseError(response));
      }
      const payload = (await response.json()) as { chunks_indexed: number };
      setSuccess(`Reindexed ${doc.filename} (${payload.chunks_indexed} chunks).`);
      await loadDocuments();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Reindex failed.");
    } finally {
      setActionId(null);
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-sm text-slate-600">
            {documents.length} document{documents.length === 1 ? "" : "s"} · {totalChunks} chunks
            indexed
          </p>
        </div>

        <div>
          <label className="inline-flex cursor-pointer items-center rounded-xl bg-slate-900 px-4 py-2.5 text-sm font-medium text-white hover:bg-slate-800">
            {uploading ? "Uploading..." : "Upload document"}
            <input
              ref={fileInputRef}
              type="file"
              accept=".pdf,.docx,.pptx,.html,.htm,.md,.txt"
              className="hidden"
              disabled={uploading}
              onChange={handleFileChange}
            />
          </label>
        </div>
      </div>

      {error && <p className="text-sm text-red-600">{error}</p>}
      {success && <p className="text-sm text-green-700">{success}</p>}

      <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white">
        <table className="min-w-full divide-y divide-slate-200 text-sm">
          <thead className="bg-slate-50">
            <tr>
              <th className="px-4 py-3 text-left font-medium text-slate-700">Filename</th>
              <th className="px-4 py-3 text-left font-medium text-slate-700">Chunks</th>
              <th className="px-4 py-3 text-left font-medium text-slate-700">Indexed</th>
              <th className="px-4 py-3 text-right font-medium text-slate-700">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {loading ? (
              <tr>
                <td colSpan={4} className="px-4 py-8 text-center text-slate-500">
                  Loading documents...
                </td>
              </tr>
            ) : documents.length === 0 ? (
              <tr>
                <td colSpan={4} className="px-4 py-8 text-center text-slate-500">
                  No documents indexed yet. Upload a PDF, DOCX, or Markdown file to get started.
                </td>
              </tr>
            ) : (
              documents.map((doc) => {
                const busy = actionId === doc.source_id;
                return (
                  <tr key={doc.source_id}>
                    <td className="px-4 py-3 font-medium text-slate-900">{doc.filename}</td>
                    <td className="px-4 py-3 text-slate-600">{doc.chunk_count}</td>
                    <td className="px-4 py-3 text-slate-600">{formatIndexedAt(doc.indexed_at)}</td>
                    <td className="px-4 py-3 text-right">
                      <div className="inline-flex gap-2">
                        <button
                          type="button"
                          disabled={busy || uploading}
                          onClick={() => void handleReindex(doc)}
                          className="rounded-lg border border-slate-300 px-3 py-1.5 text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          {busy ? "Working..." : "Reindex"}
                        </button>
                        <button
                          type="button"
                          disabled={busy || uploading}
                          onClick={() => void handleDelete(doc)}
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
