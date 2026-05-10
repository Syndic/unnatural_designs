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
		"Pass": c.Pass("ok"),
		"Warn": c.Warn("ok"),
		"Fail": c.Fail("ok"),
	} {
		if got != "ok" {
			t.Errorf("%s on disabled colorizer = %q, want %q", name, got, "ok")
		}
	}
}

func TestColorizer_Enabled(t *testing.T) {
	c, err := NewColorizer("always", nonTTY(t))
	if err != nil {
		t.Fatalf("NewColorizer: %v", err)
	}
	const reset = "\033[0m"
	cases := map[string]string{
		"Pass": c.Pass("ok"),
		"Warn": c.Warn("ok"),
		"Fail": c.Fail("ok"),
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
	if cases["Pass"] == cases["Warn"] || cases["Pass"] == cases["Fail"] || cases["Warn"] == cases["Fail"] {
		t.Error("Pass/Warn/Fail produced identical wrapping")
	}
}
