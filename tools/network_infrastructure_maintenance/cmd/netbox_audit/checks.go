package main

import (
	"context"
	"fmt"
	"sort"
	"sync"
	"time"

	audit "github.com/Syndic/unnatural_designs/tools/network_infrastructure_maintenance/internal/audit"
	netbox "github.com/Syndic/unnatural_designs/tools/network_infrastructure_maintenance/internal/netbox"
	"github.com/Syndic/unnatural_designs/tools/network_infrastructure_maintenance/internal/ui/progress"
)

// checkID is the stable identifier for a registered audit. It is the wire
// format used in netbox_audit.config.json under checks.enabled / checks.disabled.
type checkID string

const (
	checkRequiredDeviceFields  checkID = "required-device-fields"
	checkDeviceLocations       checkID = "device-locations"
	checkParentPlacement       checkID = "parent-placement"
	checkRackPlacement         checkID = "rack-placement"
	checkDeviceTypeDrift       checkID = "device-type-drift"
	checkHoneypots             checkID = "honeypots"
	checkWirelessNormalization checkID = "wireless-normalization"
	checkPoEPower              checkID = "poe-power"
	checkInterfaceVRF          checkID = "interface-vrf"
	checkPrivateIPVRF          checkID = "private-ip-vrf"
	checkIPVLAN                checkID = "ip-vlan"
	checkCables                checkID = "cables"
	checkPatchPanel            checkID = "patch-panel"
	checkModules               checkID = "modules"
	checkMACs                  checkID = "macs"
	checkDHCPReservations      checkID = "dhcp-reservations"
	checkPlannedDevices        checkID = "planned-devices"
	checkSwitchLinkSymmetry    checkID = "switch-link-symmetry"
)

// Check is a single registered audit. The registry below uses [plainCheck] for
// audits that need only the snapshot, and [ruledCheck] for audits that read a
// typed rules struct from the audit config.
type Check struct {
	id   checkID
	name string
	run  func(context.Context, *netbox.Snapshot, auditConfig) audit.CheckResult
}

func (c Check) ID() checkID  { return c.id }
func (c Check) Name() string { return c.name }
func (c Check) Run(ctx context.Context, snap *netbox.Snapshot, cfg auditConfig) audit.CheckResult {
	return c.run(ctx, snap, cfg)
}

// plainCheck registers an audit that needs only the snapshot.
func plainCheck(id checkID, name string, fn func(*netbox.Snapshot) audit.CheckResult) Check {
	return Check{
		id:   id,
		name: name,
		run: func(_ context.Context, s *netbox.Snapshot, _ auditConfig) audit.CheckResult {
			return fn(s)
		},
	}
}

// ruledCheck registers an audit that needs a typed rules slice from the audit config.
// getRules pulls the audit's specific rules struct out of the shared auditConfig.
func ruledCheck[R any](id checkID, name string, getRules func(auditConfig) R, fn func(*netbox.Snapshot, R) audit.CheckResult) Check {
	return Check{
		id:   id,
		name: name,
		run: func(_ context.Context, s *netbox.Snapshot, cfg auditConfig) audit.CheckResult {
			return fn(s, getRules(cfg))
		},
	}
}

func allChecks() []Check {
	return []Check{
		plainCheck(checkRequiredDeviceFields, "Required Device Fields", audit.RequiredDeviceFields),
		plainCheck(checkDeviceLocations, "Device Locations", audit.DeviceLocations),
		plainCheck(checkParentPlacement, "Parent Placement Consistency", audit.ParentPlacement),
		ruledCheck(checkRackPlacement, "Rack Placement",
			func(c auditConfig) audit.RackPlacementRules { return c.Rules.RackPlacement },
			audit.RackPlacement),
		plainCheck(checkDeviceTypeDrift, "Device Type Drift", audit.DeviceTypeDrift),
		plainCheck(checkHoneypots, "Honeypot Coverage", audit.Honeypots),
		ruledCheck(checkWirelessNormalization, "Wireless Normalization",
			func(c auditConfig) audit.WirelessNormalizationRules { return c.Rules.WirelessNormalization },
			audit.WirelessNormalization),
		ruledCheck(checkPoEPower, "PoE Power Sufficiency",
			func(c auditConfig) audit.POEPowerRules { return c.Rules.PoEPower },
			audit.POEPower),
		ruledCheck(checkInterfaceVRF, "Interface VRF Coverage",
			func(c auditConfig) audit.InterfaceVRFRules { return c.Rules.InterfaceVRF },
			audit.InterfaceVRF),
		ruledCheck(checkPrivateIPVRF, "Private IP VRF Coverage",
			func(c auditConfig) audit.PrivateIPVRFRules { return c.Rules.PrivateIPVRF },
			audit.PrivateIPVRF),
		plainCheck(checkIPVLAN, "IP / VLAN Consistency", audit.IPVLANConsistency),
		plainCheck(checkCables, "Cable Consistency", audit.Cables),
		plainCheck(checkPatchPanel, "Patch Panel Continuity", audit.PatchPanelContinuity),
		plainCheck(checkModules, "Module Consistency", audit.ModuleConsistency),
		plainCheck(checkMACs, "MAC Consistency", audit.MACConsistency),
		plainCheck(checkDHCPReservations, "DHCP Reservations", audit.DHCPReservations),
		plainCheck(checkPlannedDevices, "Planned Device Hygiene", audit.PlannedDevices),
		plainCheck(checkSwitchLinkSymmetry, "Switch Link Symmetry", audit.SwitchLinkSymmetry),
	}
}

