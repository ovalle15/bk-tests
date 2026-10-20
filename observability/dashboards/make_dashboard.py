"""Generate the Buildkite CI Grafana dashboard JSON.

Usage: python3 observability/dashboards/make_dashboard.py observability/dashboards/buildkite-ci.json
"""
import json
import sys

PROM = {"type": "prometheus", "uid": "prometheus"}
TEMPO = {"type": "tempo", "uid": "tempo"}
LOKI = {"type": "loki", "uid": "loki"}

SM = "traces_spanmetrics"
P = 'buildkite_pipeline=~"${pipeline:regex}"'
PHASES = "checkout|environment|pre-command|command|post-command|pre-exit|artifacts"
CLI = "pipeline-upload|annotate|meta-data-.*|artifact-upload|artifacts|artifact-download"
LOG_SEL = '{service_name="buildkite-agent"} | buildkite_pipeline_slug=~"${pipeline:regex}"'

panels = []
_id = [0]
_y = [0]


def nid():
    _id[0] += 1
    return _id[0]


def row(title, collapsed=False):
    panels.append({"type": "row", "title": title, "id": nid(), "collapsed": collapsed,
                   "gridPos": {"h": 1, "w": 24, "x": 0, "y": _y[0]}, "panels": []})
    _y[0] += 1


def place(p, w, h, x):
    p["gridPos"] = {"h": h, "w": w, "x": x, "y": _y[0]}
    p["id"] = nid()
    panels.append(p)


def next_line(h):
    _y[0] += h


def prom(expr, legend="", instant=False, fmt=None, ref="A"):
    t = {"datasource": PROM, "expr": expr, "legendFormat": legend, "refId": ref}
    if instant:
        t["instant"], t["range"] = True, False
    if fmt:
        t["format"] = fmt
    return t


