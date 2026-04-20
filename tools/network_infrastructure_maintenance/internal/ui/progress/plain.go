package progress

import (
	"fmt"
	"io"
	"os"
	"strings"
	"sync"
	"time"

	netbox "github.com/Syndic/unnatural_designs/tools/network_infrastructure_maintenance/internal/netbox"
	"github.com/Syndic/unnatural_designs/tools/network_infrastructure_maintenance/internal/shared"
)

// plainReporter renders progress as line-oriented log output. It is the
// fallback for non-TTY destinations (CI logs, piped runs) and for users who
// pass -progress plain. It de-noises the historical output: per-task "started"
// lines are dropped, per-page progress callbacks return nil, and per-check
// completions are coalesced into a single end-of-phase summary.
type plainReporter struct {
	mu     sync.Mutex
	w      io.Writer
	colors shared.Colorizer
}

func newPlainReporter(stderr *os.File, colors shared.Colorizer) *plainReporter {
	return &plainReporter{w: stderr, colors: colors}
}

func (p *plainReporter) printf(format string, args ...any) {
	p.mu.Lock()
	defer p.mu.Unlock()
	_, _ = fmt.Fprintf(p.w, format+"\n", args...)
}

func (p *plainReporter) Startupf(format string, args ...any) {
	p.printf("[netbox-audit] "+format, args...)
}

func (p *plainReporter) AnnounceChecks(ids []string) {
	p.printf("[checks] %d selected: %s", len(ids), strings.Join(ids, ", "))
}

func (p *plainReporter) SnapshotAttemptStart(attempt, max, totalTasks int) {
	p.printf("[snapshot] attempt %d/%d  collections=%d  starting", attempt, max, totalTasks)
}

// SnapshotTaskStart returns nil — the plain renderer does not surface
// per-page progress; only completions are logged.
func (p *plainReporter) SnapshotTaskStart(string) netbox.TaskProgress { return nil }

func (p *plainReporter) SnapshotTaskComplete(done, total int, stats netbox.FetchTiming, totalRequests int) {
	p.printf("[snapshot] %-30s  %5d items  %2d req  %7s   (%d/%d)  total reqs=%d",
		stats.Name,
		stats.Items,
		stats.Requests,
		shared.FormatDuration(stats.Duration),
		done,
		total,
		totalRequests,
	)
}

func (p *plainReporter) SnapshotLoadError(attempt, maxAttempts int, err error) {
	p.printf("[snapshot] attempt %d/%d failed: %v", attempt, maxAttempts, err)
}

func (p *plainReporter) SnapshotLoadRetryDelay(delay time.Duration) {
	p.printf("[snapshot] retrying in %s", shared.FormatDuration(delay))
}

func (p *plainReporter) ChecksStart(total int) {
	p.printf("[checks] running %d in parallel", total)
}

// CheckCompleted is intentionally a no-op in plain mode — per-check timings
// already appear in the final report; printing them again here is just noise.
func (p *plainReporter) CheckCompleted(int, int, string, int, time.Duration) {}

func (p *plainReporter) ChecksComplete(total, withFindings int, dur time.Duration) {
	p.printf("[checks] complete  %d/%d  %d with findings  %s", total, total, withFindings, shared.FormatDuration(dur))
}

func (p *plainReporter) Close() error { return nil }
