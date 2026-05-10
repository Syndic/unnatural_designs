package netbox

import (
	"context"
	"errors"
	"fmt"
	"sync"
	"time"
)

// ObjectTypeInterface is the NetBox content-type string for dcim.interface.
const ObjectTypeInterface = "dcim.interface"

// TaskProgress is the per-task callback returned from
// LoadObserver.SnapshotTaskStart. It is invoked once per page response while
// the named task is in flight; observers that don't track per-page progress
// may return nil from SnapshotTaskStart.
type TaskProgress = PageProgressFunc

// LoadObserver receives progress notifications during snapshot loading.
type LoadObserver interface {
	SnapshotAttemptStart(attempt, maxAttempts, taskCount int)
	// SnapshotTaskStart announces that the named fetch is about to begin and
	// returns a per-task progress callback (or nil if the observer doesn't
	// need per-page updates).
	SnapshotTaskStart(name string) TaskProgress
	SnapshotTaskComplete(completed, total int, stats FetchTiming, totalRequests int)
	SnapshotLoadError(attempt, maxAttempts int, err error)
	SnapshotLoadRetryDelay(delay time.Duration)
}

// LoadConsistentSnapshot fetches a consistent snapshot from NetBox, retrying
// up to maxAttempts times if the data changes during the fetch.
func LoadConsistentSnapshot(ctx context.Context, client *Client, maxAttempts int, retryDelay time.Duration, obs LoadObserver) (Snapshot, error) {
	var lastErr error
	var totalStart = time.Now()
	for attempt := 1; attempt <= maxAttempts; attempt++ {
		startChange, err := client.LatestChange(ctx)
		if err != nil {
			return Snapshot{}, err
		}
		if obs != nil {
			obs.SnapshotAttemptStart(attempt, maxAttempts, SnapshotTaskCount())
		}
		snap, err := loadSnapshot(ctx, client, obs)
		if err != nil {
			if obs != nil {
				obs.SnapshotLoadError(attempt, maxAttempts, err)
			}
			lastErr = err
			continue
		}
		endChange, err := client.LatestChange(ctx)
		if err != nil {
			return Snapshot{}, err
		}
		if startChange.ID == endChange.ID {
			snap.LatestChange = endChange
			snap.SnapshotAttempts = attempt
			snap.LoadStats.Duration = time.Since(totalStart)
			return snap, nil
		}
		lastErr = fmt.Errorf("NetBox state changed during load (%d -> %d)", startChange.ID, endChange.ID)
		if obs != nil {
			obs.SnapshotLoadError(attempt, maxAttempts, lastErr)
		}
		if attempt < maxAttempts {
			obs.SnapshotLoadRetryDelay(retryDelay)
			time.Sleep(retryDelay)
		}
	}
	if lastErr == nil {
		lastErr = errors.New("ran out of attempts to capture a coherent snapshot and gave up")
	}
	return Snapshot{}, lastErr
}

type snapshotTask struct {
	name string
	run  func(context.Context, *Client, *Snapshot, PageProgressFunc) (FetchTiming, error)
}

func fetchTask[T any](name, path string, set func(*Snapshot, []T)) snapshotTask {
	return snapshotTask{
		name: name,
		run: func(ctx context.Context, c *Client, s *Snapshot, progress PageProgressFunc) (FetchTiming, error) {
			data, stats, err := fetchAll[T](ctx, c, path, progress)
			set(s, data)
			return stats, err
		},
	}
}

func fetchDcimTask[T any](name string, set func(*Snapshot, []T)) snapshotTask {
	return fetchTask(name, "/api/dcim/"+name+"/", set)
}

func fetchIpamTask[T any](name string, set func(*Snapshot, []T)) snapshotTask {
	return fetchTask(name, "/api/ipam/"+name+"/", set)
}

var tasks = []snapshotTask{
	fetchDcimTask("devices", func(s *Snapshot, v []Device) { s.Devices = v }),
	fetchDcimTask("interfaces", func(s *Snapshot, v []Iface) { s.Interfaces = v }),
	fetchDcimTask("interface-templates", func(s *Snapshot, v []InterfaceTemplate) { s.InterfaceTemplates = v }),
	fetchDcimTask("console-ports", func(s *Snapshot, v []TypedComponent) { s.ConsolePorts = v }),
	fetchDcimTask("console-port-templates", func(s *Snapshot, v []TypedComponentTemplate) { s.ConsolePortTemplates = v }),
	fetchDcimTask("console-server-ports", func(s *Snapshot, v []TypedComponent) { s.ConsoleServerPorts = v }),
	fetchDcimTask("console-server-port-templates", func(s *Snapshot, v []TypedComponentTemplate) { s.ConsoleServerPortTemplates = v }),
	fetchDcimTask("power-ports", func(s *Snapshot, v []TypedComponent) { s.PowerPorts = v }),
	fetchDcimTask("power-port-templates", func(s *Snapshot, v []TypedComponentTemplate) { s.PowerPortTemplates = v }),
	fetchDcimTask("power-outlets", func(s *Snapshot, v []TypedComponent) { s.PowerOutlets = v }),
	fetchDcimTask("power-outlet-templates", func(s *Snapshot, v []TypedComponentTemplate) { s.PowerOutletTemplates = v }),
	fetchDcimTask("front-ports", func(s *Snapshot, v []FrontPort) { s.FrontPorts = v }),
	fetchDcimTask("front-port-templates", func(s *Snapshot, v []FrontPortTemplate) { s.FrontPortTemplates = v }),
	fetchDcimTask("rear-ports", func(s *Snapshot, v []RearPort) { s.RearPorts = v }),
	fetchDcimTask("rear-port-templates", func(s *Snapshot, v []RearPortTemplate) { s.RearPortTemplates = v }),
	fetchDcimTask("device-bays", func(s *Snapshot, v []NamedComponent) { s.DeviceBays = v }),
	fetchDcimTask("device-bay-templates", func(s *Snapshot, v []NamedComponentTemplate) { s.DeviceBayTemplates = v }),
	fetchDcimTask("module-bays", func(s *Snapshot, v []ModuleBay) { s.ModuleBays = v }),
	fetchDcimTask("module-bay-templates", func(s *Snapshot, v []NamedComponentTemplate) { s.ModuleBayTemplates = v }),
	fetchDcimTask("modules", func(s *Snapshot, v []Module) { s.Modules = v }),
	fetchIpamTask("ip-addresses", func(s *Snapshot, v []IPAddress) { s.IPAddresses = v }),
	fetchIpamTask("ip-ranges", func(s *Snapshot, v []IPRange) { s.IPRanges = v }),
	fetchIpamTask("prefixes", func(s *Snapshot, v []Prefix) { s.Prefixes = v }),
	fetchDcimTask("cables", func(s *Snapshot, v []Cable) { s.Cables = v }),
	fetchDcimTask("mac-addresses", func(s *Snapshot, v []MACAddressRecord) { s.MACAddresses = v }),
}

