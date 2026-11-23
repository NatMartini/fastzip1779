import { useCallback, useEffect, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { api, bytes } from "./api";
import { errorMessage, formatDateTime } from "./format";
import { Notice } from "./Notice";

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
type ErrorLogPage = { items: ErrorEvent[]; has_more: boolean };
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

const overviewRefreshSeconds = 10;
const metricsRefreshSeconds = 60;
const errorPageSize = 50;

type PollRequest<T> = (signal: AbortSignal) => Promise<T>;

function useAdminPoll<T>(request: PollRequest<T>, seconds: number) {
  const [snapshot, setSnapshot] = useState<{
    request: PollRequest<T>;
    data?: T;
    error?: string;
  }>();

  useEffect(() => {
    const controller = new AbortController();
    let pending = false;
    async function refresh() {
      if (pending) return;
      pending = true;
      try {
        const data = await request(controller.signal);
        if (!controller.signal.aborted) setSnapshot({ request, data });
      } catch (error) {
        if (!controller.signal.aborted) {
          setSnapshot((previous) => ({
            request,
            data: previous?.request === request ? previous.data : undefined,
            error: errorMessage(error),
          }));
        }
      } finally {
        pending = false;
      }
    }
    void refresh();
    const timer = window.setInterval(refresh, seconds * 1000);
    return () => {
      window.clearInterval(timer);
      controller.abort();
    };
  }, [request, seconds]);

  const result = snapshot?.request === request ? snapshot : undefined;
  function clearError() {
    setSnapshot((previous) => previous && { ...previous, error: undefined });
  }
  return { result, clearError };
}

const loadOverview = (signal: AbortSignal) =>
  api<Overview>("/admin/overview", { signal });
const loadMetrics = (signal: AbortSignal) =>
  api<Metrics>("/admin/metrics", { signal });

function JobOverview() {
  const { result, clearError } = useAdminPoll(
    loadOverview,
    overviewRefreshSeconds,
  );
  const overview = result?.data;
  return (
    <>
      {result?.error && (
        <Notice
          text={`Couldn’t refresh the job overview. ${result.error}`}
          clear={clearError}
        />
      )}
      {!overview ? (
        <p className="muted" role="status">
          {result
            ? "Service health is unavailable."
            : "Loading service health…"}
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
                        {worker.online &&
                          (worker.job_id
                            ? "Processing a job · "
                            : "Ready for work · ")}
                        Last heartbeat{" "}
                        <time dateTime={worker.seen_at}>
                          {formatDateTime(worker.seen_at)}
                        </time>
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
                  <p>{formatDateTime(overview.last_backup.created_at)}</p>
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
        </>
      )}
    </>
  );
}

function Infrastructure() {
  const { result } = useAdminPoll(loadMetrics, metricsRefreshSeconds);
  const metrics = result?.data;
  return (
    <section className="panel">
      <div className="section-heading infrastructure-heading">
        <h2>Infrastructure</h2>
        <small className="muted">
          Metrics refresh every {metricsRefreshSeconds} seconds
        </small>
      </div>
      {result?.error ? (
        <p className="muted">
          Couldn’t refresh infrastructure metrics. Retrying automatically.
        </p>
      ) : !metrics ? (
        <p className="muted" role="status">
          Loading infrastructure metrics…
        </p>
      ) : !metrics.configured ? (
        <p className="muted">
          Connect DigitalOcean Monitoring to see droplet CPU, memory, and disk
          usage.
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
  );
}

function ErrorLog() {
  const [logQuery, setLogQuery] = useState({
    service: "",
    since: "",
    until: "",
    offset: 0,
  });
  const loadPage = useCallback(
    (signal: AbortSignal) => {
      const { service, offset, since, until } = logQuery;
      const query = new URLSearchParams({
        service,
        offset: String(offset),
        limit: String(errorPageSize),
      });
      if (since) query.set("since", new Date(since).toISOString());
      if (until) query.set("until", new Date(until).toISOString());
      return api<ErrorLogPage>("/admin/errors?" + query, { signal });
    },
    [logQuery],
  );
  const { result, clearError } = useAdminPoll(loadPage, overviewRefreshSeconds);
  const page = result?.data;
  function setLogFilter(field: "service" | "since" | "until", value: string) {
    setLogQuery((previous) => ({ ...previous, [field]: value, offset: 0 }));
  }
  return (
    <section className="panel errors-panel">
      <div className="section-heading">
        <h2>Error log</h2>
        <span className="muted">Structured events across services</span>
      </div>
      {result?.error && (
        <Notice
          text={`Couldn’t refresh the error log. ${result.error}`}
          clear={clearError}
        />
      )}
      <div className="error-filters">
        <label>
          Service
          <select
            value={logQuery.service}
            onChange={(e) => setLogFilter("service", e.target.value)}
          >
            <option value="">All services</option>
            {["api", "worker", "cleanup", "backup", "monitoring"].map((s) => (
              <option key={s}>{s}</option>
            ))}
          </select>
        </label>
        <label>
          From
          <input
            type="datetime-local"
            value={logQuery.since}
            onChange={(e) => setLogFilter("since", e.target.value)}
          />
        </label>
        <label>
          Until
          <input
            type="datetime-local"
            value={logQuery.until}
            onChange={(e) => setLogFilter("until", e.target.value)}
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
            {page?.items.map((event) => (
              <tr key={event.id}>
                <td>{formatDateTime(event.created_at)}</td>
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
      {!page && !result?.error && (
        <p className="empty-small" role="status">
          {result ? "Error log is unavailable." : "Loading error log…"}
        </p>
      )}
      {page?.items.length === 0 && (
        <p className="empty-small">No errors in this view.</p>
      )}
      <div className="pagination">
        <button
          disabled={!logQuery.offset}
          onClick={() =>
            setLogQuery({
              ...logQuery,
              offset: logQuery.offset - errorPageSize,
            })
          }
        >
          <ChevronLeft size={16} />
          Previous
        </button>
        <button
          disabled={!page?.has_more}
          onClick={() =>
            setLogQuery({
              ...logQuery,
              offset: logQuery.offset + errorPageSize,
            })
          }
        >
          Next
          <ChevronRight size={16} />
        </button>
      </div>
    </section>
  );
}

export function AdminPanel() {
  return (
    <>
      <div className="page-heading">
        <div>
          <h1>Administration</h1>
          <p className="muted">
            The job overview and error log refresh every{" "}
            {overviewRefreshSeconds} seconds. User media stays private.
          </p>
        </div>
      </div>
      <JobOverview />
      <Infrastructure />
      <ErrorLog />
    </>
  );
}
