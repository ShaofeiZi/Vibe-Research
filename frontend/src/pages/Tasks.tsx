import { useState, useEffect, useCallback } from "react";
import { Clock, Play, Loader2, CheckCircle2, AlertCircle, TimerReset } from "lucide-react";
import { PageHeader } from "@/components/ui/PageHeader";
import { GlassCard } from "@/components/ui/GlassCard";
import { Disclaimer } from "@/components/ui/Disclaimer";
import { api, ApiError, type TaskState } from "@/lib/api";
import { cn } from "@/lib/utils";

// 间隔预设（分钟）。资讯雷达抓取约 20-40s，过短会把源打爆，最低限 1 分钟（后端已兜底）。
const INTERVAL_OPTS = [
  { label: "10 分钟", sec: 600 },
  { label: "30 分钟", sec: 1800 },
  { label: "1 小时", sec: 3600 },
  { label: "2 小时", sec: 7200 },
  { label: "6 小时", sec: 21600 },
];

const STATUS_MS = 15 * 1000; // 每 15s 轮询一次任务状态（只读，不触发抓取）

function StatusBadge({ s }: { s: TaskState["last_status"] }) {
  if (s === "running")
    return <span className="inline-flex items-center gap-1 text-xs text-primary"><Loader2 className="h-3 w-3 animate-spin" /> 运行中</span>;
  if (s === "ok")
    return <span className="inline-flex items-center gap-1 text-xs text-success"><CheckCircle2 className="h-3.5 w-3.5" /> 成功</span>;
  if (s === "error")
    return <span className="inline-flex items-center gap-1 text-xs text-destructive"><AlertCircle className="h-3.5 w-3.5" /> 失败</span>;
  return <span className="text-xs text-muted-foreground/60">未执行</span>;
}

