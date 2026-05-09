package progress

import (
	"bytes"
	"errors"
	"io"
	"os"
	"strings"
	"sync"
	"testing"
	"time"

	netbox "github.com/Syndic/unnatural_designs/tools/network_infrastructure_maintenance/internal/netbox"
	"github.com/Syndic/unnatural_designs/tools/network_infrastructure_maintenance/internal/shared"
)

// pipeDrainer wraps an os.Pipe pair so a richReporter can write to a real
// *os.File (mpb requires one for ANSI control sequences) while tests still
// see all output. The reader goroutine drains the pipe into an internal
// buffer that String() returns.
type pipeDrainer struct {
	w    *os.File
	r    *os.File
	mu   sync.Mutex
	buf  bytes.Buffer
	done chan struct{}
}

func newPipeDrainer(t *testing.T) *pipeDrainer {
	t.Helper()
	r, w, err := os.Pipe()
	if err != nil {
		t.Fatalf("os.Pipe: %v", err)
	}
	pd := &pipeDrainer{w: w, r: r, done: make(chan struct{})}
	go func() {
		defer close(pd.done)
		buf := make([]byte, 4096)
		for {
			n, err := pd.r.Read(buf)
			if n > 0 {
				pd.mu.Lock()
				pd.buf.Write(buf[:n])
				pd.mu.Unlock()
			}
			if err != nil {
				return
			}
		}
	}()
	return pd
}

func (pd *pipeDrainer) Close(t *testing.T) string {
	t.Helper()
	_ = pd.w.Close()
	select {
	case <-pd.done:
	case <-time.After(2 * time.Second):
		t.Fatalf("pipe drainer goroutine did not finish")
	}
	_ = pd.r.Close()
	pd.mu.Lock()
	defer pd.mu.Unlock()
	return pd.buf.String()
}

func (pd *pipeDrainer) Snapshot() string {
	pd.mu.Lock()
	defer pd.mu.Unlock()
	return pd.buf.String()
}

// stripANSI removes ANSI escape sequences so substring assertions don't trip
// on cursor-control codes mpb emits.
func stripANSI(s string) string {
	var out strings.Builder
	for i := 0; i < len(s); i++ {
		if s[i] == 0x1b {
			// Skip ESC and following CSI/OSC sequence up to a letter or BEL.
			i++
			if i < len(s) && (s[i] == '[' || s[i] == ']') {
				i++
				for i < len(s) {
					c := s[i]
					if (c >= '@' && c <= '~') || c == 0x07 {
						break
					}
					i++
				}
			}
			continue
		}
		if s[i] == '\r' {
			continue
		}
		_ = out.WriteByte(s[i])
	}
	return out.String()
}

func TestRichReporter_StartupAndAnnounce(t *testing.T) {
	pd := newPipeDrainer(t)
	r := newRichReporter(pd.w, shared.Colorizer{})

	r.Startupf("hello %s", "world")
	r.AnnounceChecks([]string{"a", "b", "c"})

	if err := r.Close(); err != nil {
		t.Fatalf("Close: %v", err)
	}
	out := stripANSI(pd.Close(t))
	if !strings.Contains(out, "[netbox-audit] hello world") {
		t.Errorf("missing startup banner; got: %q", out)
	}
	if !strings.Contains(out, "[netbox-audit] 3 checks selected") {
		t.Errorf("missing announce line; got: %q", out)
	}
}

func TestRichReporter_SnapshotLifecycle(t *testing.T) {
	pd := newPipeDrainer(t)
	r := newRichReporter(pd.w, shared.Colorizer{})

	r.SnapshotAttemptStart(1, 3, 2)

	cb1 := r.SnapshotTaskStart("devices")
	if cb1 == nil {
		t.Fatalf("SnapshotTaskStart returned nil callback")
	}
	cb1(10, 100, 1) // total > 0 path
	cb1(50, 100, 2) // current update path

	cb2 := r.SnapshotTaskStart("interfaces")
	cb2(0, 0, 0) // total == 0 path: only SetCurrent runs

	r.SnapshotTaskComplete(1, 2, netbox.FetchTiming{
		Name: "devices", Items: 100, Requests: 2, Pages: 2, Duration: 500 * time.Millisecond,
	}, 2)
	r.SnapshotTaskComplete(2, 2, netbox.FetchTiming{
		Name: "interfaces", Items: 0, Requests: 1, Pages: 1, Duration: 100 * time.Millisecond,
	}, 3)

	r.ChecksStart(5)
	r.CheckCompleted(1, 5, "rack-placement", 0, 5*time.Millisecond) // skipped (no findings)
	r.CheckCompleted(2, 5, "device-locations", 3, 8*time.Millisecond)
	r.ChecksComplete(5, 1, 50*time.Millisecond)

	if err := r.Close(); err != nil {
		t.Fatalf("Close: %v", err)
	}
	out := stripANSI(pd.Close(t))

	for _, want := range []string{
		"devices",
		"interfaces",
		"Running 5 checks",
		"device-locations",
		"3 finding(s)",
		"Checks complete",
	} {
		if !strings.Contains(out, want) {
			t.Errorf("missing %q in output:\n%s", want, out)
		}
	}
	if strings.Contains(out, "rack-placement") {
		t.Errorf("rack-placement (0 findings) should be silent; got:\n%s", out)
	}
}

