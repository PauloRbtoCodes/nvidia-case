import type { Metadata } from "next";
import Link from "next/link";
import { NavLinks } from "@/components/Nav";
import "./globals.css";

export const metadata: Metadata = {
  title: "Startup AI Radar",
  description:
    "Mapeia startups brasileiras de IA, mede defensibilidade e recomenda tecnologia NVIDIA — para o time de Startups & VCs.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="pt-BR">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link
          rel="stylesheet"
          href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap"
        />
      </head>
      <body>
        <a href="#conteudo" className="visually-hidden">Pular para o conteúdo</a>
        <header className="topbar">
          <div className="topbar-inner">
            {/* A logo leva ao Painel, não à busca: clicar na marca é "voltar ao
                início", e disparar uma tela de ação nesse gesto surpreende. */}
            <Link href="/" className="brand" aria-label="Startup AI Radar — ir para o painel">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src="/mascote.jpeg" alt="" width={52} height={52} />
              <span className="brand-text">
                <b>Startup AI Radar</b>
                <span>Startups &amp; VCs · Inception Brasil</span>
              </span>
            </Link>
            <NavLinks />
          </div>
        </header>
        <main className="shell" id="conteudo">{children}</main>
      </body>
    </html>
  );
}
