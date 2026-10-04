# ADR-0012: MLflow serves artifacts from a volume, no object store

- **Status:** Accepted
- **Date:** 2026-10-04
- **Phase:** 1

## Context

A model version is a few MB: booster, vocabulary, drift reference, metadata. The cluster lives 30 minutes at a time.

## Decision

The MLflow server proxies artifact access (`--serve-artifacts`) and stores them on a persistent volume; its backend store is the shared Postgres.

## Alternatives considered

- **MinIO / S3-compatible store**: another stateful service to run for a few MB of files.
- **Azure Blob from the start**: ties local development to a cloud account.

## Consequences

- ✅ One fewer service; clients only ever talk to MLflow.
- ⚠️ Artifacts die with the cluster; champion v1 is re-registered by the seed Job every session.
- 🔭 Sessions that need to keep models between runs: point `--artifacts-destination` at Azure Blob.
