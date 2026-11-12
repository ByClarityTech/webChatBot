import { adminFetch } from "@/lib/admin-api";

const PATH = "/api/v1/admin/settings/contact";

/** Pulls the API's own message out of an error response. */
async function failure(response: Response): Promise<Error> {
  const text = await response.text();
  try {
    const body = JSON.parse(text) as { detail?: unknown };
    if (typeof body.detail === "string") {
      return new Error(body.detail);
    }
  } catch {
    // not JSON
  }
  return new Error(text || `Request failed (${response.status})`);
}

export async function getPracticePhone(withClient: (path: string) => string): Promise<string | null> {
  const response = await adminFetch(withClient(PATH));
  if (!response.ok) {
    throw await failure(response);
  }
  return ((await response.json()) as { phone: string | null }).phone;
}

/** Saves the number, or clears it with an empty string. The API returns the stored E.164 form. */
export async function savePracticePhone(
  withClient: (path: string) => string,
  phone: string,
): Promise<string | null> {
  const response = await adminFetch(withClient(PATH), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ phone }),
  });
  if (!response.ok) {
    throw await failure(response);
  }
  return ((await response.json()) as { phone: string | null }).phone;
}
