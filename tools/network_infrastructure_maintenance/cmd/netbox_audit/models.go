package main

import (
	"time"

	audit "github.com/Syndic/unnatural_designs/tools/network_infrastructure_maintenance/internal/audit"
	netbox "github.com/Syndic/unnatural_designs/tools/network_infrastructure_maintenance/internal/netbox"
)

type snapshotMeta struct {
	Attempts int                 `json:"attempts"`
	Change   netbox.ObjectChange `json:"latest_change"`
}

type checkTiming struct {
	ID       string
	Name     string
	Duration time.Duration
	Findings int
}

type reportTiming struct {
	Total    time.Duration
	Snapshot netbox.SnapshotLoadStats
	Checks   []checkTiming
}

type report struct {
	Snapshot snapshotMeta        `json:"snapshot"`
	Checks   []audit.CheckResult `json:"checks"`
	Timing   reportTiming        `json:"-"`
}
