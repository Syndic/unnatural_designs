package main

import (
	"bytes"
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	audit "github.com/Syndic/unnatural_designs/tools/network_infrastructure_maintenance/internal/audit"
	netbox "github.com/Syndic/unnatural_designs/tools/network_infrastructure_maintenance/internal/netbox"
	"github.com/Syndic/unnatural_designs/tools/network_infrastructure_maintenance/internal/shared"
	"github.com/Syndic/unnatural_designs/tools/network_infrastructure_maintenance/internal/ui/progress"
)

func TestDefaultAuditConfig(t *testing.T) {
	cfg := defaultAuditConfig()
	if !cfg.Rules.InterfaceVRF.RequireOnInterfaces {
		t.Error("InterfaceVRF.RequireOnInterfaces should be true by default")
	}
	if !cfg.Rules.PrivateIPVRF.RequireOnPrivateIPs {
		t.Error("PrivateIPVRF.RequireOnPrivateIPs should be true by default")
	}
	if cfg.Rules.PoEPower.UnknownTypePolicy != audit.POEUnknownTypeFail {
		t.Errorf("PoEPower.UnknownTypePolicy = %q, want %q", cfg.Rules.PoEPower.UnknownTypePolicy, audit.POEUnknownTypeFail)
	}
	if len(cfg.Rules.InterfaceVRF.WANDeviceRoles) == 0 {
		t.Error("WANDeviceRoles should be populated")
	}
	if len(cfg.Rules.RackPlacement.ExemptDeviceTags) == 0 {
		t.Error("RackPlacement.ExemptDeviceTags should be populated")
	}
}

func TestLoadAuditConfig_EmptyPath(t *testing.T) {
	cfg, err := loadAuditConfig("", false)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if cfg.Rules.PoEPower.UnknownTypePolicy != audit.POEUnknownTypeFail {
		t.Error("expected default config when path is empty")
	}
}

func TestLoadAuditConfig_HappyPath(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "audit.json")
	body := `{"rules":{"poe-power":{"unknown_type_policy":"ignore"}},"checks":{"enabled":["honeypots"]}}`
	if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}
	cfg, err := loadAuditConfig(path, true)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if cfg.Rules.PoEPower.UnknownTypePolicy != audit.POEUnknownTypeIgnore {
		t.Errorf("UnknownTypePolicy = %q, want %q", cfg.Rules.PoEPower.UnknownTypePolicy, audit.POEUnknownTypeIgnore)
	}
	if len(cfg.Checks.Enabled) != 1 || cfg.Checks.Enabled[0] != "honeypots" {
		t.Errorf("Checks.Enabled = %v", cfg.Checks.Enabled)
	}
}

func TestLoadAuditConfig_BadPolicy(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "audit.json")
	body := `{"rules":{"poe-power":{"unknown_type_policy":"banana"}}}`
	if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := loadAuditConfig(path, true); err == nil {
		t.Fatal("expected error for unsupported policy")
	}
}

func TestLoadAuditConfig_MalformedJSON(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "audit.json")
	if err := os.WriteFile(path, []byte("{not json"), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := loadAuditConfig(path, true); err == nil {
		t.Fatal("expected json decode error")
	}
}

func TestLoadAuditConfig_MissingRequired(t *testing.T) {
	missing := filepath.Join(t.TempDir(), "does-not-exist.json")
	if _, err := loadAuditConfig(missing, true); err == nil {
		t.Fatal("expected error when required file is missing")
	}
}

func TestLoadAuditConfig_MissingOptional(t *testing.T) {
	missing := filepath.Join(t.TempDir(), "does-not-exist.json")
	cfg, err := loadAuditConfig(missing, false)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if cfg.Rules.PoEPower.UnknownTypePolicy != audit.POEUnknownTypeFail {
		t.Error("expected default config when optional file missing")
	}
}

func TestEnvOrDefault(t *testing.T) {
	const key = "NETBOX_AUDIT_TEST_ENV_OR_DEFAULT"
	t.Setenv(key, "")
	if got := envOrDefault(key, "fallback"); got != "fallback" {
		t.Errorf("unset: got %q, want %q", got, "fallback")
	}
	t.Setenv(key, "set-value")
	if got := envOrDefault(key, "fallback"); got != "set-value" {
		t.Errorf("set: got %q, want %q", got, "set-value")
	}
}

