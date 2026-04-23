package audit

import (
	"fmt"
	"sort"
	"strings"

	netbox "github.com/Syndic/unnatural_designs/tools/network_infrastructure_maintenance/internal/netbox"
)

func ModuleConsistency(s netbox.Snapshot) CheckResult {
	modsByBay := map[int][]netbox.Module{}
	var findings []string
	for _, mod := range s.Modules {
		if mod.ModuleBay == nil {
			findings = append(findings, fmt.Sprintf("%s module %d has no module bay", mod.Device.Name, mod.ID))
			continue
		}
		mb, ok := s.ModuleBaysByID[mod.ModuleBay.ID]
		if !ok {
			findings = append(findings, fmt.Sprintf("%s module %d references missing module bay %d", mod.Device.Name, mod.ID, mod.ModuleBay.ID))
			continue
		}
		if mb.Device.ID != mod.Device.ID {
			findings = append(findings, fmt.Sprintf("%s module %d is installed in bay %s owned by device %s", mod.Device.Name, mod.ID, mb.Name, mb.Device.Name))
		}
		modsByBay[mod.ModuleBay.ID] = append(modsByBay[mod.ModuleBay.ID], mod)
	}
	for bayID, mods := range modsByBay {
		if len(mods) > 1 {
			ids := []string{}
			for _, mod := range mods {
				ids = append(ids, fmt.Sprintf("%d", mod.ID))
			}
			mb := s.ModuleBaysByID[bayID]
			findings = append(findings, fmt.Sprintf("%s module bay %s has multiple installed modules (%s)", mb.Device.Name, mb.Name, strings.Join(ids, ", ")))
		}
	}
	for _, mb := range s.ModuleBays {
		mods := modsByBay[mb.ID]
		if mb.InstalledModule == nil && len(mods) > 0 {
			findings = append(findings, fmt.Sprintf("%s module bay %s has module list entries but no installed_module pointer", mb.Device.Name, mb.Name))
			continue
		}
		if mb.InstalledModule != nil && len(mods) == 0 {
			findings = append(findings, fmt.Sprintf("%s module bay %s points to installed module %d but no module record references the bay", mb.Device.Name, mb.Name, mb.InstalledModule.ID))
			continue
		}
		if mb.InstalledModule != nil && len(mods) == 1 && mods[0].ID != mb.InstalledModule.ID {
			findings = append(findings, fmt.Sprintf("%s module bay %s installed module pointer is %d but bay contains module %d", mb.Device.Name, mb.Name, mb.InstalledModule.ID, mods[0].ID))
		}
	}
	sort.Strings(findings)
	return CheckResult{Findings: findings}
}
