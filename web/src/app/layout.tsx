import type { Metadata } from "next";
import "./globals.css";
import { AppSidebar } from "@/components/app-sidebar";
import { QueryProvider } from "@/lib/query-provider";

export const metadata: Metadata = {
  title: "Ditto Factory",
  description: "Web control plane for the Ditto Factory agent platform",
  icons: {
    icon: "/favicon.ico",
    apple: "/ditto-180.png",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="dark">
      <body className="font-sans antialiased">
        <QueryProvider>
          <div className="flex h-screen overflow-hidden">
            <AppSidebar />
            <main className="flex-1 overflow-y-auto p-6">{children}</main>
          </div>
        </QueryProvider>
      </body>
    </html>
  );
}
