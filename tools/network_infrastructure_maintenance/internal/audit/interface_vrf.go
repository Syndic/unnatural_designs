package audit

import (
	"fmt"
	"sort"
	"strings"

	netbox "github.com/Syndic/unnatural_designs/tools/network_infrastructure_maintenance/internal/netbox"
)

type InterfaceVRFRules struct {
	WANDeviceRoles      []string `json:"wan_device_roles"`
	RequireOnInterfaces bool     `json:"require_on_interfaces"`
}

func InterfaceVRF(s *netbox.Snapshot, rules InterfaceVRFRules) CheckResult {
	if !rules.RequireOnInterfaces {
		return CheckResult{}
	}
	wanRoles := make(map[string]bool, len(rules.WANDeviceRoles))
	for _, r := range rules.WANDeviceRoles {
		wanRoles[strings.TrimSpace(r)] = true
	}
	var findings []string
	for _, it := range s.Interfaces {
		dev := s.DevicesByID[it.Device.ID]
		if isPlanned(dev) || !it.Enabled {
			continue
		}
		if it.VRF != nil {
			continue
		}
		if isWANInterface(it, dev, s.DevicesByID, wanRoles) {
			continue
		}
		if len(it.ConnectedEndpoints) == 0 &&
			len(s.IPsByInterface[it.ID]) == 0 &&
			!interfaceHasMAC(it) &&
			it.Mode == nil &&
			it.UntaggedVLAN == nil {
			continue
		}
		findings = append(findings, fmt.Sprintf("%s is missing VRF", ifaceLabel(it)))
	}
	sort.Strings(findings)
	return CheckResult{Findings: findings}
}
