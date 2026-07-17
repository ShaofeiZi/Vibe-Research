import { useEffect, useMemo, useState } from "react";
import {
  AlertCircle, BarChart3, BrainCircuit, CheckCircle2, ChevronDown,
  ChevronUp, Clock3, ExternalLink, FileSearch, FileText, History,
  Loader2, Newspaper, RefreshCw, ShieldAlert, Sparkles, Target, Trophy,
} from "lucide-react";
import { toast } from "sonner";
import { PageHeader } from "@/components/ui/PageHeader";
import { GlassCard } from "@/components/ui/GlassCard";
import {
  api, ApiError, type DailyRecommendationReport, type RecommendationCandidate,
  type RecommendationDirection, type RecommendationHistory, type RecommendationReport,
} from "@/lib/api";
import { cn } from "@/lib/utils";

const pctColor = (value: number | null) =>
  value == null ? "text-muted-foreground" : value > 0 ? "text-danger" : value < 0 ? "text-success" : "text-muted-foreground";
const signed = (value: number | null, suffix = "%") =>
  value == null ? "—" : `${value > 0 ? "+" : ""}${value.toFixed(2)}${suffix}`;
const money = (value: number | null) => {
  if (value == null) return "—";
  if (value >= 1e8) return `${(value / 1e8).toFixed(1)}亿`;
  if (value >= 1e4) return `${(value / 1e4).toFixed(0)}万`;
  return value.toFixed(0);
};
const metric = (value: number | null) => value == null || value === 0 ? "—" : value.toFixed(2);

function BulletList({ items, tone = "normal" }: { items: string[]; tone?: "normal" | "risk" | "positive" }) {
  const dot = tone === "risk" ? "bg-destructive" : tone === "positive" ? "bg-success" : "bg-primary";
  if (!items.length) return <p className="text-xs text-muted-foreground/60">暂无明确证据。</p>;
  return (
    <ul className="space-y-1.5">
      {items.map((item, index) => (
        <li key={`${item}-${index}`} className="flex gap-2 text-sm leading-relaxed text-muted-foreground">
          <span className={cn("mt-2 h-1.5 w-1.5 shrink-0 rounded-full", dot)} />
          <span>{item}</span>
        </li>
      ))}
    </ul>
  );
}

function EvidenceSummary({ report }: { report: DailyRecommendationReport }) {
  const summary = report.source_summary;
  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
      {[
        { label: "当日资讯", value: `${summary.radar_items} 条`, icon: Newspaper },
        { label: "候选池", value: "5 × 5", icon: BarChart3 },
        { label: "候选研报正文", value: `${summary.candidate_reports_read}/${summary.candidate_reports_total}`, icon: FileSearch },
        { label: "最终研报正文", value: `${summary.final_reports_read}/${summary.final_reports_total}`, icon: FileText },
      ].map(({ label, value, icon: Icon }) => (
        <div key={label} className="rounded-xl border border-border/40 bg-muted/15 p-3">
          <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
            <Icon className="h-3.5 w-3.5 text-primary" /> {label}
          </div>
          <p className="mt-1 font-mono text-base font-bold">{value}</p>
        </div>
      ))}
    </div>
  );
}