function TaskCard({ task, manualRunning, onToggle, onInterval, onDailyTime, onRun }: {
  task: TaskState;
  manualRunning: boolean;
  onToggle: (key: string, enabled: boolean) => void;
  onInterval: (key: string, sec: number) => void;
  onDailyTime: (key: string, time: string) => void;
  onRun: (key: string) => void;
}) {
  const running = task.last_status === "running" || manualRunning;
  return (
    <GlassCard glow={task.enabled} className="p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <Clock className={cn("h-4 w-4", task.enabled ? "text-primary" : "text-muted-foreground/60")} />
            <h3 className="font-semibold">{task.name}</h3>
            <code className="rounded bg-muted/40 px-1.5 py-0.5 text-[10px] text-muted-foreground">{task.key}</code>
            <span className="rounded bg-primary/10 px-1.5 py-0.5 text-[10px] text-primary">
              {task.schedule_type === "daily" ? `每日 ${task.daily_time || "—"}（北京时间）` : "固定间隔"}
            </span>
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
            <span className="inline-flex items-center gap-1.5">
              <StatusBadge s={task.last_status} />
            </span>
            <span>上次执行：<b className="text-foreground/80">{task.last_run || "—"}</b></span>
            <span className="inline-flex items-center gap-1">
              <TimerReset className="h-3 w-3" /> 下次预估：<b className="text-foreground/80">{task.next_run_est || "—"}</b>
            </span>
          </div>
          {task.last_error && (
            <p className="mt-2 max-w-xl truncate text-xs text-destructive" title={task.last_error}>
              上次错误：{task.last_error}
            </p>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {task.schedule_type === "daily" ? (
            <label className="flex items-center gap-1.5 rounded-lg border border-border bg-black/20 px-2.5 py-1 text-xs text-muted-foreground">
              北京时间
              <input
                type="time"
                value={task.daily_time || "20:00"}
                onChange={(e) => onDailyTime(task.key, e.target.value)}
                disabled={running}
                className="bg-transparent font-mono text-foreground outline-none disabled:opacity-50"
              />
            </label>
          ) : (
            <select
              value={INTERVAL_OPTS.find((o) => o.sec === task.interval_sec)?.sec ?? -1}
              onChange={(e) => onInterval(task.key, Number(e.target.value))}
              disabled={running}
              className="rounded-lg border border-border bg-black/20 px-2.5 py-1.5 text-xs outline-none focus:border-primary/50 disabled:opacity-50"
            >
              {INTERVAL_OPTS.map((o) => (
                <option key={o.sec} value={o.sec}>{o.label}</option>
              ))}
              {!INTERVAL_OPTS.some((o) => o.sec === task.interval_sec) && (
                <option value={-1} disabled>{Math.round(task.interval_sec / 60)} 分钟</option>
              )}
            </select>
          )}

          {/* 手动执行 */}
          <button
            onClick={() => onRun(task.key)}
            disabled={running}
            className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm text-muted-foreground hover:text-foreground disabled:opacity-50"
          >
            {running ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
            {running ? "执行中…" : "立即执行"}
          </button>

          {/* 开关 */}
          <button
            onClick={() => onToggle(task.key, !task.enabled)}
            role="switch"
            aria-checked={task.enabled}
            className={cn(
              "relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors",
              task.enabled ? "bg-primary" : "bg-muted",
            )}
          >
            <span className={cn(
              "inline-block h-5 w-5 transform rounded-full bg-white shadow transition-transform",
              task.enabled ? "translate-x-5" : "translate-x-0.5",
            )} />
          </button>
        </div>
      </div>
    </GlassCard>
  );
}

export function Tasks() {
  const [tasks, setTasks] = useState<TaskState[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [running, setRunning] = useState<string | null>(null); // 手动执行中的 task key

  const load = useCallback(async () => {
    try {
      setTasks(await api.tasks());
      setErr(null);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "加载失败");
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(() => load(), STATUS_MS);
    return () => clearInterval(t);
  }, [load]);

  const onToggle = async (key: string, enabled: boolean) => {
    // 乐观更新，失败回滚
    const prev = tasks;
    setTasks((ts) => ts.map((t) => (t.key === key ? { ...t, enabled } : t)));
    try {
      await api.updateTask(key, { enabled });
    } catch (e) {
      setTasks(prev);
      setErr(e instanceof ApiError ? e.message : "开关失败");
    }
  };

  const onInterval = async (key: string, sec: number) => {
    const prev = tasks;
    setTasks((ts) => ts.map((t) => (t.key === key ? { ...t, interval_sec: sec } : t)));
    try {
      await api.updateTask(key, { interval_sec: sec });
    } catch (e) {
      setTasks(prev);
      setErr(e instanceof ApiError ? e.message : "设置间隔失败");
    }
  };

  const onDailyTime = async (key: string, dailyTime: string) => {
    const prev = tasks;
    setTasks((items) => items.map((task) => (
      task.key === key ? { ...task, daily_time: dailyTime } : task
    )));
    try {
      const updated = await api.updateTask(key, { daily_time: dailyTime });
      setTasks((items) => items.map((task) => (task.key === key ? updated : task)));
    } catch (e) {
      setTasks(prev);
      setErr(e instanceof ApiError ? e.message : "设置执行时刻失败");
    }
  };

  const onRun = async (key: string) => {
    setRunning(key);
    // 立即把状态置 running，UI 即时反馈（实际状态靠轮询修正）
    setTasks((ts) => ts.map((t) => (t.key === key ? { ...t, last_status: "running" } : t)));
    try {
      const updated = await api.runTask(key);
      setTasks((ts) => ts.map((t) => (t.key === key ? updated : t)));
      setErr(null);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "执行失败");
      await load(); // 刷新拿真实状态（失败时后端已记录 last_status=error）
    } finally {
      setRunning(null);
    }
  };

  return (
    <div>
      <PageHeader title="定时任务" subtitle="后台守护线程定时刷新数据，关掉页面也会继续执行" />

      <div className="mb-4 flex items-start gap-2 rounded-lg border border-primary/25 bg-primary/5 p-3 text-xs text-muted-foreground">
        <Clock className="mt-0.5 h-4 w-4 shrink-0 text-primary/70" />
        <span>
          开启后，后端会按设定间隔自动抓取并更新缓存；你随时打开对应页面看到的都是最新数据。
          间隔任务最低 1 分钟（防过频把数据源打爆）；每日任务按北京时间固定时刻执行。状态每 15 秒自动刷新一次。
        </span>
      </div>

      {err && (
        <div className="mb-4 flex items-center gap-2 rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">
          <AlertCircle className="h-4 w-4 shrink-0" /> {err}
        </div>
      )}

      {tasks.length === 0 && !err ? (
        <p className="py-8 text-center text-sm text-muted-foreground/60">暂无定时任务。</p>
      ) : (
        <div className="space-y-3">
          {tasks.map((t) => (
            <TaskCard
              key={t.key}
              task={t}
              manualRunning={running === t.key}
              onToggle={onToggle}
              onInterval={onInterval}
              onDailyTime={onDailyTime}
              onRun={onRun}
            />
          ))}
        </div>
      )}

      <p className="mt-3 text-[11px] text-muted-foreground/60">
        任务配置存在本地（~/.vibe-research/tasks.json），重启后端后配置与调度自动恢复。
      </p>
      <Disclaimer />
    </div>
  );
}
