package audit

import (
	"fmt"
	"sort"
	"strings"

	netbox "github.com/Syndic/unnatural_designs/tools/network_infrastructure_maintenance/internal/netbox"
)

type WirelessNormalizationRules struct {
	SuppressIfConnectedWiredInterfaceIsComplete bool `json:"suppress_if_connected_wired_interface_is_complete"`
	RequireMode                                 bool `json:"require_mode"`
	RequireUntaggedVLAN                         bool `json:"require_untagged_vlan"`
	RequirePrimaryMAC                           bool `json:"require_primary_mac"`
}

func WirelessNormalization(s netbox.Snapshot, rules WirelessNormalizationRules) CheckResult {
	var findings []string
	for deviceID, ifaces := range s.InterfacesByDevice {
		dev := s.DevicesByID[deviceID]
		if hasRole(dev, RoleAccessPoint) || isPlanned(dev) {
			continue
		}
		wiredComplete := false
		for _, it := range ifaces {
			if isWirelessType(it.Type.Value) {
				continue
			}
			if !it.Enabled {
				continue
			}
			if wiredInterfaceComplete(it, s.IPsByInterface[it.ID]) {
				wiredComplete = true
				break
			}
		}
		for _, it := range ifaces {
			if !isWirelessType(it.Type.Value) || !it.Enabled {
				continue
			}
			if it.Mode != nil && it.UntaggedVLAN != nil && (!rules.RequirePrimaryMAC || it.PrimaryMACAddress != nil) {
				continue
			}
			if rules.SuppressIfConnectedWiredInterfaceIsComplete && wiredComplete {
				continue
			}
			missing := []string{}
			if rules.RequireMode && it.Mode == nil {
				missing = append(missing, "mode")
			}
			if rules.RequireUntaggedVLAN && it.UntaggedVLAN == nil {
				missing = append(missing, "untagged_vlan")
			}
			if rules.RequirePrimaryMAC && it.PrimaryMACAddress == nil {
				missing = append(missing, "primary_mac_address")
			}
			if len(missing) > 0 {
				findings = append(findings, fmt.Sprintf("%s is missing %s", ifaceLabel(it), strings.Join(missing, ", ")))
			}
		}
	}
	sort.Strings(findings)
	return CheckResult{Findings: findings}
}
