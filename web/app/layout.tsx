import type { Metadata } from "next";
import Link from "next/link";
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
          href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap"
        />
      </head>
      <body>
        {/* Primeiro tabbable da página: quem navega por teclado não deve
            percorrer a navegação inteira em toda troca de tela. */}
        <a href="#conteudo" className="visually-hidden">
          Pular para o conteúdo
        </a>
        <header className="topbar">
          <div className="topbar-inner">
            <Link href="/" className="brand">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src="/mascote.jpeg" alt="" width={34} height={34} />
              <span className="brand-text">
                <b>Startup AI Radar</b>
                <span>Startups &amp; VCs · Inception Brasil</span>
              </span>
            </Link>
            <nav className="nav" aria-label="Principal">
              <Link href="/">Busca</Link>
              <Link href="/fila">Fila</Link>
            </nav>
          </div>
        </header>
        <main className="shell" id="conteudo">
          {children}
        </main>
      </body>
    </html>
  );
}
