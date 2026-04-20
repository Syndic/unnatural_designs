package main

const (
	envNetBoxAuditColor = "NETBOX_AUDIT_COLOR"
	envNetBoxAuditCfg   = "NETBOX_AUDIT_CONFIG"

	flagBaseURL        = "netbox-base-url"
	flagTokenFile      = "netbox-token-file" //nolint:gosec // this is a flag, not a hardcoded value
	flagConfig         = "config"
	flagFormat         = "format"
	flagColor          = "color"
	flagProgress       = "progress"
	flagMaxAttempts    = "max-snapshot-attempts"
	flagRetryDelay     = "snapshot-retry-delay"
	flagFailOnFindings = "fail-on-findings"
)
