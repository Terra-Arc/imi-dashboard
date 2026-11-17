import type { ReactNode } from "react";

interface Props {
  title: string;
  subtitle?: string;
  large?: boolean;
  right?: ReactNode;
}

export function PanelHeader({ title, subtitle, large, right }: Props) {
  return (
    <div className={"panel-hd" + (large ? " panel-hd-lg" : "")}>
      <div className="panel-hd-text">
        <h3 className="panel-title">{title}</h3>
        {subtitle && <div className="panel-sub">{subtitle}</div>}
      </div>
      {right && <div className="panel-hd-right">{right}</div>}
    </div>
  );
}
