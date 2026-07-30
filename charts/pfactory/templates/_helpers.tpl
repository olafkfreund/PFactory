{{/*
Expand the name of the chart.
*/}}
{{- define "pfactory.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
Truncated at 63 chars (DNS limit). Honours .Values.fullnameOverride.
*/}}
{{- define "pfactory.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Chart name + version label.
*/}}
{{- define "pfactory.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels — emitted on every resource.
*/}}
{{- define "pfactory.labels" -}}
helm.sh/chart: {{ include "pfactory.chart" . }}
{{ include "pfactory.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: pfactory
{{- end }}

{{/*
Selector labels — also used by Service + NetworkPolicy.
*/}}
{{/*
NOTE: these are the SERVING identity. Never put them on a Job or CronJob pod
template. A Service selector is a SUBSET match, so a non-serving pod carrying them
joins the Service as an endpoint, listens on nothing, and answers its share of real
traffic with connection refused while every ordinary signal stays green. Adding a
`component` label does not help — extra labels never exclude a pod. The Service
selector additionally requires `component: server`, which is what keeps
non-serving pods out; keep it that way.
*/}}
{{- define "pfactory.selectorLabels" -}}
app.kubernetes.io/name: {{ include "pfactory.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
ServiceAccount name to use.
*/}}
{{- define "pfactory.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "pfactory.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Image reference: respects .Values.image.repository + tag, defaulting
to the chart's appVersion when tag is empty.
*/}}
{{- define "pfactory.image" -}}
{{- $tag := .Values.image.tag | default .Chart.AppVersion }}
{{- printf "%s:%s" .Values.image.repository $tag }}
{{- end }}
