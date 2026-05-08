package audit

import (
	"fmt"
	"sort"

	netbox "github.com/Syndic/unnatural_designs/tools/network_infrastructure_maintenance/internal/netbox"
)

func RequiredDeviceFields(s *netbox.Snapshot) CheckResult {
	var findings []string
	for _, d := range s.Devices {
		if d.Site == nil {
			findings = append(findings, fmt.Sprintf("%s is missing site", d.Name))
		}
		if d.Role.Name == "" {
			findings = append(findings, fmt.Sprintf("%s is missing role", d.Name))
		}
		if d.Status.Value == "" {
			findings = append(findings, fmt.Sprintf("%s is missing status", d.Name))
		}
	}
	sort.Strings(findings)
	return CheckResult{Findings: findings}
}
