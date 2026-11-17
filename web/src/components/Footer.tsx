import type { Provenance } from "../data/types";
import { formatDate } from "../util/text";

/** Where the footer credit links: the project's public repository. */
const PROJECT_URL = "https://github.com/Terra-Arc/imi-dashboard";

interface Props {
  showTerraLogo: boolean;
  attribution: string;
  disclaimer: string;
  metaVersion: string;
  /** manifest.provenance; absent in dashboards from older converters. */
  provenance?: Provenance;
}

/** "Converted Nov 4, 2026 · imi-dashboard 0.1.0": the day (UTC) the
 *  converter wrote this dashboard, and its version. Null without a record.
 *  The version's local part (from the first "+", e.g. "+unknown.g970248c89.
 *  d20261103" from an editable install) is dropped here only; the manifest
 *  and `imi-dashboard info` keep the full string. */
function convertedText(p: Provenance | undefined): string | null {
  if (!p?.convertedAt) return null;
  const version = p.converter?.version?.split("+")[0];
  const by = p.converter?.name && version ? ` · ${p.converter.name} ${version}` : "";
  return `Converted ${formatDate(p.convertedAt)}${by}`;
}

export function Footer({ showTerraLogo, attribution, disclaimer, metaVersion, provenance }: Props) {
  const converted = convertedText(provenance);
  return (
    <footer className="ftr">
      <div className="ftr-inner">
        <div className="ftr-col ftr-col-ack">
          <div className="ftr-lbl">Acknowledgements</div>
          <p className="ftr-body">{attribution}</p>
        </div>

        <div className="ftr-col ftr-col-dis">
          <div className="ftr-lbl">Disclaimer</div>
          <p className="ftr-body">{disclaimer}</p>
        </div>

        <div className={"ftr-col ftr-col-mark" + (showTerraLogo ? "" : " is-hidden")}>
          {showTerraLogo && (
            <>
              <div className="ftr-lbl">Made with IMI Dashboard by</div>
              <a className="ftr-mark" href={PROJECT_URL} target="_blank" rel="noopener noreferrer">
                <img src="./assets/logo_small.png" alt="" className="ftr-mark-logo" />
                <div className="ftr-mark-text">
                  <div className="ftr-mark-name">Terra Arc</div>
                  <div className="ftr-mark-tag">Source code on GitHub</div>
                </div>
              </a>
            </>
          )}
        </div>
      </div>

      <div className="ftr-meta">
        <span className="ftr-meta-item">{metaVersion}</span>
        {converted && (
          <>
            <span className="ftr-meta-sep">·</span>
            <span className="ftr-meta-item ftr-meta-wrap">{converted}</span>
          </>
        )}
      </div>
    </footer>
  );
}
