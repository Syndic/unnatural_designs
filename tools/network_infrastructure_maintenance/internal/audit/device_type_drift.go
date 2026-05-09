package audit

import (
	"fmt"
	"sort"

	netbox "github.com/Syndic/unnatural_designs/tools/network_infrastructure_maintenance/internal/netbox"
)

func DeviceTypeDrift(s *netbox.Snapshot) CheckResult {
	checks := []componentDriftCheck{
		newInterfaceDriftCheck(s.InterfaceTemplates, s.Interfaces),
		newTypedDriftCheck("Console ports", s.ConsolePortTemplates, s.ConsolePorts),
		newTypedDriftCheck("Console server ports", s.ConsoleServerPortTemplates, s.ConsoleServerPorts),
		newTypedDriftCheck("Power ports", s.PowerPortTemplates, s.PowerPorts),
		newTypedDriftCheck("Power outlets", s.PowerOutletTemplates, s.PowerOutlets),
		newTypedDriftCheck("Front ports", frontTemplatesToTyped(s.FrontPortTemplates), frontPortsToTyped(s.FrontPorts)),
		newTypedDriftCheck("Rear ports", rearTemplatesToTyped(s.RearPortTemplates), rearPortsToTyped(s.RearPorts)),
		newNamedDriftCheck("Device bays", s.DeviceBayTemplates, s.DeviceBays),
		newNamedDriftCheck("Module bays", s.ModuleBayTemplates, moduleBaysToNamed(s.ModuleBays)),
	}

	var drifts []DriftRecord
	for _, d := range s.Devices {
		var details []string
		for _, check := range checks {
			expected := check.expectedForDevice(d.DeviceType.ID, s.ModulesByDevice[d.ID])
			actual := check.actualByDevice[d.ID]
			checkDetails := compareComponentMaps(check.label, expected, actual, check.diffSpec)
			details = append(details, checkDetails...)
		}
		if len(details) > 0 {
			drifts = append(drifts, DriftRecord{Device: d.Name, Model: d.DeviceType.Model, Details: details})
		}
	}
	sort.Slice(drifts, func(i, j int) bool { return drifts[i].Device < drifts[j].Device })
	var findings []string
	if len(drifts) > 0 {
		findings = []string{fmt.Sprintf("%d devices drift from their expected components", len(drifts))}
	}
	return CheckResult{Findings: findings, Extra: drifts}
}
