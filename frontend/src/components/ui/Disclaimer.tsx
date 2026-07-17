import { Info } from "lucide-react";

// 普通数据页免责条；独立的「每日荐股」页使用更强的预测研究风险披露。
export function Disclaimer({ compact = false }: { compact?: boolean }) {
  if (compact) {
    return (
      <p className="text-[11px] leading-relaxed text-muted-foreground/70">
        除独立的「每日荐股」研究模块外，本页只客观呈现公开数据与榜单，不推荐个股、不预测涨跌。
      </p>
    );
  }
  return (
    <div className="mt-8 flex items-start gap-2 rounded-lg border border-border/60 bg-muted/20 p-3 text-xs leading-relaxed text-muted-foreground">
      <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" />
      <span>
        本页是中立的信息整理与 AI 接入工具。榜单（连板股 / 成交额等）均为<b className="text-foreground">客观公开数据</b>；
        除独立的「每日荐股」研究模块外，普通数据页<b className="text-foreground">只呈现事实，不推荐个股、不预测涨跌、不给买卖时机</b>。
        所有内容均不构成投资建议，请自行核实并独立决策，风险自担。
      </span>
    </div>
  );
}
