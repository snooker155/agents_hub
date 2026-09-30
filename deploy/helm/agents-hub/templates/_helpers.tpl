{{/*
Chart name plus release name, the way every resource in this chart is named,
so two releases in one namespace never collide.
*/}}
{{- define "agents-hub.fullname" -}}
{{- printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Labels applied to every resource this chart creates.
*/}}
{{- define "agents-hub.labels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}
