package progress

import (
	"os"
	"testing"

	"github.com/Syndic/unnatural_designs/tools/network_infrastructure_maintenance/internal/shared"
)

func TestParseMode(t *testing.T) {
	cases := []struct {
		in      string
		want    Mode
		wantErr bool
	}{
		{"", ModeAuto, false},
		{"auto", ModeAuto, false},
		{"AUTO", ModeAuto, false},
		{"  rich  ", ModeRich, false},
		{"plain", ModePlain, false},
		{"off", ModeOff, false},
		{"loud", ModeAuto, true},
	}
	for _, tc := range cases {
		got, err := ParseMode(tc.in)
		if (err != nil) != tc.wantErr {
			t.Errorf("ParseMode(%q) err=%v, wantErr=%v", tc.in, err, tc.wantErr)
		}
		if !tc.wantErr && got != tc.want {
			t.Errorf("ParseMode(%q)=%v, want %v", tc.in, got, tc.want)
		}
	}
}

// TestNewAutoSelectsPlainWhenNotTTY confirms the auto-detection picks plain
// when stderr is a regular file (not a terminal). /dev/null is a character
// device but also satisfies IsTerminal on some platforms; an os.Pipe
// guarantees a non-TTY file descriptor.
func TestNewAutoSelectsPlainWhenNotTTY(t *testing.T) {
	r, w, err := os.Pipe()
	if err != nil {
		t.Fatalf("os.Pipe: %v", err)
	}
	defer func() { _ = r.Close() }()
	defer func() { _ = w.Close() }()

	rep := New(w, ModeAuto, shared.Colorizer{})
	defer func() { _ = rep.Close() }()

	if _, ok := rep.(*plainReporter); !ok {
		t.Errorf("New(pipe, ModeAuto) returned %T, want *plainReporter", rep)
	}
}

func TestNewOffReturnsOffReporter(t *testing.T) {
	r, w, err := os.Pipe()
	if err != nil {
		t.Fatalf("os.Pipe: %v", err)
	}
	defer func() { _ = r.Close() }()
	defer func() { _ = w.Close() }()

	rep := New(w, ModeOff, shared.Colorizer{})
	defer func() { _ = rep.Close() }()

	if _, ok := rep.(offReporter); !ok {
		t.Errorf("New(_, ModeOff) returned %T, want offReporter", rep)
	}
}

func TestResolveAutoFallsBackToPlainWhenNoColorSet(t *testing.T) {
	t.Setenv(shared.EnvNoColor, "1")
	// TERM may be unset in some test envs; force a sane value so we exercise
	// the NO_COLOR branch specifically.
	t.Setenv("TERM", "xterm-256color")

	if got := Resolve(ModeAuto, os.Stderr); got != ModePlain {
		t.Errorf("Resolve(ModeAuto) with NO_COLOR set = %v, want ModePlain", got)
	}
}

func TestResolveAutoFallsBackToPlainWhenTermDumb(t *testing.T) {
	t.Setenv(shared.EnvNoColor, "")
	t.Setenv("TERM", shared.TermDumb)

	if got := Resolve(ModeAuto, os.Stderr); got != ModePlain {
		t.Errorf("Resolve(ModeAuto) with TERM=dumb = %v, want ModePlain", got)
	}
}

func TestResolvePassesThroughExplicitModes(t *testing.T) {
	for _, m := range []Mode{ModeRich, ModePlain, ModeOff} {
		if got := Resolve(m, os.Stderr); got != m {
			t.Errorf("Resolve(%v) = %v, want unchanged", m, got)
		}
	}
}
