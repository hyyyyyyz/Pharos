import { Icons } from "../design/icons";
import type { LiveModuleKey, ModuleKey } from "../store";
import "./ComingSoon.css";

type IconComponent = (typeof Icons)["search"];

/** Copy is verbatim from the prototype's `modInfo` map (design line 576). */
const MODULES: Record<
  Exclude<ModuleKey, LiveModuleKey>,
  { name: string; desc: string; Icon: IconComponent }
> = {
  writing: {
    name: "写作助手",
    desc: "基于文献库辅助综述与论文写作，规范引用。",
    Icon: Icons.writing,
  },
};

export function ComingSoon({ module }: { module: Exclude<ModuleKey, LiveModuleKey> }): JSX.Element {
  const { name, desc, Icon } = MODULES[module];
  return (
    <div className="ph-cs">
      <div className="ph-cs-box">
        <div className="ph-cs-ic">
          <Icon />
        </div>
        <div className="ph-cs-name">{name}</div>
        <div className="ph-cs-desc">{desc}</div>
        <span className="ph-cs-pill">即将推出</span>
      </div>
    </div>
  );
}
