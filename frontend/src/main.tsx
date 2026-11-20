import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";
import { createRoot } from "react-dom/client";
import {
  ArrowDown,
  ArrowDownToLine,
  ArrowRight,
  Check,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Clock3,
  CloudUpload,
  FileCheck2,
  FileImage,
  Files,
  Film,
  FolderClock,
  LayoutDashboard,
  LoaderCircle,
  LogOut,
  Plus,
  ShieldCheck,
  Sparkles,
  X,
  Zap,
} from "lucide-react";
import {
  api,
  ApiError,
  bytes,
  refreshCsrf,
  type Account,
  type Job,
} from "./api";
import "./style.css";

const message = (error: unknown) =>
  error instanceof Error ? error.message : "Please try again.";
const active = (job: Job) =>
  job.status === "QUEUED" || job.status === "PROCESSING";
const date = (value: string) =>
  new Date(value).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
const presets = [
  {
    id: "smaller",
    label: "Smaller file",
    note: "Maximum compression",
    icon: ArrowDown,
  },
  {
    id: "balanced",
    label: "Balanced",
    note: "Size and clarity",
    icon: Sparkles,
  },
  {
    id: "higher_quality",
    label: "Higher quality",
    note: "Preserve more detail",
    icon: FileImage,
  },
];

function Brand() {
  return (
    <span className="brand">
      <span className="brand-icon">
        <Zap size={21} fill="currentColor" />
      </span>
      fastzip<span className="brand-dot">.</span>
    </span>
  );
}

function CompressionMotion() {
  return (
    <div className="compression-motion" aria-hidden="true">
      <span className="motion-file motion-input">
        <Files />
      </span>
      <span className="motion-lane">
        <span className="motion-bolt">
          <Zap fill="currentColor" />
        </span>
      </span>
      <span className="motion-file motion-output">
        <FileCheck2 />
      </span>
    </div>
  );
}

function Notice({ text, clear }: { text: string; clear: () => void }) {
  return (
    <div className="notice" role="alert">
      <span>{text}</span>
      <button aria-label="Dismiss message" onClick={clear}>
        <X size={17} />
      </button>
    </div>
  );
}

function Auth({ done }: { done: (account: Account) => void }) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setBusy(true);
    const form = new FormData(event.currentTarget);
    try {
      await refreshCsrf();
      done(
        await api<Account>("/auth/" + mode, {
          method: "POST",
          body: JSON.stringify(Object.fromEntries(form)),
        }),
      );
    } catch (error) {
      setError(message(error));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="auth-shell">
      <div className="auth-story">
        <Brand />
        <div className="story-copy">
          <h1>
            Compress images
            <br />
            and videos.
          </h1>
          <p>Reduce the size of a JPEG, PNG, or MP4 before sharing.</p>
          <CompressionMotion />
          <div className="story-footer">
            <ShieldCheck size={18} /> Private files. Automatically deleted after
            24 hours.
          </div>
        </div>
      </div>
      <main className="auth-main">
        <div className="auth-card">
          <h2>{mode === "login" ? "Sign in" : "Create an account"}</h2>
          <p className="muted">
            {mode === "login"
              ? "Access your compression jobs and downloads."
              : "Use your email to keep your compression history."}
          </p>
          <div className="auth-tabs">
            <button
              className={mode === "login" ? "selected" : ""}
              aria-pressed={mode === "login"}
              disabled={busy}
              onClick={() => {
                setMode("login");
                setError("");
              }}
            >
              Sign in
            </button>
            <button
              className={mode === "register" ? "selected" : ""}
              aria-pressed={mode === "register"}
              disabled={busy}
              onClick={() => {
                setMode("register");
                setError("");
              }}
            >
              Create account
            </button>
          </div>
          {error && <Notice text={error} clear={() => setError("")} />}
          <form onSubmit={submit}>
            <label htmlFor="email">Email address</label>
            <input
              id="email"
              name="email"
              type="email"
              autoComplete="email"
              placeholder="you@example.com"
              required
              maxLength={254}
            />
            <label htmlFor="password">Password</label>
            <input
              id="password"
              name="password"
              type="password"
              autoComplete={
                mode === "login" ? "current-password" : "new-password"
              }
              minLength={12}
              maxLength={128}
              required
              aria-describedby={
                mode === "register" ? "password-note" : undefined
              }
            />
            {mode === "register" && (
              <span id="password-note" className="field-note">
                Use 12–128 characters.
              </span>
            )}
            <button className="primary auth-submit" disabled={busy}>
              {busy ? (
                <>
                  <LoaderCircle className="spin" size={18} /> Please wait…
                </>
              ) : (
                <>
                  {mode === "login" ? "Sign in" : "Create your account"}
                  <ArrowRight size={18} />
                </>
              )}
            </button>
          </form>
        </div>
      </main>
    </div>
  );
}

