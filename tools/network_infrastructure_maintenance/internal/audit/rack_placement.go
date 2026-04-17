package audit

import (
	"fmt"
	"sort"
	"strings"

	netbox "github.com/Syndic/unnatural_designs/tools/network_infrastructure_maintenance/internal/netbox"
)

type RackPlacementRules struct {
	ExemptChildDevices bool     `json:"exempt_child_devices"`
	ExemptDeviceTags   []string `json:"exempt_device_tags"`
}

func (r RackPlacementRules) IsTagExempt(tags []netbox.TagRef) bool {
	for _, tag := range tags {
		for _, exempt := range r.ExemptDeviceTags {
			if strings.TrimSpace(exempt) == tag.Slug {
				return true
			}
		}
	}
	return false
}

func RackPlacement(s netbox.Snapshot, rules RackPlacementRules) CheckResult {
	var findings []string
	for _, d := range s.Devices {
		if d.Rack == nil {
			continue
		}
		if rules.ExemptChildDevices && d.ParentDevice != nil {
			continue
		}
		if rules.IsTagExempt(d.Tags) {
			continue
		}
		if d.Position == nil {
			findings = append(findings, fmt.Sprintf("%s is in rack %s without a rack position", d.Name, d.Rack.Name))
			continue
		}
		if d.Face == nil || d.Face.Value == "" {
			findings = append(findings, fmt.Sprintf("%s is in rack %s at position %.1f without a face", d.Name, d.Rack.Name, *d.Position))
		}
	}
	sort.Strings(findings)
	return CheckResult{Name: "Rack Placement", Findings: findings}
}
