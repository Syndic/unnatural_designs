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

	type vlanPrefix struct {
		netboxPrefix netbox.Prefix
		parsedPrefix netip.Prefix
	}
	var prefixes []vlanPrefix
	var findings []string
	for _, p := range s.Prefixes {
		if p.VLAN == nil {
			continue
		}
		parsedPrefix, err := netip.ParsePrefix(p.Prefix)
		if err != nil {
			findings = append(
				findings,
				fmt.Sprintf("prefix %s could not be parsed while checking honeypot coverage", p.Prefix),
			)
			continue
		}
		prefixes = append(prefixes, vlanPrefix{netboxPrefix: p, parsedPrefix: parsedPrefix})
	}

	for _, vp := range prefixes {
		covered := false
		for _, hp := range honeypots {
			if vrfID(hp.VRF) != vrfID(vp.netboxPrefix.VRF) {
				continue
			}
			addr, ok := bareAddr(hp.Address)
			if ok && vp.parsedPrefix.Contains(addr) {
				covered = true
				break
			}
		}
		if !covered {
			findings = append(
				findings,
				fmt.Sprintf("%s (%s) has no honeypot IP", vp.netboxPrefix.Prefix, vp.netboxPrefix.VLAN.Name),
			)
		}
	}

	for _, hp := range honeypots {
		addr, ok := bareAddr(hp.Address)
		if !ok {
			findings = append(
				findings,
				fmt.Sprintf("%s is tagged honeypot but could not be parsed", hp.Address),
			)
			continue
		}
		matched := false
		for _, vp := range prefixes {
			if vrfID(hp.VRF) == vrfID(vp.netboxPrefix.VRF) && vp.parsedPrefix.Contains(addr) {
				matched = true
				break
			}
		}
		if !matched {
			findings = append(
				findings,
				fmt.Sprintf("%s is tagged honeypot but is not inside any VLAN-backed prefix", hp.Address),
			)
		}
	}

	sort.Strings(findings)
	return CheckResult{Findings: findings}
}