func makeChecks(ids ...string) []Check {
	out := make([]Check, 0, len(ids))
	for _, id := range ids {
		out = append(out, plainCheck(id, id, func(*netbox.Snapshot) audit.CheckResult {
			return audit.CheckResult{}
		}))
	}
	return out
}

func TestSelectChecks_Default(t *testing.T) {
	registry := makeChecks("a", "b", "c")
	got, err := selectChecks(registry, auditConfig{})
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 3 {
		t.Errorf("got %d checks, want 3", len(got))
	}
}

func TestSelectChecks_Disabled(t *testing.T) {
	registry := makeChecks("a", "b", "c")
	cfg := auditConfig{Checks: checksConfig{Disabled: []string{"b"}}}
	got, err := selectChecks(registry, cfg)
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 2 {
		t.Fatalf("got %d, want 2", len(got))
	}
	for _, c := range got {
		if c.ID() == "b" {
			t.Error("disabled check b should not appear")
		}
	}
}

func TestSelectChecks_EnabledExplicit(t *testing.T) {
	registry := makeChecks("a", "b", "c")
	cfg := auditConfig{Checks: checksConfig{Enabled: []string{"c", "a"}}}
	got, err := selectChecks(registry, cfg)
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 2 || got[0].ID() != "c" || got[1].ID() != "a" {
		t.Errorf("unexpected order/contents: %v", got)
	}
}

func TestSelectChecks_EnabledMinusDisabled(t *testing.T) {
	registry := makeChecks("a", "b", "c")
	cfg := auditConfig{Checks: checksConfig{Enabled: []string{"a", "b"}, Disabled: []string{"a"}}}
	got, err := selectChecks(registry, cfg)
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 1 || got[0].ID() != "b" {
		t.Errorf("got %v, want only b", got)
	}
}

func TestSelectChecks_UnknownEnabled(t *testing.T) {
	registry := makeChecks("a")
	cfg := auditConfig{Checks: checksConfig{Enabled: []string{"missing"}}}
	if _, err := selectChecks(registry, cfg); err == nil {
		t.Fatal("expected error for unknown enabled id")
	}
}

func TestSelectChecks_UnknownDisabled(t *testing.T) {
	registry := makeChecks("a")
	cfg := auditConfig{Checks: checksConfig{Disabled: []string{"missing"}}}
	if _, err := selectChecks(registry, cfg); err == nil {
		t.Fatal("expected error for unknown disabled id")
	}
}

func TestSelectChecks_DuplicateRegistry(t *testing.T) {
	registry := []Check{
		plainCheck("a", "a", func(*netbox.Snapshot) audit.CheckResult { return audit.CheckResult{} }),
		plainCheck("a", "a", func(*netbox.Snapshot) audit.CheckResult { return audit.CheckResult{} }),
	}
	if _, err := selectChecks(registry, auditConfig{}); err == nil {
		t.Fatal("expected duplicate id error")
	}
}

func TestAllChecks_NonEmptyAndUniqueIDs(t *testing.T) {
	registry := allChecks()
	if len(registry) == 0 {
		t.Fatal("allChecks returned empty registry")
	}
	seen := make(map[string]bool)
	for _, c := range registry {
		if c.ID() == "" {
			t.Errorf("check has empty ID: %+v", c)
		}
		if c.Name() == "" {
			t.Errorf("check %q has empty name", c.ID())
		}
		if seen[c.ID()] {
			t.Errorf("duplicate id %q", c.ID())
		}
		seen[c.ID()] = true
	}
}

func TestSortTimingDescending(t *testing.T) {
	type item struct {
		name string
		d    time.Duration
	}
	dur := func(i item) time.Duration { return i.d }

	t.Run("empty", func(t *testing.T) {
		got := sortTimingDescending(nil, dur)
		if len(got) != 0 {
			t.Errorf("got %v", got)
		}
	})

	t.Run("sorted", func(t *testing.T) {
		in := []item{{"a", 1}, {"b", 3}, {"c", 2}}
		got := sortTimingDescending(in, dur)
		if got[0].d != 3 || got[1].d != 2 || got[2].d != 1 {
			t.Errorf("unexpected order: %v", got)
		}
		// original must not be mutated
		if in[0].d != 1 || in[1].d != 3 || in[2].d != 2 {
			t.Errorf("original mutated: %v", in)
		}
	})

	t.Run("ties", func(t *testing.T) {
		in := []item{{"a", 5}, {"b", 5}, {"c", 1}}
		got := sortTimingDescending(in, dur)
		if got[2].d != 1 {
			t.Errorf("smallest should sort last: %v", got)
		}
	})
}

