/** Ícones do menu. `aria-hidden` porque o rótulo textual ao lado já nomeia a aba. */
const p = { width: 17, height: 17, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor",
  strokeWidth: 1.8, strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true };

export const IconPainel = () => (
  <svg {...p}><rect x="3" y="3" width="7" height="9" rx="1" /><rect x="14" y="3" width="7" height="5" rx="1" />
  <rect x="14" y="12" width="7" height="9" rx="1" /><rect x="3" y="16" width="7" height="5" rx="1" /></svg>
);
export const IconBusca = () => (
  <svg {...p}><circle cx="11" cy="11" r="7" /><path d="M20 20l-3.5-3.5" /></svg>
);
export const IconFila = () => (
  <svg {...p}><path d="M4 6h16M4 12h11M4 18h7" /></svg>
);
export const IconSeta = () => (
  <svg {...p} width={20} height={20}><path d="M5 12h14M13 6l6 6-6 6" /></svg>
);
