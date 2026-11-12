/**
 * Reads a Server-Sent Events response with fetch, so headers (and cookies) can be sent.
 * EventSource cannot send custom headers, and tokens must not travel in URLs.
 *
 * Calls onEvent for each event with its parsed JSON payload. Comment lines (keep-alives) are
 * ignored. Resolves when the server closes the stream or the signal aborts.
 */
export async function consumeSse(
  response: Response,
  onEvent: (event: string, data: unknown) => void,
  signal?: AbortSignal,
): Promise<void> {
  if (!response.ok || !response.body) {
    throw new Error(`Stream failed (${response.status})`);
  }
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  let eventName = "message";
  let dataLines: string[] = [];

  const dispatch = () => {
    if (dataLines.length > 0) {
      try {
        onEvent(eventName, JSON.parse(dataLines.join("\n")));
      } catch {
        // A malformed frame is skipped; the next event resyncs.
      }
    }
    eventName = "message";
    dataLines = [];
  };

  try {
    while (!signal?.aborted) {
      const { value, done } = await reader.read();
      if (done) {
        break;
      }
      buffer += value;
      let newline = buffer.indexOf("\n");
      while (newline >= 0) {
        const line = buffer.slice(0, newline).replace(/\r$/, "");
        buffer = buffer.slice(newline + 1);
        if (line === "") {
          dispatch();
        } else if (line.startsWith(":")) {
          // keep-alive comment
        } else if (line.startsWith("event:")) {
          eventName = line.slice(6).trim();
        } else if (line.startsWith("data:")) {
          dataLines.push(line.slice(5).trim());
        }
        newline = buffer.indexOf("\n");
      }
    }
  } finally {
    reader.cancel().catch(() => undefined);
  }
}
