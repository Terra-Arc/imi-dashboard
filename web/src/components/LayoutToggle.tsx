interface Props {
  layout: "split" | "chart-first";
  setLayout: (l: "split" | "chart-first") => void;
}

export function LayoutToggle({ layout, setLayout }: Props) {
  return (
    <div className="layout-toggle" role="tablist" aria-label="Layout">
      <button
        role="tab"
        aria-selected={layout === "split"}
        className={"layout-toggle-btn " + (layout === "split" ? "is-active" : "")}
        onClick={() => setLayout("split")}
        title="Split: map + chart side by side"
      >
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
          <rect x="1" y="2" width="5" height="10" rx="1" stroke="currentColor" strokeWidth="1.4" />
          <rect x="8" y="2" width="5" height="10" rx="1" stroke="currentColor" strokeWidth="1.4" />
        </svg>
      </button>
      <button
        role="tab"
        aria-selected={layout === "chart-first"}
        className={"layout-toggle-btn " + (layout === "chart-first" ? "is-active" : "")}
        onClick={() => setLayout("chart-first")}
        title="Chart-first: full-width chart over map"
      >
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
          <rect x="1" y="2" width="12" height="4" rx="1" stroke="currentColor" strokeWidth="1.4" />
          <rect x="1" y="8" width="12" height="4" rx="1" stroke="currentColor" strokeWidth="1.4" />
        </svg>
      </button>
    </div>
  );
}
