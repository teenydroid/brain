import type { Metadata } from "next";
import { Chakra_Petch, Geist, JetBrains_Mono } from "next/font/google";
import Script from "next/script";
import { SiteFooter } from "@/components/site-footer";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const jetbrains = JetBrains_Mono({
  variable: "--font-jetbrains",
  subsets: ["latin"],
});

/** The display face: squared-off and technical, for headings and the HUD. */
const chakra = Chakra_Petch({
  variable: "--font-chakra",
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
});

/** Google Analytics. Set to a different id, or blank to ship no tag at all. */
const GA_ID = process.env.NEXT_PUBLIC_GA_ID ?? "G-93MFR6JZXK";

export const metadata: Metadata = {
  title: "Teeny's Mind",
  description:
    "The open brain of Teeny, the smol AI behind @TinyHumanAI: its soul, its rules, its tools, its memory, and a log of every reflection behind every reply.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${jetbrains.variable} ${chakra.variable} dark h-full antialiased`}
    >
      <body className="crt min-h-full flex flex-col">
        {GA_ID ? (
          <>
            <Script src={`https://www.googletagmanager.com/gtag/js?id=${GA_ID}`} strategy="afterInteractive" />
            <Script id="gtag-init" strategy="afterInteractive">
              {`window.dataLayer = window.dataLayer || [];
function gtag(){dataLayer.push(arguments);}
gtag('js', new Date());
gtag('config', '${GA_ID}');`}
            </Script>
          </>
        ) : null}
        <main className="flex-1">{children}</main>
        <SiteFooter />
      </body>
    </html>
  );
}
