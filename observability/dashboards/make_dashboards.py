"""Generate the Buildkite Grafana dashboards.

Usage: python3 observability/dashboards/make_dashboards.py observability/dashboards

Writes one JSON file per dashboard, for import into Grafana Cloud. Data sources
are dashboard variables that pick the stack's grafanacloud-<stack>-traces,
-logs and -prom data sources, so the files don't depend on their UIDs.
"""
import json
import os
import re
import sys

PROM = {"type": "prometheus", "uid": "${ds_metrics}"}
TEMPO = {"type": "tempo", "uid": "${ds_traces}"}
LOKI = {"type": "loki", "uid": "${ds_logs}"}

PHASES = "checkout|environment|pre-command|command|post-command|pre-exit|artifacts"
CLI = "pipeline-upload|annotate|meta-data-.*|artifact-upload|artifacts|artifact-download"
ERR = "(?i)(error|failed|not found|denied|exit status [1-9])"

# TraceQL and LogQL filters shared by the dashboards.
TP = 'resource.buildkite.pipeline =~ "${pipeline:regex}"'
PIPELINE_LOGS = '{service_name="buildkite-agent"} | buildkite_pipeline_slug=~"${pipeline:regex}"'
LOG_SEL = PIPELINE_LOGS + ' | buildkite_build_number=~"${build}" | buildkite_job_label=~"(?i).*${step}.*"'

OK = [{"color": "green", "value": None}]
BAD = [{"color": "green", "value": None}, {"color": "red", "value": 1}]

# ---------------------------------------------------------------- variables


def ds_var(name, label, kind, regex):
    # The regex keeps Grafana Cloud's usage/ML/alert-history data sources out.
    return {"name": name, "label": label, "type": "datasource", "query": kind, "regex": regex,
            "hide": 0, "refresh": 1, "current": {}}


DS_METRICS = ds_var("ds_metrics", "Metrics", "prometheus", "/-prom$/")
DS_LOGS = ds_var("ds_logs", "Logs", "loki", "/-logs$/")
DS_TRACES = ds_var("ds_traces", "Traces", "tempo", "/-traces$/")
PIPELINE = {"name": "pipeline", "label": "Pipeline", "type": "query", "datasource": TEMPO,
            "query": {"label": "resource.buildkite.pipeline", "type": 1, "refId": "pipeline"},
            "refresh": 2, "multi": True, "includeAll": True, "allValue": ".*",
            "current": {"text": "All", "value": "$__all"}, "sort": 1}


def textbox(name, label, default):
    return {"name": name, "label": label, "type": "textbox", "query": default,
            "current": {"text": default, "value": default}}


# ---------------------------------------------------------------- layout


class Dash:
    def __init__(self):
        self.panels, self._id, self.y = [], 0, 0

    def _nid(self):
        self._id += 1
        return self._id

    def row(self, title):
        self.panels.append({"type": "row", "title": title, "id": self._nid(), "collapsed": False,
                            "gridPos": {"h": 1, "w": 24, "x": 0, "y": self.y}, "panels": []})
        self.y += 1

    def place(self, p, w, h, x):
        p["gridPos"] = {"h": h, "w": w, "x": x, "y": self.y}
        p["id"] = self._nid()
        self.panels.append(p)

    def next_line(self, h):
        self.y += h


# ---------------------------------------------------------------- query targets


def prom(expr, legend="", instant=False, ref="A"):
    t = {"datasource": PROM, "expr": expr, "legendFormat": legend, "refId": ref}
    if instant:
        t["instant"], t["range"] = True, False
    return t


def tql(query, instant=True, ref="A"):
    return {"datasource": TEMPO, "refId": ref, "queryType": "traceql",
            "metricsQueryType": "instant" if instant else "range", "query": query}


def tsearch(query, table="traces", limit=20):
    return {"datasource": TEMPO, "refId": "A", "queryType": "traceql", "tableType": table,
            "limit": limit, "query": query}


def loki(expr, legend="", instant=False, ref="A"):
    t = {"datasource": LOKI, "refId": ref, "expr": expr, "legendFormat": legend}
    if instant:
        t["queryType"] = "instant"
    return t


# ---------------------------------------------------------------- panels


