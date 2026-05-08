package audit

import (
	"fmt"
	"sort"

	netbox "github.com/Syndic/unnatural_designs/tools/network_infrastructure_maintenance/internal/netbox"
)

func DHCPReservations(s netbox.Snapshot) CheckResult {
	reservedRanges := taggedRanges(s.IPRanges, TagDHCPReserved)
	poolRanges := taggedRanges(s.IPRanges, TagDHCPPool)
	var findings []string
	findings = append(findings, overlappingRanges(reservedRanges, poolRanges)...)
	for _, ip := range s.IPAddresses {
		if !hasTag(ip.Tags, TagDHCPReserved) {
			continue
		}
		if ip.AssignedObjectType != netbox.ObjectTypeInterface {
			findings = append(findings, fmt.Sprintf("%s is tagged dhcp-reserved but is not assigned to an interface", ip.Address))
			continue
		}
		it, ok := s.InterfacesByID[ip.AssignedObjectID]
		if !ok {
			findings = append(findings, fmt.Sprintf("%s is tagged dhcp-reserved but assigned interface %d was not loaded", ip.Address, ip.AssignedObjectID))
			continue
		}
		if !ipInRanges(ip, reservedRanges) {
			findings = append(findings, fmt.Sprintf("%s is tagged dhcp-reserved but is not inside any dhcp-reserved range", ip.Address))
		}
		_, ok, multi := resolveMAC(it)
		if !ok {
			findings = append(findings, fmt.Sprintf("%s is tagged dhcp-reserved but %s has no unambiguous MAC", ip.Address, ifaceLabel(it)))
		}
		if multi && it.PrimaryMACAddress == nil && it.MACAddress == "" {
			findings = append(findings, fmt.Sprintf("%s is tagged dhcp-reserved but %s has multiple MACs and no primary MAC", ip.Address, ifaceLabel(it)))
		}
	}
	sort.Strings(findings)
	return CheckResult{Findings: findings}
}
