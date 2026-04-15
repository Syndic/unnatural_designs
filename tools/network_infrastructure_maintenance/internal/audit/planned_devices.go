package audit

import (
	"fmt"
	"sort"

	netbox "github.com/Syndic/unnatural_designs/tools/network_infrastructure_maintenance/internal/netbox"
)

func PlannedDevices(s netbox.Snapshot) CheckResult {
	var findings []string
	for _, d := range s.Devices {
		if d.Status.Value != DeviceStatusPlanned {
			continue
		}
		for _, it := range s.InterfacesByDevice[d.ID] {
			if len(it.ConnectedEndpoints) > 0 {
				findings = append(findings, fmt.Sprintf("planned device %s has a connected interface %s", d.Name, it.Name))
			}
			if len(s.IPsByInterface[it.ID]) > 0 {
				findings = append(findings, fmt.Sprintf("planned device %s has IPs assigned to interface %s", d.Name, it.Name))
			}
			if interfaceHasMAC(it) {
				findings = append(findings, fmt.Sprintf("planned device %s has MAC data on interface %s", d.Name, it.Name))
			}
		}
	}
	sort.Strings(findings)
	return CheckResult{Name: "Planned Device Hygiene", Findings: findings}
}
