/**
 * MermaidDiagram — renders Mermaid source to SVG in-browser (#133/#140).
 *
 * Used by the Cloud Assessment viewer to render the service topology
 * (findings/diagrams/cloud_topology.mmd). securityLevel 'strict' sanitises the
 * SVG; on a parse error we fall back to showing the raw source.
 */
import { useEffect, useRef, useState } from 'react';
import mermaid from 'mermaid';

mermaid.initialize({
  startOnLoad: false,
  theme: 'default',
  securityLevel: 'strict',
  // Render at natural size so a wide diagram scrolls rather than being squashed.
  flowchart: { useMaxWidth: false, htmlLabels: true },
});

let _seq = 0;

export function MermaidDiagram({ source }: { source: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    if (!source) return;
    setError(null);
    const id = `mmd-${_seq++}`;
    mermaid
      .render(id, source)
      .then(({ svg }) => {
        if (!cancelled && ref.current) ref.current.innerHTML = svg;
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [source]);

  if (error) {
    return (
      <pre className="overflow-auto rounded-lg border border-border bg-muted/30 p-3 text-xs text-muted-foreground whitespace-pre-wrap">
        {source}
      </pre>
    );
  }
  // overflow-auto → scroll a wide/tall diagram; [&_svg]:mx-auto → centre when it fits.
  return (
    <div
      ref={ref}
      className="max-h-[75vh] overflow-auto rounded-lg border border-border bg-white p-3 [&_svg]:mx-auto [&_svg]:max-w-none"
    />
  );
}
