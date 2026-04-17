import type { Source, DocType } from "@/types";
import clsx from "clsx";

const SOURCE_LABELS: Record<Source, string> = {
  bcc_minutes: "Minutes",
  ldc: "LDC",
  civicclerk: "Staff Report",
  gis: "Zoning",
};

const SOURCE_COLORS: Record<Source, string> = {
  bcc_minutes: "bg-blue-100 text-blue-800",
  ldc: "bg-green-100 text-green-800",
  civicclerk: "bg-purple-100 text-purple-800",
  gis: "bg-orange-100 text-orange-800",
};

export function SourceBadge({ source }: { source: Source }) {
  return (
    <span className={clsx("px-2 py-0.5 rounded text-xs font-medium", SOURCE_COLORS[source])}>
      {SOURCE_LABELS[source]}
    </span>
  );
}
