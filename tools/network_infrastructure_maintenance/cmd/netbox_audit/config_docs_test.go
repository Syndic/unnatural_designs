package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"

	audit "github.com/Syndic/unnatural_designs/tools/network_infrastructure_maintenance/internal/audit"
)

// The policy schema is hand-copied into CONFIG.md and CHECKS.md, and nothing about a wrong copy
// fails at runtime: encoding/json drops unknown keys silently, so a config written from a stale
// doc parses clean and leaves every knob at its default. CONFIG.md had drifted a whole level that
// way — every section documented at the top level after the real ones moved under `rules`.
//
// These tests decode the docs' own JSON with DisallowUnknownFields, which turns that silent drop
// into the error it should have been.

// jsonBlock matches a fenced ```json block, capturing its body.
var jsonBlock = regexp.MustCompile("(?s)```json\n(.*?)\n```")

// knobSection matches the lead-in CHECKS.md puts above each knob list, capturing the rules section
// the bullets below it belong to.
var knobSection = regexp.MustCompile("under `rules\\.([a-z-]+)`")

// knobField matches a knob bullet: a list item whose first token is a backticked field name.
var knobField = regexp.MustCompile("^- `([a-z0-9_]+)`")

func readDoc(t *testing.T, name string) string {
	t.Helper()
	// go_test runs in its package directory inside runfiles, where `data` lands beside the test.
	body, err := os.ReadFile(name) //nolint:gosec // callers pass literal doc names, not input
	if err != nil {
		cwd, _ := os.Getwd()
		t.Fatalf("read %s (cwd %s): %v", name, cwd, err)
	}
	return string(body)
}

// decodeStrict decodes body into an auditConfig, rejecting any key the schema does not define.
func decodeStrict(body string) error {
	dec := json.NewDecoder(strings.NewReader(body))
	dec.DisallowUnknownFields()
	var cfg auditConfig
	return dec.Decode(&cfg)
}

// TestConfigDocsBlocksMatchSchema decodes every JSON block in CONFIG.md against the real config
// type. Section examples are object-body fragments (`"rules": {…}`), so they are wrapped first.
func TestConfigDocsBlocksMatchSchema(t *testing.T) {
	blocks := jsonBlock.FindAllStringSubmatch(readDoc(t, "CONFIG.md"), -1)
	if len(blocks) == 0 {
		t.Fatal("no ```json blocks found in CONFIG.md")
	}
	for _, block := range blocks {
		body := strings.TrimSpace(block[1])
		if !strings.HasPrefix(body, "{") {
			body = "{" + body + "}"
		}
		if err := decodeStrict(body); err != nil {
			t.Errorf("CONFIG.md json block does not match the policy schema: %v\n%s", err, body)
		}
	}
}

// TestConfigDocsExampleLoads runs CONFIG.md's full example through the real loader and asserts the
// values it documents actually land. A block can satisfy the schema and still be inert if the
// loader is reached some other way, so this closes the loop the type check leaves open.
func TestConfigDocsExampleLoads(t *testing.T) {
	blocks := jsonBlock.FindAllStringSubmatch(readDoc(t, "CONFIG.md"), -1)
	// The example config is the last block and the only complete document besides the skeleton.
	body := strings.TrimSpace(blocks[len(blocks)-1][1])

	path := filepath.Join(t.TempDir(), "example.json")
	if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
		t.Fatalf("write example config: %v", err)
	}
	cfg, err := loadAuditConfig(path, true)
	if err != nil {
		t.Fatalf("CONFIG.md's example config does not load: %v", err)
	}

	if got := cfg.Rules.PoEPower.UnknownTypePolicy; got != audit.POEUnknownTypeIgnore {
		t.Errorf("UnknownTypePolicy = %q, want %q — the example sets it explicitly", got, audit.POEUnknownTypeIgnore)
	}
	if !contains(cfg.Rules.InterfaceVRF.WANDeviceRoles, "ISP Router") {
		t.Errorf("WANDeviceRoles = %v, want the example's added role", cfg.Rules.InterfaceVRF.WANDeviceRoles)
	}
	if !contains(cfg.Rules.RackPlacement.ExemptDeviceTags, "wall-mount") {
		t.Errorf("ExemptDeviceTags = %v, want the example's added tag", cfg.Rules.RackPlacement.ExemptDeviceTags)
	}
	// CONFIG.md tells the reader an added tag replaces the default rather than extending it.
	if !contains(cfg.Rules.RackPlacement.ExemptDeviceTags, "0u-rack-device") {
		t.Error("the example restates 0u-rack-device because the list replaces the default; it no longer does")
	}

	checks, err := selectChecks(allChecks(), cfg)
	if err != nil {
		t.Fatalf("select checks from the example config: %v", err)
	}
	for _, c := range checks {
		if c.ID() == checkDeviceTypeDrift {
			t.Error("device-type-drift ran despite the example config disabling it")
		}
	}
}

// TestChecksDocsKnobsMatchSchema resolves every knob CHECKS.md documents against the schema. The
// bullets name a bare field under a section named once in the lead-in, so both halves are checked
// by decoding a minimal document that mentions exactly that path.
func TestChecksDocsKnobsMatchSchema(t *testing.T) {
	var section string
	found := 0
	for _, line := range strings.Split(readDoc(t, "CHECKS.md"), "\n") {
		if m := knobSection.FindStringSubmatch(line); m != nil {
			section = m[1]
			continue
		}
		if section == "" {
			continue
		}
		m := knobField.FindStringSubmatch(line)
		if m == nil {
			// A knob list runs to the first line that is not one of its bullets.
			if !strings.HasPrefix(line, "  ") && strings.TrimSpace(line) != "" {
				section = ""
			}
			continue
		}
		found++
		probe, err := json.Marshal(map[string]any{
			"rules": map[string]any{section: map[string]any{m[1]: nil}},
		})
		if err != nil {
			t.Fatalf("build probe for rules.%s.%s: %v", section, m[1], err)
		}
		if err := decodeStrict(string(probe)); err != nil {
			t.Errorf("CHECKS.md documents rules.%s.%s, which the policy schema rejects: %v", section, m[1], err)
		}
	}
	// Every ruled check contributes at least one knob; a parser that silently matched nothing
	// would otherwise pass this test while checking nothing.
	if want := 13; found != want {
		t.Errorf("matched %d knob bullets in CHECKS.md, want %d — did the knob list format change?", found, want)
	}
}

// TestConfigDocsListEveryRuledCheck keeps CONFIG.md's section list complete: a check that grows a
// rules struct but no documentation is the drift this file exists to catch, in the one direction
// decoding the docs cannot see.
func TestConfigDocsListEveryRuledCheck(t *testing.T) {
	doc := readDoc(t, "CONFIG.md")
	// Round-tripping the defaults names every section the schema defines, whatever it holds.
	body, err := json.Marshal(defaultAuditConfig())
	if err != nil {
		t.Fatalf("marshal default config: %v", err)
	}
	var top map[string]json.RawMessage
	if err := json.Unmarshal(body, &top); err != nil {
		t.Fatalf("unmarshal default config: %v", err)
	}
	var rules map[string]json.RawMessage
	if err := json.Unmarshal(top["rules"], &rules); err != nil {
		t.Fatalf("unmarshal rules: %v", err)
	}
	for section := range rules {
		if !strings.Contains(doc, "## `rules."+section+"`") {
			t.Errorf("CONFIG.md has no `## `rules.%s`` section for a check that takes rules", section)
		}
	}
}

func contains(haystack []string, needle string) bool {
	for _, v := range haystack {
		if v == needle {
			return true
		}
	}
	return false
}