function CandidateRow({
  candidate,
  selected,
}: {
  candidate: RecommendationCandidate;
  selected: boolean;
}) {
  const report = candidate.report;
  return (
    <tr className={cn("border-t border-border/25", selected && "bg-primary/8")}>
      <td className="whitespace-nowrap px-3 py-2.5">
        <div className="flex items-center gap-2">
          <span className={cn(
            "flex h-5 w-5 items-center justify-center rounded-full text-[10px] font-bold",
            selected ? "bg-primary text-primary-foreground" : "bg-muted/60 text-muted-foreground",
          )}>{candidate.rank}</span>
          <div>
            <p className={cn("text-sm font-semibold", selected && "text-primary")}>{candidate.name}</p>
            <p className="font-mono text-[10px] text-muted-foreground">{candidate.code}</p>
          </div>
        </div>
      </td>
      <td className="whitespace-nowrap px-3 py-2.5 text-right font-mono text-xs">{candidate.price ?? "—"}</td>
      <td className={cn("whitespace-nowrap px-3 py-2.5 text-right font-mono text-xs", pctColor(candidate.change_pct))}>
        {signed(candidate.change_pct)}
      </td>
      <td className="whitespace-nowrap px-3 py-2.5 text-right text-xs text-muted-foreground">{money(candidate.amount)}</td>
      <td className="whitespace-nowrap px-3 py-2.5 text-right font-mono text-xs text-muted-foreground">{metric(candidate.pe_ttm)}</td>
      <td className="whitespace-nowrap px-3 py-2.5 text-right font-mono text-xs font-semibold text-primary">{candidate.score.toFixed(1)}</td>
      <td className="min-w-72 px-3 py-2.5">
        {report ? (
          <div>
            <div className="flex items-center gap-1.5">
              {report.read_status === "ok"
                ? <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-success" />
                : <AlertCircle className="h-3.5 w-3.5 shrink-0 text-warning" />}
              {report.pdf_url ? (
                <a
                  href={report.pdf_url}
                  target="_blank"
                  rel="noreferrer"
                  className="line-clamp-1 text-xs font-medium hover:text-primary"
                  title={report.title}
                >
                  {report.title}
                </a>
              ) : <span className="line-clamp-1 text-xs">{report.title}</span>}
            </div>
            <p className="mt-1 text-[10px] text-muted-foreground">
              {report.organization || "未知机构"} · {report.publish_date || "日期未知"} · {report.rating || "未评级"}
              {report.read_status === "ok" ? ` · 已读${report.pages_read}页` : ` · ${report.read_error || "正文不可读"}`}
            </p>
          </div>
        ) : <span className="text-xs text-muted-foreground/50">无可用研报</span>}
      </td>
    </tr>
  );
}