function JobRow({
  job,
  download,
}: {
  job: Job;
  download: (job: Job, original?: boolean) => Promise<void>;
}) {
  const Icon = job.media_type === "image" ? FileImage : Film;
  const [downloading, setDownloading] = useState<"original" | "result" | null>(
    null,
  );
  async function save(kind: "original" | "result") {
    setDownloading(kind);
    try {
      await download(job, kind === "original");
    } finally {
      setDownloading(null);
    }
  }
  return (
    <article className="job-row">
      <div className={"file-icon " + job.media_type}>
        <Icon size={23} />
      </div>
      <div className="job-name">
        <strong title={job.filename}>{job.filename}</strong>
        <span>
          {bytes(job.original_size)}
          <span className="separator">·</span>
          {date(job.created_at)}
          {job.processing_seconds !== null && (
            <>
              <span className="separator">·</span>
              <span className="job-duration">
                {job.processing_seconds < 1
                  ? `${Math.round(job.processing_seconds * 1000)} ms`
                  : `${job.processing_seconds.toFixed(1)} s`}{" "}
                processing
              </span>
            </>
          )}
          {job.status === "SUCCEEDED" && (
            <span className="expiry-note">
              Available until {date(job.expires_at)}
            </span>
          )}
        </span>
      </div>
      <div className="job-result">
        {job.status === "SUCCEEDED" ? (
          <>
            <strong>
              {bytes(
                Math.min(
                  job.output_size ?? job.original_size,
                  job.original_size,
                ),
              )}
            </strong>
            <span>
              {job.original_is_smaller
                ? "Original retained"
                : `${job.savings_percent ?? 0}% smaller`}
            </span>
          </>
        ) : (
          <>
            <span className={"status " + job.status.toLowerCase()}>
              {active(job) && <LoaderCircle size={12} className="spin" />}
              {job.status === "PROCESSING"
                ? "Compressing"
                : job.status === "QUEUED"
                  ? "In the queue"
                  : job.status === "EXPIRED"
                    ? "Expired"
                    : "Could not compress"}
            </span>
            {job.error && <span className="job-error">{job.error}</span>}
          </>
        )}
      </div>
      <div className="job-actions">
        {job.status === "SUCCEEDED" && (
          <>
            <button
              className="download"
              onClick={() => void save("result")}
              disabled={downloading !== null}
              aria-label={"Download " + job.filename}
            >
              {downloading === "result" ? (
                <LoaderCircle className="spin" size={18} />
              ) : (
                <ArrowDownToLine size={18} />
              )}
              <span>
                {job.original_is_smaller ? "Download original" : "Download"}
              </span>
            </button>
            {!job.original_is_smaller && (
              <button
                className="original"
                onClick={() => void save("original")}
                disabled={downloading !== null}
                aria-label={"Download original " + job.filename}
              >
                Original
              </button>
            )}
          </>
        )}
      </div>
    </article>
  );
}

type Overview = {
  queue_depth: number;
  completed_24h: number;
  throughput_per_hour: number;
  failure_rate_24h: number;
  average_processing_seconds: number | null;
  jobs_by_status: Record<string, number>;
  workers: {
    worker_id: string;
    node: string;
    online: boolean;
    seen_at: string;
    job_id: string | null;
  }[];
  last_backup: {
    status: string;
    created_at: string;
    size_bytes: number | null;
    message: string | null;
  } | null;
};
type ErrorEvent = {
  id: number;
  service: string;
  node: string;
  job_id: string | null;
  message: string;
  created_at: string;
};
type Metrics = {
  configured: boolean;
  droplets: {
    id: string;
    available: boolean;
    cpu_percent: number | null;
    memory_percent: number | null;
    filesystems: { mount: string; used_percent: number }[];
  }[];
};

