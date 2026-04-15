package audit

// CheckResult holds the outcome of a single audit check.
type CheckResult struct {
	Name     string        `json:"name"`
	Findings []string      `json:"findings"`
	Extra    []DriftRecord `json:"extra,omitempty"`
}

// DriftRecord describes a device that drifts from its expected component set.
type DriftRecord struct {
	Device  string   `json:"device"`
	Model   string   `json:"model"`
	Details []string `json:"details"`
}
