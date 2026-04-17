package audit

import (
	"fmt"
	"net/netip"
	"sort"

	netbox "github.com/Syndic/unnatural_designs/tools/network_infrastructure_maintenance/internal/netbox"
)

func Honeypots(s netbox.Snapshot) CheckResult {
	var honeypots []netbox.IPAddress
	for _, ip := range s.IPAddresses {
		if hasTag(ip.Tags, TagHoneypot) {
			honeypots = append(honeypots, ip)
		}
	}

	prefixes := make([]netbox.Prefix, 0, len(s.Prefixes))
	for _, p := range s.Prefixes {
		if p.VLAN != nil {
			prefixes = append(prefixes, p)
		}
	}

	var findings []string
	for _, p := range prefixes {
		prefixNet, err := netip.ParsePrefix(p.Prefix)
		if err != nil {
			findings = append(findings, fmt.Sprintf("prefix %s could not be parsed while checking honeypot coverage", p.Prefix))
			continue
		}

		found := false
		for _, ip := range honeypots {
			if vrfID(ip.VRF) != vrfID(p.VRF) {
				continue
			}
			addr, ok := bareAddr(ip.Address)
			if !ok {
				continue
			}
			if prefixNet.Contains(addr) {
				found = true
				break
			}
		}
		if !found {
			findings = append(findings, fmt.Sprintf("%s (%s) has no honeypot IP", p.Prefix, p.VLAN.Name))
		}
	}

	for _, ip := range honeypots {
		addr, ok := bareAddr(ip.Address)
		if !ok {
			findings = append(findings, fmt.Sprintf("%s is tagged honeypot but could not be parsed", ip.Address))
			continue
		}

		matched := false
		for _, p := range prefixes {
			if vrfID(ip.VRF) != vrfID(p.VRF) {
				continue
			}
			prefixNet, err := netip.ParsePrefix(p.Prefix)
			if err != nil {
				continue
			}
			if prefixNet.Contains(addr) {
				matched = true
				break
			}
		}
		if !matched {
			findings = append(findings, fmt.Sprintf("%s is tagged honeypot but is not inside any VLAN-backed prefix", ip.Address))
		}
	}

	sort.Strings(findings)
	return CheckResult{Name: "Honeypot Coverage", Findings: findings}
}
