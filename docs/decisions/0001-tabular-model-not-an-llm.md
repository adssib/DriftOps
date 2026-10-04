# ADR-0001: A LightGBM tabular model, not a fine-tuned LLM

- **Status:** Accepted
- **Date:** 2026-10-04
- **Phase:** Research

## Context

The project exists to show a closed MLOps loop: detect drift, retrain, gate, roll out. The loop has to run several times inside a 30-minute session on CPU, and the target company's product monitors tabular models.

## Decision

Predict flight disruption from schedule features with LightGBM.

## Alternatives considered

- **Fine-tuned small LLM (e.g. text-to-SQL)**: a retrain takes hours and a GPU, and drift on text needs embeddings or an LLM judge with no clean ground truth.
- **Neural net on tabular data**: usually no better than boosted trees on tabular data, slower to train, no free per-prediction explanations.

## Consequences

- ✅ Retraining takes seconds to minutes on 2 vCPUs (97 s for 6.8M rows; ~20–40 s for 8 weeks).
- ✅ 350 airports handled as a native categorical; SHAP contributions per prediction for free.
- ✅ Drift is measurable with standard effect sizes.
- ⚠️ The model itself is unremarkable (AUC 0.60–0.68 out of time). The system around it is the subject.
- 🔭 If the loop needed to demonstrate GPU training or unstructured data.
