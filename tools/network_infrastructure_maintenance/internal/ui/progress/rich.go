package progress

import (
	"fmt"
	"os"
	"sync"
	"time"

	"github.com/vbauerster/mpb/v8"
	"github.com/vbauerster/mpb/v8/decor"

	netbox "github.com/Syndic/unnatural_designs/tools/network_infrastructure_maintenance/internal/netbox"
	"github.com/Syndic/unnatural_designs/tools/network_infrastructure_maintenance/internal/shared"
)

// taskNameWidth is how wide the task-name decorator on per-task bars is. Wide
// enough to fit every snapshot collection name without truncation; narrow
// enough to leave room for items + elapsed on a standard 80-column terminal.
const taskNameWidth = 30

// richReporter renders a Homebrew/docker-pull-style sticky panel:
//   - One aggregate progress bar driven by completed-task count.
//   - One bar per in-flight collection. Each starts as an indeterminate
//     spinner and flips to a determinate bar once NetBox returns its first
//     page (which carries the authoritative item count). Bars are removed on
//     completion via mpb.BarRemoveOnComplete; one-line summaries for
//     completed tasks are written above the live region via the
//     io.Writer-implementing *mpb.Progress value.
//   - The check-execution phase is intentionally not given per-check bars —
//     checks run in microseconds, so a single "Running N checks…" /
//     "Checks complete" line pair is sufficient.
//
// The renderer is single-phase by lifetime: mpb owns the terminal during the
// snapshot phase and is shut down via finalizeSnapshotRenderer when the
// checks phase begins. Post-shutdown writes go straight to stderr — this
// avoids a race where mpb's async writer can drop check-phase lines if its
// internal refresh tick doesn't fire between ChecksStart and Close (the
// checks phase typically completes in microseconds).
type richReporter struct {
	w      *os.File
	colors shared.Colorizer
	p      *mpb.Progress

	mu       sync.Mutex
	agg      *mpb.Bar
	bars     map[string]*mpb.Bar
	snapDone bool // true once the mpb renderer has been torn down
	closed   bool
	closeErr error
}

func newRichReporter(stderr *os.File, colors shared.Colorizer) *richReporter {
	return &richReporter{
		w:      stderr,
		colors: colors,
		bars:   make(map[string]*mpb.Bar),
		p: mpb.New(
			mpb.WithOutput(stderr),
			mpb.WithRefreshRate(120*time.Millisecond),
			mpb.WithAutoRefresh(),
		),
	}
}

func (r *richReporter) Startupf(format string, args ...any) {
	// Startup banner is printed before any bars exist; write directly to
	// stderr so it appears at the top of the run output.
	fmt.Fprintf(r.w, "[netbox-audit] "+format+"\n", args...)
}

func (r *richReporter) AnnounceChecks(ids []string) {
	// Keep this brief — the full list will appear in the final report.
	fmt.Fprintf(r.w, "[netbox-audit] %d checks selected\n", len(ids))
}

func (r *richReporter) SnapshotAttemptStart(attempt, max, totalTasks int) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if attempt > 1 {
		// On retry, write a one-line note above the live region. The
		// previous attempt's aggregate bar will have been completed/removed
		// by SnapshotLoadError handling.
		fmt.Fprintf(r.p, "Snapshot attempt %d/%d (previous attempt detected a mid-load change)\n", attempt, max)
	}
	r.agg = r.p.New(int64(totalTasks),
		mpb.BarStyle().Lbound("[").Filler("=").Tip(">").Padding(" ").Rbound("]"),
		mpb.BarPriority(-1),
		mpb.PrependDecorators(
			decor.Name("Snapshot ", decor.WC{C: decor.DindentRight}),
			decor.CountersNoUnit("(%d/%d collections) "),
		),
		mpb.AppendDecorators(decor.Elapsed(decor.ET_STYLE_GO)),
	)
}

// SnapshotTaskStart adds a per-task bar with an unknown total (rendered as a
// spinner by mpb until SetTotal is called) and returns a closure that flips
// the bar to determinate progress on the first page response. The bar is
// retained in r.bars for explicit completion in SnapshotTaskComplete (mpb
// won't auto-complete a bar whose total stays at zero, e.g. for collections
// that genuinely return zero items).
func (r *richReporter) SnapshotTaskStart(name string) netbox.TaskProgress {
	bar := r.p.New(0,
		mpb.SpinnerStyle().PositionLeft(),
		mpb.BarRemoveOnComplete(),
		mpb.PrependDecorators(decor.Name(fmt.Sprintf("  %-*s", taskNameWidth, name))),
		mpb.AppendDecorators(
			decor.CurrentNoUnit("%d items "),
			decor.Elapsed(decor.ET_STYLE_GO),
		),
	)
	r.mu.Lock()
	r.bars[name] = bar
	r.mu.Unlock()
	return func(itemsSoFar, totalCount, _ int) {
		if totalCount > 0 {
			// SetTotal is idempotent for the same value; calling it on every
			// page after the first is cheap and keeps the bar accurate if
			// NetBox revises the count mid-fetch.
			bar.SetTotal(int64(totalCount), false)
		}
		bar.SetCurrent(int64(itemsSoFar))
	}
}

