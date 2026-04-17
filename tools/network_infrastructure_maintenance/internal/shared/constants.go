package shared

const (
	EnvNetBoxBaseURL   = "NETBOX_BASE_URL"
	EnvNetBoxToken     = "NETBOX_TOKEN"      //nolint:gosec // this is an env var, not a hardcoded value
	EnvNetBoxTokenFile = "NETBOX_TOKEN_FILE" //nolint:gosec // this is an env var, not a hardcoded value
	EnvNoColor         = "NO_COLOR"

	FormatText = "text"
	FormatJSON = "json"

	ColorAuto   = "auto"
	ColorAlways = "always"
	ColorNever  = "never"
	TermDumb    = "dumb"

	StatusPass = "PASS"
	StatusWarn = "WARN"
	StatusFail = "FAIL"
)
