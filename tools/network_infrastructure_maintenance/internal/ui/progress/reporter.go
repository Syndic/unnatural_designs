// Package progress renders snapshot-fetch and check-execution progress to
// the user during a netbox_audit run. It exposes two implementations of
// Reporter: a rich, mpb-driven sticky-panel renderer for interactive
// terminals, and a plain line-oriented logger for CI / piped output.
package progress

import (
	"fmt"
	"os"
	"strings"
	"time"

	netbox "github.com/Syndic/unnatural_designs/tools/network_infrastructure_maintenance/internal/netbox"
	"github.com/Syndic/unnatural_designs/tools/network_infrastructure_maintenance/internal/shared"
)

// Mode selects the rendering strategy.
type Mode int

const (
	// ModeAuto picks rich for interactive terminals, plain otherwise.
	ModeAuto Mode = iota
	// ModeRich forces the mpb sticky-panel renderer.
	ModeRich
	// ModePlain forces the line-oriented renderer.
	ModePlain
	// ModeOff suppresses all progress output.
	ModeOff
)

// Mode string constants used for the -progress CLI flag.
const (
	ModeAutoName  = "auto"
	ModeRichName  = "rich"
	ModePlainName = "plain"
	ModeOffName   = "off"
)

// ParseMode parses a CLI flag value into a Mode. Returns an error for
// unrecognized values.
func ParseMode(s string) (Mode, error) {
	switch strings.ToLower(strings.TrimSpace(s)) {
	case "", ModeAutoName:
		return ModeAuto, nil
	case ModeRichName:
		return ModeRich, nil
	case ModePlainName:
		return ModePlain, nil
	case ModeOffName:
		return ModeOff, nil
	default:
		return ModeAuto, fmt.Errorf("expected %s, %s, %s, or %s", ModeAutoName, ModeRichName, ModePlainName, ModeOffName)
	}
}

// Reporter receives lifecycle events for the snapshot-load and check-execution
// phases of a netbox_audit run.
type Reporter interface {
	netbox.LoadObserver

	// Startupf prints a one-line banner before any progress UI is drawn.
	Startupf(format string, args ...any)

	// AnnounceChecks records which checks are about to run. In rich mode this
	// is summarised; in plain mode it is logged in full.
	AnnounceChecks(ids []string)

	// ChecksStart and ChecksComplete bracket the check-execution phase.
	ChecksStart(total int)
	CheckCompleted(done, total int, name string, findings int, dur time.Duration)
	ChecksComplete(total, withFindings int, dur time.Duration)

	// Close flushes any pending render output and tears the renderer down.
	// Safe to call more than once.
	Close() error
}

// New returns a Reporter for the given mode. ModeAuto resolves against
// stderr's TTY status and the NO_COLOR / TERM environment.
func New(stderr *os.File, mode Mode, colors shared.Colorizer) Reporter {
	resolved := Resolve(mode, stderr)
	switch resolved {
	case ModeRich:
		return newRichReporter(stderr, colors)
	case ModeOff:
		return offReporter{}
	default:
		return newPlainReporter(stderr, colors)
	}
}

// offReporter discards every event. Used for ModeOff.
type offReporter struct{}

func (offReporter) SnapshotAttemptStart(int, int, int)                              {}
func (offReporter) SnapshotTaskStart(string) netbox.TaskProgress                    { return nil }
func (offReporter) SnapshotTaskComplete(int, int, netbox.FetchTiming, int)          {}
func (offReporter) SnapshotLoadError(int, int, error)                               {}
func (offReporter) SnapshotLoadRetryDelay(time.Duration)                            {}
func (offReporter) Startupf(string, ...any)                                         {}
func (offReporter) AnnounceChecks([]string)                                         {}
func (offReporter) ChecksStart(int)                                                 {}
func (offReporter) CheckCompleted(int, int, string, int, time.Duration)             {}
func (offReporter) ChecksComplete(int, int, time.Duration)                          {}
func (offReporter) Close() error                                                    { return nil }

// Resolve collapses ModeAuto to a concrete mode based on the runtime
// environment. Other modes are returned unchanged.
func Resolve(mode Mode, stderr *os.File) Mode {
	if mode != ModeAuto {
		return mode
	}
	if os.Getenv(shared.EnvNoColor) != "" {
		return ModePlain
	}
	if term := os.Getenv("TERM"); term == "" || term == shared.TermDumb {
		return ModePlain
	}
	if !shared.IsTerminal(stderr) {
		return ModePlain
	}
	return ModeRich
}
