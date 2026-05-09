package audit

import (
	"fmt"
	"sort"
	"strings"

	netbox "github.com/Syndic/unnatural_designs/tools/network_infrastructure_maintenance/internal/netbox"
)

func Cables(s *netbox.Snapshot) CheckResult {
	var findings []string
	for _, c := range s.Cables {
		if c.Type == "" {
			findings = append(findings, fmt.Sprintf("Cable #%d is missing type", c.ID))
		}
		if c.Status.Value == "" {
			findings = append(findings, fmt.Sprintf("Cable #%d is missing status", c.ID))
		}
		if len(c.ATerminations) == 0 || len(c.BTerminations) == 0 {
			findings = append(findings, fmt.Sprintf("Cable #%d is missing a termination on side %s", c.ID, missingCableSide(c)))
		}
	}
	sort.Strings(findings)
	return CheckResult{Findings: findings}
}

func missingCableSide(c netbox.Cable) string {
	sides := []string{}
	if len(c.ATerminations) == 0 {
		sides = append(sides, "A")
	}
	if len(c.BTerminations) == 0 {
		sides = append(sides, "B")
	}
	return strings.Join(sides, "+")
}
