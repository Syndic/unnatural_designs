package shared

import (
	"os"
	"strings"
	"testing"
	"time"
)

func TestFormatDuration(t *testing.T) {
	tests := []struct {
		name string
		in   time.Duration
		want string
	}{
		{"zero", 0, "0s"},
		{"negative", -time.Second, "0s"},
		{"sub-millisecond", 123 * time.Microsecond, "123µs"},
		{"sub-millisecond rounded", 123456 * time.Nanosecond, "123µs"},
		{"sub-second", 250 * time.Millisecond, "250ms"},
		{"sub-second rounded", 1500 * time.Microsecond, "2ms"},
		{"seconds", 3500 * time.Millisecond, "3.5s"},
		{"minutes", 65 * time.Second, "1m5s"},
		{"hours", 2*time.Hour + 30*time.Minute, "2h30m0s"},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			if got := FormatDuration(tc.in); got != tc.want {
				t.Errorf("FormatDuration(%v) = %q, want %q", tc.in, got, tc.want)
			}
		})
	}
}

func nonTTY(t *testing.T) *os.File {
	t.Helper()
	f, err := os.CreateTemp(t.TempDir(), "nontty-*")
	if err != nil {
		t.Fatalf("CreateTemp: %v", err)
	}
	t.Cleanup(func() { _ = f.Close() })
	return f
}

func TestIsTerminal_NonTTY(t *testing.T) {
	if IsTerminal(nonTTY(t)) {
		t.Error("IsTerminal(regular file) = true, want false")
	}
}

func TestIsTerminal_StatError(t *testing.T) {
	f := nonTTY(t)
	_ = f.Close()
	if IsTerminal(f) {
		t.Error("IsTerminal(closed file) = true, want false")
	}
}

func TestNewColorizer(t *testing.T) {
	t.Setenv(EnvNoColor, "")
	t.Setenv("TERM", "xterm-256color")

	tests := []struct {
		name        string
		mode        string
		wantErr     bool
		wantEnabled bool
	}{
		{"empty defaults to auto, non-tty disables", "", false, false},
		{"auto, non-tty disables", "auto", false, false},
		{"AUTO mixed case", "Auto", false, false},
		{"auto with whitespace", "  auto  ", false, false},
		{"always", "always", false, true},
		{"never", "never", false, false},
		{"invalid", "rainbow", true, false},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			c, err := NewColorizer(tc.mode, nonTTY(t))
			if (err != nil) != tc.wantErr {
				t.Fatalf("NewColorizer err = %v, wantErr = %v", err, tc.wantErr)
			}
			if tc.wantErr {
				return
			}
			if c.enabled != tc.wantEnabled {
				t.Errorf("enabled = %v, want %v", c.enabled, tc.wantEnabled)
			}
		})
	}
}

func TestNewColorizer_AutoNoColorEnv(t *testing.T) {
	t.Setenv(EnvNoColor, "1")
	t.Setenv("TERM", "xterm-256color")
	c, err := NewColorizer("auto", nonTTY(t))
	if err != nil {
		t.Fatalf("NewColorizer: %v", err)
	}
	if c.enabled {
		t.Error("auto with NO_COLOR set: enabled = true, want false")
	}
}

func TestNewColorizer_AutoDumbTerm(t *testing.T) {
	t.Setenv(EnvNoColor, "")
	t.Setenv("TERM", TermDumb)
	c, err := NewColorizer("auto", nonTTY(t))
	if err != nil {
		t.Fatalf("NewColorizer: %v", err)
	}
	if c.enabled {
		t.Error("auto with TERM=dumb: enabled = true, want false")
	}
}

func TestColorizer_Disabled(t *testing.T) {
	c, err := NewColorizer("never", nonTTY(t))
	if err != nil {
		t.Fatalf("NewColorizer: %v", err)
	}
	for name, got := range map[string]string{
		"Pass":      c.Pass("ok"),
		"Warn":      c.Warn("ok"),
		"Fail":      c.Fail("ok"),
		"Accent":    c.Accent("ok"),
		"Track":     c.Track("ok"),
		"PassBlock": c.PassBlock("ok"),
		"WarnBlock": c.WarnBlock("ok"),
		"FailBlock": c.FailBlock("ok"),
	} {
		if got != "ok" {
			t.Errorf("%s on disabled colorizer = %q, want %q", name, got, "ok")
		}
	}
	// Tag carries its bracket text even with colors disabled — that's the
	// pipe-safe fallback.
	if got := c.Tag(StatusPass); got != "[PASS]" {
		t.Errorf("Tag(PASS) on disabled colorizer = %q, want %q", got, "[PASS]")
	}
}