func selectChecks(registry []Check, cfg auditConfig) ([]Check, error) {
	byID := make(map[checkID]Check, len(registry))
	for _, check := range registry {
		if _, exists := byID[check.ID()]; exists {
			return nil, fmt.Errorf("duplicate check id %q", check.ID())
		}
		byID[check.ID()] = check
	}
	disabled := make(map[checkID]bool, len(cfg.Checks.Disabled))
	for _, id := range cfg.Checks.Disabled {
		if _, ok := byID[id]; !ok {
			return nil, fmt.Errorf("unknown check id %q", id)
		}
		disabled[id] = true
	}
	if len(cfg.Checks.Enabled) == 0 {
		selected := make([]Check, 0, len(registry))
		for _, check := range registry {
			if !disabled[check.ID()] {
				selected = append(selected, check)
			}
		}
		return selected, nil
	}
	selected := make([]Check, 0, len(cfg.Checks.Enabled))
	for _, id := range cfg.Checks.Enabled {
		check, ok := byID[id]
		if !ok {
			return nil, fmt.Errorf("unknown check id %q", id)
		}
		if disabled[id] {
			continue
		}
		selected = append(selected, check)
	}
	return selected, nil
}

func runAudit(ctx context.Context, snap *netbox.Snapshot, cfg auditConfig, checks []Check, reporter progress.Reporter) report {
	start := time.Now()
	results := make([]audit.CheckResult, len(checks))
	timings := make([]checkTiming, len(checks))
	type checkOutput struct {
		index  int
		result audit.CheckResult
		timing checkTiming
	}
	out := make(chan checkOutput, len(checks))
	var wg sync.WaitGroup
	for i, check := range checks {
		i, check := i, check
		wg.Add(1)
		go func() {
			defer wg.Done()
			started := time.Now()
			result := check.Run(ctx, snap, cfg)
			result.Name = check.Name()
			duration := time.Since(started)
			findingCount := len(result.Findings)
			for _, drift := range result.Extra {
				findingCount += len(drift.Details)
			}
			out <- checkOutput{
				index:  i,
				result: result,
				timing: checkTiming{ID: check.ID(), Name: check.Name(), Duration: duration, Findings: findingCount},
			}
		}()
	}
	go func() {
		wg.Wait()
		close(out)
	}()
	completed := 0
	withFindings := 0
	for item := range out {
		results[item.index] = item.result
		timings[item.index] = item.timing
		completed++
		if item.timing.Findings > 0 {
			withFindings++
		}
		reporter.CheckCompleted(completed, len(checks), item.timing.Name, item.timing.Findings, item.timing.Duration)
	}
	reporter.ChecksComplete(len(checks), withFindings, time.Since(start))

	return report{
		Snapshot: snapshotMeta{Attempts: snap.SnapshotAttempts, Change: snap.LatestChange},
		Checks:   results,
		Timing: reportTiming{
			Total:    time.Since(start),
			Snapshot: snap.LoadStats,
			Checks:   timings,
		},
	}
}

func sortTimingDescending[T any](in []T, duration func(T) time.Duration) []T {
	out := append([]T(nil), in...)
	sort.Slice(out, func(i, j int) bool {
		return duration(out[i]) > duration(out[j])
	})
	return out
}
