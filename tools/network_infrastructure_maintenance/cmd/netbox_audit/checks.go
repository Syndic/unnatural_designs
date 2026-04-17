package main

import (
	"context"
	"fmt"
	"sort"
	"sync"
	"time"

	audit "github.com/Syndic/unnatural_designs/tools/network_infrastructure_maintenance/internal/audit"
	netbox "github.com/Syndic/unnatural_designs/tools/network_infrastructure_maintenance/internal/netbox"
)

type Check interface {
	ID() string
	Name() string
	Run(context.Context, netbox.Snapshot, auditConfig) audit.CheckResult
}

type simpleCheck struct {
	id   string
	name string
	run  func(context.Context, netbox.Snapshot, auditConfig) audit.CheckResult
}

func (c simpleCheck) ID() string   { return c.id }
func (c simpleCheck) Name() string { return c.name }
func (c simpleCheck) Run(ctx context.Context, snap netbox.Snapshot, cfg auditConfig) audit.CheckResult {
	return c.run(ctx, snap, cfg)
}

func allChecks() []Check {
	return []Check{
		simpleCheck{"required-device-fields", "Required Device Fields", func(_ context.Context, s netbox.Snapshot, _ auditConfig) audit.CheckResult { return audit.RequiredDeviceFields(s) }},
		simpleCheck{"device-locations", "Device Locations", func(_ context.Context, s netbox.Snapshot, _ auditConfig) audit.CheckResult { return audit.DeviceLocations(s) }},
		simpleCheck{"parent-placement", "Parent Placement Consistency", func(_ context.Context, s netbox.Snapshot, _ auditConfig) audit.CheckResult { return audit.ParentPlacement(s) }},
		simpleCheck{"rack-placement", "Rack Placement", func(_ context.Context, s netbox.Snapshot, cfg auditConfig) audit.CheckResult { return audit.RackPlacement(s, cfg.Rules.RackPlacement) }},
		simpleCheck{"device-type-drift", "Device Type Drift", func(_ context.Context, s netbox.Snapshot, _ auditConfig) audit.CheckResult { return audit.DeviceTypeDrift(s) }},
		simpleCheck{"honeypots", "Honeypot Coverage", func(_ context.Context, s netbox.Snapshot, _ auditConfig) audit.CheckResult { return audit.Honeypots(s) }},
		simpleCheck{"wireless-normalization", "Wireless Normalization", func(_ context.Context, s netbox.Snapshot, cfg auditConfig) audit.CheckResult { return audit.WirelessNormalization(s, cfg.Rules.WirelessNormalization) }},
		simpleCheck{"poe-power", "PoE Power Sufficiency", func(_ context.Context, s netbox.Snapshot, cfg auditConfig) audit.CheckResult { return audit.POEPower(s, cfg.Rules.PoEPower) }},
		simpleCheck{"interface-vrf", "Interface VRF Coverage", func(_ context.Context, s netbox.Snapshot, cfg auditConfig) audit.CheckResult { return audit.InterfaceVRF(s, cfg.Rules.InterfaceVRF) }},
		simpleCheck{"private-ip-vrf", "Private IP VRF Coverage", func(_ context.Context, s netbox.Snapshot, cfg auditConfig) audit.CheckResult { return audit.PrivateIPVRF(s, cfg.Rules.PrivateIPVRF) }},
		simpleCheck{"ip-vlan", "IP / VLAN Consistency", func(_ context.Context, s netbox.Snapshot, _ auditConfig) audit.CheckResult { return audit.IPVLANConsistency(s) }},
		simpleCheck{"cables", "Cable Consistency", func(_ context.Context, s netbox.Snapshot, _ auditConfig) audit.CheckResult { return audit.Cables(s) }},
		simpleCheck{"patch-panel", "Patch Panel Continuity", func(_ context.Context, s netbox.Snapshot, _ auditConfig) audit.CheckResult { return audit.PatchPanelContinuity(s) }},
		simpleCheck{"modules", "Module Consistency", func(_ context.Context, s netbox.Snapshot, _ auditConfig) audit.CheckResult { return audit.ModuleConsistency(s) }},
		simpleCheck{"macs", "MAC Consistency", func(_ context.Context, s netbox.Snapshot, _ auditConfig) audit.CheckResult { return audit.MACConsistency(s) }},
		simpleCheck{"dhcp-reservations", "DHCP Reservations", func(_ context.Context, s netbox.Snapshot, _ auditConfig) audit.CheckResult { return audit.DHCPReservations(s) }},
		simpleCheck{"planned-devices", "Planned Device Hygiene", func(_ context.Context, s netbox.Snapshot, _ auditConfig) audit.CheckResult { return audit.PlannedDevices(s) }},
		simpleCheck{"switch-link-symmetry", "Switch Link Symmetry", func(_ context.Context, s netbox.Snapshot, _ auditConfig) audit.CheckResult { return audit.SwitchLinkSymmetry(s) }},
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

func runAudit(ctx context.Context, snap netbox.Snapshot, cfg auditConfig, checks []Check, reporter *progressReporter) report {
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
	for item := range out {
		results[item.index] = item.result
		timings[item.index] = item.timing
		completed++
		reporter.CheckCompleted(completed, len(checks), item.timing)
	}
	reporter.ChecksComplete(len(checks))

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
