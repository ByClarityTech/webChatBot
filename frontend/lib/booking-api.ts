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

export type BookingCreatePayload = {
  name: string;
  phone: string;
  email: string;
  slot_start: string;
  thread_id?: string | null;
};

export type BookingResponse = {
  id: string;
  status: string;
  slot_start: string;
  slot_end: string;
  message: string;
};

async function parseApiError(response: Response): Promise<string> {
  const text = await response.text();
  try {
    const body = JSON.parse(text) as { detail?: string | Array<{ msg?: string }> };
    if (typeof body.detail === "string") {
      return body.detail;
    }
    if (Array.isArray(body.detail)) {
      const messages = body.detail
        .map((item) => item.msg)
        .filter((msg): msg is string => Boolean(msg));
      if (messages.length > 0) {
        return messages.join(", ");
      }
    }
  } catch {
    // not JSON
  }
  return text || `Request failed (${response.status})`;
}

export async function getAvailability(date: string): Promise<AvailabilityResponse> {
  const response = await fetch(
    `${API_URL}/api/v1/booking/availability?date=${encodeURIComponent(date)}`,
  );

  if (!response.ok) {
    throw new Error(await parseApiError(response));
  }

  return response.json();
}

export async function createBooking(payload: BookingCreatePayload): Promise<BookingResponse> {
  const response = await fetch(`${API_URL}/api/v1/booking`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      name: payload.name,
      phone: payload.phone,
      email: payload.email,
      slot_start: payload.slot_start,
      thread_id: payload.thread_id ?? null,
    }),
  });

  if (!response.ok) {
    throw new Error(await parseApiError(response));
  }

  return response.json();
}
