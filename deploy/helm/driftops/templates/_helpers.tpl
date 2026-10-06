{{- define "driftops.name" -}}driftops{{- end -}}

{{- define "driftops.labels" -}}
app.kubernetes.io/name: driftops
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/part-of: driftops
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end -}}

{{- define "driftops.selector" -}}
app.kubernetes.io/name: driftops
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{- define "driftops.image" -}}
{{ .Values.image.repository }}:{{ required "image.tag is required (Makefile sets it in dev, CI in prod)" .Values.image.tag }}
{{- end -}}

{{/* Env every DriftOps process needs. The DB password comes from the Secret, never values. */}}
{{- define "driftops.env" -}}
- name: DRIFTOPS_DB_PASSWORD
  valueFrom:
    secretKeyRef: {name: {{ .Release.Name }}-secrets, key: db-password}
- name: DRIFTOPS_DB_URL
  value: postgresql://driftops:$(DRIFTOPS_DB_PASSWORD)@{{ .Release.Name }}-postgres:5432/driftops
- name: MLFLOW_TRACKING_URI
  value: http://{{ .Release.Name }}-mlflow:5000
- name: DRIFTOPS_MODEL_NAME
  value: {{ .Values.model.name | quote }}
- name: DRIFTOPS_PORT
  value: "8000"
{{- end -}}

{{- define "driftops.podSecurity" -}}
securityContext:
  runAsNonRoot: true
  runAsUser: 10001
  fsGroup: 10001
  seccompProfile: {type: RuntimeDefault}
{{- end -}}

{{/* Block until Postgres accepts connections: Jobs and MLflow start alongside it. */}}
{{- define "driftops.waitForPostgres" -}}
- name: wait-for-postgres
  image: {{ .Values.postgres.image }}
  command: ["sh", "-c", "until pg_isready -h {{ .Release.Name }}-postgres -U driftops; do sleep 2; done"]
  securityContext:
    runAsUser: 70
    runAsNonRoot: true
    allowPrivilegeEscalation: false
    readOnlyRootFilesystem: true
    capabilities: {drop: [ALL]}
  resources:
    requests: {cpu: 10m, memory: 16Mi}
    limits: {memory: 64Mi}
{{- end -}}
