import type { SiteConfig } from "../data/types";
import { applyTemplate } from "../util/text";
import { LayoutToggle } from "./LayoutToggle";

interface Props {
  config: SiteConfig;
  view: "overview" | "diagnostics";
  setView: (v: "overview" | "diagnostics") => void;
  layout: "split" | "chart-first";
  setLayout: (l: "split" | "chart-first") => void;
  showLayoutToggle: boolean;
  isSinglePeriod: boolean;
  templateVars: Record<string, string>;
}

export function Header({ config, view, setView, layout, setLayout, showLayoutToggle, isSinglePeriod, templateVars }: Props) {
  const onDiag = view === "diagnostics";
  const isCustom = config.headerBrand === "custom";
  const text = config.text;

  const eyebrow = onDiag
    ? text.heading_context_diagnostics
    : isSinglePeriod
      ? text.heading_context_single
      : text.heading_context_timeseries;

  const h1 = onDiag ? text.diagnostics_heading : text.overview_heading;

  const subTemplate = onDiag
    ? (isSinglePeriod ? text.subtitle_diagnostics_single : text.subtitle_diagnostics_timeseries)
    : (isSinglePeriod ? text.subtitle_single : text.subtitle_timeseries);
  const subtitle = applyTemplate(subTemplate, templateVars);

  // With header_brand "custom", the user's own logo, name and tagline, each
  // optional; with none of them the header has no brand column at all.
  const hasCustomText = Boolean(config.brandName || config.brandTagline);
  const showBrand = !isCustom || Boolean(config.logoUrl) || hasCustomText;

  return (
    <header className={"hdr" + (showBrand ? "" : " hdr-no-brand")}>
      {showBrand && (
        <div className={"hdr-brand " + (isCustom ? "hdr-brand-custom" : "")}>
          {isCustom ? (
            <>
              {config.logoUrl && (
                <img src={config.logoUrl} alt={config.brandName ?? "Logo"} className="hdr-logo" />
              )}
              {hasCustomText && (
                <div className="hdr-brand-text">
                  {config.brandName && <div className="hdr-brandname">{config.brandName}</div>}
                  {config.brandTagline && <div className="hdr-tagline">{config.brandTagline}</div>}
                </div>
              )}
            </>
          ) : (
            <>
              <img src="./assets/logo_small.png" alt="Terra Arc" className="hdr-logo" />
              <div className="hdr-brand-text">
                <div className="hdr-brandname">Terra Arc</div>
                <div className="hdr-tagline">Bridging data and impact</div>
              </div>
            </>
          )}
        </div>
      )}

      <div className="hdr-title">
        <div className="hdr-eyebrow">
          <span className="hdr-crumb">{text.heading_context}</span>
          <span className="hdr-eyebrow-sep">·</span>
          <span className="hdr-crumb hdr-crumb-muted">{eyebrow}</span>
        </div>
        <h1 className="hdr-h1">{h1}</h1>
        <div className="hdr-sub">{subtitle}</div>
      </div>

      <div className="hdr-controls">
        {showLayoutToggle && <LayoutToggle layout={layout} setLayout={setLayout} />}
        <button
          className={"hdr-viewlink " + (onDiag ? "is-back" : "")}
          onClick={() => setView(onDiag ? "overview" : "diagnostics")}
          title={onDiag ? "Back to overview" : "Open diagnostics"}
        >
          {onDiag ? (
            <>
              <svg width="12" height="12" viewBox="0 0 12 12"><path d="M8 2 L4 6 L8 10" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" /></svg>
              Overview
            </>
          ) : (
            <>
              Diagnostics
              <svg width="12" height="12" viewBox="0 0 12 12"><path d="M4 2 L8 6 L4 10" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" /></svg>
            </>
          )}
        </button>
      </div>
    </header>
  );
}
