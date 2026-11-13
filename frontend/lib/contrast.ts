// Mirrors backend/app/services/widget_branding.py. The API decides what is saved; this only
// shows the admin the same figure while they type.

export const MIN_CONTRAST = 4.5;
export const WHITE = "#FFFFFF";
export const NEAR_BLACK = "#111827";

const HEX = /^#[0-9A-Fa-f]{6}$/;

function channel(value: number): number {
  const scaled = value / 255;
  return scaled <= 0.03928 ? scaled / 12.92 : ((scaled + 0.055) / 1.055) ** 2.4;
}

export function relativeLuminance(color: string): number {
  if (!HEX.test(color)) {
    throw new Error("Colors must look like #1D4ED8");
  }
  const red = channel(parseInt(color.slice(1, 3), 16));
  const green = channel(parseInt(color.slice(3, 5), 16));
  const blue = channel(parseInt(color.slice(5, 7), 16));
  return 0.2126 * red + 0.7152 * green + 0.0722 * blue;
}

/** WCAG contrast ratio between two colors, from 1 to 21. */
export function contrastRatio(first: string, second: string): number {
  const [lighter, darker] = [relativeLuminance(first), relativeLuminance(second)].sort(
    (a, b) => b - a,
  );
  return (lighter + 0.05) / (darker + 0.05);
}

/** Whichever of white or near-black reads better on this background. */
export function textColorFor(background: string): string {
  return contrastRatio(WHITE, background) >= contrastRatio(NEAR_BLACK, background)
    ? WHITE
    : NEAR_BLACK;
}

export function isValidHex(color: string): boolean {
  return HEX.test(color);
}

/** The header's text color and ratio, or null while the value is not a full hex color. */
export function headerContrast(background: string): { textColor: string; ratio: number } | null {
  if (!HEX.test(background)) {
    return null;
  }
  const textColor = textColorFor(background);
  return { textColor, ratio: contrastRatio(textColor, background) };
}
