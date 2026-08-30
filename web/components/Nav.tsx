"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { IconBusca, IconFila, IconPainel } from "@/components/Icons";

const ABAS = [
  { href: "/", label: "Painel", Icon: IconPainel },
  { href: "/busca", label: "Buscar", Icon: IconBusca },
  { href: "/fila", label: "Fila", Icon: IconFila },
];

/**
 * Abas com estado ativo real.
 *
 * `aria-current="page"` não é enfeite: é o que faz leitor de tela anunciar em
 * qual aba a pessoa está. O destaque visual (fundo verde-claro + peso) sai do
 * mesmo atributo, então os dois nunca divergem.
 */
export function NavLinks() {
  const path = usePathname();
  return (
    <nav className="nav" aria-label="Seções">
      {ABAS.map(({ href, label, Icon }) => {
        const ativo = href === "/" ? path === "/" : path.startsWith(href);
        return (
          <Link key={href} href={href} aria-current={ativo ? "page" : undefined}>
            <Icon />
            {label}
          </Link>
        );
      })}
    </nav>
  );
}
