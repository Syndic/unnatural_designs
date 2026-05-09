package progress

import (
	"os"
	"testing"
	"time"

	netbox "github.com/Syndic/unnatural_designs/tools/network_infrastructure_maintenance/internal/netbox"
	"github.com/Syndic/unnatural_designs/tools/network_infrastructure_maintenance/internal/shared"
)

func netboxFetchTimingForTest() netbox.FetchTiming {
	return netbox.FetchTiming{Name: "x", Items: 1, Requests: 1, Pages: 1, Duration: time.Millisecond}
}

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

func TestNewRichReturnsRichReporter(t *testing.T) {
	r, w, err := os.Pipe()
	if err != nil {
		t.Fatalf("os.Pipe: %v", err)
	}
	defer func() { _ = r.Close() }()
	defer func() { _ = w.Close() }()

	rep := New(w, ModeRich, shared.Colorizer{})
	defer func() { _ = rep.Close() }()

	if _, ok := rep.(*richReporter); !ok {
		t.Errorf("New(_, ModeRich) returned %T, want *richReporter", rep)
	}
}

func TestNewPlainReturnsPlainReporter(t *testing.T) {
	r, w, err := os.Pipe()
	if err != nil {
		t.Fatalf("os.Pipe: %v", err)
	}
	defer func() { _ = r.Close() }()
	defer func() { _ = w.Close() }()

	rep := New(w, ModePlain, shared.Colorizer{})
	defer func() { _ = rep.Close() }()

	if _, ok := rep.(*plainReporter); !ok {
		t.Errorf("New(_, ModePlain) returned %T, want *plainReporter", rep)
	}
}

// TestOffReporter_AllMethodsAreNoops exercises every offReporter method so its
// no-op surface is covered and any future divergence surfaces in coverage.
func TestOffReporter_AllMethodsAreNoops(t *testing.T) {
	var rep Reporter = offReporter{}

	rep.Startupf("init %s", "x")
	rep.AnnounceChecks([]string{"a", "b"})
	rep.SnapshotAttemptStart(1, 3, 5)
	if cb := rep.SnapshotTaskStart("devices"); cb != nil {
		t.Errorf("offReporter.SnapshotTaskStart returned non-nil callback")
	}
	rep.SnapshotTaskComplete(1, 5, netboxFetchTimingForTest(), 1)
	rep.SnapshotLoadError(1, 3, errString("boom"))
	rep.SnapshotLoadRetryDelay(0)
	rep.ChecksStart(2)
	rep.CheckCompleted(1, 2, "x", 0, 0)
	rep.ChecksComplete(2, 0, 0)
	if err := rep.Close(); err != nil {
		t.Errorf("offReporter.Close: %v", err)
	}
}
