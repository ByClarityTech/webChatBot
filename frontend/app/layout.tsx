import type { Metadata } from "next";
import "./globals.css";

const siteTitle = process.env.NEXT_PUBLIC_SITE_TITLE?.trim() || "Website Assistant";

export const metadata: Metadata = {
  title: siteTitle,
  description: "Website assistant",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
