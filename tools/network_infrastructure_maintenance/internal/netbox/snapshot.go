package netbox

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"sync"
	"time"
)

// ObjectTypeInterface is the NetBox content-type string for dcim.interface.
const ObjectTypeInterface = "dcim.interface"

// LoadObserver receives progress notifications during snapshot loading.
type LoadObserver interface {
	SnapshotAttemptStart(attempt, maxAttempts, taskCount int)
	SnapshotTaskStart(name string)
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
		lastErr = errors.New("Ran out of attempts to capture a coherent snapshot. Gave up.")
	}
	return Snapshot{}, lastErr
}

type snapshotTask struct {
	name string
	run  func(context.Context, *Client, *Snapshot) (FetchTiming, error)
}

func fetchTask[T any](name, path string, set func(*Snapshot, []T)) snapshotTask {
	return snapshotTask{
		name: name,
		run: func(ctx context.Context, c *Client, s *Snapshot) (FetchTiming, error) {
			data, stats, err := fetchAll[T](ctx, c, path)
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
		if obs != nil {
			obs.SnapshotTaskStart(task.name)
		}
		wg.Add(1)
		go func() {
			defer wg.Done()
			stats, err := task.run(ctx, c, &snap)
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
	var errs []string
	for result := range results {
		if result.err != nil {
			errs = append(errs, fmt.Sprintf("%s: %v", result.name, result.err))
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
		return Snapshot{}, errors.New(strings.Join(errs, "; "))
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
}

func fetchAll[T any](ctx context.Context, client *Client, path string) ([]T, FetchTiming, error) {
	started := time.Now()
	out, requests, pages, err := FetchAll[T](ctx, client, path)
	if err != nil {
		return nil, FetchTiming{}, err
	}
	return out, FetchTiming{Requests: requests, Pages: pages, Items: len(out), Duration: time.Since(started)}, nil
}
