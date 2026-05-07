package audit

import (
	"fmt"
	"sort"

	netbox "github.com/Syndic/unnatural_designs/tools/network_infrastructure_maintenance/internal/netbox"
)

func SwitchLinkSymmetry(s netbox.Snapshot) CheckResult {
	var findings []string
	for _, c := range s.Cables {
		if len(c.ATerminations) != 1 || len(c.BTerminations) != 1 {
			continue
		}
		a := c.ATerminations[0]
		b := c.BTerminations[0]
		if a.ObjectType != netbox.ObjectTypeInterface || b.ObjectType != netbox.ObjectTypeInterface {
			continue
		}
		ia, oka := s.InterfacesByID[a.ObjectID]
		ib, okb := s.InterfacesByID[b.ObjectID]
		if !oka || !okb {
			continue
		}
		da := s.DevicesByID[ia.Device.ID]
		db := s.DevicesByID[ib.Device.ID]
		if !hasRole(da, RoleSwitch) || !hasRole(db, RoleSwitch) {
			continue
		}
		if !sameSwitchPortConfig(ia, ib) {
			findings = append(findings, fmt.Sprintf("switch link cable #%d is asymmetric: %s vs %s", c.ID, ifaceLabel(ia), ifaceLabel(ib)))
		}
	}
	sort.Strings(findings)
	return CheckResult{Findings: findings}
}