func TestColorizer_Enabled(t *testing.T) {
	c, err := NewColorizer("always", nonTTY(t))
	if err != nil {
		t.Fatalf("NewColorizer: %v", err)
	}
	const reset = "\033[0m"
	cases := map[string]string{
		"Pass":   c.Pass("ok"),
		"Warn":   c.Warn("ok"),
		"Fail":   c.Fail("ok"),
		"Accent": c.Accent("ok"),
		"Track":  c.Track("ok"),
	}
	for name, got := range cases {
		if !strings.Contains(got, "ok") {
			t.Errorf("%s output %q missing payload", name, got)
		}
		if !strings.HasSuffix(got, reset) {
			t.Errorf("%s output %q missing ANSI reset suffix", name, got)
		}
		if !strings.HasPrefix(got, "\033[") {
			t.Errorf("%s output %q missing ANSI prefix", name, got)
		}
	}
	// Pass/Warn/Fail/Accent must all be distinguishable from each other.
	seen := map[string]string{}
	for name, got := range cases {
		if prior, ok := seen[got]; ok {
			t.Errorf("%s and %s produced identical wrapping %q", prior, name, got)
		}
		seen[got] = name
	}
	// Warn uses ANSI 3 so it tracks the user's terminal theme, rather than a
	// hardcoded 256-color value.
	if !strings.Contains(c.Warn("ok"), "\033[33m") {
		t.Errorf("Warn() should use ANSI 3 (\\033[33m); got %q", c.Warn("ok"))
	}
	if strings.Contains(c.Warn("ok"), "38;5;214") {
		t.Errorf("Warn() still emits the old 256-color 214; got %q", c.Warn("ok"))
	}
}

func TestColorizer_Block_BoldAnsi0FgOnColoredBg(t *testing.T) {
	c, err := NewColorizer("always", nonTTY(t))
	if err != nil {
		t.Fatalf("NewColorizer: %v", err)
	}
	cases := []struct {
		name   string
		got    string
		prefix string
		glyph  string
	}{
		{"PassBlock", c.PassBlock("✓"), "\033[42;30;1m", "✓"},
		{"WarnBlock", c.WarnBlock("!"), "\033[43;30;1m", "!"},
		{"FailBlock", c.FailBlock("✗"), "\033[41;30;1m", "✗"},
	}
	for _, tc := range cases {
		if !strings.HasPrefix(tc.got, tc.prefix) {
			t.Errorf("%s missing packed SGR prefix %q; got %q", tc.name, tc.prefix, tc.got)
		}
		if !strings.HasSuffix(tc.got, "\033[0m") {
			t.Errorf("%s missing reset suffix; got %q", tc.name, tc.got)
		}
		if !strings.Contains(tc.got, " "+tc.glyph+" ") {
			t.Errorf("%s missing space-padded glyph; got %q", tc.name, tc.got)
		}
		// Reverse-video must NOT be in use — the earlier SGR 7 approach let
		// bold land on the background in many terminals.
		if strings.Contains(tc.got, "\033[7m") {
			t.Errorf("%s still emits SGR 7 reverse-video; got %q", tc.name, tc.got)
		}
	}

	// Disabled colorizer still returns the bare glyph (no padding) so the
	// NO_COLOR output stays a single visible cell wide.
	disabled, err := NewColorizer("never", nonTTY(t))
	if err != nil {
		t.Fatalf("NewColorizer: %v", err)
	}
	if got := disabled.PassBlock("✓"); got != "✓" {
		t.Errorf("disabled PassBlock(✓) = %q, want %q (no padding under NO_COLOR)", got, "✓")
	}
}

func TestColorizer_Tag(t *testing.T) {
	enabled, err := NewColorizer("always", nonTTY(t))
	if err != nil {
		t.Fatalf("NewColorizer: %v", err)
	}
	disabled, err := NewColorizer("never", nonTTY(t))
	if err != nil {
		t.Fatalf("NewColorizer: %v", err)
	}

	expectedPrefix := map[string]string{
		StatusPass: "\033[42;30;1m",
		StatusWarn: "\033[43;30;1m",
		StatusFail: "\033[41;30;1m",
	}
	for _, status := range []string{StatusPass, StatusWarn, StatusFail} {
		bracketed := "[" + status + "]"
		if got := disabled.Tag(status); got != bracketed {
			t.Errorf("disabled Tag(%s) = %q, want %q", status, got, bracketed)
		}
		got := enabled.Tag(status)
		if !strings.HasPrefix(got, expectedPrefix[status]) {
			t.Errorf("enabled Tag(%s) missing packed SGR prefix %q; got %q", status, expectedPrefix[status], got)
		}
		// Colored form is space-padded, not bracketed — brackets only ship
		// on the NO_COLOR fallback so the pipe artifact stays readable.
		if !strings.Contains(got, " "+status+" ") {
			t.Errorf("enabled Tag(%s) missing space-padded label; got %q", status, got)
		}
		if strings.Contains(got, bracketed) {
			t.Errorf("enabled Tag(%s) should not contain brackets; got %q", status, got)
		}
	}

	// Unknown status names fall back to plain brackets — defensive default.
	if got := enabled.Tag("MAYBE"); got != "[MAYBE]" {
		t.Errorf("Tag(unknown) = %q, want %q", got, "[MAYBE]")
	}
}
