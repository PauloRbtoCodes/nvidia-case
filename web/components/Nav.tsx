"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const ABAS = [
  { href: "/", label: "Painel" },
  { href: "/busca", label: "Varredura" },
  { href: "/fila", label: "Fila" },
];

/**
 * Abas com estado ativo real.
 *
 * `aria-current="page"` não é enfeite: é o que faz leitor de tela anunciar em
 * qual aba a pessoa está. O destaque visual — peso e a régua verde embaixo —
 * sai do mesmo atributo, então os dois nunca divergem.
 */
export function NavLinks() {
  const path = usePathname();
  return (
    <nav className="tabs" aria-label="Seções">
      {ABAS.map(({ href, label }) => {
        const ativo = href === "/" ? path === "/" : path.startsWith(href);
        return (
          <Link key={href} href={href} aria-current={ativo ? "page" : undefined}>
            {label}
          </Link>
        );
      })}
    </nav>
  );
}
