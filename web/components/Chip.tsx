import { BUCKET_LABEL, BUCKET_VARS, type Bucket } from "@/lib/api";

/**
 * Bucket como pastilha. Cor + marcador + rótulo textual: quem não distingue
 * matiz lê o nome, e a bolinha dá forma ao estado mesmo em tons de cinza.
 */
export function BucketChip({ bucket }: { bucket: Bucket }) {
  const v = BUCKET_VARS[bucket] ?? BUCKET_VARS.monitorar;
  return (
    <span
      className="chip"
      style={{ ["--chip-fg" as string]: v.fg, ["--chip-wash" as string]: v.wash }}
    >
      {BUCKET_LABEL[bucket] ?? bucket}
    </span>
  );
}
