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
        {/* Archivo variável: o eixo de largura (wdth) é usado como elemento de
            design — nome de empresa e título saem expandidos, corpo fica normal.
            Uma família só, sem par display/corpo. */}
        <link
          rel="stylesheet"
          href="https://fonts.googleapis.com/css2?family=Archivo:wdth,wght@75..125,400..700&display=swap"
        />
      </head>
      <body>
        <a href="#conteudo" className="visually-hidden">Pular para o conteúdo</a>
        <header className="masthead">
          <div className="masthead-inner">
            {/* A marca leva ao painel: clicar nela é "voltar ao início", e
                disparar uma tela de ação nesse gesto surpreende. */}
            <Link href="/" className="mark" aria-label="Startup AI Radar — ir para o painel">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src="/mascote.jpeg" alt="" width={34} height={34} />
              <b>Startup AI Radar</b>
            </Link>
            <NavLinks />
          </div>
        </header>
        <main className="shell" id="conteudo">{children}</main>
      </body>
    </html>
  );
}
