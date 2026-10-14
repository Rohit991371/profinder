import type { Metadata } from "next";
import { Inter } from "next/font/google";
import Link from "next/link";
import Script from "next/script";
import "./globals.css";
import Navbar from "@/components/Navbar";
import Providers from "@/components/Providers";
import { ensureSchema } from "@/lib/prisma";
import { SITE } from "@/lib/seo";

const inter = Inter({ subsets: ["latin"], display: "swap" });
const GA_ID = process.env.NEXT_PUBLIC_GA_ID;

export const metadata: Metadata = {
  metadataBase: new URL(SITE),
  title: { default: "ProFinder — Find Universities, Professors & Research Opportunities", template: "%s | ProFinder" },
  description: "Find Chinese universities, professors, and research areas. Compare what is stored, then track an application.",
  alternates: { canonical: "/" },
  openGraph: {
    type: "website",
    siteName: "ProFinder",
    url: SITE,
    title: "ProFinder — Find Universities, Professors & Research Opportunities",
    description: "Find Chinese universities, professors, and research areas. Compare what is stored, then track an application.",
  },
  twitter: {
    card: "summary",
    title: "ProFinder — Find Universities, Professors & Research Opportunities",
    description: "Find Chinese universities, professors, and research areas.",
  },
  robots: { index: true, follow: true },
  icons: {
    icon: [{ url: "/icon.png", type: "image/png", sizes: "192x192" }],
    apple: [{ url: "/apple-icon.png", sizes: "180x180", type: "image/png" }],
    shortcut: "/favicon.ico",
  },
};

export default async function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  await ensureSchema();
  return (
    <html lang="en">
      <body className={inter.className + " min-h-screen bg-[var(--paper)] text-[var(--gray-900)] antialiased"}>
        <Providers>
          <Navbar />
          <main>{children}</main>
          <footer className="mt-16 border-t border-[var(--gray-200)] py-8">
            <div className="page-container flex flex-col gap-6 text-sm text-[var(--gray-500)] sm:flex-row sm:items-start sm:justify-between">
              <div>
                <p>
                  <img src="/logo.png" alt="ProFinder Now" className="h-7 w-auto" />
                </p>
                <p className="mt-1 max-w-xs text-xs text-[var(--gray-500)]">
                  Stored records only. Match scores are not admission predictions.
                </p>
              </div>
              <nav className="flex flex-wrap gap-x-4 gap-y-2" aria-label="Footer">
                <Link href="/universities">Universities</Link>
                <Link href="/programs">Programs</Link>
                <Link href="/professors">Professors</Link>
                <Link href="/find">Find My Match</Link>
                <Link href="/scholarship">Funding</Link>
                <Link href="/compare">Compare</Link>
                <Link href="/apply">Apply for Me</Link>
                <Link href="/pricing">Plans</Link>
                <Link href="/support">Support</Link>
              </nav>
            </div>
          </footer>
        </Providers>
        {GA_ID ? (
          <>
            <Script src={"https://www.googletagmanager.com/gtag/js?id=" + GA_ID} strategy="afterInteractive" />
            <Script id="google-analytics" strategy="afterInteractive">
              {"window.dataLayer = window.dataLayer || [];\nfunction gtag(){window.dataLayer.push(arguments);}\ngtag('js', new Date());\ngtag('config', '" +
                GA_ID +
                "');"}
            </Script>
          </>
        ) : null}
      </body>
    </html>
  );
}
