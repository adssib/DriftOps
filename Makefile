# DriftOps: local workflow. `make up` builds the whole system on k3d.
SHELL := /bin/bash
CLUSTER    ?= driftops
NAMESPACE  ?= driftops
TAG        ?= dev-$(shell git rev-parse --short HEAD 2>/dev/null || echo none)-$(shell date +%s)
IMAGE      := driftops:$(TAG)
GIT_SHA    := $(shell git rev-parse --short HEAD 2>/dev/null)$(shell git diff --quiet 2>/dev/null || echo -dirty)
SCENARIO   ?= covid

.PHONY: help data champion cluster image deploy up reload down test lint smoke status logs sim-status

help:            ## list targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | sed 's/:.*##/\t/'

data:            ## fetch BTS 2018-01 → 2020-06 (2018 trains champion v1; 2020 is replayed)
	uv run python -m driftops.data 2018-01 2020-06

champion: data/bundles/v1/meta.json   ## champion v1's bundle (the research booster)
data/bundles/v1/meta.json:
	uv run python -m driftops champion --out data/bundles/v1

cluster:         ## create the k3d cluster (idempotent)
	@k3d cluster list $(CLUSTER) >/dev/null 2>&1 || \
	  k3d cluster create --config deploy/k3d.yaml --volume "$(CURDIR)/data:/data@server:0"

image:           ## build the image, prove it holds this checkout's code, load it into the cluster
	docker build --build-arg GIT_SHA=$(GIT_SHA) -t $(IMAGE) .
	@test "$$(scripts/source_digest.sh $(IMAGE))" = "$$(scripts/source_digest.sh)" \
	  || { echo "image source != checkout source: stale build"; exit 1; }
	@echo "image $(IMAGE) holds source $$(scripts/source_digest.sh)"
	k3d image import $(IMAGE) -c $(CLUSTER)
	@echo $(TAG) > .image-tag

deploy:          ## install / upgrade everything with helmfile (dev)
	IMAGE_TAG=$$(cat .image-tag) SCENARIO=$(SCENARIO) helmfile -f deploy/helmfile.yaml.gotmpl -e dev sync

up: champion cluster image deploy smoke  ## the whole system, from nothing

reload: image deploy   ## rebuild the image and roll it out

down:            ## delete the cluster
	k3d cluster delete $(CLUSTER)

test:            ## unit tests (DB tests need DRIFTOPS_TEST_DB_URL)
	uv run pytest -q

lint:            ## ruff + helm lint + kubeconform
	uv run ruff check . && uv run ruff format --check .
	helm lint deploy/helm/driftops -f deploy/helm/driftops/values-dev.yaml
	helm template driftops deploy/helm/driftops -f deploy/helm/driftops/values-dev.yaml | kubeconform -strict -summary
	helm template driftops deploy/helm/driftops -f deploy/helm/driftops/values-prod.yaml --set image.tag=ci | kubeconform -strict -summary

smoke:           ## helm test + one prediction through the ingress
	helm test driftops -n $(NAMESPACE) --logs
	curl -fsS -X POST localhost:8080/predict -H 'content-type: application/json' \
	  -d '{"carrier":"AA","origin":"ORD","dest":"LGA","flight_date":"2020-03-27","dep_time":"18:05"}'; echo

status:          ## pods, jobs, simulator progress
	kubectl -n $(NAMESPACE) get pods,jobs
	@$(MAKE) -s sim-status

sim-status:
	kubectl -n $(NAMESPACE) exec deploy/driftops-simulator -- python -c \
	  "import urllib.request;print(urllib.request.urlopen('http://localhost:8000/status').read().decode())"

logs:            ## follow the server's JSON logs
	kubectl -n $(NAMESPACE) logs -f -l app.kubernetes.io/component=server --max-log-requests 4