function Admin() {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [events, setEvents] = useState<ErrorEvent[]>([]);
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [metricsError, setMetricsError] = useState(false);
  const [service, setService] = useState("");
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState("");
  useEffect(() => {
    let mounted = true;
    const load = async () => {
      const query = new URLSearchParams({ service, offset: String(offset) });
      if (since) query.set("since", new Date(since).toISOString());
      if (until) query.set("until", new Date(until).toISOString());
      try {
        const [summary, errors] = await Promise.all([
          api<Overview>("/admin/overview"),
          api<{ items: ErrorEvent[] }>("/admin/errors?" + query),
        ]);
        if (mounted) {
          setOverview(summary);
          setEvents(errors.items);
          setError("");
        }
      } catch (error) {
        if (mounted) setError(message(error));
      }
    };
    void load();
    const timer = setInterval(load, 10000);
    return () => {
      mounted = false;
      clearInterval(timer);
    };
  }, [service, since, until, offset]);
  useEffect(() => {
    let mounted = true;
    const load = () =>
      api<Metrics>("/admin/metrics")
        .then((data) => {
          if (mounted) {
            setMetrics(data);
            setMetricsError(false);
          }
        })
        .catch(() => {
          if (mounted) setMetricsError(true);
        });
    void load();
    const timer = setInterval(load, 60000);
    return () => {
      mounted = false;
      clearInterval(timer);
    };
  }, []);
  return (
    <>
      <div className="page-heading">
        <div>
          <h1>Administration</h1>
          <p className="muted">
            Service health and job metadata. User media stays private.
          </p>
        </div>
        <span className="live-dot">Refreshes every 10s</span>
      </div>
      {error && <Notice text={error} clear={() => setError("")} />}
      {!overview ? (
        <p className="muted" role="status">
          {error ? "Service health is unavailable." : "Loading service health…"}
        </p>
      ) : (
        <>
          <div className="stats-grid admin-stats">
            {[
              ["Queue depth", overview.queue_depth],
              ["Completed · 24h", overview.completed_24h],
              ["Jobs / hour · 24h", overview.throughput_per_hour],
              [
                "Failure rate · 24h",
                `${(overview.failure_rate_24h * 100).toFixed(1)}%`,
              ],
            ].map(([label, value]) => (
              <div className="stat" key={label}>
                <span>{label}</span>
                <strong>{value}</strong>
              </div>
            ))}
          </div>
          <div className="status-breakdown" aria-label="Jobs by status">
            {Object.entries(overview.jobs_by_status).map(([status, count]) => (
              <span className={"status " + status.toLowerCase()} key={status}>
                {status}: {count}
              </span>
            ))}
          </div>
          <div className="admin-columns">
            <section className="panel">
              <h2>
                Workers{" "}
                <span className="count">
                  {overview.workers.filter((w) => w.online).length} online
                </span>
              </h2>
              {overview.workers.length ? (
                overview.workers.map((worker) => (
                  <div className="worker" key={worker.worker_id}>
                    <span
                      className={"dot " + (worker.online ? "online" : "")}
                    />
                    <div>
                      <strong>{worker.node}</strong>
                      <span>
                        {worker.job_id ? "Processing a job" : "Ready for work"}{" "}
                        · {date(worker.seen_at)}
                      </span>
                    </div>
                    <span>{worker.online ? "Online" : "Offline"}</span>
                  </div>
                ))
              ) : (
                <p className="muted">No worker heartbeats yet.</p>
              )}
            </section>
            <section className="panel">
              <h2>Latest backup</h2>
              {overview.last_backup ? (
                <>
                  <span
                    className={
                      "status " + overview.last_backup.status.toLowerCase()
                    }
                  >
                    {overview.last_backup.status}
                  </span>
                  <p>{date(overview.last_backup.created_at)}</p>
                  <p className="muted">
                    {overview.last_backup.size_bytes
                      ? bytes(overview.last_backup.size_bytes) +
                        " · encrypted with age"
                      : overview.last_backup.message}
                  </p>
                </>
              ) : (
                <p className="muted">
                  No backup recorded. Configure an age recipient to enable
                  encrypted backups.
                </p>
              )}
            </section>
          </div>
          <section className="panel">
            <h2>Infrastructure</h2>
            {metricsError ? (
              <p className="muted">
                Couldn’t refresh infrastructure metrics. Retrying automatically.
              </p>
            ) : !metrics ? (
              <p className="muted" role="status">
                Loading infrastructure metrics…
              </p>
            ) : !metrics.configured ? (
              <p className="muted">
                Connect DigitalOcean Monitoring to see droplet CPU, memory, and
                disk usage.
              </p>
            ) : (
              <div className="metrics-grid">
                {metrics.droplets.map((d) => (
                  <div className="metric" key={d.id}>
                    <strong>Droplet {d.id}</strong>
                    {d.available ? (
                      <>
                        <p>
                          CPU {d.cpu_percent?.toFixed(1) ?? "—"}% · Memory{" "}
                          {d.memory_percent?.toFixed(1) ?? "—"}%
                        </p>
                        {d.filesystems.map((f) => (
                          <small key={f.mount}>
                            {f.mount}: {f.used_percent}% used
                          </small>
                        ))}
                      </>
                    ) : (
                      <p className="muted">Metrics unavailable</p>
                    )}
                  </div>
                ))}
              </div>
            )}
          </section>
          <section className="panel errors-panel">
            <div className="section-heading">
              <h2>Error log</h2>
              <span className="muted">Structured events across services</span>
            </div>
            <div className="error-filters">
              <label>
                Service
                <select
                  value={service}
                  onChange={(e) => {
                    setService(e.target.value);
                    setOffset(0);
                  }}
                >
                  <option value="">All services</option>
                  {["api", "worker", "cleanup", "backup", "monitoring"].map(
                    (s) => (
                      <option key={s}>{s}</option>
                    ),
                  )}
                </select>
              </label>
              <label>
                From
                <input
                  type="datetime-local"
                  value={since}
                  onChange={(e) => {
                    setSince(e.target.value);
                    setOffset(0);
                  }}
                />
              </label>
              <label>
                Until
                <input
                  type="datetime-local"
                  value={until}
                  onChange={(e) => {
                    setUntil(e.target.value);
                    setOffset(0);
                  }}
                />
              </label>
            </div>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Time</th>
                    <th>Service / node</th>
                    <th>Message</th>
                    <th>Job</th>
                  </tr>
                </thead>
                <tbody>
                  {events.map((event) => (
                    <tr key={event.id}>
                      <td>{date(event.created_at)}</td>
                      <td>
                        {event.service}
                        <small>{event.node}</small>
                      </td>
                      <td>{event.message}</td>
                      <td>
                        <code>{event.job_id?.slice(0, 8) ?? "—"}</code>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {events.length === 0 && (
              <p className="empty-small">No errors in this view.</p>
            )}
            <div className="pagination">
              <button
                disabled={!offset}
                onClick={() => setOffset(Math.max(0, offset - 50))}
              >
                <ChevronLeft size={16} />
                Previous
              </button>
              <button
                disabled={events.length < 50}
                onClick={() => setOffset(offset + 50)}
              >
                Next
                <ChevronRight size={16} />
              </button>
            </div>
          </section>
        </>
      )}
    </>
  );
}

function Workspace({
  account,
  signOut,
}: {
  account: Account;
  signOut: () => void;
}) {
  const [view, setView] = useState<"compress" | "history" | "admin">(
    "compress",
  );
  const [page, setPage] = useState<{
    items: Job[];
    total: number;
    active_count: number;
    active_limit: number;
    offset: number;
  } | null>(null);
  const [offset, setOffset] = useState(0);
  const [file, setFile] = useState<File | null>(null);
  const [preset, setPreset] = useState("balanced");
  const [drag, setDrag] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [jobsError, setJobsError] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);
  const [announcement, setAnnouncement] = useState("");
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (view === "admin") return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function load() {
      let delay = 3000;
      try {
        const data = await api<Omit<NonNullable<typeof page>, "offset">>(
          "/jobs?limit=30&offset=" + offset,
          { signal: controller.signal },
        );
        if (controller.signal.aborted) return;
        setPage({ ...data, offset });
        setJobsError("");
        if (data.active_count === 0) delay = 10000;
      } catch (error) {
        if (controller.signal.aborted) return;
        if (error instanceof ApiError && error.status === 401) {
          signOut();
          return;
        }
        setJobsError(message(error));
      }
      timer = setTimeout(() => void load(), delay);
    }
    void load();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [offset, refreshKey, signOut, view]);
  function clearFile() {
    setFile(null);
    if (input.current) input.current.value = "";
  }
  function choose(file: File | undefined) {
    if (!file) return;
    setAnnouncement("");
    const image = /\.(jpg|jpeg|png)$/i.test(file.name);
    if (!/\.(jpg|jpeg|png|mp4)$/i.test(file.name)) {
      clearFile();
      setError("Choose a JPEG, PNG, or MP4 file.");
      return;
    }
    if (file.size > (image ? 20 : 100) * 1024 * 1024 || file.size === 0) {
      clearFile();
      setError(
        image
          ? "Choose a nonempty image up to 20 MiB."
          : "Choose a nonempty video up to 100 MiB.",
      );
      return;
    }
    setFile(file);
    setError("");
  }
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!file || busy || atLimit) return;
    setBusy(true);
    setError("");
    setAnnouncement("");
    const form = new FormData();
    form.set("file", file);
    form.set("preset", preset);
    try {
      const job = await api<Job>("/jobs", { method: "POST", body: form });
      clearFile();
      setAnnouncement(
        `${job.filename} uploaded. Check its status in your jobs.`,
      );
      setRefreshKey((key) => key + 1);
    } catch (error) {
      setError(message(error));
      if (error instanceof ApiError && error.status === 409)
        setRefreshKey((key) => key + 1);
    } finally {
      setBusy(false);
    }
  }
  async function download(job: Job, original = false) {
    setError("");
    try {
      const result = await api<{ url: string }>(
        "/jobs/" +
          job.id +
          "/download?kind=" +
          (original ? "original" : "result"),
      );
      const anchor = document.createElement("a");
      anchor.href = result.url;
      anchor.rel = "noreferrer";
      anchor.click();
    } catch (error) {
      setError(message(error));
      if (error instanceof ApiError && error.status === 410)
        setRefreshKey((key) => key + 1);
    }
  }
  const jobs = page?.offset === offset ? page.items : [];
  const total = page?.total ?? 0;
  const currentActive = page?.active_count;
  const activeLimit = page?.active_limit;
  const atLimit =
    currentActive !== undefined &&
    activeLimit !== undefined &&
    currentActive >= activeLimit;
  const shown = view === "compress" ? jobs.slice(0, 5) : jobs;
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <Brand />
        <nav aria-label="Main navigation">
          <button
            className={view === "compress" ? "selected" : ""}
            aria-current={view === "compress" ? "page" : undefined}
            onClick={() => {
              setView("compress");
              setOffset(0);
            }}
          >
            <Plus size={19} />
            New compression
            <ChevronRight className="nav-arrow" size={15} />
          </button>
          <button
            className={view === "history" ? "selected" : ""}
            aria-current={view === "history" ? "page" : undefined}
            onClick={() => setView("history")}
          >
            <FolderClock size={19} />
            File history
          </button>
          {account.role === "admin" && (
            <button
              className={view === "admin" ? "selected" : ""}
              aria-current={view === "admin" ? "page" : undefined}
              onClick={() => setView("admin")}
            >
              <LayoutDashboard size={19} />
              Administration
            </button>
          )}
        </nav>
        <div className="privacy-card">
          <ShieldCheck size={24} />
          <strong>Private uploads</strong>
          <p>Files are deleted after 24 hours. Your job history is kept.</p>
        </div>
        <div className="account">
          <span className="avatar">{account.email[0].toUpperCase()}</span>
          <div>
            <strong title={account.email}>{account.email}</strong>
            <span>
              {account.role === "admin" ? "Administrator" : "Personal account"}
            </span>
          </div>
          <button
            title="Sign out"
            aria-label="Sign out"
            onClick={async () => {
              try {
                await api("/auth/logout", { method: "POST" });
                signOut();
              } catch (error) {
                setError(message(error));
              }
            }}
          >
            <LogOut size={17} />
          </button>
        </div>
      </aside>
      <div className="main-shell">
        <main className="content">
          {error && <Notice text={error} clear={() => setError("")} />}
          {view === "admin" ? (
            <Admin />
          ) : (
            <>
              <div
                className={
                  view === "compress"
                    ? "page-heading compress-heading"
                    : "page-heading"
                }
              >
                <div>
                  <h1>
                    {view === "compress" ? "Compress a file" : "File history"}
                  </h1>
                  <p className="muted">
                    {view === "compress"
                      ? "Upload an image or video and choose how much to compress it."
                      : "Track your jobs and download results within 24 hours of upload."}
                  </p>
                </div>
                {view === "compress" && <CompressionMotion />}
              </div>
              {view === "compress" && (
                <div className="workspace-grid">
                  <form className="upload-panel" onSubmit={submit}>
                    <div className="step-label">
                      <h2>Choose a file</h2>
                      {currentActive !== undefined && (
                        <span className="step-note">
                          {currentActive} of {activeLimit} jobs active
                        </span>
                      )}
                    </div>
                    <input
                      hidden
                      id="file"
                      type="file"
                      accept=".jpg,.jpeg,.png,.mp4"
                      ref={input}
                      onChange={(e) => choose(e.target.files?.[0])}
                      disabled={busy}
                    />
                    <button
                      type="button"
                      className={
                        "dropzone " +
                        (drag ? "dragging" : "") +
                        (file ? " has-file" : "")
                      }
                      disabled={busy}
                      onClick={() => input.current?.click()}
                      onDragOver={(e) => {
                        e.preventDefault();
                        setDrag(true);
                      }}
                      onDragLeave={() => setDrag(false)}
                      onDrop={(e) => {
                        e.preventDefault();
                        setDrag(false);
                        if (busy) return;
                        if (e.dataTransfer.files.length > 1) {
                          clearFile();
                          setError("Choose one file at a time.");
                        } else choose(e.dataTransfer.files[0]);
                      }}
                    >
                      <span className="upload-orbit">
                        {file ? (
                          <Check size={29} />
                        ) : (
                          <CloudUpload size={29} strokeWidth={1.5} />
                        )}
                      </span>
                      <strong>
                        {file ? file.name : "Drop a file or browse files"}
                      </strong>
                      <span>
                        {file
                          ? bytes(file.size) +
                            " · Click to choose a different file"
                          : "JPEG or PNG up to 20 MiB · MP4 up to 100 MiB"}
                      </span>
                    </button>
                    {file && (
                      <button
                        className="text-button remove-file"
                        type="button"
                        onClick={clearFile}
                        disabled={busy}
                      >
                        <X size={16} /> Remove file
                      </button>
                    )}
                    <div className="step-label second">
                      <h2>Compression quality</h2>
                    </div>
                    <fieldset className="presets">
                      <legend className="sr-only">Compression quality</legend>
                      {presets.map((option) => (
                        <label
                          className={
                            "preset " + (preset === option.id ? "selected" : "")
                          }
                          key={option.id}
                        >
                          <input
                            type="radio"
                            name="preset"
                            value={option.id}
                            checked={preset === option.id}
                            onChange={() => setPreset(option.id)}
                            disabled={busy}
                          />
                          <option.icon size={19} />
                          <strong>{option.label}</strong>
                          <span>{option.note}</span>
                          {preset === option.id && (
                            <CheckCircle2 size={14} className="preset-check" />
                          )}
                        </label>
                      ))}
                    </fieldset>
                    <button
                      className="primary compress-button"
                      disabled={!file || busy || atLimit}
                    >
                      {busy ? (
                        <>
                          <LoaderCircle size={19} className="spin" />
                          Uploading & checking your file…
                        </>
                      ) : (
                        <>
                          Compress file
                          <ArrowRight size={19} />
                        </>
                      )}
                    </button>
                    {atLimit && (
                      <p className="upload-footnote">
                        Wait for an active job to finish before uploading
                        another file.
                      </p>
                    )}
                  </form>
                  <aside className="tips panel">
                    <div className="limits">
                      <h2>File requirements</h2>
                      <p>
                        <FileImage size={17} />
                        <span>
                          <strong>JPEG or static PNG</strong>Up to 25
                          megapixels. Compresses to WebP.
                        </span>
                      </p>
                      <p>
                        <Film size={17} />
                        <span>
                          <strong>MP4 video</strong>Up to 1080p and 2 minutes.
                          Compresses to H.264 MP4.
                        </span>
                      </p>
                      <p>
                        <Clock3 size={17} />
                        <span>
                          <strong>24-hour download window</strong>FastZip keeps
                          the original when compression saves no space.
                        </span>
                      </p>
                    </div>
                  </aside>
                </div>
              )}
              <section className="recent panel">
                <div className="section-heading">
                  <h2>
                    {view === "compress" ? "Recent jobs" : "All jobs"}{" "}
                    {page && <span className="count">{total}</span>}
                  </h2>
                  {view === "compress" && total > 0 && (
                    <button
                      className="text-button"
                      onClick={() => setView("history")}
                    >
                      View all files
                      <ArrowRight size={15} />
                    </button>
                  )}
                </div>
                <div role="status">
                  {announcement && (
                    <p className="upload-confirmation">
                      <CheckCircle2 size={18} />
                      {announcement}
                    </p>
                  )}
                </div>
                {jobsError && (
                  <div className="history-error" role="status">
                    <span>Couldn’t refresh jobs. {jobsError}</span>
                    <button
                      type="button"
                      className="text-button"
                      onClick={() => setRefreshKey((key) => key + 1)}
                    >
                      Retry
                    </button>
                  </div>
                )}
                {page?.offset !== offset ? (
                  !jobsError && (
                    <div className="empty">
                      <LoaderCircle className="spin" />
                      <p>Loading your files…</p>
                    </div>
                  )
                ) : shown.length === 0 ? (
                  <div className="empty">
                    <span>
                      <FolderClock size={26} strokeWidth={1.4} />
                    </span>
                    <strong>No jobs yet</strong>
                    <p>Your files will appear here after your first upload.</p>
                  </div>
                ) : (
                  shown.map((job) => (
                    <JobRow key={job.id} job={job} download={download} />
                  ))
                )}
                {view === "history" && total > 30 && (
                  <div className="pagination">
                    <button
                      disabled={offset === 0}
                      onClick={() => setOffset(Math.max(0, offset - 30))}
                    >
                      <ChevronLeft size={16} />
                      Previous
                    </button>
                    <span>
                      {offset + 1}–{Math.min(offset + 30, total)} of {total}
                    </span>
                    <button
                      disabled={offset + 30 >= total}
                      onClick={() => setOffset(offset + 30)}
                    >
                      Next
                      <ChevronRight size={16} />
                    </button>
                  </div>
                )}
              </section>
            </>
          )}
        </main>
      </div>
    </div>
  );
}

function App() {
  const [account, setAccount] = useState<Account | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const signOut = useCallback(() => setAccount(null), []);
  useEffect(() => {
    void api<Account>("/auth/me")
      .then(setAccount)
      .catch((error) => {
        if (!(error instanceof ApiError && error.status === 401))
          setError(message(error));
      })
      .finally(() => setLoading(false));
  }, []);
  if (loading)
    return (
      <div className="loading-screen">
        <Brand />
        <LoaderCircle className="spin" />
        <span>Checking your session…</span>
      </div>
    );
  return (
    <>
      {error && <Notice text={error} clear={() => setError("")} />}
      {account ? (
        <Workspace account={account} signOut={signOut} />
      ) : (
        <Auth done={setAccount} />
      )}
    </>
  );
}
createRoot(document.getElementById("root")!).render(<App />);