func (r *richReporter) SnapshotTaskComplete(_, _ int, stats netbox.FetchTiming, _ int) {
	// Print the one-line summary above the live region, mark the per-task
	// bar complete (which removes it via BarRemoveOnComplete), and advance
	// the aggregate bar.
	fmt.Fprintf(r.p, "  %s %-*s %5d items  %2d req  %7s\n",
		r.colors.Pass("✓"),
		taskNameWidth,
		stats.Name,
		stats.Items,
		stats.Requests,
		shared.FormatDuration(stats.Duration),
	)
	r.mu.Lock()
	if bar, ok := r.bars[stats.Name]; ok {
		// SetTotal(N, true) marks complete regardless of current item count,
		// which handles the zero-item collection case where total was never
		// set above zero.
		bar.SetTotal(int64(stats.Items), true)
		delete(r.bars, stats.Name)
	}
	if r.agg != nil {
		r.agg.Increment()
	}
	r.mu.Unlock()
}

func (r *richReporter) SnapshotLoadError(attempt, maxAttempts int, err error) {
	fmt.Fprintf(r.p, "  %s snapshot attempt %d/%d failed: %v\n",
		r.colors.Fail("✗"), attempt, maxAttempts, err)
	r.mu.Lock()
	// Drop any per-task bars that are still alive — on retry the loader will
	// create fresh ones for the next attempt.
	for name, bar := range r.bars {
		bar.Abort(true)
		delete(r.bars, name)
	}
	if r.agg != nil {
		r.agg.Abort(true)
		r.agg = nil
	}
	r.mu.Unlock()
}

func (r *richReporter) SnapshotLoadRetryDelay(delay time.Duration) {
	fmt.Fprintf(r.p, "  retrying in %s\n", shared.FormatDuration(delay))
}

// finalizeSnapshotRenderer drains the mpb renderer and switches the reporter
// into "post-snapshot" mode. After this returns, all writes go directly to
// stderr; the aggregate bar (and any per-task summary lines that were already
// flushed) freezes in place at the bottom of the live region.
//
// Idempotent — safe to call from ChecksStart and again from Close.
func (r *richReporter) finalizeSnapshotRenderer() {
	r.mu.Lock()
	if r.snapDone {
		r.mu.Unlock()
		return
	}
	r.snapDone = true
	r.mu.Unlock()
	// Wait blocks until every bar is in a terminal state (complete or aborted)
	// and the render goroutine drains any pending writer output before
	// returning. After this point, r.p must not be written to.
	r.p.Wait()
}

func (r *richReporter) ChecksStart(total int) {
	r.finalizeSnapshotRenderer()
	r.mu.Lock()
	defer r.mu.Unlock()
	fmt.Fprintf(r.w, "Running %d checks…\n", total)
}

func (r *richReporter) CheckCompleted(_, _ int, name string, findings int, dur time.Duration) {
	// In rich mode, only surface checks that actually flagged something.
	// Clean checks stay quiet — they appear in the final report regardless.
	if findings == 0 {
		return
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	fmt.Fprintf(r.w, "  %s %s  %d finding(s)  %s\n",
		r.colors.Warn("!"),
		name,
		findings,
		shared.FormatDuration(dur),
	)
}

func (r *richReporter) ChecksComplete(total, withFindings int, dur time.Duration) {
	marker := r.colors.Pass("✓")
	if withFindings > 0 {
		marker = r.colors.Warn("!")
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	fmt.Fprintf(r.w, "%s Checks complete  %d/%d  %d with findings  %s\n",
		marker, total, total, withFindings, shared.FormatDuration(dur),
	)
}

// Close drains the renderer if the checks phase never began (e.g. snapshot
// load failed). When ChecksStart has already torn the renderer down, Close
// is effectively a no-op. Safe to call more than once.
func (r *richReporter) Close() error {
	r.mu.Lock()
	already := r.closed
	r.closed = true
	r.mu.Unlock()
	if already {
		return r.closeErr
	}
	r.finalizeSnapshotRenderer()
	return nil
}
