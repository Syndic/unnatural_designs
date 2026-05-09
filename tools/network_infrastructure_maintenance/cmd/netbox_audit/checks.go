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

// Check is a single registered audit. The registry below uses [plainCheck] for
// audits that need only the snapshot, and [ruledCheck] for audits that read a
// typed rules struct from the audit config.
type Check struct {
	id   string
	name string
	run  func(context.Context, *netbox.Snapshot, auditConfig) audit.CheckResult
}

func (c Check) ID() string   { return c.id }
func (c Check) Name() string { return c.name }
func (c Check) Run(ctx context.Context, snap *netbox.Snapshot, cfg auditConfig) audit.CheckResult {
	return c.run(ctx, snap, cfg)
}

// plainCheck registers an audit that needs only the snapshot.
func plainCheck(id, name string, fn func(*netbox.Snapshot) audit.CheckResult) Check {
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
func ruledCheck[R any](id, name string, getRules func(auditConfig) R, fn func(*netbox.Snapshot, R) audit.CheckResult) Check {
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
		plainCheck("required-device-fields", "Required Device Fields", audit.RequiredDeviceFields),
		plainCheck("device-locations", "Device Locations", audit.DeviceLocations),
		plainCheck("parent-placement", "Parent Placement Consistency", audit.ParentPlacement),
		ruledCheck("rack-placement", "Rack Placement",
			func(c auditConfig) audit.RackPlacementRules { return c.Rules.RackPlacement },
			audit.RackPlacement),
		plainCheck("device-type-drift", "Device Type Drift", audit.DeviceTypeDrift),
		plainCheck("honeypots", "Honeypot Coverage", audit.Honeypots),
		ruledCheck("wireless-normalization", "Wireless Normalization",
			func(c auditConfig) audit.WirelessNormalizationRules { return c.Rules.WirelessNormalization },
			audit.WirelessNormalization),
		ruledCheck("poe-power", "PoE Power Sufficiency",
			func(c auditConfig) audit.POEPowerRules { return c.Rules.PoEPower },
			audit.POEPower),
		ruledCheck("interface-vrf", "Interface VRF Coverage",
			func(c auditConfig) audit.InterfaceVRFRules { return c.Rules.InterfaceVRF },
			audit.InterfaceVRF),
		ruledCheck("private-ip-vrf", "Private IP VRF Coverage",
			func(c auditConfig) audit.PrivateIPVRFRules { return c.Rules.PrivateIPVRF },
			audit.PrivateIPVRF),
		plainCheck("ip-vlan", "IP / VLAN Consistency", audit.IPVLANConsistency),
		plainCheck("cables", "Cable Consistency", audit.Cables),
		plainCheck("patch-panel", "Patch Panel Continuity", audit.PatchPanelContinuity),
		plainCheck("modules", "Module Consistency", audit.ModuleConsistency),
		plainCheck("macs", "MAC Consistency", audit.MACConsistency),
		plainCheck("dhcp-reservations", "DHCP Reservations", audit.DHCPReservations),
		plainCheck("planned-devices", "Planned Device Hygiene", audit.PlannedDevices),
		plainCheck("switch-link-symmetry", "Switch Link Symmetry", audit.SwitchLinkSymmetry),
	}
}

func selectChecks(registry []Check, cfg auditConfig) ([]Check, error) {
	byID := make(map[string]Check, len(registry))
	for _, check := range registry {
		if _, exists := byID[check.ID()]; exists {
			return nil, fmt.Errorf("duplicate check id %q", check.ID())
		}
		byID[check.ID()] = check
	}
	disabled := make(map[string]bool, len(cfg.Checks.Disabled))
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
