# Build targets for bk-tests. `make build` is what the Build step in
# .buildkite/pipeline.annotation.yaml runs.

PYTHON ?= python3
BUILD_DIR := build
DASHBOARDS := observability/dashboards

# Print every DeprecationWarning, so the Build step can annotate them.
export PYTHONWARNINGS ?= default

.PHONY: build validate pipeline dashboards test clean

## build: validate queue routing, generate the pipeline and the dashboards
build: validate pipeline dashboards

## validate: check .buildkite/queue-routing.json
validate:
	$(PYTHON) scripts/queue_router.py validate

## pipeline: generate the dynamic pipeline into build/pipeline.json
pipeline:
	mkdir -p $(BUILD_DIR)
	$(PYTHON) scripts/generate_pipeline.py > $(BUILD_DIR)/pipeline.json

## dashboards: regenerate the Grafana dashboards and fail if the committed JSON is stale
dashboards:
	mkdir -p $(BUILD_DIR)/dashboards
	$(PYTHON) $(DASHBOARDS)/make_dashboards.py $(BUILD_DIR)/dashboards
	@for f in $(BUILD_DIR)/dashboards/*.json; do \
	  cmp -s "$$f" "$(DASHBOARDS)/$$(basename $$f)" || { \
	    echo "$(DASHBOARDS)/$$(basename $$f) is out of date. Run: $(PYTHON) $(DASHBOARDS)/make_dashboards.py $(DASHBOARDS)"; \
	    exit 1; }; \
	done
	@echo "Dashboards match the generator"

## test: run the unit tests
test:
	$(PYTHON) -m unittest discover -s scripts -p 'test_*.py'

clean:
	rm -rf $(BUILD_DIR)