func TestTotalFindings(t *testing.T) {
	if got := totalFindings(report{}); got != 0 {
		t.Errorf("empty: got %d, want 0", got)
	}
	rep := report{Checks: []audit.CheckResult{
		{Findings: []string{"x", "y"}},
		{Findings: []string{"z"}, Extra: []audit.DriftRecord{{Details: []string{"a", "b"}}}},
	}}
	if got := totalFindings(rep); got != 5 {
		t.Errorf("got %d, want 5", got)
	}
}

func TestWriteTextReport(t *testing.T) {
	colors, err := shared.NewColorizer(shared.ColorNever, os.Stdout)
	if err != nil {
		t.Fatal(err)
	}
	rep := report{
		Snapshot: snapshotMeta{Attempts: 2, Change: netbox.ObjectChange{ID: 42}},
		Checks: []audit.CheckResult{
			{Name: "first", Findings: []string{"oops"}},
			{Name: "second", Extra: []audit.DriftRecord{{Device: "dev1", Model: "m1", Details: []string{"d1"}}}},
			{Name: "third"},
		},
		Timing: reportTiming{
			Total: 100 * time.Millisecond,
			Snapshot: netbox.SnapshotLoadStats{
				Duration:     50 * time.Millisecond,
				RequestCount: 7,
				Fetches: []netbox.FetchTiming{
					{Name: "devices", Duration: 30 * time.Millisecond, Requests: 3, Items: 100},
					{Name: "ips", Duration: 20 * time.Millisecond, Requests: 2, Items: 50},
				},
			},
			Checks: []checkTiming{
				{ID: "first", Name: "first", Duration: 5 * time.Millisecond, Findings: 1},
				{ID: "third", Name: "third", Duration: 1 * time.Millisecond},
			},
		},
	}
	var buf bytes.Buffer
	writeTextReport(&buf, rep, colors)
	out := buf.String()
	wants := []string{
		"Snapshot: 2 attempt(s), latest change #42",
		"Checks: 3",
		"Total findings: 2",
		"Snapshot collections by duration:",
		"devices",
		"Check durations:",
		"[PASS] third",
		"[WARN] first",
		"[WARN] second",
		"dev1",
	}
	for _, w := range wants {
		if !strings.Contains(out, w) {
			t.Errorf("output missing %q\n--- output ---\n%s", w, out)
		}
	}
}

func TestRunAudit_Smoke(t *testing.T) {
	reporter := progress.New(os.Stderr, progress.ModeOff, shared.Colorizer{})
	defer func() { _ = reporter.Close() }()

	called := false
	checks := []Check{
		plainCheck("smoke", "Smoke Check", func(*netbox.Snapshot) audit.CheckResult {
			called = true
			return audit.CheckResult{Findings: []string{"f1"}}
		}),
	}
	snap := &netbox.Snapshot{}
	rep := runAudit(context.Background(), snap, defaultAuditConfig(), checks, reporter)
	if !called {
		t.Error("check.Run was not invoked")
	}
	if len(rep.Checks) != 1 {
		t.Fatalf("rep.Checks len = %d, want 1", len(rep.Checks))
	}
	if rep.Checks[0].Name != "Smoke Check" {
		t.Errorf("Name = %q, want %q", rep.Checks[0].Name, "Smoke Check")
	}
	if len(rep.Timing.Checks) != 1 || rep.Timing.Checks[0].Findings != 1 {
		t.Errorf("unexpected timings: %+v", rep.Timing.Checks)
	}
}

func TestRunAudit_RuledCheckReceivesRules(t *testing.T) {
	reporter := progress.New(os.Stderr, progress.ModeOff, shared.Colorizer{})
	defer func() { _ = reporter.Close() }()

	var got audit.InterfaceVRFRules
	check := ruledCheck("ruled", "Ruled",
		func(c auditConfig) audit.InterfaceVRFRules { return c.Rules.InterfaceVRF },
		func(_ *netbox.Snapshot, r audit.InterfaceVRFRules) audit.CheckResult {
			got = r
			return audit.CheckResult{}
		})
	cfg := defaultAuditConfig()
	_ = runAudit(context.Background(), &netbox.Snapshot{}, cfg, []Check{check}, reporter)
	if !got.RequireOnInterfaces {
		t.Error("ruledCheck did not receive rules from config")
	}
}
