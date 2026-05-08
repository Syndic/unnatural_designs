package audit

import (
	"fmt"
	"sort"

	netbox "github.com/Syndic/unnatural_designs/tools/network_infrastructure_maintenance/internal/netbox"
)

func IPVLANConsistency(s netbox.Snapshot) CheckResult {
	prefixes := parsePrefixes(s.Prefixes)
	var findings []string
	for _, ip := range s.IPAddresses {
		if ip.AssignedObjectType != netbox.ObjectTypeInterface {
			continue
		}
		it, ok := s.InterfacesByID[ip.AssignedObjectID]
		if !ok || it.Mode == nil || it.Mode.Value != VLANModeAccess || it.UntaggedVLAN == nil {
			continue
		}
		addr, ok := bareAddr(ip.Address)
		if !ok {
			continue
		}
		match := bestPrefixMatch(prefixes, addr, vrfID(ip.VRF))
		if match == nil || match.VLAN == nil {
			continue
		}
		if match.VLAN.ID != it.UntaggedVLAN.ID {
			findings = append(findings, fmt.Sprintf("%s carries %s but access VLAN is %s and best prefix VLAN is %s", ifaceLabel(it), ip.Address, it.UntaggedVLAN.Name, match.VLAN.Name))
		}
	}
	sort.Strings(findings)
	return CheckResult{Findings: findings}
}
