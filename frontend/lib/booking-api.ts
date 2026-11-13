const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type AvailabilitySlot = {
  start: string;
  end: string;
};

export type AvailabilityResponse = {
  date: string;
  timezone: string;
  slots: AvailabilitySlot[];
};

export type SuggestionsResponse = {
  slots: AvailabilitySlot[];
  timezone: string;
};

export type BookingCreatePayload = {
  /** Slug of the client whose widget this is. */
  client: string;
  name: string;
  phone: string;
  email: string;
  slot_start: string;
  thread_id?: string | null;
  /** Required: asked of every visitor. */
  is_current_patient: boolean;
  /** Only when the chat has no consent yet: the box ticked in the booking card. */
  consent_accepted?: boolean;
  consent_text_version?: number | null;
};

export type BookingResponse = {
  id: string;
  status: string;
  slot_start: string;
  slot_end: string;
  message: string;
};

/** The API's stable codes. The widget branches on these, never on the message. */
export type BookingErrorCode =
  | "SLOT_TAKEN"
  | "SESSION_ENDED"
  | "CONSENT_UNAVAILABLE"
  | "CURRENT_PATIENT_REQUIRED"
  | null;

export class BookingError extends Error {
  readonly code: BookingErrorCode;
  readonly status: number;

  constructor(message: string, code: BookingErrorCode, status: number) {
    super(message);
    this.name = "BookingError";
    this.code = code;
    this.status = status;
  }
}

async function parseApiError(
  response: Response,
): Promise<{ message: string; code: BookingErrorCode }> {
  const text = await response.text();
  try {
    const body = JSON.parse(text) as {
      detail?: string | Array<{ msg?: string }>;
      code?: string;
    };
    const code = (body.code ?? null) as BookingErrorCode;
    if (typeof body.detail === "string") {
      return { message: body.detail, code };
    }
    if (Array.isArray(body.detail)) {
      const messages = body.detail
        .map((item) => item.msg)
        .filter((msg): msg is string => Boolean(msg));
      if (messages.length > 0) {
        return { message: messages.join(", "), code };
      }
    }
  } catch {
    // not JSON
  }
  return { message: text || `Request failed (${response.status})`, code: null };
}

export async function getAvailability(client: string, date: string): Promise<AvailabilityResponse> {
  const params = new URLSearchParams({ client, date });
  const response = await fetch(`${API_URL}/api/v1/booking/availability?${params}`);

  if (!response.ok) {
    const failure = await parseApiError(response);
    throw new BookingError(failure.message, failure.code, response.status);
  }

  return response.json();
}

/**
 * Up to three of the next open slots. Never throws: no suggestions is a normal state, and the
 * day picker still works without them.
 */
export async function getSuggestions(client: string): Promise<SuggestionsResponse> {
  const empty = { slots: [], timezone: "UTC" };
  try {
    const params = new URLSearchParams({ client });
    const response = await fetch(`${API_URL}/api/v1/booking/suggestions?${params}`);
    if (!response.ok) {
      return empty;
    }
    return (await response.json()) as SuggestionsResponse;
  } catch {
    return empty;
  }
}

export async function createBooking(payload: BookingCreatePayload): Promise<BookingResponse> {
  const response = await fetch(`${API_URL}/api/v1/booking`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      client: payload.client,
      name: payload.name,
      phone: payload.phone,
      email: payload.email,
      slot_start: payload.slot_start,
      thread_id: payload.thread_id ?? null,
      consent_accepted: payload.consent_accepted ?? false,
      consent_text_version: payload.consent_text_version ?? null,
      is_current_patient: payload.is_current_patient,
    }),
  });

  if (!response.ok) {
    const failure = await parseApiError(response);
    throw new BookingError(failure.message, failure.code, response.status);
  }

  return response.json();
}