func TestRichReporter_RetryAttemptBanner(t *testing.T) {
	pd := newPipeDrainer(t)
	r := newRichReporter(pd.w, shared.Colorizer{})

	r.SnapshotAttemptStart(1, 3, 2)
	cb := r.SnapshotTaskStart("devices")
	cb(5, 100, 1)
	r.SnapshotLoadError(1, 3, errors.New("boom"))
	r.SnapshotLoadRetryDelay(250 * time.Millisecond)
	// attempt > 1 prints the retry banner via fmt.Fprintf to r.p.
	r.SnapshotAttemptStart(2, 3, 2)
	cb2a := r.SnapshotTaskStart("devices")
	cb2a(1, 1, 1)
	cb2b := r.SnapshotTaskStart("interfaces")
	cb2b(1, 1, 1)
	r.SnapshotTaskComplete(1, 2, netbox.FetchTiming{
		Name: "devices", Items: 1, Requests: 1, Pages: 1, Duration: 10 * time.Millisecond,
	}, 1)
	r.SnapshotTaskComplete(2, 2, netbox.FetchTiming{
		Name: "interfaces", Items: 1, Requests: 1, Pages: 1, Duration: 10 * time.Millisecond,
	}, 2)

	if err := r.Close(); err != nil {
		t.Fatalf("Close: %v", err)
	}
	out := stripANSI(pd.Close(t))

	for _, want := range []string{
		"Snapshot attempt 2/3",
		"snapshot attempt 1/3 failed: boom",
		"retrying in",
	} {
		if !strings.Contains(out, want) {
			t.Errorf("missing %q in output:\n%s", want, out)
		}
	}
}

func TestRichReporter_CloseIsIdempotent(t *testing.T) {
	pd := newPipeDrainer(t)
	r := newRichReporter(pd.w, shared.Colorizer{})

	r.Startupf("init")
	if err := r.Close(); err != nil {
		t.Fatalf("first Close: %v", err)
	}
	if err := r.Close(); err != nil {
		t.Fatalf("second Close: %v", err)
	}
	_ = pd.Close(t)
}

func TestRichReporter_ChecksCompleteWithoutFindingsUsesPassMarker(t *testing.T) {
	pd := newPipeDrainer(t)
	r := newRichReporter(pd.w, shared.Colorizer{})

	r.ChecksStart(2)
	r.ChecksComplete(2, 0, 20*time.Millisecond)

	if err := r.Close(); err != nil {
		t.Fatalf("Close: %v", err)
	}
	out := stripANSI(pd.Close(t))
	if !strings.Contains(out, "Checks complete") {
		t.Errorf("missing Checks complete line:\n%s", out)
	}
	if !strings.Contains(out, "0 with findings") {
		t.Errorf("expected 0 with findings:\n%s", out)
	}
}

// Compile-time interface assertion: richReporter implements Reporter.
var _ Reporter = (*richReporter)(nil)

// Sanity: the drainer reads everything written. Guards against the test
// helper silently swallowing writes if mpb's writer ever changes.
func TestPipeDrainer_RoundTrip(t *testing.T) {
	pd := newPipeDrainer(t)
	if _, err := io.WriteString(pd.w, "hello\n"); err != nil {
		t.Fatalf("write: %v", err)
	}
	out := pd.Close(t)
	if !strings.Contains(out, "hello") {
		t.Errorf("drainer lost write; got %q", out)
	}
}
