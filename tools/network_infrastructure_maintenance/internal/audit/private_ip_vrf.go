package audit

import (
	"fmt"
	"sort"

	netbox "github.com/Syndic/unnatural_designs/tools/network_infrastructure_maintenance/internal/netbox"
)

type PrivateIPVRFRules struct {
	RequireOnPrivateIPs bool `json:"require_on_private_ips"`
	RequireOnPublicIPs  bool `json:"require_on_public_ips"`
}

func PrivateIPVRF(s netbox.Snapshot, rules PrivateIPVRFRules) CheckResult {
	var findings []string
	for _, ip := range s.IPAddresses {
		addr, ok := bareAddr(ip.Address)
		if !ok {
			continue
		}
		if addr.IsPrivate() {
			if rules.RequireOnPrivateIPs && ip.VRF == nil {
				findings = append(findings, fmt.Sprintf("%s is private but has no VRF", ip.Address))
			}
			continue
		}
		if rules.RequireOnPublicIPs && ip.VRF == nil {
			findings = append(findings, fmt.Sprintf("%s is public but has no VRF", ip.Address))
		}
	}
	sort.Strings(findings)
	return CheckResult{Findings: findings}
}
