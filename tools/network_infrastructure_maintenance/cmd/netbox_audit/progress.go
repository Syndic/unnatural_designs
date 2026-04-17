package main

import (
	"fmt"
	"os"
	"strings"
	"sync"
	"time"

	netbox "github.com/Syndic/unnatural_designs/tools/network_infrastructure_maintenance/internal/netbox"
	"github.com/Syndic/unnatural_designs/tools/network_infrastructure_maintenance/internal/shared"
)

type progressReporter struct {
	mu sync.Mutex
}

func newProgressReporter() *progressReporter {
	return &progressReporter{}
}

func (p *progressReporter) Printf(format string, args ...any) {
	p.mu.Lock()
	defer p.mu.Unlock()
	fmt.Fprintf(os.Stderr, format+"\n", args...)
}

func (p *progressReporter) AnnounceChecks(checks []Check) {
	ids := make([]string, 0, len(checks))
	for _, check := range checks {
		ids = append(ids, fmt.Sprintf("%s (%s)", check.Name(), check.ID()))
	}
	p.Printf("Checks selected for this run (%d): %s", len(checks), strings.Join(ids, ", "))
}

func (p *progressReporter) SnapshotAttemptStart(attempt, max, totalTasks int) {
	p.Printf("Snapshot attempt %d/%d: fetching %d collections in parallel", attempt, max, totalTasks)
}

func (p *progressReporter) SnapshotTaskStart(name string) {
	p.Printf("Snapshot fetch started: %s", name)
}

func (p *progressReporter) SnapshotTaskComplete(done, total int, stats netbox.FetchTiming, totalRequests int) {
	p.Printf("Snapshot %d/%d complete: %s (%d requests, %d items, %s). Requests so far: %d",
		done,
		total,
		stats.Name,
		stats.Requests,
		stats.Items,
		shared.FormatDuration(stats.Duration),
		totalRequests,
	)
}

func (p *progressReporter) SnapshotLoadError(attempt, maxAttempts int, err error) {
	p.Printf("Snapshot attempt %d/%d failed: %v", attempt, maxAttempts, err)
}

func (p *progressReporter) SnapshotLoadRetryDelay(delay time.Duration) {
	p.Printf("Delaying for %s before retry", shared.FormatDuration(delay))
}

func (p *progressReporter) ChecksStart(total int) {
	p.Printf("Running %d checks in parallel", total)
}

func (p *progressReporter) CheckCompleted(done, total int, timing checkTiming) {
	p.Printf("Check %d/%d complete: %s (%d findings, %s)",
		done,
		total,
		timing.Name,
		timing.Findings,
		shared.FormatDuration(timing.Duration),
	)
}

func (p *progressReporter) ChecksComplete(total int) {
	p.Printf("Check execution complete: %d/%d finished", total, total)
}
