package progress

import (
	"bytes"
	"strings"
	"testing"
	"time"

	netbox "github.com/Syndic/unnatural_designs/tools/network_infrastructure_maintenance/internal/netbox"
	"github.com/Syndic/unnatural_designs/tools/network_infrastructure_maintenance/internal/shared"
)

// newTestPlainReporter constructs a plainReporter that writes to buf with a
// disabled colorizer (zero-value Colorizer has color disabled).
func newTestPlainReporter(buf *bytes.Buffer) *plainReporter {
	return &plainReporter{w: buf, colors: shared.Colorizer{}}
}

func TestPlainReporter_NoStartedLines(t *testing.T) {
	var buf bytes.Buffer
	r := newTestPlainReporter(&buf)

	if cb := r.SnapshotTaskStart("devices"); cb != nil {
		t.Errorf("SnapshotTaskStart returned non-nil callback (%v); plain mode must not surface per-page progress", cb)
	}
	if got := buf.String(); got != "" {
		t.Errorf("SnapshotTaskStart wrote %q; plain mode must not emit started lines", got)
	}
}

func TestPlainReporter_OneLinePerCompletion(t *testing.T) {
	var buf bytes.Buffer
	r := newTestPlainReporter(&buf)

	r.SnapshotTaskComplete(1, 2, netbox.FetchTiming{Name: "devices", Items: 168, Requests: 1, Pages: 1, Duration: 800 * time.Millisecond}, 1)
	r.SnapshotTaskComplete(2, 2, netbox.FetchTiming{Name: "interfaces", Items: 401, Requests: 1, Pages: 1, Duration: 1200 * time.Millisecond}, 2)

	lines := splitNonEmpty(buf.String())
	if len(lines) != 2 {
		t.Fatalf("got %d lines, want 2 (one per completion); output:\n%s", len(lines), buf.String())
	}
	if !strings.Contains(lines[0], "devices") || !strings.Contains(lines[0], "168 items") {
		t.Errorf("first line missing expected fields: %q", lines[0])
	}
	if !strings.Contains(lines[1], "interfaces") || !strings.Contains(lines[1], "401 items") {
		t.Errorf("second line missing expected fields: %q", lines[1])
	}
}

func TestPlainReporter_PerCheckCompletionsAreSilent(t *testing.T) {
	var buf bytes.Buffer
	r := newTestPlainReporter(&buf)

	r.CheckCompleted(1, 18, "rack-placement", 2, 12*time.Millisecond)
	r.CheckCompleted(2, 18, "device-locations", 0, 8*time.Millisecond)

	if got := buf.String(); got != "" {
		t.Errorf("CheckCompleted wrote %q; plain mode coalesces per-check output into the final summary", got)
	}
}

func TestPlainReporter_ChecksCompleteSummary(t *testing.T) {
	var buf bytes.Buffer
	r := newTestPlainReporter(&buf)

	r.ChecksStart(18)
	r.ChecksComplete(18, 3, 250*time.Millisecond)

	lines := splitNonEmpty(buf.String())
	if len(lines) != 2 {
		t.Fatalf("got %d lines, want 2 (start + complete); output:\n%s", len(lines), buf.String())
	}
	if !strings.Contains(lines[1], "complete") || !strings.Contains(lines[1], "3 with findings") {
		t.Errorf("summary line missing expected fields: %q", lines[1])
	}
}

func TestPlainReporter_NoColorWhenColorizerDisabled(t *testing.T) {
	var buf bytes.Buffer
	r := newTestPlainReporter(&buf)

	r.Startupf("starting %s", "audit")
	r.SnapshotAttemptStart(1, 5, 25)
	r.SnapshotLoadError(1, 5, errString("boom"))
	r.ChecksComplete(18, 3, 100*time.Millisecond)

	if strings.Contains(buf.String(), "\x1b[") {
		t.Errorf("output contains ANSI escape sequence with disabled colorizer:\n%s", buf.String())
	}
}

func TestPlainReporter_CloseIsNoop(t *testing.T) {
	var buf bytes.Buffer
	r := newTestPlainReporter(&buf)

	if err := r.Close(); err != nil {
		t.Errorf("Close: %v", err)
	}
	if err := r.Close(); err != nil {
		t.Errorf("second Close: %v", err)
	}
}

// errString is a tiny error helper so we don't have to pull in errors.New
// just for one call site.
type errString string

func (e errString) Error() string { return string(e) }

func splitNonEmpty(s string) []string {
	raw := strings.Split(strings.TrimRight(s, "\n"), "\n")
	out := raw[:0]
	for _, line := range raw {
		if line != "" {
			out = append(out, line)
		}
	}
	return out
}
