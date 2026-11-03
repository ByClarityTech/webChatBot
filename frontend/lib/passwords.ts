// Strong passwords for admin-created users and resets. Uses the browser's CSPRNG
// (crypto.getRandomValues) only; never Math.random.

// Look-alike characters (0/O/o, 1/l/I) are left out so passwords can be read aloud or retyped.
const UPPER = "ABCDEFGHJKLMNPQRSTUVWXYZ";
const LOWER = "abcdefghijkmnpqrstuvwxyz";
const DIGITS = "23456789";
const SYMBOLS = "!@#$%^&*-_=+?";
const ALL = UPPER + LOWER + DIGITS + SYMBOLS;

export const GENERATED_PASSWORD_LENGTH = 20;

// Mirrors backend/app/core/security.py password_problem; the API checks again.
export const PASSWORD_MIN_LENGTH = 12;
export const PASSWORD_MAX_LENGTH = 128;

/** Why a new password would be refused, or null if it is acceptable. */
export function passwordProblem(password: string, email: string): string | null {
  if (password.length < PASSWORD_MIN_LENGTH) {
    return `Password must be at least ${PASSWORD_MIN_LENGTH} characters`;
  }
  if (password.length > PASSWORD_MAX_LENGTH) {
    return `Password must be at most ${PASSWORD_MAX_LENGTH} characters`;
  }
  if (password.trim().toLowerCase() === email.trim().toLowerCase()) {
    return "Password must not be the same as the email address";
  }
  return null;
}

/** Uniform random integer in [0, max), without modulo bias. */
function randomIndex(max: number): number {
  const limit = Math.floor(0x1_0000_0000 / max) * max;
  const buffer = new Uint32Array(1);
  for (;;) {
    crypto.getRandomValues(buffer);
    if (buffer[0] < limit) {
      return buffer[0] % max;
    }
  }
}

function pick(chars: string): string {
  return chars[randomIndex(chars.length)];
}

/** At least one character from each group, the rest from all groups, then shuffled. */
export function generatePassword(length: number = GENERATED_PASSWORD_LENGTH): string {
  if (length < 12) {
    throw new Error("Generated passwords must be at least 12 characters");
  }
  const chars = [pick(UPPER), pick(LOWER), pick(DIGITS), pick(SYMBOLS)];
  while (chars.length < length) {
    chars.push(pick(ALL));
  }
  for (let i = chars.length - 1; i > 0; i--) {
    const j = randomIndex(i + 1);
    [chars[i], chars[j]] = [chars[j], chars[i]];
  }
  return chars.join("");
}
