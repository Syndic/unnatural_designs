package main

import (
	"fmt"
	"io"
	"os"
	"time"

	netbox "github.com/Syndic/unnatural_designs/tools/network_infrastructure_maintenance/internal/netbox"
	"github.com/Syndic/unnatural_designs/tools/network_infrastructure_maintenance/internal/shared"
)

func totalFindings(rep report) int {
	total := 0
	for _, check := range rep.Checks {
		total += len(check.Findings)
		for _, drift := range check.Extra {
			total += len(drift.Details)
		}
	}
	return total
}

func printTextReport(rep report, colors shared.Colorizer) {
	writeTextReport(os.Stdout, rep, colors)
}

func writeTextReport(w io.Writer, rep report, colors shared.Colorizer) {
	checksWithFindings := 0
	for _, check := range rep.Checks {
		if len(check.Findings) > 0 || len(check.Extra) > 0 {
			checksWithFindings++
		}
	}

	_, _ = fmt.Fprintf(w, "Snapshot: %d attempt(s), latest change #%d\n", rep.Snapshot.Attempts, rep.Snapshot.Change.ID)
	_, _ = fmt.Fprintf(w, "Checks: %d\n", len(rep.Checks))
	_, _ = fmt.Fprintf(w, "Checks with findings: %d\n", checksWithFindings)
	_, _ = fmt.Fprintf(w, "Total findings: %d\n", totalFindings(rep))
	_, _ = fmt.Fprintf(w, "Timing: total=%s, snapshot=%s (%d requests)\n",
		shared.FormatDuration(rep.Timing.Total),
		shared.FormatDuration(rep.Timing.Snapshot.Duration),
		rep.Timing.Snapshot.RequestCount,
	)

	if len(rep.Timing.Snapshot.Fetches) > 0 {
		_, _ = fmt.Fprintf(w, "Snapshot collections by duration:\n")
		for _, fetch := range sortTimingDescending(
			rep.Timing.Snapshot.Fetches,
			func(f netbox.FetchTiming) time.Duration { return f.Duration },
		) {
			_, _ = fmt.Fprintf(w,
				"- %s: %s, %d requests, %d items\n",
				fetch.Name,
				shared.FormatDuration(fetch.Duration),
				fetch.Requests,
				fetch.Items,
			)
		}
	}
	if len(rep.Timing.Checks) > 0 {
		_, _ = fmt.Fprintf(w, "Check durations:\n")
		for _, timing := range sortTimingDescending(
			rep.Timing.Checks,
			func(t checkTiming) time.Duration { return t.Duration },
		) {
			_, _ = fmt.Fprintf(w, "- %s: %s, %d findings\n", timing.Name, shared.FormatDuration(timing.Duration), timing.Findings)
		}
	}

	for _, check := range rep.Checks {
		status := shared.StatusPass
		if len(check.Findings) > 0 || len(check.Extra) > 0 {
			status = shared.StatusWarn
		}
		coloredStatus := colors.Pass(status)
		if status == shared.StatusWarn {
			coloredStatus = colors.Warn(status)
		}
		count := len(check.Findings)
		for _, drift := range check.Extra {
			count += len(drift.Details)
		}
		_, _ = fmt.Fprintf(w, "\n[%s] %s (%d)\n", coloredStatus, check.Name, count)
		for _, finding := range check.Findings {
			_, _ = fmt.Fprintf(w, "- %s\n", finding)
		}
		for _, drift := range check.Extra {
			_, _ = fmt.Fprintf(w, "- %s (%s)\n", drift.Device, drift.Model)
			for _, detail := range drift.Details {
				_, _ = fmt.Fprintf(w, "  %s\n", detail)
			}
		}
	}
}
