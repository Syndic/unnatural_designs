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

func (r InterfaceVRFRules) IsWANRole(role string) bool {
	for _, wr := range r.WANDeviceRoles {
		if strings.TrimSpace(wr) == role {
			return true
		}
	}
	return false
}

func InterfaceVRF(s netbox.Snapshot, rules InterfaceVRFRules) CheckResult {
	if !rules.RequireOnInterfaces {
		return CheckResult{Name: "Interface VRF Coverage"}
	}
	var findings []string
	for _, it := range s.Interfaces {
		dev := s.DevicesByID[it.Device.ID]
		if dev.Status.Value == DeviceStatusPlanned || !it.Enabled {
			continue
		}
		if it.VRF != nil {
			continue
		}
		if isWANInterface(it, dev, s.DevicesByID, rules) {
			continue
		}
		if len(it.ConnectedEndpoints) == 0 &&
			len(s.IPsByInterface[it.ID]) == 0 &&
			!interfaceHasMAC(it) &&
			it.Mode == nil &&
			it.UntaggedVLAN == nil {
			continue
		}
		findings = append(findings, fmt.Sprintf("%s %s is missing VRF", dev.Name, it.Name))
	}
	sort.Strings(findings)
	return CheckResult{Name: "Interface VRF Coverage", Findings: findings}
}
