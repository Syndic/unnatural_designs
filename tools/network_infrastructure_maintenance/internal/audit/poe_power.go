package audit

import (
	"fmt"
	"sort"

	netbox "github.com/Syndic/unnatural_designs/tools/network_infrastructure_maintenance/internal/netbox"
)

type POEPowerRules struct {
	CheckPoweredDeviceSupply bool   `json:"check_powered_device_supply"`
	RequirePSEModeOnPeer     bool   `json:"require_pse_mode_on_peer"`
	UnknownTypePolicy        string `json:"unknown_type_policy"`
}

func POEPower(s netbox.Snapshot, rules POEPowerRules) CheckResult {
	if !rules.CheckPoweredDeviceSupply {
		return CheckResult{Name: "PoE Power Sufficiency"}
	}
	var findings []string
	for _, it := range s.Interfaces {
		if choiceValue(it.POEMode) != POEModePD || !it.Enabled || len(it.ConnectedEndpoints) == 0 {
			continue
		}
		requiredType := choiceValue(it.POEType)
		matchedPeer := false
		for _, ep := range it.ConnectedEndpoints {
			peer, ok := s.InterfacesByID[ep.ID]
			if !ok {
				continue
			}
			matchedPeer = true
			if rules.RequirePSEModeOnPeer && choiceValue(peer.POEMode) != POEModePSE {
				findings = append(findings, fmt.Sprintf("%s %s requires PoE but peer %s %s is not modeled as a PSE interface", it.Device.Name, it.Name, peer.Device.Name, peer.Name))
				continue
			}
			supplyType := choiceValue(peer.POEType)
			ok, reason := poeSupplySufficient(supplyType, requiredType, rules)
			if !ok {
				if reason == "" {
					reason = "insufficient PoE type"
				}
				findings = append(findings, fmt.Sprintf("%s %s requires %s but is powered by %s %s (%s): %s", it.Device.Name, it.Name, blank(requiredType), peer.Device.Name, peer.Name, blank(supplyType), reason))
			}
		}
		if !matchedPeer {
			findings = append(findings, fmt.Sprintf("%s %s requires PoE but its connected peer interface was not available in the snapshot", it.Device.Name, it.Name))
		}
	}
	sort.Strings(findings)
	return CheckResult{Name: "PoE Power Sufficiency", Findings: findings}
}