function DirectionCard({
  direction,
  selectedCode,
}: {
  direction: RecommendationDirection;
  selectedCode: string;
}) {
  const [open, setOpen] = useState(true);
  const selected = direction.stocks.some((stock) => stock.code === selectedCode);
  return (
    <GlassCard className={cn("!p-0 overflow-hidden", selected && "ring-1 ring-primary/40")} glow={selected}>
      <button
        onClick={() => setOpen((value) => !value)}
        className="flex w-full flex-wrap items-center gap-3 px-4 py-3 text-left"
      >
        <span className={cn(
          "flex h-8 w-8 shrink-0 items-center justify-center rounded-xl font-mono text-sm font-extrabold",
          selected ? "bg-primary text-primary-foreground" : "bg-primary/15 text-primary",
        )}>
          {direction.rank}
        </span>
        <div className="min-w-36 flex-1">
          <div className="flex items-center gap-2">
            <h3 className="font-bold">{direction.name}</h3>
            {selected && <span className="rounded-full bg-primary/15 px-2 py-0.5 text-[10px] font-medium text-primary">最终方向</span>}
          </div>
          <p className="mt-0.5 text-[11px] text-muted-foreground">
            行业涨幅排名 #{direction.industry_rank} · 上涨 {direction.up_count} / 下跌 {direction.down_count}
          </p>
        </div>
        <div className="flex items-center gap-5 text-right">
          <div>
            <p className="text-[10px] text-muted-foreground">涨跌幅</p>
            <p className={cn("font-mono text-sm font-bold", pctColor(direction.change_pct))}>{signed(direction.change_pct)}</p>
          </div>
          <div>
            <p className="text-[10px] text-muted-foreground">方向分</p>
            <p className="font-mono text-sm font-bold text-primary">{direction.score.toFixed(1)}</p>
          </div>
          {open ? <ChevronUp className="h-4 w-4 text-muted-foreground" /> : <ChevronDown className="h-4 w-4 text-muted-foreground" />}
        </div>
      </button>

      {open && (
        <div className="border-t border-border/30">
          <div className="grid gap-3 bg-muted/10 px-4 py-3 lg:grid-cols-[1fr_2fr]">
            <div className="grid grid-cols-3 gap-2">
              <div className="rounded-lg bg-muted/25 p-2">
                <p className="text-[10px] text-muted-foreground">上涨占比</p>
                <p className="mt-0.5 font-mono text-xs font-bold">{(direction.breadth * 100).toFixed(0)}%</p>
              </div>
              <div className="rounded-lg bg-muted/25 p-2">
                <p className="text-[10px] text-muted-foreground">资金净额</p>
                <p className="mt-0.5 font-mono text-xs font-bold">{direction.fund_flow?.net ?? "—"}</p>
              </div>
              <div className="rounded-lg bg-muted/25 p-2">
                <p className="text-[10px] text-muted-foreground">关联消息</p>
                <p className="mt-0.5 font-mono text-xs font-bold">{direction.news.length} 条</p>
              </div>
            </div>
            <div className="min-w-0">
              <p className="mb-1.5 text-[10px] font-medium text-muted-foreground">当日方向消息</p>
              {direction.news.length ? (
                <div className="flex flex-wrap gap-1.5">
                  {direction.news.slice(0, 4).map((item, index) => (
                    <a
                      key={`${item.url}-${index}`}
                      href={item.url}
                      target="_blank"
                      rel="noreferrer"
                      title={item.title}
                      className="max-w-full truncate rounded-md bg-muted/35 px-2 py-1 text-[10px] text-muted-foreground hover:text-primary"
                    >
                      {item.scope === "market" ? "市场 · " : ""}
                      {item.title}
                    </a>
                  ))}
                </div>
              ) : <p className="text-xs text-muted-foreground/50">暂无关联资讯，方向分由市场数据构成。</p>}
            </div>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[940px]">
              <thead className="bg-muted/20 text-[10px] uppercase text-muted-foreground">
                <tr>
                  <th className="px-3 py-2 text-left font-medium">候选股票</th>
                  <th className="px-3 py-2 text-right font-medium">现价</th>
                  <th className="px-3 py-2 text-right font-medium">涨跌</th>
                  <th className="px-3 py-2 text-right font-medium">成交额</th>
                  <th className="px-3 py-2 text-right font-medium">PE</th>
                  <th className="px-3 py-2 text-right font-medium">候选分</th>
                  <th className="px-3 py-2 text-left font-medium">最新研报 / 正文状态</th>
                </tr>
              </thead>
              <tbody>
                {direction.stocks.map((candidate) => (
                  <CandidateRow key={candidate.code} candidate={candidate} selected={candidate.code === selectedCode} />
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </GlassCard>
  );
}

function ReportCard({ report }: { report: RecommendationReport }) {
  const [excerptOpen, setExcerptOpen] = useState(false);
  const analysis = report.analysis;
  return (
    <GlassCard className="!p-0 overflow-hidden">
      <div className="border-b border-border/30 p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <div className="flex items-start gap-2">
              <FileText className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
              <div>
                <h4 className="text-sm font-semibold leading-relaxed">{report.title}</h4>
                <p className="mt-1 text-[11px] text-muted-foreground">
                  {report.organization || "未知机构"} · {report.researcher || "分析师未披露"} · {report.publish_date || "日期未知"}
                </p>
              </div>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <span className="rounded-full bg-primary/15 px-2 py-1 text-[10px] font-medium text-primary">{report.rating || "未评级"}</span>
            {report.read_status === "ok" ? (
              <span className="inline-flex items-center gap-1 rounded-full bg-success/10 px-2 py-1 text-[10px] text-success">
                <CheckCircle2 className="h-3 w-3" /> 已读正文 {report.pages_read} 页
              </span>
            ) : (
              <span className="inline-flex items-center gap-1 rounded-full bg-warning/10 px-2 py-1 text-[10px] text-warning">
                <AlertCircle className="h-3 w-3" /> 正文未读
              </span>
            )}
            {report.pdf_url && (
              <a
                href={report.pdf_url}
                target="_blank"
                rel="noreferrer"
                title="打开原研报 PDF"
                className="rounded-lg border border-border p-1.5 text-muted-foreground hover:border-primary/40 hover:text-primary"
              >
                <ExternalLink className="h-3.5 w-3.5" />
              </a>
            )}
          </div>
        </div>
        <div className="mt-3 grid grid-cols-3 gap-2 text-center text-[11px]">
          {[
            ["本年预测 PE", report.predicted_pe?.this_year],
            ["本年预测 EPS", report.predicted_eps?.this_year],
            ["原文页数", report.pages || null],
          ].map(([label, value]) => (
            <div key={String(label)} className="rounded-lg bg-muted/25 p-2">
              <p className="text-muted-foreground">{label}</p>
              <p className="mt-0.5 font-mono font-bold">{value ?? "—"}</p>
            </div>
          ))}
        </div>
      </div>

      <div className="grid gap-4 p-4 lg:grid-cols-2">
        <div>
          <p className="mb-2 text-xs font-semibold text-foreground">研报核心摘要</p>
          <p className="text-sm leading-relaxed text-muted-foreground">
            {analysis?.summary || report.read_error || "暂无可用分析。"}
          </p>
        </div>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <p className="mb-2 text-xs font-semibold text-success">积极因素</p>
            <BulletList items={analysis?.positive_factors || []} tone="positive" />
          </div>
          <div>
            <p className="mb-2 text-xs font-semibold text-destructive">风险因素</p>
            <BulletList items={analysis?.risk_factors || []} tone="risk" />
          </div>
        </div>
      </div>
      {analysis?.forecast_assumptions?.length ? (
        <div className="border-t border-border/25 px-4 py-3">
          <p className="mb-1.5 text-[11px] font-medium text-muted-foreground">预测假设</p>
          <div className="flex flex-wrap gap-1.5">
            {analysis.forecast_assumptions.map((item, index) => (
              <span key={`${item}-${index}`} className="rounded-md bg-muted/30 px-2 py-1 text-[10px] text-muted-foreground">{item}</span>
            ))}
          </div>
        </div>
      ) : null}
      {report.excerpt && (
        <div className="border-t border-border/25">
          <button
            onClick={() => setExcerptOpen((value) => !value)}
            className="flex w-full items-center justify-between px-4 py-2.5 text-left text-[11px] text-muted-foreground hover:text-foreground"
          >
            <span>查看 PDF 正文摘录</span>
            {excerptOpen ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
          </button>
          {excerptOpen && <p className="max-h-52 overflow-auto bg-muted/10 px-4 py-3 text-xs leading-relaxed text-muted-foreground">{report.excerpt}</p>}
        </div>
      )}
    </GlassCard>
  );
}

function RecommendationView({ report }: { report: DailyRecommendationReport }) {
  const recommendation = report.recommendation;
  return (
    <>
      {report.warnings.length > 0 && (
        <div className="mb-4 rounded-xl border border-warning/30 bg-warning/5 p-3">
          <div className="flex items-center gap-2 text-sm font-medium text-warning">
            <AlertCircle className="h-4 w-4" /> 本次报告存在降级项
          </div>
          <ul className="mt-2 space-y-1 text-xs text-muted-foreground">
            {report.warnings.map((warning, index) => <li key={`${warning}-${index}`}>- {warning}</li>)}
          </ul>
        </div>
      )}

      <GlassCard glow className="relative mb-5 overflow-hidden">
        <div className="grid gap-5 lg:grid-cols-[1.2fr_.8fr]">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <span className="inline-flex items-center gap-1 rounded-full bg-primary/15 px-2.5 py-1 text-[11px] font-semibold text-primary">
                <Trophy className="h-3.5 w-3.5" /> 今晚唯一观察标的
              </span>
              <span className="rounded-full bg-muted/45 px-2.5 py-1 text-[11px] text-muted-foreground">{recommendation.horizon}</span>
              <span className={cn(
                "inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-[11px]",
                report.analysis_mode === "ai" ? "bg-success/10 text-success" : "bg-warning/10 text-warning",
              )}>
                <BrainCircuit className="h-3.5 w-3.5" />
                {report.analysis_mode === "ai" ? "AI 综合研判" : "量化降级结果"}
              </span>
            </div>
            <div className="mt-4 flex flex-wrap items-end gap-x-4 gap-y-1">
              <h2 className="text-3xl font-black tracking-tight text-glow">{recommendation.name}</h2>
              <span className="font-mono text-xl font-bold text-primary">{recommendation.code}</span>
              <span className="mb-1 rounded bg-muted/40 px-2 py-0.5 text-xs text-muted-foreground">{recommendation.direction}</span>
            </div>
            <p className="mt-4 max-w-3xl text-sm leading-7 text-foreground/90">{recommendation.thesis}</p>
            <div className="mt-4 grid gap-3 sm:grid-cols-3">
              {[
                ["市场判断", recommendation.market_analysis],
                ["方向逻辑", recommendation.direction_analysis],
                ["候选比较", recommendation.candidate_comparison],
              ].map(([label, text]) => (
                <div key={label} className="rounded-xl border border-border/40 bg-muted/15 p-3">
                  <p className="mb-1.5 text-[11px] font-semibold text-primary">{label}</p>
                  <p className="text-xs leading-relaxed text-muted-foreground">{text || "暂无"}</p>
                </div>
              ))}
            </div>
          </div>
          <div className="space-y-3">
            <div className="rounded-xl border border-border/40 bg-muted/15 p-4">
              <div className="flex items-center justify-between">
                <span className="text-xs text-muted-foreground">综合置信度</span>
                <span className="font-mono text-lg font-black text-primary">{recommendation.confidence}</span>
              </div>
              <p className="mt-1 text-[10px] leading-relaxed text-muted-foreground/70">候选池内的相对判断，不代表统计上涨概率。</p>
            </div>
            <EvidenceSummary report={report} />
          </div>
        </div>
      </GlassCard>

      <div className="mb-6 grid gap-4 lg:grid-cols-3">
        <GlassCard>
          <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold">
            <Sparkles className="h-4 w-4 text-primary" /> 可能催化
          </h3>
          <BulletList items={recommendation.catalysts} tone="positive" />
        </GlassCard>
        <GlassCard>
          <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold">
            <ShieldAlert className="h-4 w-4 text-destructive" /> 主要风险
          </h3>
          <BulletList items={recommendation.risks} tone="risk" />
        </GlassCard>
        <GlassCard>
          <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold">
            <Target className="h-4 w-4 text-warning" /> 失效条件
          </h3>
          <BulletList items={recommendation.invalidation_conditions} tone="risk" />
        </GlassCard>
      </div>

      <div className="mb-3 flex flex-wrap items-end justify-between gap-2">
        <div>
          <h2 className="text-lg font-bold">五个方向 · 每方向五只股票</h2>
          <p className="mt-1 text-xs text-muted-foreground">按方向强度、市场广度、资金、消息、个股流动性与研报覆盖构建；最终标的必须来自这 25 只。</p>
        </div>
        <span className="font-mono text-[11px] text-muted-foreground">生成于 {report.generated_at}</span>
      </div>
      <div className="mb-7 space-y-3">
        {report.directions.map((direction) => (
          <DirectionCard key={direction.board_code} direction={direction} selectedCode={recommendation.code} />
        ))}
      </div>

      <div className="mb-3">
        <h2 className="text-lg font-bold">最终标的研报与交叉分析</h2>
        <p className="mt-1 text-xs text-muted-foreground">最多读取三份最新研报 PDF 正文，保留原文链接、正文摘录和逐篇分析。</p>
      </div>
      <GlassCard className="mb-4">
        <div className="grid gap-4 lg:grid-cols-3">
          {[
            ["综合结论", recommendation.report_analysis.overall],
            ["机构共识", recommendation.report_analysis.consensus],
            ["机构分歧", recommendation.report_analysis.differences],
          ].map(([label, text]) => (
            <div key={label}>
              <p className="mb-1.5 text-xs font-semibold text-primary">{label}</p>
              <p className="text-sm leading-relaxed text-muted-foreground">{text || "暂无明确结论。"}</p>
            </div>
          ))}
        </div>
        <div className="mt-4 grid gap-4 border-t border-border/30 pt-4 lg:grid-cols-2">
          <div>
            <p className="mb-2 text-xs font-semibold">关键假设</p>
            <BulletList items={recommendation.report_analysis.key_assumptions} />
          </div>
          <div>
            <p className="mb-2 text-xs font-semibold text-destructive">研报层风险</p>
            <BulletList items={recommendation.report_analysis.report_risks} tone="risk" />
          </div>
        </div>
      </GlassCard>
      <div className="space-y-3">
        {recommendation.reports.map((item) => <ReportCard key={item.info_code} report={item} />)}
      </div>

      <div className="mt-5 flex items-start gap-2 rounded-xl border border-destructive/30 bg-destructive/5 p-4 text-xs leading-relaxed text-muted-foreground">
        <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
        <span>{report.disclaimer}</span>
      </div>
    </>
  );
}

export function DailyRecommendation() {
  const [latest, setLatest] = useState<DailyRecommendationReport | null>(null);
  const [displayed, setDisplayed] = useState<DailyRecommendationReport | null>(null);
  const [history, setHistory] = useState<RecommendationHistory[]>([]);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.dailyRecommendation();
      setLatest(data.latest);
      setDisplayed(data.latest);
      setHistory(data.history);
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : "加载每日荐股失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const generate = async () => {
    setGenerating(true);
    setError(null);
    toast.info("正在抓取消息、构建 25 只候选并读取研报 PDF，可能需要数分钟");
    try {
      const report = await api.generateRecommendation(true);
      setLatest(report);
      setDisplayed(report);
      const dashboard = await api.dailyRecommendation();
      setHistory(dashboard.history);
      toast.success(`今日报告已生成：${report.recommendation.name}（${report.recommendation.code}）`);
    } catch (reason) {
      const message = reason instanceof ApiError ? reason.message : "生成失败";
      setError(message);
      toast.error(message);
    } finally {
      setGenerating(false);
    }
  };

  const selectHistory = async (date: string) => {
    if (displayed?.date === date) return;
    setLoading(true);
    setError(null);
    try {
      setDisplayed(await api.recommendationByDate(date));
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : "历史报告加载失败");
    } finally {
      setLoading(false);
    }
  };

  const subtitle = useMemo(() => {
    if (!displayed) return "每天北京时间固定时刻，从五个方向 × 每方向五只候选中，结合消息、市场和研报给出唯一观察标的";
    return `${displayed.date} · ${displayed.date === latest?.date ? "最新报告" : "历史报告"} · ${displayed.analysis_mode === "ai" ? "AI 综合研判" : "量化降级"}`;
  }, [displayed, latest]);

  return (
    <div>
      <PageHeader
        title="每日荐股"
        subtitle={subtitle}
        actions={
          <button
            onClick={generate}
            disabled={generating}
            className="inline-flex items-center gap-1.5 rounded-lg bg-primary/15 px-4 py-2 text-sm font-medium text-primary shadow-glow hover:bg-primary/25 disabled:opacity-50"
          >
            {generating ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
            {generating ? "生成中…" : latest ? "重新生成今晚报告" : "立即生成首份报告"}
          </button>
        }
      />

      <div className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-primary/25 bg-primary/5 p-3">
        <div className="flex items-start gap-2 text-xs text-muted-foreground">
          <Clock3 className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
          <span>默认每天北京时间 20:00 自动执行。可在「定时任务」页修改时刻；后端需持续运行，且建议先在「接入 AI」配置模型。</span>
        </div>
        {history.length > 0 && (
          <label className="flex items-center gap-2 text-xs text-muted-foreground">
            <History className="h-3.5 w-3.5" />
            历史报告
            <select
              value={displayed?.date || ""}
              onChange={(event) => selectHistory(event.target.value)}
              className="rounded-lg border border-border bg-black/20 px-2.5 py-1.5 text-xs outline-none focus:border-primary/50"
            >
              {history.map((item) => (
                <option key={item.date} value={item.date}>
                  {item.date} · {item.name} {item.code}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>

      {error && (
        <div className="mb-4 flex items-center gap-2 rounded-xl border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">
          <AlertCircle className="h-4 w-4 shrink-0" /> {error}
        </div>
      )}

      {loading ? (
        <GlassCard>
          <div className="flex flex-col items-center gap-3 py-16 text-center text-sm text-muted-foreground">
            <Loader2 className="h-8 w-8 animate-spin text-primary" />
            正在读取荐股研究报告…
          </div>
        </GlassCard>
      ) : displayed ? (
        <RecommendationView report={displayed} />
      ) : (
        <GlassCard glow>
          <div className="flex flex-col items-center gap-3 py-16 text-center">
            <Sparkles className="h-10 w-10 text-primary" />
            <div>
              <h2 className="text-lg font-bold">还没有荐股报告</h2>
              <p className="mt-2 max-w-xl text-sm leading-relaxed text-muted-foreground">
                点击“立即生成首份报告”，系统会抓取当天消息和市场数据，筛选五个方向、每方向五只股票，
                读取候选研报 PDF，再综合生成唯一观察标的与研报分析。
              </p>
            </div>
            <button
              onClick={generate}
              disabled={generating}
              className="mt-2 inline-flex items-center gap-2 rounded-lg bg-primary px-5 py-2.5 text-sm font-semibold text-primary-foreground disabled:opacity-50"
            >
              {generating ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
              {generating ? "正在生成…" : "立即生成"}
            </button>
          </div>
        </GlassCard>
      )}
    </div>
  );
}
