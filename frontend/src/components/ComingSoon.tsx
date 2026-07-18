import { Icons } from "../design/icons";
import type { ModuleKey } from "../store";
import "./ComingSoon.css";

type IconComponent = (typeof Icons)["search"];

/** Copy is verbatim from the prototype's `modInfo` map (design line 576). */
const MODULES: Record<
  Exclude<ModuleKey, "library">,
  { name: string; desc: string; Icon: IconComponent }
> = {
  search: {
    name: "文献检索",
    desc: "跨库检索论文，一键导入到文库并翻译。",
    Icon: Icons.search,
  },
  kb: {
    name: "研究知识库",
    desc: "把论文、笔记与洞见沉淀为可检索的知识库。",
    Icon: Icons.kb,
  },
  writing: {
    name: "写作助手",
    desc: "基于文献库辅助综述与论文写作，规范引用。",
    Icon: Icons.writing,
  },
};

export function ComingSoon({ module }: { module: Exclude<ModuleKey, "library"> }): JSX.Element {
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