def stat(title, expr, unit="none", desc="", decimals=None, thresholds=None):
    p = {"type": "stat", "title": title, "description": desc, "datasource": PROM,
         "targets": [prom(expr, instant=True)],
         "fieldConfig": {"defaults": {"unit": unit, "color": {"mode": "thresholds"},
                                      "thresholds": {"mode": "absolute", "steps": thresholds or [{"color": "blue", "value": None}]}},
                         "overrides": []},
         "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                     "colorMode": "value", "graphMode": "none", "textMode": "value", "justifyMode": "center"}}
    if decimals is not None:
        p["fieldConfig"]["defaults"]["decimals"] = decimals
    return p


def ts(title, targets, unit="s", desc="", stack=False, bars=False):
    custom = {"drawStyle": "bars" if bars else "line", "lineWidth": 1, "fillOpacity": 60 if bars else 10,
              "pointSize": 4, "showPoints": "auto", "spanNulls": True,
              "stacking": {"mode": "normal" if stack else "none", "group": "A"}}
    return {"type": "timeseries", "title": title, "description": desc, "datasource": PROM, "targets": targets,
            "fieldConfig": {"defaults": {"unit": unit, "custom": custom, "color": {"mode": "palette-classic"}}, "overrides": []},
            "options": {"legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
                        "tooltip": {"mode": "multi", "sort": "desc"}}}


def bargauge(title, expr, legend, unit="s", desc=""):
    return {"type": "bargauge", "title": title, "description": desc, "datasource": PROM,
            "targets": [prom(expr, legend=legend, instant=True)],
            "fieldConfig": {"defaults": {"unit": unit, "decimals": 2, "color": {"mode": "continuous-BlYlRd"}, "min": 0}, "overrides": []},
            "options": {"orientation": "horizontal", "displayMode": "gradient", "showUnfilled": True, "valueMode": "color",
                        "namePlacement": "left", "sizing": "auto",
                        "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}}}


# Span-derived panels query Tempo with TraceQL metrics, which count every span
# exactly. Prometheus span-metric counters miss a series' first increment, and
# CI steps run too rarely for that to average out.
TP = 'resource.buildkite.pipeline =~ "${pipeline:regex}"'


def tql(query, instant=True, ref="A"):
    return {"datasource": TEMPO, "refId": ref, "queryType": "traceql",
            "metricsQueryType": "instant" if instant else "range", "query": query}


def tstat(title, query, unit="none", desc="", decimals=None, thresholds=None):
    p = stat(title, "", unit=unit, desc=desc, decimals=decimals, thresholds=thresholds)
    p["datasource"], p["targets"] = TEMPO, [tql(query)]
    p["fieldConfig"]["defaults"]["noValue"] = "0" if unit == "none" else "-"
    return p


def tbar(title, query, unit="s", desc=""):
    p = bargauge(title, "", "", unit=unit, desc=desc)
    p["datasource"], p["targets"] = TEMPO, [tql(query)]
    # One frame per group; the frame name is the group's value.
    p["fieldConfig"]["defaults"]["displayName"] = "${__series.name}"
    p["fieldConfig"]["defaults"]["noValue"] = "no data in range"
    if unit == "none":
        p["fieldConfig"]["defaults"]["decimals"] = 0
    return p


# ---------------------------------------------------------------- Overview
row("Overview")
ok = [{"color": "green", "value": None}]
bad = [{"color": "green", "value": None}, {"color": "red", "value": 1}]
place(tstat("Jobs run", f'{{ name = "command" && {TP} }} | count_over_time()',
            desc="One `command` span per job.", thresholds=ok), 4, 4, 0)
place(tstat("Failed jobs", f'{{ name = "command" && status = error && {TP} }} | count_over_time()',
            desc="Jobs whose command span ended with an error status.", thresholds=bad), 4, 4, 4)
place(tstat("Avg command time", f'{{ name = "command" && {TP} }} | avg_over_time(duration)', unit="s", decimals=2,
            desc="Average duration of the step's script."), 4, 4, 8)
place(tstat("Avg checkout time", f'{{ name = "checkout" && {TP} }} | avg_over_time(duration)', unit="s", decimals=2,
            desc="Average time in checkout (git clone, fetch, checkout, clean)."), 4, 4, 12)
place(stat("Jobs running now", "sum(buildkite_deduper_jobs_running)",
           desc="Jobs agent-stack-k8s considers in flight.", thresholds=ok), 4, 4, 16)
place(stat("Max in flight", "max(buildkite_limiter_max_in_flight)",
           desc="config.max-in-flight on the controller.", thresholds=ok), 4, 4, 20)
next_line(4)

# ---------------------------------------------------------------- Where time goes
row("Where job time goes")
place(tbar("Average time per job phase",
           f'{{ name =~ "{PHASES}" && {TP} }} | avg_over_time(duration) by (name)',
           desc="Average duration of each top-level phase across the selected pipelines and time range."), 8, 9, 0)
p95 = ts("p95 duration: checkout, git fetch, command",
         [tql(f'{{ name =~ "checkout|git.fetch|command" && {TP} }} | quantile_over_time(duration, .95) by (name)', instant=False)],
         desc="Tempo buckets durations in powers of two, so p95 values are approximate. Gaps mean no jobs ran.")
p95["datasource"] = TEMPO
p95["transformations"] = [{"id": "renameByRegex", "options": {"regex": "\\{name=\"([^\"]+)\".*", "renamePattern": "$1"}}]
p95["fieldConfig"]["defaults"]["custom"]["drawStyle"] = "points"
p95["fieldConfig"]["defaults"]["custom"]["pointSize"] = 6
place(p95, 16, 9, 8)
next_line(9)

place(tbar("Runs per step", f'{{ name = "command" && {TP} }} | count_over_time() by (resource.buildkite.job_label)',
           unit="none", desc="Jobs per step label."), 8, 8, 0)
place(tbar("Average command time per step",
           f'{{ name = "command" && {TP} }} | avg_over_time(duration) by (resource.buildkite.job_label)',
           desc="Average script duration per step label."), 8, 8, 8)
place(tbar("Failures per step",
           f'{{ name = "command" && status = error && {TP} }} | count_over_time() by (resource.buildkite.job_label)',
           unit="none", desc="Failed jobs per step label. Empty means no failures in the range."), 8, 8, 16)
next_line(8)

# ---------------------------------------------------------------- CLI overhead
row("Agent CLI overhead")
place(tbar("Total time in agent CLI calls",
           f'{{ name =~ "{CLI}" && {TP} }} | sum_over_time(duration) by (name)',
           desc="Time your scripts spend in buildkite-agent subcommands (pipeline upload, annotate, meta-data, artifacts)."), 12, 7, 0)
place(tbar("Average time per CLI call", f'{{ name =~ "{CLI}" && {TP} }} | avg_over_time(duration) by (name)',
           desc="Average duration of a single call."), 12, 7, 12)
next_line(7)

# ---------------------------------------------------------------- Failures
row("Failures")
place({"type": "table", "title": "Recent failed traces", "datasource": TEMPO,
       "description": "Traces with an error span. Click a trace ID to open it, then use 'Logs for this span'.",
       "targets": [{"datasource": TEMPO, "refId": "A", "queryType": "traceql", "tableType": "traces", "limit": 20,
                    "query": '{ status = error && resource.buildkite.pipeline =~ "${pipeline:regex}" }'}],
       "fieldConfig": {"defaults": {}, "overrides": []}, "options": {"showHeader": True}}, 12, 9, 0)
place({"type": "logs", "title": "Error lines in job logs", "datasource": LOKI,
       "targets": [{"datasource": LOKI, "refId": "A",
                    "expr": LOG_SEL + ' |~ "(?i)(error|failed|not found|denied|exit status [1-9])"'}],
       "options": {"showTime": True, "wrapLogMessage": True, "sortOrder": "Descending", "enableLogDetails": True,
                   "dedupStrategy": "none", "prettifyLogMessage": False}}, 12, 9, 12)
next_line(9)

# ---------------------------------------------------------------- Logs
row("Job logs")
place({"type": "logs", "title": "Job logs (filter with the search box)", "datasource": LOKI,
       "description": "Open a line's details to see its build, job and trace_id; the trace_id links to Tempo.",
       "targets": [{"datasource": LOKI, "refId": "A", "expr": LOG_SEL + ' |= "$search"'}],
       "options": {"showTime": True, "wrapLogMessage": True, "sortOrder": "Descending", "enableLogDetails": True,
                   "dedupStrategy": "none", "prettifyLogMessage": False}}, 24, 10, 0)
next_line(10)

# ---------------------------------------------------------------- Controller
row("agent-stack-k8s controller")
place(ts("Jobs in flight", [prom("sum(buildkite_deduper_jobs_running)", "running"),
                            prom("max(buildkite_limiter_max_in_flight)", "max in flight", ref="B"),
                            prom("sum(buildkite_limiter_tokens_available)", "tokens available", ref="C")],
         unit="none", desc="Running jobs against the max-in-flight limit."), 8, 8, 0)
place(ts("Time to create a Kubernetes Job (p50 / p95)",
         [prom("histogram_quantile(0.5, sum by (le) (rate(buildkite_job_end_to_end_seconds_bucket[$__rate_interval])))", "p50"),
          prom("histogram_quantile(0.95, sum by (le) (rate(buildkite_job_end_to_end_seconds_bucket[$__rate_interval])))", "p95", ref="B")],
         desc="From the controller's query returning the job to the Kubernetes Job being created."), 8, 8, 8)
place(ts("Controller problems",
         [prom("sum(increase(buildkite_job_watcher_jobs_stalled_without_pod_total[$__rate_interval]))", "stalled without pod"),
          prom("sum(increase(buildkite_job_watcher_jobs_failed_before_agent_acquire_total[$__rate_interval]))", "pod failed before agent started", ref="B"),
          prom("sum(increase(buildkite_scheduler_jobs_failed_on_buildkite_total[$__rate_interval]))", "failed by scheduler", ref="C"),
          prom("sum(increase(buildkite_pod_watcher_jobs_failed_on_buildkite_total[$__rate_interval]))", "failed by pod watcher", ref="D")],
         unit="none", bars=True, stack=True,
         desc="Jobs the controller had to fail or clean up. Should stay at zero."), 8, 8, 16)
next_line(8)

# ---------------------------------------------------------------- Gateway
row("Telemetry pipeline (otel-gateway)", collapsed=False)
place(ts("Received per second",
         [prom('sum(rate(otelcol_receiver_accepted_spans_total[$__rate_interval]))', "spans"),
          prom('sum(rate(otelcol_receiver_accepted_log_records_total[$__rate_interval]))', "log records", ref="B"),
          prom('sum(rate(otelcol_receiver_accepted_metric_points_total[$__rate_interval]))', "metric points", ref="C")],
         unit="short", desc="What otel-gateway accepted from agents, job pods and scrapes."), 12, 7, 0)
place(ts("Export failures and refusals",
         [prom('sum by (exporter) (rate(otelcol_exporter_send_failed_spans_total[$__rate_interval]))', "{{exporter}} spans"),
          prom('sum by (exporter) (rate(otelcol_exporter_send_failed_log_records_total[$__rate_interval]))', "{{exporter}} logs", ref="B"),
          prom('sum by (exporter) (rate(otelcol_exporter_send_failed_metric_points_total[$__rate_interval]))', "{{exporter}} metrics", ref="C"),
          prom('sum by (receiver) (rate(otelcol_receiver_refused_spans_total[$__rate_interval]))', "{{receiver}} refused spans", ref="D")],
         unit="short", desc="Anything above zero means telemetry is being lost between the gateway and a backend."), 12, 7, 12)
next_line(7)

dash = {
    "uid": "buildkite-ci",
    "title": "Buildkite CI",
    "description": "Job phases, failures and logs from Buildkite agent OpenTelemetry data, plus agent-stack-k8s controller health.",
    "tags": ["buildkite", "otel"],
    "timezone": "browser",
    "editable": True,
    "graphTooltip": 1,
    "refresh": "1m",
    "schemaVersion": 39,
    "time": {"from": "now-6h", "to": "now"},
    "templating": {"list": [
        {"name": "pipeline", "label": "Pipeline", "type": "query", "datasource": PROM,
         "query": {"query": f"label_values({SM}_calls_total, buildkite_pipeline)", "refId": "pipeline"},
         "definition": f"label_values({SM}_calls_total, buildkite_pipeline)",
         "refresh": 2, "multi": True, "includeAll": True, "allValue": ".*",
         "current": {"text": "All", "value": "$__all"}, "sort": 1},
        {"name": "search", "label": "Log search", "type": "textbox", "query": "", "current": {"text": "", "value": ""}},
    ]},
    "links": [],
    "annotations": {"list": []},
    "panels": panels,
}
json.dump(dash, open(sys.argv[1], "w"), indent=2)
print(f"{len(panels)} panels -> {sys.argv[1]}")
