package audit

import (
	"fmt"
	"sort"

	netbox "github.com/Syndic/unnatural_designs/tools/network_infrastructure_maintenance/internal/netbox"
)

func ParentPlacement(s *netbox.Snapshot) CheckResult {
	var findings []string
	for _, d := range s.Devices {
		if d.ParentDevice == nil {
			continue
		}
		parent, ok := s.DevicesByID[d.ParentDevice.ID]
		if !ok {
			findings = append(findings, fmt.Sprintf("%s references missing parent device id=%d", d.Name, d.ParentDevice.ID))
			continue
		}
		if d.Site != nil && parent.Site != nil && d.Site.ID != parent.Site.ID {
			findings = append(findings, fmt.Sprintf("%s site %s differs from parent %s site %s", d.Name, d.Site.Name, parent.Name, parent.Site.Name))
		}
		switch {
		case d.Rack == nil && parent.Rack != nil:
			findings = append(findings, fmt.Sprintf("%s is missing rack while parent %s is in rack %s", d.Name, parent.Name, parent.Rack.Name))
		case d.Rack != nil && parent.Rack == nil:
			findings = append(findings, fmt.Sprintf("%s is in rack %s while parent %s has no rack", d.Name, d.Rack.Name, parent.Name))
		case d.Rack != nil && parent.Rack != nil && d.Rack.ID != parent.Rack.ID:
			findings = append(findings, fmt.Sprintf("%s rack %s differs from parent %s rack %s", d.Name, d.Rack.Name, parent.Name, parent.Rack.Name))
		}
		if d.Location != nil && parent.Location != nil && d.Location.ID != parent.Location.ID {
			findings = append(findings, fmt.Sprintf("%s location %s differs from parent %s location %s", d.Name, d.Location.Name, parent.Name, parent.Location.Name))
		}
	}
	sort.Strings(findings)
	return CheckResult{Findings: findings}
}