def stat(title, target, ds, unit="none", desc="", decimals=None, thresholds=None, no_value=None):
    defaults = {"unit": unit, "color": {"mode": "thresholds"},
                "thresholds": {"mode": "absolute", "steps": thresholds or [{"color": "blue", "value": None}]}}
    if decimals is not None:
        defaults["decimals"] = decimals
    if no_value is not None:
        defaults["noValue"] = no_value
    return {"type": "stat", "title": title, "description": desc, "datasource": ds, "targets": [target],
            "fieldConfig": {"defaults": defaults, "overrides": []},
            "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                        "colorMode": "value", "graphMode": "none", "textMode": "value", "justifyMode": "center"}}


def tstat(title, query, unit="none", desc="", decimals=None, thresholds=None):
    return stat(title, tql(query), TEMPO, unit, desc, decimals, thresholds,
                no_value="0" if unit == "none" else "-")


def ts(title, targets, ds, unit="s", desc="", stack=False, bars=False, points=False, rename=None):
    custom = {"drawStyle": "bars" if bars else ("points" if points else "line"), "lineWidth": 1,
              "fillOpacity": 60 if bars else 10, "pointSize": 6 if points else 4, "showPoints": "auto",
              "spanNulls": True, "stacking": {"mode": "normal" if stack else "none", "group": "A"}}
    p = {"type": "timeseries", "title": title, "description": desc, "datasource": ds, "targets": targets,
         "fieldConfig": {"defaults": {"unit": unit, "custom": custom, "color": {"mode": "palette-classic"}},
                         "overrides": []},
         "options": {"legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
                     "tooltip": {"mode": "multi", "sort": "desc"}}}
    if rename:
        # TraceQL metrics name series like {name="checkout", p=0.95}; keep the value.
        p["transformations"] = [{"id": "renameByRegex",
                                 "options": {"regex": '.*' + re.escape(rename) + '="([^"]+)".*', "renamePattern": "$1"}}]
    return p


def bargauge(title, target, ds, unit="s", desc="", decimals=2):
    defaults = {"unit": unit, "decimals": decimals, "color": {"mode": "continuous-BlYlRd"}, "min": 0,
                "noValue": "no data in range"}
    if ds is TEMPO:
        # One frame per group; the frame name is the group's value.
        defaults["displayName"] = "${__series.name}"
    return {"type": "bargauge", "title": title, "description": desc, "datasource": ds, "targets": [target],
            "fieldConfig": {"defaults": defaults, "overrides": []},
            "options": {"orientation": "horizontal", "displayMode": "gradient", "showUnfilled": True,
                        "valueMode": "color", "namePlacement": "left", "sizing": "auto",
                        "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}}}


def tbar(title, query, unit="s", desc=""):
    return bargauge(title, tql(query), TEMPO, unit, desc, decimals=0 if unit == "none" else 2)


def trace_table(title, query, desc="", table="traces", limit=20):
    return {"type": "table", "title": title, "description": desc, "datasource": TEMPO,
            "targets": [tsearch(query, table, limit)], "fieldConfig": {"defaults": {}, "overrides": []},
            "options": {"showHeader": True}}


def logs(title, expr, desc=""):
    return {"type": "logs", "title": title, "description": desc, "datasource": LOKI, "targets": [loki(expr)],
            "options": {"showTime": True, "wrapLogMessage": True, "sortOrder": "Descending",
                        "enableLogDetails": True, "dedupStrategy": "none", "prettifyLogMessage": False}}


def text(content, title=""):
    return {"type": "text", "title": title, "options": {"mode": "markdown", "content": content}}


def links():
    return [{"title": "Buildkite dashboards", "type": "dashboards", "tags": ["buildkite"],
             "asDropdown": True, "includeVars": True, "keepTime": True}]


def dashboard(uid, title, desc, d, variables, time_from="now-6h"):
    return {"uid": uid, "title": title, "description": desc, "tags": ["buildkite", "otel"],
            "timezone": "browser", "editable": True, "graphTooltip": 1, "refresh": "1m",
            "schemaVersion": 39, "time": {"from": time_from, "to": "now"},
            "templating": {"list": variables}, "links": links(), "annotations": {"list": []},
            "panels": d.panels}


# ================================================================ Buildkite CI


def ci():
    d = Dash()
    d.row("Overview")
    d.place(tstat("Jobs run", f'{{ name = "command" && {TP} }} | count_over_time()',
                  desc="One `command` span per job.", thresholds=OK), 4, 4, 0)
    d.place(tstat("Failed jobs", f'{{ name = "command" && status = error && {TP} }} | count_over_time()',
                  desc="Jobs whose command span ended with an error status.", thresholds=BAD), 4, 4, 4)
    d.place(tstat("Avg command time", f'{{ name = "command" && {TP} }} | avg_over_time(duration)', unit="s",
                  decimals=2, desc="Average duration of the step's script."), 4, 4, 8)
    d.place(tstat("Avg checkout time", f'{{ name = "checkout" && {TP} }} | avg_over_time(duration)', unit="s",
                  decimals=2, desc="Average time in checkout (git clone, fetch, checkout, clean)."), 4, 4, 12)
    d.place(stat("Jobs running now", prom("sum(buildkite_deduper_jobs_running)", instant=True), PROM,
                 desc="Jobs agent-stack-k8s considers in flight.", thresholds=OK), 4, 4, 16)
    d.place(stat("Max in flight", prom("max(buildkite_limiter_max_in_flight)", instant=True), PROM,
                 desc="config.max-in-flight on the controller.", thresholds=OK), 4, 4, 20)
    d.next_line(4)

    d.row("Where job time goes")
    d.place(tbar("Average time per job phase",
                 f'{{ name =~ "{PHASES}" && {TP} }} | avg_over_time(duration) by (name)',
                 desc="Average duration of each top-level phase across the selected pipelines and time range."),
            8, 9, 0)
    d.place(ts("p95 duration: checkout, git fetch, command",
               [tql(f'{{ name =~ "checkout|git.fetch|command" && {TP} }} | quantile_over_time(duration, .95) by (name)',
                    instant=False)], TEMPO, points=True, rename="name",
               desc="Tempo buckets durations in powers of two, so p95 values are approximate. Gaps mean no jobs ran."),
            16, 9, 8)
    d.next_line(9)
    d.place(tbar("Runs per step", f'{{ name = "command" && {TP} }} | count_over_time() by (resource.buildkite.job_label)',
                 unit="none", desc="Jobs per step label."), 8, 8, 0)
    d.place(tbar("Average command time per step",
                 f'{{ name = "command" && {TP} }} | avg_over_time(duration) by (resource.buildkite.job_label)',
                 desc="Average script duration per step label."), 8, 8, 8)
    d.place(tbar("Failures per step",
                 f'{{ name = "command" && status = error && {TP} }} | count_over_time() by (resource.buildkite.job_label)',
                 unit="none", desc="Failed jobs per step label. Empty means no failures in the range."), 8, 8, 16)
    d.next_line(8)

    d.row("Agent CLI overhead")
    d.place(tbar("Total time in agent CLI calls", f'{{ name =~ "{CLI}" && {TP} }} | sum_over_time(duration) by (name)',
                 desc="Time your scripts spend in buildkite-agent subcommands."), 12, 7, 0)
    d.place(tbar("Average time per CLI call", f'{{ name =~ "{CLI}" && {TP} }} | avg_over_time(duration) by (name)',
                 desc="Average duration of a single call."), 12, 7, 12)
    d.next_line(7)

    d.row("Failures")
    d.place(trace_table("Recent failed traces", f'{{ status = error && {TP} }}',
                        desc="Click a trace ID to open it, then use 'Logs for this span'."), 12, 9, 0)
    d.place(logs("Error lines in job logs", PIPELINE_LOGS + f' |~ "{ERR}"'), 12, 9, 12)
    d.next_line(9)

    return dashboard("buildkite-ci", "Buildkite CI",
                     "Job phases, failures and CLI overhead from Buildkite agent OpenTelemetry data.",
                     d, [DS_METRICS, DS_LOGS, DS_TRACES, PIPELINE])


# ================================================================ Logs


def logs_dash():
    d = Dash()
    d.place(text("Job output from every Buildkite job, sent by the agent over OTLP. Filter with the variables "
                 "above: **Build** is a regex on the build number (e.g. `88` or `8[5-9]`), **Step contains** "
                 "matches part of the step label, and **Search** filters the lines in the bottom panel. Open "
                 "a line's details to see its build, job and `trace_id`; the trace ID links to the job's trace."),
            24, 3, 0)
    d.next_line(3)

    d.row("Volume")
    d.place(ts("Log lines by pipeline", [loki(f'sum by (buildkite_pipeline_slug) (count_over_time({LOG_SEL} [$__auto]))',
                                              "{{buildkite_pipeline_slug}}")], LOKI, unit="short", bars=True,
               stack=True), 12, 8, 0)
    d.place(ts("Log lines by step", [loki(f'sum by (buildkite_job_label) (count_over_time({LOG_SEL} [$__auto]))',
                                          "{{buildkite_job_label}}")], LOKI, unit="short", bars=True, stack=True),
            12, 8, 12)
    d.next_line(8)

    d.row("Errors")
    d.place(ts("Error lines by pipeline",
               [loki(f'sum by (buildkite_pipeline_slug) (count_over_time({LOG_SEL} |~ "{ERR}" [$__auto]))',
                     "{{buildkite_pipeline_slug}}")], LOKI, unit="short", bars=True, stack=True,
               desc="Lines matching error, failed, not found, denied or a non-zero exit status."), 12, 8, 0)
    d.place(logs("Error lines", LOG_SEL + f' |~ "{ERR}"'), 12, 8, 12)
    d.next_line(8)

    d.row("Jobs")
    jobs = {"type": "table", "title": "Log lines per job",
            "description": "Every job in the range with its line count and error-line count. Sort by a column header.",
            "datasource": LOKI,
            "targets": [loki(f'sum by (buildkite_pipeline_slug, buildkite_build_number, buildkite_job_label) '
                             f'(count_over_time({LOG_SEL} [$__range]))', instant=True, ref="A"),
                        loki(f'sum by (buildkite_pipeline_slug, buildkite_build_number, buildkite_job_label) '
                             f'(count_over_time({LOG_SEL} |~ "{ERR}" [$__range]))', instant=True, ref="B")],
            "transformations": [
                {"id": "merge", "options": {}},
                {"id": "organize", "options": {"excludeByName": {"Time": True},
                                               "renameByName": {"buildkite_pipeline_slug": "Pipeline",
                                                                "buildkite_build_number": "Build",
                                                                "buildkite_job_label": "Step",
                                                                "Value #A": "Lines", "Value #B": "Error lines"}}}],
            "fieldConfig": {"defaults": {"custom": {"align": "auto"}},
                            "overrides": [{"matcher": {"id": "byName", "options": "Error lines"},
                                           "properties": [{"id": "custom.cellOptions", "value": {"type": "color-text"}},
                                                          {"id": "thresholds", "value": {"mode": "absolute", "steps": BAD}},
                                                          {"id": "color", "value": {"mode": "thresholds"}}]}]},
            "options": {"showHeader": True, "sortBy": [{"displayName": "Build", "desc": True}]}}
    d.place(jobs, 12, 9, 0)
    d.place(logs("Exit statuses", LOG_SEL + ' |~ "(?i)(exited with status|user command error)"'), 12, 9, 12)
    d.next_line(9)

    d.row("Search")
    d.place(logs("Job logs", LOG_SEL + ' |= "$search"',
                 desc="All matching job output, newest first."), 24, 12, 0)
    d.next_line(12)

    # Traces is here only for the pipeline list, which comes from Tempo.
    return dashboard("buildkite-logs", "Buildkite logs", "Explore Buildkite job output in Loki.", d,
                     [DS_LOGS, DS_TRACES, PIPELINE, textbox("build", "Build", ".*"),
                      textbox("step", "Step contains", ""), textbox("search", "Search", "")])


# ================================================================ Traces


def traces_dash():
    d = Dash()
    d.place(text("Each Buildkite job produces two traces under agent-stack-k8s, one for the checkout "
                 "container and one for the command container. Both have the job's step as the root span "
                 "and share `buildkite.job_id`. Click a trace ID to open the trace; from any span, "
                 "**Logs for this span** opens its job output."), 24, 3, 0)
    d.next_line(3)

    d.row("Jobs over time")
    d.place(ts("Jobs by pipeline", [tql(f'{{ name = "command" && {TP} }} | count_over_time() by (resource.buildkite.pipeline)',
                                        instant=False)], TEMPO, unit="short", bars=True, stack=True,
               rename="resource.buildkite.pipeline"), 12, 8, 0)
    d.place(ts("Failed jobs by pipeline",
               [tql(f'{{ name = "command" && status = error && {TP} }} | count_over_time() by (resource.buildkite.pipeline)',
                    instant=False)], TEMPO, unit="short", bars=True, stack=True, rename="resource.buildkite.pipeline"),
            12, 8, 12)
    d.next_line(8)

    d.row("Durations")
    d.place(ts("p95 command time by step",
               [tql(f'{{ name = "command" && {TP} }} | quantile_over_time(duration, .95) by (resource.buildkite.job_label)',
                    instant=False)], TEMPO, points=True, rename="resource.buildkite.job_label",
               desc="Approximate: Tempo buckets durations in powers of two."), 12, 9, 0)
    d.place(ts("Average time per phase",
               [tql(f'{{ name =~ "{PHASES}" && {TP} }} | avg_over_time(duration) by (name)', instant=False)],
               TEMPO, points=True, rename="name"), 12, 9, 12)
    d.next_line(9)
    d.place(tbar("Git operations: average time",
                 f'{{ name =~ "git\\\\..*|repo-checkout" && {TP} }} | avg_over_time(duration) by (name)',
                 desc="Where checkout time goes: fetch, checkout, clean, sparse checkout, commit verification."),
            12, 8, 0)
    d.place(tbar("Slowest steps: average command time",
                 f'{{ name = "command" && {TP} }} | avg_over_time(duration) by (resource.buildkite.job_label)'),
            12, 8, 12)
    d.next_line(8)

    d.row("Find a job")
    d.place(trace_table("Jobs (command spans)",
                        f'{{ name = "command" && {TP} }} | select(resource.buildkite.build_number, '
                        f'resource.buildkite.job_label, resource.buildkite.branch)',
                        table="spans", limit=50,
                        desc="One row per job with its duration. Sort by Duration to find the slowest."), 12, 10, 0)
    d.place(trace_table("Failed spans", f'{{ status = error && {TP} }} | select(resource.buildkite.build_number, '
                                        f'resource.buildkite.job_label)', table="spans", limit=50,
                        desc="Every span that ended with an error, with its build and step."), 12, 10, 12)
    d.next_line(10)

    return dashboard("buildkite-traces", "Buildkite traces", "Explore Buildkite job traces in Tempo.", d,
                     [DS_TRACES, PIPELINE])


# ================================================================ Infrastructure


def infra_dash():
    d = Dash()
    d.row("agent-stack-k8s controller")
    d.place(stat("Jobs running now", prom("sum(buildkite_deduper_jobs_running)", instant=True), PROM, thresholds=OK),
            6, 4, 0)
    d.place(stat("Max in flight", prom("max(buildkite_limiter_max_in_flight)", instant=True), PROM, thresholds=OK),
            6, 4, 6)
    d.place(stat("Jobs created in range",
                 prom("round(sum(increase(buildkite_scheduler_job_create_success_total[$__range])))", instant=True),
                 PROM, thresholds=OK, desc="Kubernetes Jobs the controller created."), 6, 4, 12)
    d.place(stat("Controller up", prom('max(up{job="buildkite/agent-stack-k8s"})', instant=True), PROM,
                 thresholds=[{"color": "red", "value": None}, {"color": "green", "value": 1}],
                 desc="1 when otel-gateway can scrape the controller."), 6, 4, 18)
    d.next_line(4)
    d.place(ts("Jobs in flight", [prom("sum(buildkite_deduper_jobs_running)", "running"),
                                  prom("max(buildkite_limiter_max_in_flight)", "max in flight", ref="B"),
                                  prom("sum(buildkite_limiter_tokens_available)", "tokens available", ref="C")],
               PROM, unit="none"), 8, 8, 0)
    d.place(ts("Time to create a Kubernetes Job (p50 / p95)",
               [prom("histogram_quantile(0.5, sum by (le) (rate(buildkite_job_end_to_end_seconds_bucket[$__rate_interval])))", "p50"),
                prom("histogram_quantile(0.95, sum by (le) (rate(buildkite_job_end_to_end_seconds_bucket[$__rate_interval])))", "p95", ref="B")],
               PROM, desc="From the controller's query returning the job to the Kubernetes Job being created."), 8, 8, 8)
    d.place(ts("Controller problems",
               [prom("sum(increase(buildkite_job_watcher_jobs_stalled_without_pod_total[$__rate_interval]))", "stalled without pod"),
                prom("sum(increase(buildkite_job_watcher_jobs_failed_before_agent_acquire_total[$__rate_interval]))",
                     "pod failed before agent started", ref="B"),
                prom("sum(increase(buildkite_scheduler_jobs_failed_on_buildkite_total[$__rate_interval]))", "failed by scheduler", ref="C"),
                prom("sum(increase(buildkite_pod_watcher_jobs_failed_on_buildkite_total[$__rate_interval]))", "failed by pod watcher", ref="D")],
               PROM, unit="none", bars=True, stack=True, desc="Jobs the controller had to fail or clean up. Should stay at zero."),
            8, 8, 16)
    d.next_line(8)
    d.place(ts("Buildkite API query time (p95)",
               [prom("histogram_quantile(0.95, sum by (le) (rate(buildkite_monitor_job_query_seconds_bucket[$__rate_interval])))", "p95")],
               PROM, desc="How long the controller's poll for queued jobs takes."), 12, 7, 0)
    d.place(ts("Agent pings and log uploads",
               [prom("sum(rate(buildkite_agent_pings_sent_total[$__rate_interval]))", "pings/s"),
                prom("sum(rate(buildkite_agent_pings_errors_total[$__rate_interval]))", "ping errors/s", ref="B"),
                prom("sum(rate(buildkite_agent_logs_chunk_uploads_errored_total[$__rate_interval]))", "log upload errors/s", ref="C")],
               PROM, unit="short"), 12, 7, 12)
    d.next_line(7)

    d.row("Telemetry pipeline (otel-gateway)")
    d.place(ts("Received per second",
               [prom("sum(rate(otelcol_receiver_accepted_spans_total[$__rate_interval]))", "spans"),
                prom("sum(rate(otelcol_receiver_accepted_log_records_total[$__rate_interval]))", "log records", ref="B"),
                prom("sum(rate(otelcol_receiver_accepted_metric_points_total[$__rate_interval]))", "metric points", ref="C")],
               PROM, unit="short", desc="What otel-gateway accepted from agents, job pods and scrapes."), 12, 8, 0)
    d.place(ts("Sent per second, by exporter",
               [prom("sum by (exporter) (rate(otelcol_exporter_sent_spans_total[$__rate_interval]))", "{{exporter}} spans"),
                prom("sum by (exporter) (rate(otelcol_exporter_sent_log_records_total[$__rate_interval]))", "{{exporter}} logs", ref="B"),
                prom("sum by (exporter) (rate(otelcol_exporter_sent_metric_points_total[$__rate_interval]))", "{{exporter}} metrics", ref="C")],
               PROM, unit="short", desc="Local backends and Grafana Cloud side by side."), 12, 8, 12)
    d.next_line(8)
    d.place(ts("Export failures and refusals",
               [prom("sum by (exporter) (rate(otelcol_exporter_send_failed_spans_total[$__rate_interval]))", "{{exporter}} spans"),
                prom("sum by (exporter) (rate(otelcol_exporter_send_failed_log_records_total[$__rate_interval]))", "{{exporter}} logs", ref="B"),
                prom("sum by (exporter) (rate(otelcol_exporter_send_failed_metric_points_total[$__rate_interval]))", "{{exporter}} metrics", ref="C"),
                prom("sum by (receiver) (rate(otelcol_receiver_refused_spans_total[$__rate_interval]))", "{{receiver}} refused spans", ref="D")],
               PROM, unit="short", desc="Anything above zero means telemetry is being lost."), 12, 8, 0)
    d.place(ts("Exporter queue size", [prom("sum by (exporter) (otelcol_exporter_queue_size)", "{{exporter}}")],
               PROM, unit="short", desc="Batches waiting to be sent. Growth means a backend is slow or down."), 12, 8, 12)
    d.next_line(8)

    return dashboard("buildkite-infra", "Buildkite infrastructure",
                     "agent-stack-k8s controller health and the otel-gateway telemetry pipeline.", d, [DS_METRICS])


if __name__ == "__main__":
    out = sys.argv[1]
    for name, build in (("buildkite-ci", ci), ("buildkite-logs", logs_dash),
                        ("buildkite-traces", traces_dash), ("buildkite-infra", infra_dash)):
        dash = build()
        path = os.path.join(out, name + ".json")
        with open(path, "w") as f:
            json.dump(dash, f, indent=2)
        print(f"{len(dash['panels']):3} panels -> {path}")
