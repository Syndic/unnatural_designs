package audit

import (
	"fmt"
	"sort"

	netbox "github.com/Syndic/unnatural_designs/tools/network_infrastructure_maintenance/internal/netbox"
)

func DeviceLocations(s netbox.Snapshot) CheckResult {
	var findings []string
	for _, d := range s.Devices {
		if d.Location == nil {
			findings = append(findings, fmt.Sprintf("%s is missing location", d.Name))
		}
	}
	sort.Strings(findings)
	return CheckResult{Findings: findings}
}