func snapshotTasks() []snapshotTask {
	return tasks
}

// SnapshotTaskCount returns the number of parallel fetch tasks in a snapshot load.
func SnapshotTaskCount() int {
	return len(tasks)
}

func loadSnapshot(ctx context.Context, c *Client, obs LoadObserver) (Snapshot, error) {
	var snap Snapshot
	tasks := snapshotTasks()
	type taskResult struct {
		name  string
		stats FetchTiming
		err   error
	}
	results := make(chan taskResult, len(tasks))
	var wg sync.WaitGroup
	for _, task := range tasks {
		task := task
		var progress PageProgressFunc
		if obs != nil {
			progress = obs.SnapshotTaskStart(task.name)
		}
		wg.Add(1)
		go func() {
			defer wg.Done()
			stats, err := task.run(ctx, c, &snap, progress)
			stats.Name = task.name
			results <- taskResult{name: task.name, stats: stats, err: err}
		}()
	}

	go func() {
		wg.Wait()
		close(results)
	}()

	completedTasks := 0
	totalRequests := 0
	var fetches []FetchTiming
	var errs []error
	for result := range results {
		if result.err != nil {
			// Wrap with %w so callers can errors.Is/As against the underlying
			// per-task error (e.g. context.Canceled from a cancelled fetch).
			errs = append(errs, fmt.Errorf("%s: %w", result.name, result.err))
			continue
		}
		completedTasks++
		totalRequests += result.stats.Requests
		fetches = append(fetches, result.stats)
		if obs != nil {
			obs.SnapshotTaskComplete(completedTasks, len(tasks), result.stats, totalRequests)
		}
	}
	if len(errs) > 0 {
		return Snapshot{}, errors.Join(errs...)
	}
	snap.LoadStats.RequestCount = totalRequests
	snap.LoadStats.Fetches = fetches
	snap.buildIndexes()
	return snap, nil
}

// buildIndexes pre-computes lookup maps that are used by multiple audit
// checks. Building them here — once, after all data is fetched — means the
// parallel checks can share read-only maps instead of each independently
// iterating the same slices.
func (s *Snapshot) buildIndexes() {
	s.DevicesByID = make(map[int]Device, len(s.Devices))
	for _, d := range s.Devices {
		s.DevicesByID[d.ID] = d
	}

	s.InterfacesByID = make(map[int]Iface, len(s.Interfaces))
	s.InterfacesByDevice = make(map[int][]Iface)
	for _, it := range s.Interfaces {
		s.InterfacesByID[it.ID] = it
		s.InterfacesByDevice[it.Device.ID] = append(s.InterfacesByDevice[it.Device.ID], it)
	}

	s.IPsByInterface = make(map[int][]IPAddress)
	for _, ip := range s.IPAddresses {
		if ip.AssignedObjectType == ObjectTypeInterface {
			s.IPsByInterface[ip.AssignedObjectID] = append(s.IPsByInterface[ip.AssignedObjectID], ip)
		}
	}

	s.ModuleBaysByID = make(map[int]ModuleBay, len(s.ModuleBays))
	for _, mb := range s.ModuleBays {
		s.ModuleBaysByID[mb.ID] = mb
	}

	s.ModulesByDevice = make(map[int][]Module)
	s.ModulesByBay = make(map[int][]Module)
	for _, m := range s.Modules {
		s.ModulesByDevice[m.Device.ID] = append(s.ModulesByDevice[m.Device.ID], m)
		// Only index modules whose bay actually exists in the snapshot. Modules referencing
		// a missing bay are surfaced as findings by ModuleConsistency, not silently grouped.
		if m.ModuleBay != nil {
			if _, ok := s.ModuleBaysByID[m.ModuleBay.ID]; ok {
				s.ModulesByBay[m.ModuleBay.ID] = append(s.ModulesByBay[m.ModuleBay.ID], m)
			}
		}
	}
}

func fetchAll[T any](ctx context.Context, client *Client, path string, progress PageProgressFunc) ([]T, FetchTiming, error) {
	started := time.Now()
	out, requests, pages, err := FetchAllWithProgress[T](ctx, client, path, progress)
	if err != nil {
		return nil, FetchTiming{}, err
	}
	return out, FetchTiming{Requests: requests, Pages: pages, Items: len(out), Duration: time.Since(started)}, nil
}
