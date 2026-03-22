import "./globals.css";
import "react-diff-view/style/index.css";

import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = {
  title: "PaperTrace",
  description: "Trace paper repositories back to their likely base code and mapped contributions.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
